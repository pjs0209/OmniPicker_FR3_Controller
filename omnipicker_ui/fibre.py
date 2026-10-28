"""Minimal OmniPicker USB Fibre transport. All endpoint IDs come from live JSON.

USB identity/interface and endpoint-zero discovery follow the OP1 Fibre protocol.
Configuration meanings: https://www.agibot.com/DOCS/PM/news (sections 6.2, 6.4).
Generic scalar/function access is driven by the connected firmware descriptor.
"""
import json
import math
import struct

VID, PID = 0x1001, 0x03EF
FORMATS = {"bool": "?", "uint8": "B", "uint32": "I", "int32": "i", "float": "f"}
FIELDS = {
    "node_id": ("can_node_id", "uint32"),
    "rs485_mode": ("rs485_mode", "bool"),
    "modbus_mode": ("modbus_mode", "bool"),
    "baudrate": ("uart_baudrate", "uint32"),
    "stopbits": ("uart_stopbits", "uint32"),
    "enable_on_boot": ("motor.config.enable_on_boot", "bool"),
}
INFO = {
    "serial_number": ("serial_number", "uint32"),
    "firmware": ("fw_version", "uint32"),
    "error_code": ("motor.error_code", "uint32"),
    "calibrated": ("motor.config.calib_valid", "bool"),
    "current_limit": ("motor.config.current_limit", "float"),
    "velocity_limit": ("motor.config.velocity_limit", "float"),
}


def crc16(data, initial=1):
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x3D65 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def flatten(nodes, parent=""):
    result = {}
    for node in nodes:
        name = node.get("name", "")
        path = f"{parent}.{name}" if parent else name
        if path in result:
            raise RuntimeError("중복된 USB 엔드포인트 경로")
        result[path] = node
        result.update(flatten(node.get("members", []), path))
        for direction in ("inputs", "outputs"):
            for parameter in node.get(direction, []):
                result[path + "." + parameter["name"]] = parameter
    return result


def usb_modules():
    try:
        import usb.core
        import usb.util
        return usb.core, usb.util
    except ImportError as exc:
        raise RuntimeError("PyUSB 설치 필요: python3 -m pip install -r omnipicker_ui/requirements.txt") from exc


def devices():
    core, _ = usb_modules()
    return [dict(bus=d.bus, address=d.address, label=f"OmniPicker · USB {d.bus}:{d.address}")
            for d in core.find(find_all=True, idVendor=VID, idProduct=PID)]


