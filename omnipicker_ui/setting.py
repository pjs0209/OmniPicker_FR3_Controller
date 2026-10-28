"""Local USB settings application; USB claims begin only after user connects."""
import atexit
import hmac
import math
import os
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager

from flask import Flask, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from .fibre import DemoFibre, FibreDevice, FIELDS, devices
from .validation import number

PORT = int(os.getenv("SETTING_PORT", "8089"))
DEMO = os.getenv("SETTING_DEMO", "0") == "1"
TOKEN = secrets.token_urlsafe(32)
app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4096)


class USBPanel:
    def __init__(self):
        self.lock = threading.RLock()
        self.usb_lock = threading.Lock()
        self.before_connect=lambda:None
        self.after_close=lambda:None
        self.bus_claimed=False
        self.device = None
        self.busy = False
        self.settings = None
        self.info = None
        self.ranges = None
        self.error = ""
        self.updated = None
        self.unsaved = False
        self.enabled_until = 0
        self.target = None
        self.events = deque(maxlen=80)
        self.event_id = 0
        self.event("시스템", "USB 설정 대기" if not DEMO else "데모 모드 · USB 통신 없음")

    def event(self, source, message, level="info"):
        with self.lock:
            self.event_id += 1
            self.events.appendleft(dict(id=self.event_id, time=time.time(), source=source,
                                        message=message, level=level))

    @contextmanager
    def operation(self, require_device=True):
        if not self.usb_lock.acquire(blocking=False):
            raise RuntimeError("USB 작업 처리 중입니다.")
        with self.lock:
            self.busy = True
        try:
            if require_device and self.device is None:
                raise RuntimeError("USB 장치를 먼저 연결하세요.")
            yield
        finally:
            with self.lock:
                self.busy = False
            self.usb_lock.release()

    def snapshot(self):
        with self.lock:
            return dict(mode="demo" if DEMO else "hardware", connected=self.device is not None,
                        busy=self.busy, settings=self.settings, info=self.info, ranges=self.ranges,
                        error=self.error, updated=self.updated, unsaved=self.unsaved, target=self.target,
                        armed=self.device is not None and time.monotonic() < self.enabled_until,
                        seconds=max(0, math.ceil(self.enabled_until-time.monotonic())), events=list(self.events))

    def refresh(self):
        settings, info, ranges = self.device.settings(), self.device.info(), self.device.ranges()
        with self.lock:
            self.settings, self.info, self.ranges = settings, info, ranges
            self.updated, self.error = time.time(), ""

    def close(self):
        if self.device:
            self.device.close()
        with self.lock:
            self.device = None
            self.enabled_until = 0
        if self.bus_claimed:
            self.bus_claimed=False
            self.after_close()

    def connect(self, bus, address):
        with self.operation(False):
            if self.device:
                raise RuntimeError("이미 USB 장치에 연결되어 있습니다.")
            try:
                self.before_connect()
                self.bus_claimed=True
                self.device = (DemoFibre if DEMO else FibreDevice)(bus=bus, address=address)
                self.refresh()
                self.event("USB", "연결됨 · 장치 정보와 설정을 읽었습니다. 모터 상태는 변경하지 않았습니다.")
            except Exception:
                self.close()
                raise

    def apply(self, values):
        with self.operation():
            self.enabled_until = 0
            self.device.disable()
            # Mark dirty before the first write; a lost response can mean a partial apply.
            self.unsaved = True
            try:
                for key, value in values.items():
                    path = FIELDS[key][0]
                    self.device.write(path, value)
                    if self.device.settings()[key] != value:
                        raise RuntimeError(f"{key}: 쓰기 후 읽기 검증 실패")
                self.refresh()
                self.event("설정", "모터 비활성화 → RAM 설정 적용 → 읽기 검증 완료. 영구 저장은 별도입니다.")
            except Exception:
                self.close()
                raise


panel = USBPanel()
atexit.register(panel.close)


