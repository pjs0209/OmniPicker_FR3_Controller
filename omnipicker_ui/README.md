# OmniPicker · FR3 UI

Python 3.10 이상. 실행 파일 2개와 `omnipicker_ui/`를 함께 유지합니다.

```bash
cd OmniPicker_FR3_Controller
python3 -m pip install -r omnipicker_ui/requirements.txt
python3 OmniPicker_FR3_Controller.py --ip 192.168.57.2 --slave-id 1
```

통합 UI: http://127.0.0.1:8088

통합 UI의 **OmniPicker → 설정**에서 RS485 기능과 USB 전용 설정을 별도 창으로 엽니다. USB 전용 화면은 USB-C 연결이 있을 때만 사용하며, 기존 RS485 배선만으로는 USB/CAN 전용 기능을 실행할 수 없습니다. 독립 USB 설정 UI도 실행할 수 있습니다.

```bash
python3 OmniPicker_Setting.py
```

USB UI: http://127.0.0.1:8089. `--demo`는 장비에 연결하지 않으며 `--port`로 포트를 변경합니다.

## 독립 제어 SDK

`omnipicker_ui/sdk.py`의 `OmniPickerFR3`가 제어 계층이며, UI는 이 SDK를 호출합니다. SDK는 Flask나 FR3 WebApp에 의존하지 않습니다.

FR3 원본 공개 메서드 594개는 `sdk.fr3`로 접근합니다. OmniPicker 공개 RS485 레지스터와 가감속·비동기 전송·완료 대기·접촉 완료 정책을 제공하며, UI의 **SDK 기능**에서 전체 목록과 USB/CAN 전용 기능을 확인할 수 있습니다. 상세 사용법과 검증 범위는 [SDK_API.md](SDK_API.md)에 있습니다.

- 로봇팔: PC → LAN → FAIRINO SDK / RPC
- 그리퍼: PC → LAN → FR3 TCP 8080 → 기존 컨트롤박스 RS485 → OmniPicker
- 상태: RPC 20003과 SDK 피드백, 실제 그리퍼 레지스터

추가 USB 연결이나 별도 Lua 파일·시스템 변수 브리지는 사용하지 않습니다. FR3 내부 명령 해석기는 제조사 구현에 따라 Lua 런타임을 사용합니다.

FR3의 Modbus 응답 버퍼를 공유하므로 같은 장비에는 SDK 인스턴스 하나를 사용합니다. UI와 독립 스크립트 사이를 전환할 때는 기존 연결을 먼저 종료합니다.

```python
from omnipicker_ui import OmniPickerFR3

# 장비 없이 동시 제어 확인
with OmniPickerFR3(demo=True) as controller:
    controller.arm()
    result = controller.move_together(
        axis=0, delta=0.5, velocity=5,
        position=140, speed=20, torque=30,
    )
    print(result)
```

```python
# 실제 장비: 연결과 상태 조회만 수행
with OmniPickerFR3("192.168.57.2", slave_id=1) as controller:
    print(controller.snapshot())
```

작업영역 확인 후 `arm()`으로 60초 동안 새 명령을 허용합니다. `joint(axis, delta, velocity=5, tool=0, user=0)`, `grip(position, speed, torque)`, `move_together(...)`, `probe_gripper()`, `disarm()`, `stop()`, `close()`를 제공합니다. 관절 인덱스는 0~5, 각도 증분의 별도 상·하한은 없으며 0이 아닌 유한한 값을 사용합니다. 목표 관절은 FR3 소프트 한계 안이어야 하며 로봇 속도는 1~100%입니다. 동시 제어는 두 명령의 실행을 겹치며 서보 주기 동기화는 제공하지 않습니다.

그리퍼 위치는 0~255, 기본 속도·토크는 시험 범위 10~60의 RAW 단위입니다. Nm나 mm/s 단위가 아닙니다. 레지스터 10~15에 위치·속도·토크·가속도·감속도·트리거 1을 기록하고, 장치에서 파라미터를 다시 읽어 확인합니다. 가감속 기본값은 40이며 UI의 **운동 설정**에서 변경합니다. 기본 완료 정책은 실제 위치와 도달 상태를 확인합니다. **접촉 시 완료**를 선택하면 상태 2를 접촉 완료로 구분합니다. 오류·응답 유실 시 자동 재전송하지 않습니다.

읽기는 펌웨어 레지스터 0과 상태 레지스터 20~24를 한 개씩 조회합니다. 읽기 어댑터는 로봇 이동이 끝날 때까지 대기할 수 있으므로, 동시 전송 시 3초 이내의 사전 상태를 사용해 쓰기를 먼저 보내고 완료를 확인합니다. 상태가 오래되었으면 새 구동을 거부합니다.

