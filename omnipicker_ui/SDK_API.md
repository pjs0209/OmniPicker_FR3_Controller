# 통합 SDK API 범위

이 SDK는 FR3 원본 API 접근과 OmniPicker 통신 어댑터를 제공합니다. API 제공 여부와 실장비 검증 여부는 별개입니다.

| 대상 | 제공 범위 | 현재 연결에서의 상태 |
|---|---|---|
| FR3 | 설치된 `fairino.Robot.RPC`의 공개 메서드 594개, 원본 타입·상수·콜백 | `sdk.fr3`에서 원본 SDK로 전달. 개별 장치·펌웨어·옵션 지원 여부는 제조사 SDK를 따름 |
| OmniPicker OP1 Modbus | 문서에 공개된 레지스터 12개, 운동·상태·파라미터 읽기/쓰기 | 기존 FR3 LAN + RS485 경로 |
| OmniPicker Fibre | 관측한 스키마의 속성 62개와 함수 13개에 대한 범용 접근 | 별도 `OmniPickerUSB` 클래스. 통합 RS485 연결에서 자동 사용하지 않음 |
| OP1 USB/CAN 전용 SDK API | 전체 기능 목록과 필요 인터페이스 표시 | RS485에서는 `UnsupportedFeature` 발생 |

현재 장비는 OmniPicker2025 / OP1, 펌웨어 335입니다. 다른 제품인 OmniPicker 3 / OP3의 촉각·gentle-grip API를 이 장비에 적용하지 않습니다.

FR3 목록은 설치된 원본 `Robot.py`를 AST로 읽어 만들었습니다. 원본 코드를 복제하거나 미확인 XML-RPC 메서드를 추측하지 않습니다. 목록·원본 해시는 `sdk_catalog.json`에 있습니다. UI의 **SDK 기능**에서 이름·인자를 검색할 수 있습니다.

## FR3 원본 SDK

연결된 통합 SDK에서 다음처럼 호출합니다.

```python
pose = sdk.fr3.GetActualTCPPose()
joints = sdk.fr3.GetActualJointPosDegree()
result = sdk.fr3.GetForwardKin(joints[1])

# 원본 모듈의 enum, 구조체, 상수
vendor_types = sdk.fr3.types

# 실제 제조사 RPC 객체와 API 목록
vendor_client = sdk.fr3.vendor
methods = sdk.fr3.methods()
```

`sdk.fr3.<method>`는 원본 메서드 이름·인자·콜백·반환값을 유지합니다. 통합 조그 함수의 속도 1~100% 범위와 60초 허용은 원본 호출에 적용되지 않습니다. 조그 각도 증분에는 별도 상·하한을 두지 않으며 결과 목표를 컨트롤러 소프트 한계로 검증합니다. 원본 SDK를 직접 사용하는 호출 경로입니다. 기본 이동 API는 실행 중 상태를 추적하여 RS485 전송이 이동 종료까지 직렬화되는 것을 방지합니다.

긴 블로킹 호출의 RPC 시간 제한은 해당 스레드에만 지정합니다. 시간 초과 후 쓰기를 자동 재전송하지 않습니다.

```python
with sdk.fr3.timeout(30):
    result = sdk.fr3.MoveL(target_pose, 0, 0, vel=5)
```

594개 함수 전체를 실장비에서 실행해 검증한 것은 아닙니다. 데모에서 구현하지 않은 원본 함수는 명시적으로 오류를 반환합니다. 라이브러리 이름에 있는 SDK 버전 문자열과 `GetSDKVersion()`의 내장 문자열이 다를 수 있으므로 소스 SHA256도 함께 기록합니다.

## OmniPicker RS485

```python
from omnipicker_ui import OmniPickerFR3, GripperCommand

with OmniPickerFR3(demo=True) as sdk:
    sdk.arm()
    sdk.omnipicker.stage(140, speed=20, torque=30,
                         acceleration=50, deceleration=60)
    # stage는 트리거를 쓰지 않습니다.
    sdk.omnipicker.trigger(wait=False)
    result = sdk.omnipicker.wait(timeout=8, tolerance=3)
    print(result)
```

