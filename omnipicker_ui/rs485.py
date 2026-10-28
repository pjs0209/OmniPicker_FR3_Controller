"""OmniPicker register driver: RPC reads and native TCP writes over FR3 RS485."""
import math
import threading
import time
from .validation import number
from .motion import GripperCommand, GripperLimits, completion_options


def success(reply, operation):
    code = reply[0] if isinstance(reply, (list, tuple)) else reply
    if code != 0:
        raise RuntimeError(f"{operation} 실패: {reply}")


def decode_registers(reply, count):
    if not isinstance(reply, (list, tuple)) or not reply or reply[0] != 0:
        raise RuntimeError(f"Modbus 데이터 오류: {reply}")
    values = reply[1:]
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]
    if len(values) != count:
        raise RuntimeError(f"Modbus 응답 길이: {len(values)} / {count}")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 65535 or int(value) != value:
            raise RuntimeError("Modbus 레지스터 값 오류")
        result.append(int(value))
    return result


class RS485Gripper:
    def __init__(self, rpc, slave=1, delay=.15, write_verified=False, writer=None, limits=None):
        self.rpc, self.slave, self.delay = rpc, slave, delay
        self.writer = writer
        self.limits = limits or GripperLimits()
        self.bus_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.updated = 0.
        self.firmware = None
        self.suspended = False
        self.feedback = None
        self.target = None
        self.speed = None
        self.torque = None
        self.acceleration = self.deceleration = None
        self.contact_completed=False
        self.error = "RS485 미확인"
        self.uncertain = False
        self.busy = False
        self.last = ""
        # Never call the defective raw RPC ModbusRegWrite adapter.
        self.write_verified = bool(write_verified and writer is not None)

    def read_registers(self, address, count):
        if self.suspended:raise RuntimeError("USB 설정 중 · RS485 잠금")
        success(self.rpc.ModbusRegRead(3, address, count, self.slave, 0), "Modbus 읽기")
        # The controller reads asynchronously; immediate GetData can return old data.
        if self.delay:
            time.sleep(self.delay)
        return decode_registers(self.rpc.ModbusRegGetData(count, 0), count)

    def _read(self):
        firmware = self.read_registers(0, 1)[0]
        if not 256 <= firmware <= 65535:
            raise RuntimeError(f"그리퍼 펌웨어 응답 없음: {firmware}")
        # Individual reads were verified against firmware 3.3.5. Its raw RPC
        # multi-register response order differs from the Lua interface.
        feedback = [self.read_registers(address, 1)[0] for address in range(20, 25)]
        if feedback[1] > 3 or any(value > 255 for value in feedback[2:]):
            raise RuntimeError(f"그리퍼 상태 응답 형식 오류: {feedback}")
        with self.state_lock:
            self.firmware, self.feedback = firmware, feedback
            self.updated, self.error = time.monotonic(), ""
        return feedback

    def refresh(self):
        if not self.bus_lock.acquire(blocking=False):
            return
        try:
            self._read()
        except Exception as exc:
            with self.state_lock:
                self.updated, self.error = 0, str(exc)
        finally:
            self.bus_lock.release()

    def snapshot(self):
        with self.state_lock:
            fresh = self.feedback is not None and time.monotonic()-self.updated < 3
            data = self.feedback if fresh else None
            return dict(connected=fresh, feedback_valid=fresh,
                        actual_position=data[2] if data else None,
                        error_code=data[0] if data else None, motion_state=data[1] if data else None,
                        actual_speed=data[3] if data else None, actual_torque=data[4] if data else None,
                        firmware=self.firmware, slave_id=self.slave, target=self.target,
                        speed=self.speed, torque=self.torque, fault=self.uncertain, busy=self.busy,
                        acceleration=self.acceleration,deceleration=self.deceleration,
                        contact_completed=bool(fresh and self.contact_completed and data[1]==2),
                        writable=self.write_verified,
                        last=self.last, reason=self.error or (f"그리퍼 오류 {data[0]}" if data and data[0] else
                            "접촉 완료" if data and data[1]==2 and self.contact_completed else
                            "정체" if data and data[1] == 2 else "낙하" if data and data[1] == 3 else
                            "연결됨" if fresh else "RS485 응답 없음"),
                        transport="FR3 TCP/RPC → RS485", reached=bool(data and self.target is not None and data[0] == 0 and data[1] == 0 and abs(data[2]-self.target) <= 3))

    def probe(self):
        with self.bus_lock:
            values = self._read()
            if values[0] == 0 and values[1] == 0:
                self.uncertain = False
        return self.snapshot()

    def command(self, position, speed, torque, before_commit, use_cached=False, *, acceleration=40,
                deceleration=40, wait=True, timeout=8, tolerance=3, accept_contact=False, execute=True):
        if not self.write_verified:
            raise RuntimeError("FR3 TCP 쓰기 경로 미확인 · 구동 차단")
        command=GripperCommand(position,speed,torque,acceleration,deceleration).validate(self.limits)
        timeout,tolerance=completion_options(wait,timeout,tolerance,accept_contact)
        if type(execute) is not bool: raise ValueError('execute는 bool이어야 합니다.')
        position,speed,torque,acceleration,deceleration=command.registers()
        with self.bus_lock:
            if self.uncertain:
                raise RuntimeError("이전 명령 응답 불확실 · 실제 상태 확인 후 재조회 필요")
            self.busy = True
            committed = False
            try:
                if use_cached:
                    # The legacy RPC read adapter waits for an idle arm even if
                    # a thread flag is supplied. During MoveJ, use the recent
                    # preflight sample so the native write can actually overlap
                    # arm motion; only completion verification may wait.
                    with self.state_lock:
                        if self.feedback is None or time.monotonic()-self.updated >= 3:
                            raise RuntimeError("동시 제어 전 그리퍼 상태 재조회 필요")
                        values = self.feedback[:]
                else:
                    values = self._read()
                if values[0] != 0:
                    raise RuntimeError(f"그리퍼 오류 코드: {values[0]}")
                before_commit()
                committed = True
                if execute: self.contact_completed=False
                success(self.writer(10, command.registers()+([1] if execute else []), self.slave), "Modbus 쓰기")
                if execute:
                    self.target,self.speed,self.torque=position,speed,torque
                    self.acceleration,self.deceleration=acceleration,deceleration
                # Native acknowledgment means the instruction was accepted.
                # Verify the actual device's parameter registers independently.
                parameters = [self.read_registers(address, 1)[0] for address in range(10, 15)]
                if parameters != command.registers():
                    raise RuntimeError(f"그리퍼 명령 레지스터 불일치: {parameters}")
                if not execute:
                    return dict(staged=True,trigger_sent=False,readback_verified=True,parameters=parameters)
                if not wait:
                    return dict(accepted=True,completed=False,reached=False,readback_verified=True,
                                target=position,speed=speed,torque=torque,acceleration=acceleration,deceleration=deceleration)
                return self._wait(position,timeout,tolerance,accept_contact)
            except Exception as exc:
                self.last = str(exc)
                if committed:
                    self.uncertain = True
                raise
            finally:
                self.busy = False

    def _wait(self, position, timeout, tolerance, accept_contact):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            values=self._read()
            if values[0] != 0: raise RuntimeError(f'그리퍼 오류 코드: {values[0]}')
            contact=values[1]==2
            reached=values[1]==0 and abs(values[2]-position)<=tolerance
            if reached or (contact and accept_contact):
                self.contact_completed=contact
                self.last=f'목표 {position} · 현재 {values[2]} · 속도 {self.speed} · 토크 {self.torque}'
                return dict(completed=True,reached=reached,contact=contact,actual_position=values[2],
                            target=position,speed=self.speed,torque=self.torque,
                            acceleration=self.acceleration,deceleration=self.deceleration)
            if values[1] in (2,3): raise RuntimeError(f"그리퍼 {'정체' if contact else '낙하'} 상태")
            if self.delay==0: time.sleep(.01)
        raise RuntimeError('그리퍼 위치 도달 시간 초과')

    def wait(self, timeout=8, tolerance=3, accept_contact=False):
        timeout,tolerance=completion_options(True,timeout,tolerance,accept_contact)
        with self.bus_lock:
            if self.target is None: raise RuntimeError('대기할 그리퍼 목표가 없습니다.')
            self.busy=True
            try: return self._wait(self.target,timeout,tolerance,accept_contact)
            except Exception:
                self.uncertain=True
                raise
            finally: self.busy=False

    def write_parameter(self, address, value, before_commit):
        if not self.write_verified: raise RuntimeError('FR3 TCP 쓰기 경로 미확인 · 구동 차단')
        address=number(address,10,15,'레지스터',True)
        low,high={10:(0,255),11:(self.limits.speed_min,self.limits.speed_max),
                  12:(self.limits.torque_min,self.limits.torque_max),13:(1,255),14:(1,255),15:(0,0)}[address]
        value=number(value,low,high,'레지스터 값',True)
        with self.bus_lock:
            if self.uncertain: raise RuntimeError('이전 명령 응답 불확실 · 상태 재조회 필요')
            self.busy=True
            committed=False
            try:
                if self._read()[0]!=0: raise RuntimeError('그리퍼 오류 상태')
                before_commit()
                committed=True
                success(self.writer(address,[value],self.slave),'Modbus 파라미터 쓰기')
                if self.read_registers(address,1)!=[value]: raise RuntimeError('그리퍼 파라미터 읽기 불일치')
                return dict(staged=True,trigger_sent=False,address=address,value=value,readback_verified=True)
            except Exception:
                if committed: self.uncertain=True
                raise
            finally: self.busy=False