직접 RPC `ModbusRegWrite`는 사용하지 않습니다. 현재 쓰기 경로는 실장비에서 검증한 WebApp v3.9.8 / 제어기 V3.9.35-QX / OmniPicker 펌웨어 335에 한해 활성화됩니다. 다른 버전은 읽기 전용입니다.

## 목표 미리보기와 적용

- **관절 숫자 · 직접 조그**: 6개 목표 각도를 숫자로 입력하면 청색 쉐도우만 바뀝니다. **목표 적용**을 누르면 MoveJ로 이동합니다. 이 모드의 관절 ± 버튼은 기존처럼 즉시 조그하며, 0°보다 큰 각도 증분과 속도 1~100%를 직접 입력합니다. 각도 증분에 2° 상한이나 0.01° 하한을 두지 않습니다.
- **3D 관절 축**: 각 관절의 실제 회전축을 조작하는 화살표·링만 표시합니다. 화살표·링을 마우스로 잡고 드래그하면 포인터의 회전각에 맞춰 목표 미리보기가 연속으로 바뀝니다. 단순 클릭은 각도를 바꾸지 않고, 숫자 증분은 3D 드래그에 적용하지 않습니다. 관절 ± 버튼은 입력한 증분으로 미리보기를 변경합니다. **목표 적용**에서 실제로 이동합니다.
- **엔드이펙터 기준**: TCP의 XYZ 이동 화살표와 Roll/Pitch/Yaw 회전 링을 마우스로 잡고 자유롭게 드래그하거나 이동량을 숫자로 입력합니다. 드래그는 증분으로 스냅하지 않으며 IK 미리보기를 직렬·제한 빈도로 갱신합니다. 실제 컨트롤러의 FK/IK로 미리보기를 계산하고 **목표 적용**에서 MoveL로 이동합니다. 활성 툴 번호를 사용하고 작업좌표계는 0이어야 합니다. 드래그는 시작 시점의 미리보기 TCP 좌표계로 계산하며, 숫자는 **현재 → 목표**에서 잡은 기준 TCP의 이동량입니다. 회전 합성은 Rz(Yaw) Ry(Pitch) Rx(Roll)입니다.
- 숫자 ± 버튼의 이동 증분은 0.1~10 mm입니다. 관절·회전 증분은 0° 초과이며 별도 상한이 없습니다. 3D 드래그와 TCP 목표 입력에는 기존 ±100 mm / ±30° 제한을 두지 않고 컨트롤러의 IK 해와 관절 소프트 한계로 검증합니다. 드래그 중에는 카메라 조작을 잠시 멈추고 놓으면 복원합니다. 포인터 취소·창 포커스 상실 시 드래그 시작 목표로 복원합니다. 관절 목표 범위는 FR3에서 읽은 소프트 한계를 표시하고 적용 직전에 다시 확인합니다.
- 목표는 45초 후 만료되고 한 번만 적용됩니다. 잠금·정지·연결 끊김, 현재 관절의 0.5° 초과 변경, 활성 TCP/툴 변경 시 다시 미리보기해야 합니다. 적용 직전에도 제어 허용이 필요합니다.

그리퍼 설정 창은 레지스터 12개 조회, 파라미터 개별 기록, 5개 동작 파라미터 기록 후 별도 트리거, 도달 대기, 접촉 정책, 입력 범위 변경을 제공합니다. 기본 속도·토크 10~60 RAW, 제조사 범위 0~255 RAW를 구분합니다. 설정 창에서 입력 범위를 0~255 안에서 변경할 수 있으며 변경 시 새 명령이 잠깁니다. 가감속 1~255 RAW도 최소·최대를 표시합니다.

USB 화면의 **전체 USB 기능**은 연결된 장치의 JSON을 읽어 속성·함수 편집기를 만듭니다. 펌웨어가 제공하는 읽기/쓰기 권한과 데이터 타입을 사용합니다. 쓰기·함수 실행은 USB 제어 허용 후 실행 버튼으로 명시적으로 실행합니다. 실제 USB 연결 동안 통합 RS485 명령과 폴링은 중지되고, 연결 해제 후 새 제어 허용이 필요합니다. USB 화면을 여는 것만으로 장치를 연결하지 않습니다. 펌웨어 바이너리 업데이트와 CAN 프레임 송신은 이 화면의 기능이 아닙니다.

새 목표 적용·툴 모드는 86개 단위 테스트와 브라우저 테스트로 검증했습니다. 실장비의 FK/IK 미리보기 계산과 관절 소프트 한계 조회는 확인했으며 실제 FR3 이동 검증은 수행하지 않았습니다. CAD 기구학은 시뮬레이터용이며 실제 이동은 FR3 컨트롤러의 FK/IK와 소프트 한계를 사용합니다.