| API | 동작 |
|---|---|
| `move(position, speed=40, torque=40, acceleration=40, deceleration=40, wait=True, timeout=8, tolerance=3, accept_contact=False)` | 운동 파라미터와 트리거를 기록하고 장치 파라미터를 다시 읽어 확인 |
| `send_frame(GripperCommand(...), **options)` | 다섯 운동 파라미터를 RS485 명령으로 전송 |
| `stage(position, speed, torque, acceleration=40, deceleration=40)` | 레지스터 10~14에 파라미터만 기록 |
| `trigger(**options)` | 현재 파라미터를 읽고 검증한 뒤 운동 명령 실행 |
| `wait(timeout=8, tolerance=3, accept_contact=False)` | 새 운동 명령 없이 현재 목표 완료 대기 |
| `read(name)` / `write(name, value)` | 공개 레지스터의 이름으로 접근. 읽기 전용 상태 쓰기는 거부 |
| `read_registers(address, count=1)` | 공개된 주소만 한 개씩 읽어 원래 순서로 반환 |
| `state()` | 실제 오류·상태·위치·속도·토크 조회 |
| `gesture_target(name)` / `move_gesture(name, **options)` | `closed=0`, `half_open=127`, `open=255` |
| `features()` / `supports(name)` | 현재 RS485 경로의 기능 지원 여부 조회 |

공개 이름은 `firmware_version`, `target_position`, `target_speed`, `target_torque`, `target_acceleration`, `target_deceleration`, `motion_trigger`, `error_code`, `motion_state`, `actual_position`, `actual_speed`, `actual_torque`입니다.

일반 파라미터 `write()`는 운동 트리거를 보내지 않습니다. `motion_trigger`에 비영 값을 쓰면 검증된 전체 운동 명령으로 실행합니다. 0을 쓰는 것은 플래그 기록이며 그리퍼 정지 명령이 아닙니다.

`wait=False`는 `accepted=True, completed=False`를 반환합니다. 전송 수락을 실제 도달로 취급하지 않습니다. `accept_contact=True`를 명시한 경우 상태 2를 접촉 완료로 처리하며 `completed=True, contact=True, reached=False`로 구분합니다. 기본값은 상태 2를 오류로 처리합니다.

위치·속도·토크·가감속은 RAW 명령값이며 Nm·mm/s 단위가 아닙니다. 기본 속도·토크 범위는 10~60, 가감속은 1~255입니다. 제조사 프로토콜 전체 범위는 생성 시 명시적으로 선택합니다.

```python
from omnipicker_ui import GripperLimits

controller = OmniPickerFR3(demo=True, gripper_limits=GripperLimits.full_range())
# speed / torque 0~255. 이 생성 자체로 장비에 연결하거나 명령을 보내지 않습니다.
```

## 원본 이동 API와 동시 전송

```python
def move_pose_and_grip(sdk, target_pose):
    sdk.arm()
    return sdk.parallel(
        "MoveL", args=(target_pose, 0, 0), kwargs={"vel": 5},
        gripper=GripperCommand(140, 20, 30, 50, 60),
        rpc_timeout=30,
    )
```

`parallel()`은 `MoveJ`, `MoveL`, `MoveC`, `Circle`, `NewSpiral`, `SplinePTP`, `NewSplinePoint`를 지원합니다. 장치 상태·그리퍼 값·원본 메서드의 호출 인자를 확인한 뒤 두 작업을 실행합니다. 원본 이동 API의 반환값은 `result['robot']`에 유지됩니다. 기존 소폭 조그 편의 API인 `move_together()`도 유지합니다.

두 작업의 실행을 겹치는 방식이며 서보 주기 동기화는 제공하지 않습니다. 스트리밍·스플라인 시작/종료 등 전체 원본 API는 `sdk.fr3`에서 호출합니다. 같은 FR3의 Modbus 버퍼는 공유되므로 UI와 별도 스크립트를 함께 연결할 때는 기존 연결을 먼저 종료합니다.

FR3 사용자 프로그램 실행 중에는 통합 SDK의 RS485 자동 조회를 멈춥니다. 사용자 프로그램이 사용 중인 Modbus 응답을 가져가지 않도록 해당 구간의 그리퍼 상태는 미확인으로 표시합니다.

## USB / CAN 전용 기능

`sdk.omnipicker.StartMotorCalibration()`, `SetCanNodeId()`, `UpdateFirmwareViaFlash()`와 같은 USB/CAN 전용 메서드는 현재 RS485에서 `UnsupportedFeature`를 발생시킵니다. 예외의 `feature`, `transport`, `requires` 필드로 이유를 확인할 수 있습니다. 미지원 기능에 대해 성공값을 만들지 않습니다.

별도 USB 설정 연결에서는 `OmniPickerUSB`의 `read(path)`, `write(path, value)`, `call(path, **args)`를 통해 실제 장치 Fibre JSON의 속성·함수에 접근합니다. 엔드포인트 번호는 연결할 때 장치에서 읽습니다. 예를 들어 모터 상태 요청·오류 해제·오프셋 적용·전류 게인 계산·위치/속도/전류/토크/MIT 명령·설정 저장/초기화·재시작·DFU 진입이 포함됩니다. 연결과 호출은 명시적으로 실행해야 합니다.