class FibreDevice:
    def __init__(self, bus=None, address=None):
        core, self.util = usb_modules()
        matches = list(core.find(find_all=True, idVendor=VID, idProduct=PID))
        if bus is not None and address is not None:
            matches = [d for d in matches if d.bus == bus and d.address == address]
        if len(matches) != 1:
            raise RuntimeError("USB-C로 OmniPicker 한 대를 연결하거나 목록에서 장치를 선택하세요.")
        self.dev = matches[0]
        self.sequence = 0
        self.claimed = False
        try:
            if self.dev.is_kernel_driver_active(2):
                raise RuntimeError("USB 인터페이스 2가 사용 중입니다. REF-CLI/SDK 프로그램을 종료하세요.")
            self.util.claim_interface(self.dev, 2)
            self.claimed = True
            raw = self.descriptor()
            self.schema = json.loads(raw.decode("utf-8").rstrip("\x00"))
            self.endpoints = flatten(self.schema)
            self.crc = crc16(raw)
            self.validate()
        except Exception:
            self.close()
            raise

    def exchange(self, endpoint, payload=b"", count=0, crc=None):
        self.sequence = self.sequence % 0x7FFF + 1
        packet = (struct.pack("<HHH", self.sequence, endpoint | 0x8000, count)
                  + payload + struct.pack("<H", self.crc if crc is None else crc))
        self.dev.write(0x03, packet, timeout=2000)
        reply = bytes(self.dev.read(0x83, 64, timeout=2000))
        if len(reply) < 2 or struct.unpack("<H", reply[:2])[0] != self.sequence | 0x8000:
            raise RuntimeError("USB Fibre 시퀀스 응답 불일치. 자동 재전송하지 않습니다.")
        if len(reply[2:]) > count:
            raise RuntimeError("USB Fibre 응답 길이 초과")
        return reply[2:]

    def descriptor(self):
        raw = bytearray()
        for _ in range(256*1024//30 + 1):
            part = self.exchange(0, struct.pack("<I", len(raw)), count=30, crc=1)
            if not part:
                return bytes(raw)
            raw.extend(part)
        raise RuntimeError("USB 디스크립터 크기 초과")

    def endpoint(self, path, dtype=None, write=False):
        node = self.endpoints.get(path)
        if not node or (dtype and node.get("type") != dtype):
            raise RuntimeError(f"이 펌웨어에서 지원하지 않는 엔드포인트: {path}")
        if write and "w" not in node.get("access", ""):
            raise RuntimeError(f"읽기 전용 엔드포인트: {path}")
        eid = node.get("id")
        if isinstance(eid, bool) or not isinstance(eid, int) or not 0 < eid < 0x8000:
            raise RuntimeError(f"USB 엔드포인트 ID 오류: {path}")
        return node

    def validate(self):
        for path, dtype in FIELDS.values():
            self.endpoint(path, dtype, write=True)
        for path, dtype in INFO.values():
            self.endpoint(path, dtype)
        for path, args in [("motor.request_state", [("state", "uint8")]),
                           ("motor.ctrl.set_mit", [(k, "float") for k in ("pos", "vel", "tor", "kp", "kd")]),
                           ("save_config", []), ("reboot", [])]:
            function = self.endpoint(path, "function")
            if [(n["name"], n["type"]) for n in function.get("inputs", [])] != args:
                raise RuntimeError(f"USB 함수 인자 불일치: {path}")
            for key, dtype in args:
                self.endpoint(f"{path}.{key}", dtype, write=True)
        self.endpoint("motor.request_state.result", "bool")
        self.endpoint("save_config.result", "bool")
        for key in ("pos_min", "pos_max", "kp_min", "kp_max", "kd_min", "kd_max"):
            self.endpoint("motor.config.mit_params." + key, "float")

    def read(self, path, dtype=None):
        node = self.endpoint(path, dtype)
        if node['type'] not in FORMATS: raise RuntimeError(f'USB 스칼라 속성이 아님: {path}')
        fmt = "<" + FORMATS[node["type"]]
        data = self.exchange(node["id"], count=struct.calcsize(fmt))
        if len(data) != struct.calcsize(fmt):
            raise RuntimeError(f"USB 응답 길이 오류: {path}")
        value = struct.unpack(fmt, data)[0]
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError(f"USB 숫자 응답 오류: {path}")
        return value

    @staticmethod
    def typed_value(dtype,value,path):
        if dtype not in FORMATS:raise ValueError(f'USB 스칼라 속성이 아님: {path}')
        if dtype=='bool':
            if type(value) is not bool:raise ValueError(f'{path}: bool 필요')
        elif dtype=='float':
            if isinstance(value,bool) or not math.isfinite(float(value)) or abs(float(value))>3.4028234e38:raise ValueError(f'{path}: float 범위 필요')
            value=float(value)
        else:
            bounds={'uint8':(0,255),'uint32':(0,2**32-1),'int32':(-2**31,2**31-1)}
            if isinstance(value,bool) or not isinstance(value,int) or not bounds[dtype][0]<=value<=bounds[dtype][1]:raise ValueError(f'{path}: {dtype} 범위의 정수 필요')
        return value

    def write(self,path,value):
        node=self.endpoint(path,write=True)
        value=self.typed_value(node['type'],value,path)
        self.exchange(node['id'],struct.pack('<'+FORMATS[node['type']],value))

    def call(self, path, **args):
        function = self.endpoint(path, "function")
        if set(args) != {n["name"] for n in function.get("inputs", [])}:
            raise RuntimeError("USB 함수 인자 오류")
        args={key:self.typed_value(self.endpoint(path+'.'+key,write=True)['type'],value,path+'.'+key) for key,value in args.items()}
        for key, value in args.items():
            self.write(path + "." + key, value)
        self.exchange(function["id"])
        output = {n["name"]: self.read(path + "." + n["name"]) for n in function.get("outputs", [])}
        if "result" in output and output["result"] is not True:
            raise RuntimeError(f"장치에서 요청을 거부했습니다: {path}")
        return output

    def settings(self):
        return {key: self.read(path, dtype) for key, (path, dtype) in FIELDS.items()}

    def info(self):
        values = {key: self.read(path, dtype) for key, (path, dtype) in INFO.items()}
        version = values["firmware"]
        values["firmware_hex"] = f"0x{version:08X}"
        values["schema_crc"] = f"0x{self.crc:04X}"
        return values

    def ranges(self):
        return {key: self.read("motor.config.mit_params." + key, "float")
                for key in ("pos_min", "pos_max", "kp_min", "kp_max", "kd_min", "kd_max")}

    def disable(self):
        self.call("motor.request_state", state=0)

    def close(self):
        if getattr(self, "claimed", False):
            try:
                self.util.release_interface(self.dev, 2)
            except Exception:
                pass
            self.claimed = False
        self.util.dispose_resources(self.dev)


class DemoFibre:
    persisted = dict(node_id=1, rs485_mode=True, modbus_mode=True, baudrate=115200,
                     stopbits=0, enable_on_boot=False)

    def __init__(self, **kwargs):
        self.values = self.persisted.copy()
        from pathlib import Path
        features=json.loads((Path(__file__).parent/'sdk_catalog.json').read_text())['omnipicker_fibre']['features']
        self.schema=[];self.endpoints={};self.extra={}
        for i,f in enumerate(features):
            node=dict(name=f['name'],type=f.get('type','function'),id=i+1,access=f.get('access',''),inputs=f.get('inputs',[]),outputs=f.get('outputs',[]))
            if f['kind']=='function':node['type']='function'
            self.schema.append(node);self.endpoints[f['name']]=node
            if node['type'] in FORMATS:self.extra[f['name']]=False if node['type']=='bool' else 0
        self.extra.update({path:self.values[key] for key,(path,_) in FIELDS.items()})

    def settings(self):
        return self.values.copy()

    def info(self):
        return dict(serial_number="DEMO-0001", firmware_hex="DEMO · 3.3.5", error_code=0,
                    calibrated=True, current_limit=2., velocity_limit=10., schema_crc="DEMO")

    def ranges(self):
        return dict(pos_min=0., pos_max=1., kp_min=0., kp_max=500., kd_min=0., kd_max=5.)

    def read(self,path,dtype=None):
        if path not in self.extra:raise ValueError('알 수 없는 USB 속성')
        return self.extra[path]

    def write(self,path,value):
        node=self.endpoints.get(path)
        if not node or 'w' not in node['access']:raise ValueError('읽기 전용/없는 속성')
        value=FibreDevice.typed_value(node['type'],value,path);self.extra[path]=value
        key=next((k for k,v in FIELDS.items() if v[0]==path),None)
        if key:self.values[key]=value

    def call(self,path,**kwargs):
        node=self.endpoints.get(path)
        if not node or node['type']!='function':raise ValueError('알 수 없는 USB 함수')
        if set(kwargs)!={n['name'] for n in node['inputs']}:raise ValueError('USB 함수 인자 오류')
        for arg in node['inputs']:FibreDevice.typed_value(arg['type'],kwargs[arg['name']],path)
        if path=='save_config':type(self).persisted=self.values.copy()
        return {n['name']:True if n['type']=='bool' else 0 for n in node['outputs']}

    def disable(self):
        pass

    def close(self):
        pass
