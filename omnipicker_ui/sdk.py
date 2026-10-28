"""Standalone FR3 + OmniPicker SDK. No Flask, WebApp, USB, or Lua file dependency."""
import importlib
import ipaddress
import math
import os
import sys
import threading
import time
import xmlrpc.client
from contextlib import contextmanager
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .robot_control import RobotControl
from .connection import GripperConnection
from .kinematics import forward, inverse, cad_limits
from .validation import number, increment
from .rs485 import RS485Gripper
from .native import NativeTCP
from .api import FR3API, OmniPickerAPI, vendor_catalog, TRACKED_MOTION, bind_vendor_arguments
from .motion import GripperCommand, GripperLimits, completion_options


def checked(reply, name, data=False):
    code = reply[0] if isinstance(reply, (tuple, list)) else reply
    if code != 0:
        raise RuntimeError(f"{name} 실패 (SDK 코드 {code})")
    return reply[1] if data else reply


class OneShotTransport(xmlrpc.client.Transport):
    """Bounded calls without XML-RPC's implicit retry of an ambiguous write."""
    def __init__(self, timeout=3):
        super().__init__()
        self.timeout=timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection

    def request(self, host, handler, body, verbose=False):
        try:
            return self.single_request(host, handler, body, verbose)
        except (OSError, xmlrpc.client.ProtocolError) as exc:
            # The SDK retries socket.error indefinitely. Raise a different type.
            raise RuntimeError("FR3 통신 시간 초과/연결 오류. 명령은 자동 재전송하지 않습니다.") from exc


class IndependentRPC:
    """Each SDK call owns a connection, so StopMotion can bypass MoveJ."""
    def __init__(self, ip):
        self.url = f"http://{ip}:20003"
        self.local=threading.local()

    @contextmanager
    def timeout(self, seconds):
        seconds=number(seconds,.1,600,'RPC 시간 제한')
        previous=getattr(self.local,'timeout',3)
        self.local.timeout=seconds
        try: yield
        finally: self.local.timeout=previous

    def __getattr__(self, name):
        def call(*args):
            with xmlrpc.client.ServerProxy(self.url, transport=OneShotTransport(getattr(self.local,'timeout',3))) as proxy:
                return getattr(proxy, name)(*args)
        return call


def load_sdk(sdk_path=None):
    paths = [sdk_path or os.getenv("FAIRINO_SDK_PATH", ""),
             str(Path.home() / "fr5_teleop/fairino-python-sdk-2.2.8_robot_v3.9.8/linux")]
    for path in paths:
        if path and (Path(path) / "fairino/Robot.py").is_file() and path not in sys.path:
            sys.path.insert(0, path)
            break
    try:
        return importlib.import_module("fairino.Robot")
    except ImportError as exc:
        raise RuntimeError("FAIRINO SDK가 필요합니다. FAIRINO_SDK_PATH를 SDK의 linux 폴더로 설정하세요.") from exc


