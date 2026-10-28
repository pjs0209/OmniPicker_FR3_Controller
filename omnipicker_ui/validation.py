"""Strict numeric validation shared by the two independent applications."""
import math


def number(value, low, high, name, integer=False):
    if isinstance(value, bool):
        raise ValueError(f"{name}: 숫자를 입력하세요.")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high or (integer and not value.is_integer()):
        raise ValueError(f"{name}: {low}~{high} 범위{'의 정수' if integer else ''}만 허용합니다.")
    return int(value) if integer else value


def increment(value, name='이동 각도'):
    """A finite signed increment, with no UI-imposed angular cap."""
    result=number(value,-math.inf,math.inf,name)
    if result==0:raise ValueError(f'{name}: 0이 아닌 유한한 값 필요')
    return result