이 범용 USB 인터페이스에 펌웨어 이미지 전송기나 CAN 드라이버를 구현한 것은 아닙니다. 실제 업데이트와 CAN 기능은 해당 제조사 SDK 도구가 필요합니다. 현재 RS485 배선은 유지하며 USB 연결을 추가하지 않았습니다.

지원 범위 근거: [FAIRINO SDK 배포](https://fairino-doc-en.readthedocs.io/3.9.8/download.html), [AGIBOT OmniPicker 매뉴얼](https://www.agibot.com/DOCS/PM/news), 설치된 OP1 C++ 헤더 및 장치에서 관측한 Fibre 스키마.


## 미리보기 → 명시적 적용

```python
# 장비 없이 호출 흐름 확인. 실제 장비는 demo=False를 명시하고 작업영역을 확인합니다.
with OmniPickerFR3(demo=True) as sdk:
    current = sdk.snapshot()['robot']['joints']
    target = current[:]
    target[0] += 0.5
    plan = sdk.preview_robot('joint', joints=target)  # 읽기·계산만 수행
    sdk.arm()
    result = sdk.apply_robot_plan(plan['id'], velocity=5)
    tool_plan = sdk.preview_robot('tool', delta=[1, 0, 0, 0, 0, 0], tool=0, user=0)
    result = sdk.apply_robot_plan(tool_plan['id'], velocity=5)
```

`preview_robot(kind, joints=None, delta=None, reference=None, tool=0, user=0)`는 45초 유효한 단발 목표를 생성합니다. `joint`는 MoveJ, `tool`은 컨트롤러 FK → 로컬 TCP 변환 → GetInverseKinRef → MoveL을 사용합니다. `motion_limits()`로 UI 입력 범위와 컨트롤러 관절 소프트 한계를 조회합니다. 기준 관절이 0.5° 초과 바뀌거나 epoch/툴/TCP 오프셋이 바뀌면 적용을 거부합니다. 반환 딕셔너리를 변경해도 서버에 저장된 목표는 바뀌지 않습니다. 응답 유실 후 이동을 자동 재전송하지 않습니다.

통합 UI `/gripper/settings`에 RS485 파라미터/트리거/도달 대기/입력 범위와 `/gripper/usb/`의 USB 설정 화면을 제공합니다. UI의 입력 범위 변경은 장치 레지스터 쓰기가 아니며 새 명령을 잠급니다. USB 연결 중에는 통합 RS485 읽기/쓰기를 거부합니다. USB 전용 기능의 제공 여부는 연결된 장치의 Fibre JSON으로 확인합니다. 현재 RS485 배선만으로 USB/CAN 전용 API를 변환 실행하지 않습니다.

3D 화살표·링은 연속 포인터 드래그로 동작하며 숫자 증분에 스냅하지 않습니다. TCP 드래그는 최대 약 6.7 Hz로 한 번에 하나의 읽기 전용 FK/IK 미리보기 요청만 처리하며 최신 포인터 목표로 후속 요청합니다. 실제 이동은 여전히 `apply_robot_plan()`의 명시적 호출에서만 실행됩니다. `joint_step` / `rotation_step`의 `[0, null]`은 0 초과·상한 없음을 의미하고, `tool_translation` / `tool_rotation`의 `[null, null]`은 임의 UI 한계 대신 IK/소프트 한계를 사용함을 의미합니다.


## 연결 복구와 콘솔

`recover_rs485()`는 팔/그리퍼 처리 중·USB 설정 중·사용자 FR3 프로그램 실행 중이면 거부합니다. 새 명령을 잠근 뒤 검증된 FR3 버전과 기존 115200/8N1 RTU 설정을 확인하고 동일한 설정만 한 번 재적용합니다. 설정 읽기 검증과 실제 그리퍼 피드백·펌웨어 검증 후 `communication_verified`를 반환하며 위치/속도/토크/트리거를 기록하지 않습니다. 설정 함수의 반환값과 실제 통신 검증을 `acknowledged`로 구분하고 명령은 자동 재전송하지 않습니다.

`preview_robot()`는 기준 TCP `reference_pose`와 목표 TCP `pose`를 함께 반환합니다. UI는 기준 TCP와 상대 이동으로 요청 좌표/3D TCP 핸들을 즉시 표시하고, SDK IK 결과는 검증된 관절 쉐도우로 표시합니다. FK/IK가 실패하면 적용을 잠그고 이유를 표시합니다. 편집하지 않은 목표는 실제 로봇 피드백을 따르고, 편집 중에는 기준을 고정합니다.

콘솔의 포인트 불러오기와 JSON 가져오기는 장치 명령을 보내지 않습니다. 상태 메뉴는 실제 피드백과 이벤트를, 설정 메뉴는 모델 장착과 SDK 기능 목록을 제공합니다.