@app.before_request
def guard():
    if request.host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        abort(403)
    if request.method == "POST":
        if not hmac.compare_digest(request.headers.get("X-Control-Token", ""), TOKEN):
            abort(403)
        if request.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            abort(403)


@app.after_request
def headers(response):
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN" if request.script_root else "DENY"})
    return response


@app.errorhandler(Exception)
def error(exc):
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    if not request.path.startswith("/api/"):
        raise exc
    panel.error = str(exc)
    panel.event("오류", str(exc), "error")
    return jsonify(error=str(exc)), 400


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    return data


@app.get("/")
def index():
    return render_template("setting.html", token=TOKEN, demo=DEMO, api_base=request.script_root+"/api/")


@app.get("/api/status")
def status():
    return jsonify(panel.snapshot())


@app.get("/api/devices")
def scan():
    return jsonify(devices=[dict(bus=1, address=1, label="DEMO · OmniPicker USB")] if DEMO else devices())


@app.post("/api/connect")
def connect():
    d = payload()
    bus = number(d["bus"], 1, 65535, "USB bus", True) if d.get("bus") is not None else None
    address = number(d["address"], 1, 255, "USB address", True) if d.get("address") is not None else None
    panel.connect(bus, address)
    return jsonify(ok=True)


@app.post("/api/disconnect")
def disconnect():
    with panel.operation():
        # Disable only on an explicit user disconnect, never on read-only connect.
        panel.device.disable()
        panel.close()
        panel.event("USB", "모터 비활성화 후 USB 연결을 해제했습니다.")
    return jsonify(ok=True)


@app.post("/api/refresh")
def refresh():
    with panel.operation():
        try:
            panel.refresh()
        except Exception:
            panel.close()
            raise
    return jsonify(ok=True)


@app.post("/api/settings")
def settings():
    d = payload()
    if d.get("confirm") != "APPLY_SETTINGS":
        raise ValueError("설정 적용 확인이 필요합니다.")
    values = d.get("settings")
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError("설정 항목이 올바르지 않습니다.")
    for key in ("rs485_mode", "modbus_mode", "enable_on_boot"):
        if not isinstance(values[key], bool):
            raise ValueError(f"{key}: 참/거짓 값이 필요합니다.")
    values["node_id"] = number(values["node_id"], 1, 247, "노드 ID", True)
    values["baudrate"] = number(values["baudrate"], 9600, 1000000, "보드레이트", True)
    if values["baudrate"] not in (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600):
        raise ValueError("지원 목록의 보드레이트를 선택하세요.")
    values["stopbits"] = number(values["stopbits"], 0, 2, "정지 비트 코드", True)
    panel.apply(values)
    return jsonify(ok=True)


@app.post("/api/save")
def save():
    if payload().get("confirm") != "SAVE_FLASH":
        raise ValueError("플래시 저장 확인이 필요합니다.")
    with panel.operation():
        panel.enabled_until = 0
        panel.device.disable()
        try:
            panel.device.call("save_config")
            panel.unsaved = False
            panel.event("저장", "플래시 저장 응답 수신. 전원을 다시 켠 후 USB로 재연결하여 값을 확인하세요.")
        except Exception as exc:
            panel.event("저장", "저장 응답 불확실 · 전원 재인가 후 설정을 다시 읽으세요. 자동 재시도하지 않습니다.", "warning")
            raise RuntimeError("저장 중 연결 종료/오류. 저장 여부는 재연결 후 확인해야 합니다.") from exc
        finally:
            # A successful save can reboot firmware. Always require a new USB claim.
            panel.close()
    return jsonify(ok=True)


@app.post("/api/enable")
def enable():
    if payload().get("confirm") != "PHYSICAL_AREA_CLEAR":
        raise ValueError("그리퍼 작업영역 확인이 필요합니다.")
    with panel.operation():
        panel.refresh()
        if panel.info["error_code"] != 0 or not panel.info["calibrated"]:
            raise RuntimeError("모터 오류 또는 캘리브레이션 미완료 상태입니다.")
        # Allow commands; actual motor enable is part of the explicit test action.
        panel.enabled_until = time.monotonic() + 60
    return jsonify(ok=True)