## 실제 장비 / UI

1. FR3는 RS485 Modbus Master, 그리퍼는 주소 1 / 115200 / 8N1로 맞춥니다.
2. UI에서 **연결** 후 그리퍼 피드백을 확인합니다.
3. 작업영역 확인 → **60초 제어 허용**.
4. 관절 ±와 그리퍼 **전송**으로 각각 제어합니다.
5. **직접 조그 시 그리퍼 동시 전송**을 선택하면 관절 ±가 현재 그리퍼 목표·속도·토크도 함께 전송합니다.

다른 FR3 프로그램이 실행 중이면 그리퍼 명령을 거부합니다. 기존 프로그램을 자동 정지하거나 로드·삭제하지 않고 모터 활성화 설정도 변경하지 않습니다.

`FR3 정지 / Esc`는 FR3 StopMotion과 새 명령 잠금을 요청합니다. 그리퍼 정지는 제공하지 않습니다. `disarm()`·`close()`·허용 시간 만료는 이미 전달된 동작을 취소하지 않습니다. `그리퍼 재조회`는 상태를 다시 읽고 오류 없는 도달 상태일 때 통신 불확실 잠금을 해제합니다.

실장비에서 TCP 명령, RS485 파라미터 쓰기·읽기·복원, 속도·토크 값 적용, 실제 위치 **0 → 16/255** 변화를 확인했습니다. 목표 13, 속도/토크 20 시험에서는 **정체 상태 2**를 반환하여 추가 구동을 중단했습니다. 완전한 도달·원위치 복귀와 실제 FR3 관절 동시 이동은 검증 완료 상태가 아닙니다. 근거는 `qa/live_results/native_stage.json`과 `native_motion_20.json`입니다.

SDK는 기존 `~/fr5_teleop/fairino-python-sdk-2.2.8_robot_v3.9.8/linux`를 사용합니다. 다른 경로는 `--sdk-path` 또는 `OmniPickerFR3(sdk_path=...)`로 지정합니다.

## 3D

- 실제 제조사 STL과 URDF 관절 구조 사용
- 회색: 현재 관절값 / 청색 반투명: 목표 미리보기
- 우클릭: 회전 / 휠: 확대 / 좌클릭: 이동
- 목표 각도 입력은 미리보기만 변경하며 관절 ± 버튼으로 실제 이동
- 그리퍼 확대, 뷰 초기화, 전체화면 지원
- `모델·장착`에서 FR3WMS/FR3WML과 플랜지 기준 장착 오프셋 지정

현재 장비는 FR3WMS의 명목 기구학과 측정 플랜지 위치가 2 mm 이내로 일치하여 기본 모델로 사용합니다. 개별 보정값과 장착 어댑터 치수는 현장 값에 맞춰야 합니다.

OmniPicker는 제조사 9링크·8관절과 mimic 관계를 사용합니다. 개구량 기반 자세이며 물체 접촉에 따른 수동 손가락 변형까지 측정하지 않습니다. 실제 위치가 없으면 현재 손가락을 숨기고 목표만 표시합니다. USB 펌웨어는 실제 위치 엔드포인트를 제공하지 않습니다.

모델과 Three.js는 모두 로컬 파일입니다. 출처와 SHA256은 `static/models/sources.json`에 있습니다.

