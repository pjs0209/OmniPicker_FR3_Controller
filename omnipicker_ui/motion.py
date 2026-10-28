"""Raw OmniPicker Modbus command values, distinct from physical units."""
from dataclasses import dataclass
from .validation import number


@dataclass(frozen=True)
class GripperLimits:
    speed_min: int = 10
    speed_max: int = 60
    torque_min: int = 10
    torque_max: int = 60

    def __post_init__(self):
        for name in ('speed_min','speed_max','torque_min','torque_max'):
            value = number(getattr(self,name),0,255,name,True)
            object.__setattr__(self,name,value)
        if self.speed_min > self.speed_max or self.torque_min > self.torque_max:
            raise ValueError('그리퍼 범위의 최솟값이 최댓값보다 큼')

    @classmethod
    def full_range(cls):
        return cls(0,255,0,255)


@dataclass(frozen=True)
class GripperCommand:
    position: int
    speed: int = 40
    torque: int = 40
    acceleration: int = 40
    deceleration: int = 40

    def validate(self, limits):
        return type(self)(number(self.position,0,255,'목표',True),
            number(self.speed,limits.speed_min,limits.speed_max,'속도',True),
            number(self.torque,limits.torque_min,limits.torque_max,'토크',True),
            number(self.acceleration,1,255,'가속도',True),number(self.deceleration,1,255,'감속도',True))

    def registers(self):
        return [self.position,self.speed,self.torque,self.acceleration,self.deceleration]


def completion_options(wait, timeout, tolerance, accept_contact):
    if type(wait) is not bool or type(accept_contact) is not bool:
        raise ValueError('wait / accept_contact는 bool이어야 합니다.')
    return (number(timeout,.1,120,'대기 시간'), number(tolerance,0,10,'위치 허용 오차',True))
