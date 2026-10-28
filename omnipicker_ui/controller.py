#!/usr/bin/env python3
"""Local UI adapter for the standalone FR3 + OmniPicker SDK."""
import os
import secrets
import hmac
from flask import Flask, abort, jsonify, render_template, request
from .validation import number, increment
from .sdk import OmniPickerFR3, DemoRobot, OneShotTransport
from .rs485 import RS485Gripper

PORT = int(os.getenv("WEB_PORT", "8088"))
ROBOT_IP = os.getenv("FR3_IP", "192.168.57.2")
DEMO = os.getenv("CONTROL_DEMO", "0") == "1"
SLAVE_ID = int(os.getenv("GRIPPER_SLAVE", "1"))
TOKEN = secrets.token_urlsafe(32)
app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4096)


class ControlPanel(OmniPickerFR3):
    def __init__(self):
        super().__init__(ROBOT_IP, slave_id=SLAVE_ID, demo=DEMO)


panel = ControlPanel()


@app.before_request
def guard():
    if request.host not in (f"127.0.0.1:{PORT}",f"localhost:{PORT}"): abort(403)
    if request.method == "POST":
        if not hmac.compare_digest(request.headers.get("X-Control-Token",""),TOKEN): abort(403)
        if request.headers.get("Origin") not in (None,f"http://127.0.0.1:{PORT}",f"http://localhost:{PORT}"): abort(403)


@app.after_request
def headers(response):
    response.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY"})
    return response


@app.errorhandler(Exception)
def error(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc,HTTPException): return jsonify(error=exc.description),exc.code
    if request.path.startswith("/api/"):
        panel.event("오류",str(exc),"error")
        return jsonify(error=str(exc)),400 if isinstance(exc,(ValueError,TypeError,KeyError,RuntimeError)) else 500
    raise exc


@app.get("/")
def index(): return render_template("controller.html",token=TOKEN,robot_ip=ROBOT_IP,demo=DEMO,slave_id=SLAVE_ID)


@app.get("/api/status")
def status(): return jsonify(panel.snapshot())


@app.get("/api/sdk/features")
def sdk_features(): return jsonify(panel.features())


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data,dict): raise ValueError("JSON 객체 필요")
    return data


@app.post("/api/connect")
def connect():
    panel.connect(str(payload().get("ip",ROBOT_IP)),wait=False)
    return jsonify(ok=True),202


@app.post("/api/enable")
def enable():
    if payload().get("confirm") != "PHYSICAL_AREA_CLEAR": raise ValueError("작업영역 확인 필요")
    panel.arm()
    return jsonify(ok=True,seconds=60)


@app.post("/api/lock")
def lock():
    panel.disarm()
    return jsonify(ok=True)


@app.post("/api/joint")
def joint():
    data = payload()
    axis = number(data.get("axis"),0,5,"관절",True)
    delta = increment(data.get("delta"))
    return jsonify(ok=True,result=panel.joint(axis,delta,number(data.get("vel",5),1,100,"속도"),
        number(data.get("tool",0),0,14,"툴",True),number(data.get("user",0),0,14,"좌표계",True)))


@app.post("/api/gripper")
def grip():
    data = payload()
    return jsonify(ok=True,result=panel.grip(number(data.get("position"),0,255,"목표",True),
        number(data.get("speed",40),panel.gripper_limits.speed_min,panel.gripper_limits.speed_max,"속도",True),number(data.get("torque",40),panel.gripper_limits.torque_min,panel.gripper_limits.torque_max,"토크",True),
        acceleration=data.get('acceleration',40),deceleration=data.get('deceleration',40),
        accept_contact=data.get('accept_contact',False),timeout=data.get('timeout',8),tolerance=data.get('tolerance',3)))


@app.post("/api/gripper/probe")
def probe(): return jsonify(ok=True,result=panel.probe_gripper())


@app.post("/api/together")
def together():
    data = payload()
    return jsonify(ok=True,result=panel.move_together(axis=data.get("axis"),delta=data.get("delta"),
        velocity=data.get("vel",5),tool=data.get("tool",0),user=data.get("user",0),
        position=data.get("position"),speed=data.get("speed",10),torque=data.get("torque",10),
        acceleration=data.get('acceleration',40),deceleration=data.get('deceleration',40),
        accept_contact=data.get('accept_contact',False)))


