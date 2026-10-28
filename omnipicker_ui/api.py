"""Vendor API access and explicit transport capabilities. No device discovery on import."""
from contextlib import nullcontext
import ast
from copy import deepcopy
from functools import lru_cache, wraps
import json
import inspect
from pathlib import Path
import threading

from .motion import GripperCommand
from .validation import number

TRACKED_MOTION={'MoveJ','MoveL','MoveC','Circle','NewSpiral','SplinePTP','NewSplinePoint'}


class UnsupportedFeature(RuntimeError):
    def __init__(self, feature, transport, requires, reason=''):
        self.feature, self.transport, self.requires = feature, transport, tuple(requires)
        message = f'{feature}: {transport}에서 지원하지 않음'
        if requires: message += ' · 필요: '+', '.join(requires)
        if reason: message += ' · '+reason
        super().__init__(message)


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).resolve().parent/'sdk_catalog.json').read_text())


def vendor_catalog():
    return deepcopy(_catalog())


def bind_vendor_arguments(name, args, kwargs):
    """Check call shape from vendor source without evaluating its default values."""
    spec=next((m for m in _catalog()['fr3']['methods'] if m['name']==name),None)
    if spec is None: raise AttributeError(name)
    tree=ast.parse('def '+spec['signature']+':\n pass').body[0].args
    parameters=[]
    positional=[*tree.posonlyargs,*tree.args]
    default_start=len(positional)-len(tree.defaults)
    for i,arg in enumerate(positional):
        kind=inspect.Parameter.POSITIONAL_ONLY if i<len(tree.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
        parameters.append(inspect.Parameter(arg.arg,kind,default=inspect.Parameter.empty if i<default_start else object()))
    if tree.vararg: parameters.append(inspect.Parameter(tree.vararg.arg,inspect.Parameter.VAR_POSITIONAL))
    for arg,default in zip(tree.kwonlyargs,tree.kw_defaults):
        parameters.append(inspect.Parameter(arg.arg,inspect.Parameter.KEYWORD_ONLY,
                          default=inspect.Parameter.empty if default is None else object()))
    if tree.kwarg: parameters.append(inspect.Parameter(tree.kwarg.arg,inspect.Parameter.VAR_KEYWORD))
    inspect.Signature(parameters).bind(*args,**kwargs)


class FR3API:
    """The original FAIRINO SDK's public methods, arguments and return values."""
    def __init__(self, owner): self._owner = owner

    @property
    def vendor(self):
        client = self._owner.client
        if client is None or self._owner.closed.is_set(): raise RuntimeError('FR3 연결 필요')
        return client

    @property
    def types(self):
        """Original enums, ctypes structures and helper types from Robot.py."""
        return self._owner.vendor_module()

    def methods(self): return deepcopy(_catalog()['fr3']['methods'])

    def timeout(self, seconds):
        seconds = number(seconds,.1,600,'RPC 시간 제한')
        if self._owner.demo: return nullcontext()
        return self.vendor.robot.timeout(seconds)

    def __dir__(self):
        return sorted(set(super().__dir__()) | {m['name'] for m in _catalog()['fr3']['methods']})

    def __getattr__(self, name):
        if name.startswith('_'): raise AttributeError(name)
        method = getattr(self.vendor,name,None)
        if callable(method):
            if name not in TRACKED_MOTION: return method
            @wraps(method)
            def tracked(*args,**kwargs):
                owner=self._owner
                with owner.lock:
                    if owner.robot_busy: raise RuntimeError('FR3 이동 중')
                    owner.robot_busy=True
                try: return method(*args,**kwargs)
                finally: owner.robot_busy=False
            return tracked
        if any(m['name']==name for m in _catalog()['fr3']['methods']) and self._owner.demo:
            raise UnsupportedFeature(name,'demo',['original FAIRINO SDK'],'이 메서드는 시뮬레이션하지 않습니다.')
        raise AttributeError(f'FAIRINO SDK 메서드 없음: {name}')


REGISTERS = {0:'firmware_version',10:'target_position',11:'target_speed',12:'target_torque',
             13:'target_acceleration',14:'target_deceleration',15:'motion_trigger',
             20:'error_code',21:'motion_state',22:'actual_position',23:'actual_speed',24:'actual_torque'}


class OmniPickerAPI:
    """All documented OP1 Modbus registers; unavailable vendor APIs fail explicitly."""
    def __init__(self, owner): self._owner = owner

    @property
    def transport(self): return 'rs485'

    def features(self):
        result = []
        for address,name in REGISTERS.items():
            result.append(dict(device='omnipicker',name=name,kind='register',address=address,
                               available=True,access='rw' if 10<=address<=15 else 'r',requires=['rs485']))
        for feature in _catalog()['omnipicker_fibre']['features']:
            result.append(dict(feature,device='omnipicker',available=False,
                               reason='OP1 Modbus 레지스터에 이 기능이 없습니다.'))
        for feature in _catalog()['omnipicker_vendor']['methods']:
            local = feature['name'] in ('GetNumOfJointMotors','GetDoA')
            result.append(dict(feature,device='omnipicker',kind='vendor_method',available=local,
                               reason='' if local else '원본 SDK의 해당 USB/CAN 전용 API입니다.'))
        return result

    def supports(self, name):
        return any(f['name']==name and f['available'] for f in self.features())

    def read_registers(self, address, count=1):
        address=number(address,0,65535,'레지스터',True)
        count=number(count,1,len(REGISTERS),'레지스터 수',True)
        if any(a not in REGISTERS for a in range(address,address+count)):
            raise ValueError('문서에 있는 OP1 레지스터만 조회할 수 있습니다.')
        if self._owner.external_bus_pause:raise RuntimeError('USB 설정 중 · RS485 조회 잠금')
        driver=self._owner.gripper
        if not driver: raise RuntimeError('FR3 연결 필요')
        state=self._owner.client.robot.GetProgramState()
        if not isinstance(state,(list,tuple)) or tuple(state)!=(0,1):
            raise RuntimeError('FR3 프로그램 정지 상태에서 RS485를 조회합니다.')
        with driver.bus_lock:
            # Preserve register order rather than the legacy RPC bulk order.
            return [driver.read_registers(a,1)[0] for a in range(address,address+count)]

    def read(self, name):
        address=next((a for a,n in REGISTERS.items() if n==name),None)
        if address is None: self._unsupported(name)
        return self.read_registers(address)[0]

    def move(self, position, speed=40, torque=40, **options):
        return self._owner.grip(position,speed,torque,**options)

    def send_frame(self, command, **options):
        if not isinstance(command,GripperCommand): raise TypeError('GripperCommand 필요')
        return self.move(**command.__dict__,**options)

    def stage(self, position, speed=40, torque=40, acceleration=40, deceleration=40):
        return self._owner._grip(position,speed,torque,acceleration=acceleration,
                                 deceleration=deceleration,execute=False)

    def trigger(self, **options):
        position,speed,torque,acceleration,deceleration=self.read_registers(10,5)
        return self.move(position,speed,torque,acceleration=acceleration,deceleration=deceleration,**options)

    def wait(self, **options):
        if self._owner.external_bus_pause:raise RuntimeError('USB 설정 중 · RS485 조회 잠금')
        if not self._owner.gripper: raise RuntimeError('FR3 연결 필요')
        state=self._owner.client.robot.GetProgramState()
        if not isinstance(state,(list,tuple)) or tuple(state)!=(0,1):
            raise RuntimeError('FR3 프로그램 정지 상태에서 완료를 조회합니다.')
        try: return self._owner.gripper.wait(**options)
        except Exception:
            self._owner.disarm()
            raise

    def state(self): return self._owner.probe_gripper()

    def gesture_target(self, gesture):
        try: return {'closed':0,'half_open':127,'open':255}[gesture]
        except (KeyError,TypeError) as exc: raise ValueError('gesture: closed / half_open / open') from exc

    def move_gesture(self, gesture, **options):
        return self.move(self.gesture_target(gesture),**options)

    def GetNumOfJointMotors(self): return 1
    def GetDoA(self): return 1

    def write(self, name, value):
        address=next((a for a,n in REGISTERS.items() if n==name),None)
        if address is None: self._unsupported(name)
        if not 10<=address<=15: raise ValueError(f'읽기 전용 레지스터: {name}')
        if address==15 and number(value,0,255,'트리거',True)!=0: return self.trigger()
        return self._owner._write_gripper_parameter(address,value)

    def call(self, name, **kwargs):
        if name in REGISTERS.values(): raise TypeError('레지스터는 read() / write()를 사용합니다.')
        self._unsupported(name)

    def _unsupported(self, name):
        for f in self.features():
            if f['name']==name:
                raise UnsupportedFeature(name,'FR3 RS485',f.get('requires',[]),f.get('reason',''))
        raise AttributeError(f'OmniPicker 기능 없음: {name}')

    def __getattr__(self, name):
        if any(m['name']==name for m in _catalog()['omnipicker_vendor']['methods']):
            def unsupported(*args,**kwargs): self._unsupported(name)
            return unsupported
        raise AttributeError(name)


class OmniPickerUSB:
    """Full live Fibre property/function access for the separate USB setting tool.

    Never opened by OmniPickerFR3. An explicit connect() is required. Firmware
    image transfer and CAN operations belong to their vendor transports.
    """
    def __init__(self, device=None):
        self.device=device
        self.lock=threading.RLock()

    def connect(self, bus=None, address=None):
        from .fibre import FibreDevice
        with self.lock:
            if self.device: raise RuntimeError('USB가 이미 연결되어 있습니다.')
            self.device=FibreDevice(bus,address)
        return self

    def _device(self):
        if self.device is None: raise RuntimeError('USB 연결 필요')
        return self.device

    def read(self, path):
        with self.lock: return self._device().read(path)

    def write(self, path, value):
        with self.lock: return self._device().write(path,value)

    def call(self, path, **args):
        with self.lock: return self._device().call(path,**args)

    def features(self):
        from .fibre import flatten
        if self.device is None: return deepcopy(_catalog()['omnipicker_fibre']['features'])
        return [dict(node,name=name) for name,node in flatten(self._device().schema).items()
                if node.get('type') not in ('object','json')]

    def close(self):
        with self.lock:
            if self.device: self.device.close()
            self.device=None

    def __enter__(self): return self if self.device else self.connect()
    def __exit__(self,*exc): self.close()