- [FAIRINO 공식 모델](https://github.com/FAIR-INNOVATION/frcobot_ros2/tree/fcf0c7f0d60d949d8a9a4238f929a44d07f60379/fairino_description)
- [AGIBOT 공식 모델](https://github.com/AgibotTech/genie_sim_robot_model/tree/6d5b2159806ae0e64b432b638e9ee288d8bddc8f/robots/genie/g2)
- [OmniPicker 프로토콜](https://www.agibot.com/DOCS/PM/news)

모델 재생성 시에만 NumPy가 필요합니다.

```bash
python3 omnipicker_ui/tools/fetch_models.py
python3 omnipicker_ui/tools/prepare_models.py
```

## USB 설정

장치 검색 → 연결 → 편집 → RAM 적용 → 플래시 저장 → 전원 재인가 → 재조회.

CAN/Modbus ID, RS485/TTL, Modbus/전용 프로토콜, 보드레이트, 정지 비트, 전원 인가 시 활성화를 설정합니다. USB 테스트의 Kp/Kd는 해당 명령에만 적용합니다. 장치의 Fibre JSON으로 엔드포인트·타입·CRC를 확인합니다.

USB 권한 오류가 나면 관리자가 규칙을 설치하고 USB를 다시 연결합니다.

```text
# /etc/udev/rules.d/70-omnipicker.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="1001", ATTR{idProduct}=="03ef", MODE="0660", TAG+="uaccess"
```

```bash
sudo udevadm control --reload-rules
```

## 검증

```bash
python3 -m unittest discover -s omnipicker_ui/tests -v
python3 omnipicker_ui/qa/diagnose.py --ip 192.168.57.2
python3 -m omnipicker_ui.qa.sdk_check --demo --motion --parallel-demo
python3 -m omnipicker_ui.qa.sdk_check --ip 192.168.57.2
```

실장비 검증 도구는 로컬 UI에 접속하며 결과를 `qa/live_results/`에 저장합니다. motion은 사전 작업영역 확인이 필요하며 그리퍼만 약 5% 이동 후 복귀합니다. FR3 관절은 이동시키지 않습니다.

```bash
python3 omnipicker_ui/qa/live_check.py connect
python3 omnipicker_ui/qa/live_check.py probe
python3 omnipicker_ui/qa/live_check.py motion --confirmed-clear
```

브라우저 QA는 새 데모 서버를 8098/8099에서 실행하고 임시 Chrome 디버깅 포트를 9224로 설정해 사용합니다. 실장비 모드에서는 실행을 거부합니다.

```bash
python3 OmniPicker_FR3_Controller.py --demo --port 8098
python3 OmniPicker_Setting.py --demo --port 8099
node omnipicker_ui/qa/browser.mjs
```

새 목표/축 조작 UI의 브라우저 QA: `node omnipicker_ui/qa/motion-browser.mjs` (동일한 8098 데모 서버·9224 전용 Chrome 환경). 결과는 `qa/live_results/motion_ui_verification.json`, 읽기 전용 실장비 FK/IK 확인은 `motion_preview_readonly.json`입니다.

연속 드래그 검증: `node omnipicker_ui/qa/drag-browser.mjs` (8098 데모 서버와 9224 전용 Chrome). 실제 마우스 드래그·클릭 무동작·증분 독립성·소프트 한계·포인터 해제/취소·큰/작은 숫자 증분·모바일을 확인합니다.


## 조작 콘솔

왼쪽 메뉴의 조작/포인트/상태/설정으로 작업을 나눕니다. 조작 화면의 관절/TCP/그리퍼 탭과 3D 조작 모드는 함께 전환됩니다. TCP 탭은 현재·목표의 절대 좌표와 TCP 기준 상대 이동량을 따로 표시합니다. 목표 좌표와 3D TCP 화살표는 포인터 입력 즉시 바뀌고, 로봇 쉐도우는 컨트롤러 FK/IK가 검증한 관절로 갱신됩니다. IK 해가 없거나 기준이 바뀌면 목표 불가를 표시하고 적용을 잠급니다. 실제 장비 이동은 목표 적용에서만 실행합니다.

관절 드래그 모드에는 조작 가능한 회전 호와 화살표만 표시합니다. 선택한 관절은 강조하며 비조작 XYZ 표식과 관절 라벨은 제거했습니다.

포인트는 현재/목표 관절, 속도, 툴·좌표계와 그리퍼 파라미터를 이 브라우저에 최대 100개 저장합니다. 포인트 불러오기는 미리보기만 바꾸며 자동 이동하지 않습니다. JSON 가져오기는 전체 형식을 검증한 후 목록을 바꾸고, 내보내기는 포인트 파일로 저장합니다.

RS485 오류 103의 **연결 복구**는 검증된 FR3 버전과 현재 RTU 설정 `(0,7,8,0,1,1)`을 확인한 후 같은 설정을 한 번 재적용하고 설정·펌웨어·피드백을 읽어 검증합니다. 위치·속도·토크·트리거는 쓰지 않습니다. 설정 명령의 반환값이 이전 Lua 결과일 수 있어 응답 유무와 실제 통신 검증을 구분합니다. 응답 불확실 시 설정 명령을 자동 재전송하지 않습니다. 알 수 없는 펌웨어는 버전 불일치로 표시하지 않고 조회 대기로 표시합니다. 유효한 335 응답이 돌아오면 버전 검증 후 쓰기 경로를 복구합니다.

`node omnipicker_ui/qa/console-browser.mjs`는 데모에서 IK 지연 중 목표 좌표/3D 마커 즉시 갱신, 검증된 관절 쉐도우, 모드 전환 시 이전 응답 무시, 포인트 저장/불러오기/가져오기, 장치 상태, SDK 목록, 복구, 데스크톱·모바일 화면을 확인합니다.


공개 저장소에는 장비별 `qa/live_results/` 기록과 로컬 SDK 원본을 포함하지 않습니다. 모델·SDK 인벤토리의 출처와 해시는 보존하며, 인벤토리의 원본 파일 경로는 `external-sdk/` 기준 표기로 정리했습니다.
