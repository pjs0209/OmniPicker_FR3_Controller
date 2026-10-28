"""Verified RS485 connectivity and explicit, motion-free recovery."""
class GripperConnection:
    def verify_gripper_transport(self,refresh_version=False):
        if self.demo:return
        if not self.native or not self.gripper:return
        if refresh_version:
            try:
                version=self.native.version().split(',')
                self.native_version_valid=len(version)==3 and version[1:]==['v3.9.8','V3.9.35-QX']
                if not self.native_version_valid:self.transport_error='검증된 FR3 TCP 버전과 다름'
            except Exception as exc:
                self.native_version_valid=False;self.transport_error=str(exc)
        if not self.native_version_valid:
            self.gripper.write_verified=False
            self.transport_error=self.transport_error or '검증된 FR3 TCP 버전과 다름'
        elif self.gripper.firmware is None:
            self.gripper.write_verified=False
            self.transport_error='RS485 응답 미확인 · 펌웨어 조회 대기'
        elif self.gripper.firmware!=335:
            self.gripper.write_verified=False
            self.transport_error=f'지원하지 않는 OmniPicker 펌웨어: {self.gripper.firmware} (필요: 335)'
        else:
            self.gripper.writer=self.native.write_registers
            self.gripper.write_verified=True
            self.transport_error=''

    def recover_rs485(self):
        with self.lock:
            if not self.snapshot()['robot']['connected'] or not self.gripper:raise RuntimeError('FR3 연결 필요')
            if self.robot_busy or self.gripper_busy or self.external_bus_pause or self.gripper.snapshot()['busy']:
                raise RuntimeError('장치 처리 중 · 정지 후 재연결하세요.')
            self.disarm();self.gripper_busy=True;self.recovering=True
        try:
            from .sdk import checked
            if checked(self.client.robot.GetProgramState(),'프로그램 상태',True)!=1:
                raise RuntimeError('다른 FR3 프로그램 실행 중')
            if checked(self.client.robot.GetExDevProtocol(),'RS485 프로토콜',True)!=4098:
                raise RuntimeError('RS485 Modbus Master 설정 필요')
            with self.gripper.bus_lock:
                params=[0,7,8,0,1,1] if self.demo else self.native.reopen_rtu()
            result=self.gripper.probe()
            self.verify_gripper_transport(refresh_version=True)
            if not result['feedback_valid']:raise RuntimeError(result['reason'])
            self.event('RS485','연결 복구 · 펌웨어 '+str(result['firmware']))
            return dict(parameters=params,communication_verified=True,acknowledged=True if self.demo else bool(getattr(self.native,'last_reopen_acknowledged',False)),state=self.gripper.snapshot())
        except Exception as exc:
            self.event('RS485',str(exc),'error');raise
        finally:
            with self.lock:self.gripper_busy=False;self.recovering=False