@app.post("/api/stop")
def stop():
    panel.stop()
    return jsonify(ok=True)


@app.post('/api/robot/preview')
def robot_preview():
    d=payload()
    return jsonify(ok=True,result=panel.preview_robot(d.get('kind','joint'),joints=d.get('joints'),
        delta=d.get('delta'),reference=d.get('reference'),tool=d.get('tool',0),user=d.get('user',0)))


@app.post('/api/robot/apply')
def robot_apply():
    d=payload()
    return jsonify(ok=True,result=panel.apply_robot_plan(d.get('plan_id'),d.get('vel',5)))


@app.get('/gripper/settings')
def gripper_settings():
    return render_template('gripper_settings.html',token=TOKEN,demo=DEMO)


@app.post('/api/gripper/stage')
def gripper_stage():
    d=payload()
    return jsonify(ok=True,result=panel.omnipicker.stage(d.get('position'),d.get('speed'),d.get('torque'),
        acceleration=d.get('acceleration',40),deceleration=d.get('deceleration',40)))


@app.post('/api/gripper/trigger')
def gripper_trigger():
    d=payload()
    return jsonify(ok=True,result=panel.omnipicker.trigger(accept_contact=d.get('accept_contact',False)))


@app.post('/api/gripper/parameter')
def gripper_parameter():
    d=payload()
    return jsonify(ok=True,result=panel.omnipicker.write(d.get('name'),d.get('value')))


def mount_usb_settings():
    from . import setting
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    setting.PORT=PORT;setting.DEMO=DEMO
    def pause():
        with panel.lock:
            if panel.robot_busy or panel.gripper_busy: raise RuntimeError('로봇/그리퍼 처리 중')
            panel.external_bus_pause=True
            panel.disarm()
        # Drain a read already in flight before claiming USB.
        if panel.gripper:
            with panel.gripper.bus_lock: panel.gripper.suspended=True
    setting.panel.before_connect=pause
    def resume():
        if panel.gripper:
            with panel.gripper.bus_lock:panel.gripper.suspended=False
        panel.external_bus_pause=False
    setting.panel.after_close=resume
    app.wsgi_app=DispatcherMiddleware(app.wsgi_app,{'/gripper/usb':setting.app})


mount_usb_settings()


@app.get('/api/gripper/registers')
def gripper_registers():
    from .api import REGISTERS
    if not panel.gripper or panel.robot_busy or panel.gripper_busy:raise RuntimeError('FR3 연결·정지 상태 확인 필요')
    if panel.external_bus_pause:raise RuntimeError('USB 설정 중 · RS485 조회 잠금')
    if panel.client.robot.GetProgramState() not in ((0,1),[0,1]):raise RuntimeError('다른 FR3 프로그램 실행 중')
    with panel.gripper.bus_lock:
        values={a:panel.gripper.read_registers(a,1)[0] for a in REGISTERS}
    return jsonify(registers=[dict(address=a,name=name,value=values[a],access='rw' if 10<=a<=15 else 'r') for a,name in REGISTERS.items()])


@app.post('/api/gripper/limits')
def gripper_limits():
    from .motion import GripperLimits
    d=payload()
    limits=GripperLimits(**{k:d[k] for k in ('speed_min','speed_max','torque_min','torque_max')})
    with panel.lock:
        if panel.robot_busy or panel.gripper_busy:raise RuntimeError('처리 중')
        panel.disarm();panel.gripper_limits=limits
        if panel.gripper:panel.gripper.limits=limits
    return jsonify(ok=True,limits=panel.motion_limits())


@app.post('/api/gripper/wait')
def gripper_wait():
    d=payload()
    return jsonify(ok=True,result=panel.omnipicker.wait(timeout=d.get('timeout',8),tolerance=d.get('tolerance',3),accept_contact=d.get('accept_contact',False)))


@app.post('/api/gripper/recover')
def gripper_recover():
    if payload().get('confirm')!='REOPEN_RS485':raise ValueError('RS485 재연결 확인 필요')
    return jsonify(ok=True,result=panel.recover_rs485())