class DemoRobot:
    def __init__(self):
        self.robot = self
        self.joints = [0., -65., 100., -125., -90., 0.]
        self.grip_position, self.grip_speed, self.grip_torque = 127, 0, 0
        self.read_address, self.read_count = 0, 1
        self.stopped = threading.Event()
        self.writes = []
        self.registers = {address: 0 for address in range(10, 16)}

    def GetActualJointPosDegree(self): return 0, self.joints[:]
    def GetActualTCPPose(self): return 0, forward(self.joints)
    def GetForwardKin(self,joints): return 0, forward(joints)
    def GetInverseKinRef(self,kind,target,reference): return 0, inverse(target,reference)
    def GetJointSoftLimitDeg(self): return 0, [v for pair in cad_limits() for v in pair]
    def GetActualTCPNum(self): return 0, 0
    def GetActualWObjNum(self): return 0, 0
    def GetTCPOffset(self): return 0, [0.]*6
    def MoveL(self,target,joint_pos=None,**kwargs):
        return self.MoveJ(joint_pos if joint_pos is not None else inverse(target,self.joints),**kwargs)
    def GetExDevProtocol(self): return 0, 4098
    def GetProgramState(self): return 0, 1
    def GetLoadedProgram(self): return 0, ""

    def ModbusRegRead(self, function, address, count, slave, thread):
        self.read_address, self.read_count = address, count
        return 0

    def ModbusRegGetData(self, count, thread):
        registers = {**self.registers,0:335,20:0,21:0,22:self.grip_position,23:self.grip_speed,24:self.grip_torque}
        return [0, *[registers.get(self.read_address+i,0) for i in range(count)]]

    def ModbusRegWrite(self, function, address, count, values, slave, thread):
        self.writes.append((function, address, count, values[:], slave, thread))
        self.registers.update({address+i:value for i,value in enumerate(values)})
        if self.registers[15]:
            self.grip_position, self.grip_speed, self.grip_torque = [self.registers[a] for a in (10,11,12)]
            self.registers[15] = 0
        return 0

    def write_registers(self, address, values, slave=1):
        return self.ModbusRegWrite(16,address,len(values),values,slave,1)

    def MoveJ(self, target, **kwargs):
        self.stopped.clear()
        start = self.joints[:]
        for step in range(1, 21):
            if self.stopped.wait(.025): return 1
            self.joints = [a+(b-a)*step/20 for a,b in zip(start,target)]
        return 0

    def StopMotion(self):
        self.stopped.set()
        return 0