@app.post("/api/lock")
def lock():
    panel.enabled_until = 0
    return jsonify(ok=True)


@app.post("/api/move")
def move():
    d = payload()
    position = number(d.get("position"), 0, 1, "목표 위치")
    kp = number(d.get("kp"), 0, 50, "Kp")
    kd = number(d.get("kd"), 0, 5, "Kd")
    with panel.operation():
        if time.monotonic() >= panel.enabled_until:
            raise RuntimeError("USB 동작 테스트를 먼저 허용하세요.")
        panel.refresh()
        if panel.info["error_code"] != 0 or not panel.info["calibrated"]:
            raise RuntimeError("모터 오류 또는 캘리브레이션 미완료 상태입니다.")
        for name, value in [("pos", position), ("kp", kp), ("kd", kd)]:
            if not panel.ranges[name+"_min"] <= value <= panel.ranges[name+"_max"]:
                raise ValueError(f"{name}: 장치에서 읽은 MIT 허용 범위 밖입니다.")
        if time.monotonic() >= panel.enabled_until:
            raise RuntimeError("USB 동작 허용 시간이 만료되었거나 잠겼습니다.")
        try:
            panel.device.call("motor.request_state", state=1)
            panel.device.call("motor.ctrl.set_mit", pos=position, vel=0., tor=0., kp=kp, kd=kd)
            panel.target = position
            panel.event("동작", f"USB MIT 목표 {position:.3f} · Kp {kp:g} / Kd {kd:g} 전송. 실제 도달 미확인")
        except Exception:
            panel.enabled_until = 0
            panel.close()
            raise
    return jsonify(ok=True, actual_position=None)


@app.post("/api/disable")
def disable():
    panel.enabled_until = 0
    with panel.operation():
        panel.device.disable()
        panel.event("모터", "모터 비활성화 요청 응답 수신 · 파지력이 해제될 수 있습니다.", "warning")
    return jsonify(ok=True)


def usb_features():
    result=[]
    def visit(nodes,parent=''):
        for node in nodes:
            path=parent+'.'+node['name'] if parent else node['name']
            if node.get('type')=='object':visit(node.get('members',[]),path)
            elif node.get('type') in ('bool','uint8','uint32','int32','float','function'):
                result.append(dict(name=path,**{k:node[k] for k in ('type','access','inputs','outputs') if k in node}))
    visit(panel.device.schema)
    return result


@app.get('/api/endpoints')
def endpoints():
    with panel.operation():return jsonify(features=usb_features())


@app.post('/api/endpoint/read')
def endpoint_read():
    path=str(payload().get('path',''))
    with panel.operation():return jsonify(value=panel.device.read(path))


@app.post('/api/endpoint/execute')
def endpoint_execute():
    d=payload();path=str(d.get('path',''))
    if d.get('confirm')!='EXECUTE_USB_ENDPOINT':raise ValueError('USB 실행 확인 필요')
    with panel.operation():
        if time.monotonic()>=panel.enabled_until:raise RuntimeError('USB 제어 허용 필요')
        features={f['name']:f for f in usb_features()}
        if path not in features:raise ValueError('장치 엔드포인트에 없는 기능')
        feature=features[path];reset=path in ('save_config','reboot','erase_config')
        try:
            if feature['type']=='function':
                args=d.get('args',{})
                if not isinstance(args,dict):raise ValueError('함수 인자 객체 필요')
                result=panel.device.call(path,**args)
            else:
                panel.device.write(path,d.get('value'));panel.unsaved=True
                result=panel.device.read(path)
            panel.event('USB',path+' 실행 응답')
            return jsonify(ok=True,result=result)
        except Exception:
            panel.enabled_until=0
            panel.close()
            raise
        finally:
            if reset:panel.close()
