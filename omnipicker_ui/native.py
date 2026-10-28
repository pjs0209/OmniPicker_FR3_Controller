"""FR3 controller TCP command channel; no HTTP/WebApp or uploaded program.

Frame format: FAIRINO V3.9.8 Controller Communication Command Protocol §1.1.
The controller interprets native instruction strings internally. This does not
upload, load, replace, or run a user Lua file or peripheral protocol.
"""
import ipaddress
import socket
import threading
import time

from .validation import number


def frame(sequence, command, content):
    body = content.encode("ascii")
    if b"III" in body or len(body) > 2048:
        raise ValueError("FR3 명령 형식 오류")
    return f"/f/bIII{sequence}III{command}III{len(body)}III".encode() + body + b"III/b/f"


class NativeTCP:
    """One acknowledged command per connection. Never retries a sent command."""
    def __init__(self, ip, port=8080, timeout=3.):
        self.ip = str(ipaddress.IPv4Address(ip))
        self.port, self.timeout = port, timeout
        self.sequence = 0
        self.lock = threading.Lock()

    def _request(self, command, content):
        with self.lock:
            self.sequence = self.sequence % 65535 + 1
            sequence = self.sequence
            outgoing = frame(sequence, command, content)
            with socket.create_connection((self.ip, self.port), timeout=self.timeout) as conn:
                deadline = time.monotonic() + self.timeout
                conn.sendall(outgoing)
                incoming = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("FR3 TCP 응답 시간 초과 · 재전송 안 함")
                    conn.settimeout(remaining)
                    block = conn.recv(4096)
                    if not block:
                        raise RuntimeError("FR3 TCP 응답 유실 · 재전송 안 함")
                    incoming.extend(block)
                    if len(incoming) > 8192:
                        raise RuntimeError("FR3 TCP 응답 길이 초과")
                    if b"III/b/f" not in incoming:
                        continue
                    parts = bytes(incoming).split(b"III", 5)
                    if len(parts) != 6 or parts[0] != b"/f/b" or parts[5] != b"/b/f":
                        raise RuntimeError("FR3 TCP 응답 프레임 오류")
                    try:
                        reply_sequence, reply_command, size = map(int, parts[1:4])
                    except ValueError as exc:
                        raise RuntimeError("FR3 TCP 응답 헤더 오류") from exc
                    if (reply_sequence, reply_command) != (sequence, command) or size != len(parts[4]):
                        raise RuntimeError("FR3 TCP 응답 번호/길이 불일치")
                    return parts[4].decode("ascii")

    def version(self):
        return self._request(905, "GetSoftwareVersion()")

    def write_registers(self, address, values, slave=1):
        # Limit this SDK transport to the OmniPicker command block. Reject all
        # malformed values before opening a device connection.
        address = number(address, 10, 15, "레지스터", True)
        slave = number(slave, 1, 247, "슬레이브 주소", True)
        values = [number(value, 0, 255, "레지스터 값", True) for value in values]
        if not values or address + len(values) > 16:
            raise ValueError("OmniPicker 명령 레지스터 10~15만 허용")
        payload = ",".join(map(str, values))
        # 1165 is the controller's RTU-write command category. Native commands
        # use the instruction parser, avoiding the defective raw XML-RPC write
        # adapter. isthread=1 permits RS485 work during a MoveJ RPC.
        reply = self._request(1165, f"ModbusRegWrite(16,{address},{len(values)},{{{payload}}},{slave},1)")
        if reply != "1":
            raise RuntimeError(f"FR3 RS485 쓰기 거부: {reply}")
        return 0

    def rtu_parameters(self):
        reply=self._request(1151,'ModbusSlaveGetParam_RTU()')
        try: values=tuple(int(v) for v in reply.split(','))
        except ValueError as exc:raise RuntimeError('FR3 RTU 설정 응답 오류') from exc
        if len(values)!=6:raise RuntimeError('FR3 RTU 설정 응답 길이 오류')
        return values

    def reopen_rtu(self):
        """Reopen the validated bus with identical parameters; never sends a motor command."""
        if self.version().split(',')[1:]!=['v3.9.8','V3.9.35-QX']:
            raise RuntimeError('검증된 FR3 TCP 버전과 다름')
        values=self.rtu_parameters()
        if values!=(0,7,8,0,1,1):
            raise RuntimeError(f'현재 RTU 설정 {values}: 검증된 115200/8N1 설정이 필요합니다.')
        reply=self._request(1150,'ModbusSlaveSetParam_RTU('+','.join(map(str,values))+')')
        # This firmware may return a stale Lua result for the void setter.
        # Verify unchanged serial parameters and let the owner verify real bus feedback.
        self.last_reopen_acknowledged=reply=='1'
        if self.rtu_parameters()!=values:raise RuntimeError('RS485 설정 읽기 검증 불일치')
        return list(values)