class OmniPickerFR3(GripperConnection,RobotControl):
    """Joint jog and gripper position/speed/torque through one LAN connection.

    Constructing or connecting never enables motors or sends motion. Call arm()
    after checking the work area. Values for gripper speed/torque are RAW units.
    """
    def __init__(self, ip="192.168.57.2", slave_id=1, demo=False, sdk_path=None, gripper_limits=None):
        ipaddress.IPv4Address(ip)
        slave_id = number(slave_id,1,247,"슬레이브 주소",True)
        self.demo, self.slave_id, self.sdk_path = demo, slave_id, sdk_path
        self.gripper_limits=gripper_limits or GripperLimits()
        if not isinstance(self.gripper_limits,GripperLimits): raise TypeError('GripperLimits 필요')
        self.fr3=FR3API(self)
        self.omnipicker=OmniPickerAPI(self)
        self._sdk_module=None
        self.closed = threading.Event()
        self.monitor_thread = None
        self.native = None
        self.native_version_valid=False
        self.recovering=False
        self.transport_error = ""
        self.lock = threading.RLock()
        self.client = None
        self.gripper = None
        self.connecting = self.connected = False
        self.ip, self.error = ip, ""
        self.joints = self.pose = self.target_joints = None
        self.updated = self.enabled_until = 0
        self.epoch = 0
        self.robot_busy = self.robot_fault = self.gripper_busy = False
        self.protocol = self.program = self.program_state = None
        self.diagnosed_at = 0
        self.events, self.event_id = deque(maxlen=80), 0
        self.init_robot_control()
        self.event("시스템", "DEMO" if self.demo else "연결 대기")

    def event(self, source, message, level="info"):
        with self.lock:
            self.event_id += 1
            self.events.appendleft(dict(id=self.event_id,time=time.time(),source=source,message=message,level=level))

    def snapshot(self):
        with self.lock:
            fresh = self.connected and time.monotonic()-self.updated < 3
            grip = self.gripper.snapshot() if self.gripper else dict(connected=False,feedback_valid=False,
                actual_position=None,error_code=None,motion_state=None,fault=False,busy=False,target=None,
                firmware=None,slave_id=self.slave_id,reason="FR3 미연결",last="",transport="FR3 TCP/RPC → RS485")
            if not fresh:
                grip.update(connected=False,feedback_valid=False,actual_position=None,reason="FR3 미연결")
            if self.external_bus_pause:
                grip.update(connected=False,feedback_valid=False,actual_position=None,reason='USB 설정 중 · RS485 일시 중지')
            grip.update(protocol=self.protocol,program=self.program,program_state=self.program_state,
                        transport_error=self.transport_error,recovering=self.recovering,busy=self.gripper_busy or grip['busy'])
            return dict(mode="demo" if self.demo else "hardware",ip=self.ip,
                robot=dict(connected=fresh,connecting=self.connecting,error=self.error,
                    joints=self.joints if fresh else None,pose=self.pose if fresh else None,
                    target_joints=self.target_joints,busy=self.robot_busy,fault=self.robot_fault,
                    armed=fresh and time.monotonic()<self.enabled_until,
                    seconds=max(0,math.ceil(self.enabled_until-time.monotonic())) if fresh else 0,
                    usb_settings_active=self.external_bus_pause,limits=self.motion_limits(),active_tool=self.active_tool,active_user=self.active_user,tcp_offset=self.tcp_offset),
                gripper=grip,events=list(self.events))

    def connect(self, ip=None, wait=True, timeout=15):
        ip = ip or self.ip
        ipaddress.IPv4Address(ip)
        with self.lock:
            if self.closed.is_set(): raise RuntimeError("종료된 SDK 객체")
            if self.connecting or self.client is not None:
                raise RuntimeError("이미 연결했거나 연결 중 · 재연결은 서버 재시작")
            self.connecting, self.ip = True, ip
        threading.Thread(target=self._connect,daemon=True).start()
        if wait:
            deadline = time.monotonic()+timeout
            while self.connecting and time.monotonic()<deadline:
                time.sleep(.02)
            if self.connecting or not self.connected:
                raise RuntimeError(self.error or "FR3 연결 시간 초과")
        return self

    def _connect(self):
        try:
            if self.demo:
                client = DemoRobot()
            else:
                client = self.vendor_module().RPC(self.ip)
                client.SetReConnectParam(False,1,100)
                client.robot = IndependentRPC(self.ip)
            with self.lock:
                self.client = client
                self.gripper = RS485Gripper(client.robot,self.slave_id,delay=0 if self.demo else .15,
                    write_verified=self.demo, writer=client.write_registers if self.demo else None,
                    limits=self.gripper_limits)
            self.poll()
            try: self.read_motion_configuration()
            except Exception as exc: self.event("FR3",str(exc),"error")
            if not self.demo:
                try:
                    self.native = NativeTCP(self.ip)
                    self.verify_gripper_transport(refresh_version=True)
                except Exception as exc:
                    self.transport_error = str(exc)
                    self.event("RS485",str(exc),"error")
            self.event("FR3", "연결됨")
            if not self.closed.is_set():
                self.monitor_thread = threading.Thread(target=self._monitor,daemon=True)
                self.monitor_thread.start()
        except Exception as exc:
            with self.lock:
                self.error, self.connected = str(exc), False
            self.event("연결",str(exc),"error")
        finally:
            self.connecting = False

    def poll(self):
        joints = checked(self.client.GetActualJointPosDegree(),"관절 조회",True)
        pose = checked(self.client.GetActualTCPPose(),"TCP 조회",True)
        if len(joints)!=6 or len(pose)!=6 or not all(math.isfinite(float(v)) for v in [*joints,*pose]):
            raise RuntimeError("FR3 위치 응답 형식 오류")
        with self.lock:
            self.joints,self.pose,self.connected,self.updated,self.error = list(joints),list(pose),True,time.monotonic(),""
        program_state=checked(self.client.robot.GetProgramState(),'프로그램 상태',True)
        self.program_state=program_state
        # The legacy RPC read adapter waits for arm-idle. A monitor read must
        # not seize the RS485 lock before a concurrent native write can run.
        # A user program may also own the controller's shared Modbus buffer.
        if not self.robot_busy and not self.gripper_busy and not self.external_bus_pause and program_state==1:
            self.gripper.refresh()
            self.verify_gripper_transport()
        elif program_state!=1:
            with self.gripper.state_lock:
                self.gripper.updated=0
                self.gripper.error='FR3 프로그램 실행 중 · RS485 조회 대기'
        if time.monotonic()-self.diagnosed_at > 5:
            self.read_diagnostics()

    def read_diagnostics(self):
        self.diagnosed_at = time.monotonic()
        for attribute,method in [("protocol","GetExDevProtocol"),("program","GetLoadedProgram"),("program_state","GetProgramState")]:
            try:
                value = checked(getattr(self.client.robot,method)(),method,True)
                with self.lock: setattr(self,attribute,value)
            except Exception: pass

    def _monitor(self):
        while not self.closed.is_set():
            try: self.poll()
            except Exception as exc:
                with self.lock:
                    changed = self.connected
                    self.connected,self.error,self.enabled_until = False,str(exc),0
                    self.epoch += 1
                if changed: self.event("연결",str(exc),"error")
            self.closed.wait(.25)

    def require_enabled(self):
        if self.external_bus_pause: raise RuntimeError("USB 설정 중 · RS485 제어 잠금")
        if not self.snapshot()["robot"]["armed"]: raise RuntimeError("FR3 연결·제어 허용 확인")

    def arm(self):
        with self.lock:
            state = self.snapshot()
            if self.external_bus_pause: raise RuntimeError("USB 설정 중 · 통합 제어 잠금")
            if not state["robot"]["connected"]: raise RuntimeError("FR3 미연결")
            if self.robot_busy or self.robot_fault or self.gripper_busy or state["gripper"]["fault"]:
                raise RuntimeError("처리 중이거나 이전 응답 불확실")
            self.enabled_until = time.monotonic()+60
        self.event("제어","60초 허용")

    def disarm(self):
        with self.lock:
            self.enabled_until = 0
            self.epoch += 1

    def joint(self, axis, delta, velocity=5, tool=0, user=0):
        axis = number(axis,0,5,"관절",True)
        delta = increment(delta)
        velocity = number(velocity,1,100,"속도")
        tool = number(tool,0,14,"툴",True)
        user = number(user,0,14,"좌표계",True)
        with self.lock:
            self.require_enabled()
            if self.robot_busy: raise RuntimeError("FR3 이동 중")
            self.robot_busy, epoch = True,self.epoch
        dispatched = False
        try:
            if checked(self.client.robot.GetProgramState(),"프로그램 상태",True) != 1:
                raise RuntimeError("다른 FR3 프로그램 실행 중")
            target = list(checked(self.client.GetActualJointPosDegree(),"관절 조회",True))
            target[axis] += delta
            self.read_motion_configuration()
            self.validate_joint_target(target)
            with self.lock:
                self.require_enabled()
                if epoch != self.epoch: raise RuntimeError("이동 취소됨")
                self.target_joints = target[:]
            dispatched = True
            with self.fr3.timeout(120):
                checked(self.client.MoveJ(target,tool=tool,user=user,vel=velocity,blendT=-1.),"MoveJ")
            self.event("FR3",f"J{axis+1} {delta:+g}°")
            return dict(target=target)
        except Exception:
            self.robot_fault = dispatched
            self.disarm()
            raise
        finally: self.robot_busy = False

    def grip(self, position, speed, torque, **options):
        return self._grip(position,speed,torque,use_cached=self.robot_busy,**options)

    def _grip(self, position, speed, torque, *, use_cached=False, acceleration=40,deceleration=40,
              wait=True,timeout=8,tolerance=3,accept_contact=False,execute=True):
        cmd=GripperCommand(position,speed,torque,acceleration,deceleration).validate(self.gripper_limits)
        timeout,tolerance=completion_options(wait,timeout,tolerance,accept_contact)
        if type(execute) is not bool: raise ValueError('execute는 bool이어야 합니다.')
        position,speed,torque,acceleration,deceleration=cmd.registers()
        with self.lock:
            self.require_enabled()
            if self.gripper_busy: raise RuntimeError("그리퍼 처리 중")
            if not self.gripper or not self.gripper.snapshot()["connected"]: raise RuntimeError("RS485 응답 없음")
            self.gripper_busy, epoch = True,self.epoch
        try:
            if checked(self.client.robot.GetProgramState(),"프로그램 상태",True) != 1:
                raise RuntimeError("다른 FR3 프로그램 실행 중")
            if checked(self.client.robot.GetExDevProtocol(),"RS485 프로토콜",True) != 4098:
                raise RuntimeError("RS485 Modbus Master 설정 필요")
            def before_commit():
                with self.lock:
                    self.require_enabled()
                    if self.epoch != epoch: raise RuntimeError("그리퍼 전송 취소됨")
            result = self.gripper.command(position,speed,torque,before_commit,use_cached=use_cached,
                acceleration=acceleration,deceleration=deceleration,wait=wait,timeout=timeout,
                tolerance=tolerance,accept_contact=accept_contact,execute=execute)
            self.event("OmniPicker",self.gripper.last)
            return result
        except Exception:
            self.disarm()
            raise
        finally: self.gripper_busy = False

    def probe_gripper(self):
        if self.external_bus_pause: raise RuntimeError("USB 설정 중 · RS485 조회 잠금")
        if not self.client or not self.gripper: raise RuntimeError("FR3 미연결")
        if checked(self.client.robot.GetProgramState(),'프로그램 상태',True)!=1:
            raise RuntimeError('다른 FR3 프로그램 실행 중 · RS485 조회 대기')
        result=self.gripper.probe()
        self.verify_gripper_transport(refresh_version=True)
        return self.gripper.snapshot()

    def _write_gripper_parameter(self, address, value):
        with self.lock:
            self.require_enabled()
            if not self.gripper or self.gripper_busy: raise RuntimeError('그리퍼 연결/처리 상태 확인 필요')
            self.gripper_busy=True
            epoch=self.epoch
        try:
            if checked(self.client.robot.GetProgramState(),'프로그램 상태',True)!=1:
                raise RuntimeError('다른 FR3 프로그램 실행 중')
            if checked(self.client.robot.GetExDevProtocol(),'RS485 프로토콜',True)!=4098:
                raise RuntimeError('RS485 Modbus Master 설정 필요')
            def before_commit():
                with self.lock:
                    self.require_enabled()
                    if self.epoch!=epoch: raise RuntimeError('파라미터 전송 취소됨')
            return self.gripper.write_parameter(address,value,before_commit)
        except Exception:
            self.disarm()
            raise
        finally: self.gripper_busy=False

    def stop(self):
        self.disarm()
        if self.client is None: raise RuntimeError("FR3 미연결")
        checked(self.client.StopMotion(),"StopMotion")
        self.event("FR3","정지 요청 응답")

    def move_together(self, *, axis, delta, position, speed=10, torque=10,
                      velocity=5, tool=0, user=0, acceleration=40,deceleration=40,accept_contact=False):
        """Overlap a joint jog and gripper motion; not a timed servo sync."""
        axis = number(axis,0,5,"관절",True)
        delta = increment(delta)
        velocity = number(velocity,1,100,"속도")
        tool = number(tool,0,14,"툴",True)
        user = number(user,0,14,"좌표계",True)
        cmd=GripperCommand(position,speed,torque,acceleration,deceleration).validate(self.gripper_limits)
        completion_options(True,8,3,accept_contact)
        position,speed,torque,acceleration,deceleration=cmd.registers()
        with self.lock:
            self.require_enabled()
            if self.robot_busy or self.gripper_busy: raise RuntimeError("처리 중")
        if not self.gripper or not self.gripper.write_verified:
            raise RuntimeError("FR3 TCP 쓰기 경로 미확인")
        state = self.probe_gripper()
        if state['error_code'] != 0 or state['motion_state'] != 0:
            raise RuntimeError("그리퍼 상태 확인 필요")
        if checked(self.client.robot.GetProgramState(),"프로그램 상태",True) != 1:
            raise RuntimeError("다른 FR3 프로그램 실행 중")
        if checked(self.client.robot.GetExDevProtocol(),"RS485 프로토콜",True) != 4098:
            raise RuntimeError("RS485 Modbus Master 설정 필요")
        self.read_motion_configuration()
        target=list(checked(self.client.GetActualJointPosDegree(),'관절 조회',True))
        target[axis]+=delta
        self.validate_joint_target(target)
        result = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(self.joint,axis,delta,velocity,tool,user):"robot",
                       pool.submit(self._grip,position,speed,torque,use_cached=True,
                           acceleration=acceleration,deceleration=deceleration,accept_contact=accept_contact):"gripper"}
            try:
                for future in as_completed(futures): result[futures[future]] = future.result()
            except Exception:
                self.disarm()
                for future in futures: future.cancel()
                if self.robot_busy:
                    try: self.stop()
                    except Exception as exc: self.event("정지",str(exc),"error")
                raise
        return result

    def vendor_module(self):
        if self._sdk_module is None: self._sdk_module=load_sdk(self.sdk_path)
        return self._sdk_module

    def parallel(self, fr3_method, *, args=(), kwargs=None, gripper, rpc_timeout=30,
                 accept_contact=False):
        """Run a vendor blocking motion API and an OP1 command concurrently.

        Original motion arguments/return values are preserved. This is not a
        servo-period synchronization API. Streaming methods remain on fr3.*.
        """
        if fr3_method not in TRACKED_MOTION: raise ValueError('parallel은 FR3 블로킹 이동 API를 사용합니다.')
        if not isinstance(args,(list,tuple)) or (kwargs is not None and not isinstance(kwargs,dict)):
            raise TypeError('args는 list/tuple, kwargs는 dict이어야 합니다.')
        bind_vendor_arguments(fr3_method,args,kwargs or {})
        if not isinstance(gripper,GripperCommand): raise TypeError('GripperCommand 필요')
        cmd=gripper.validate(self.gripper_limits)
        rpc_timeout=number(rpc_timeout,.1,600,'RPC 시간 제한')
        completion_options(True,8,3,accept_contact)
        operation=getattr(self.fr3,fr3_method)
        with self.lock:
            self.require_enabled()
            if self.robot_busy or self.gripper_busy: raise RuntimeError('처리 중')
        state=self.probe_gripper()
        if not self.gripper.write_verified or state['error_code']!=0 or state['motion_state']!=0:
            raise RuntimeError('그리퍼 상태 확인 필요')
        if checked(self.client.robot.GetProgramState(),'프로그램 상태',True)!=1:
            raise RuntimeError('다른 FR3 프로그램 실행 중')
        if checked(self.client.robot.GetExDevProtocol(),'RS485 프로토콜',True)!=4098:
            raise RuntimeError('RS485 Modbus Master 설정 필요')
        epoch=self.epoch
        def robot_operation():
            with self.lock:
                self.require_enabled()
                if self.epoch!=epoch: raise RuntimeError('동시 전송 취소됨')
            with self.fr3.timeout(rpc_timeout):
                try:
                    reply=operation(*args,**(kwargs or {}))
                    checked(reply,fr3_method)
                    return reply
                except Exception:
                    self.robot_fault=True
                    self.disarm()
                    raise
        result={}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(robot_operation):'robot',
                     pool.submit(self._grip,**cmd.__dict__,use_cached=True,accept_contact=accept_contact):'gripper'}
            try:
                for future in as_completed(futures): result[futures[future]]=future.result()
            except Exception:
                self.disarm()
                for future in futures: future.cancel()
                if self.robot_busy:
                    try: self.stop()
                    except Exception as exc: self.event('정지',str(exc),'error')
                raise
        return result

    def features(self):
        """Software API coverage; this is not a hardware validation certificate."""
        catalog=vendor_catalog()
        return dict(fr3=dict(interface='sdk.fr3',transport='LAN / original FAIRINO SDK',
                    count=len(catalog['fr3']['methods']),methods=catalog['fr3']['methods'],
                    connected=self.connected,source_sha256=catalog['fr3']['sha256']),
                    omnipicker=dict(interface='sdk.omnipicker',transport='FR3 RS485 Modbus',
                    features=self.omnipicker.features(),connected=bool(self.gripper and self.gripper.snapshot()['connected'])),
                    sources=catalog['sources'])

    def close(self):
        """Release connections and lock new commands. Does not cancel sent motion."""
        self.disarm()
        self.closed.set()
        if self.monitor_thread and self.monitor_thread is not threading.current_thread():
            self.monitor_thread.join(timeout=6)
        if self.client and not self.demo:
            self.client.CloseRPC()
        self.connected = False

    def __enter__(self): return self.connect()
    def __exit__(self, *exc): self.close()
