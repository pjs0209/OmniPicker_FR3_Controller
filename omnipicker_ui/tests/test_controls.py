"""No robot or USB access: protocol framing, guards, faults and parallel control."""
import os
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ["CONTROL_DEMO"] = "1"
os.environ["SETTING_DEMO"] = "1"
from omnipicker_ui import controller, setting
from omnipicker_ui.fibre import DemoFibre, FibreDevice, crc16, flatten


class FibreTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc16(b"\x01\x02\x03\x04", 0x1337), 0x672E)

    def test_live_endpoint_ids_used(self):
        fibre = FibreDevice.__new__(FibreDevice)
        fibre.endpoints = flatten([dict(name="can_node_id", id=413, type="uint32", access="rw")])
        fibre.exchange = Mock(return_value=struct.pack("<I", 7))
        self.assertEqual(fibre.read("can_node_id", "uint32"), 7)
        fibre.exchange.assert_called_once_with(413, count=4)
        fibre.exchange = Mock(return_value=b"")
        fibre.write("can_node_id", 17)
        fibre.exchange.assert_called_once_with(413, struct.pack("<I", 17))

    def test_readonly_and_type_mismatch_fail_before_usb(self):
        fibre = FibreDevice.__new__(FibreDevice)
        fibre.endpoints = flatten([dict(name="can_node_id", id=42, type="uint32", access="r")])
        fibre.exchange = Mock()
        with self.assertRaises(RuntimeError):
            fibre.write("can_node_id", 3)
        with self.assertRaises(RuntimeError):
            fibre.read("can_node_id", "float")
        fibre.exchange.assert_not_called()

    def test_wrong_reply_sequence_and_short_data_rejected(self):
        fibre = FibreDevice.__new__(FibreDevice)
        fibre.dev = Mock()
        fibre.dev.read.return_value = b"\x07\x80"
        fibre.sequence, fibre.crc = 0, 0x1234
        with self.assertRaises(RuntimeError):
            fibre.exchange(10)
        fibre.dev.write.assert_called_once()
        fibre.endpoints = flatten([dict(name="value", id=10, type="uint32", access="r")])
        fibre.exchange = Mock(return_value=b"\x01")
        with self.assertRaises(RuntimeError):
            fibre.read("value")

    def test_network_write_never_retries(self):
        transport = controller.OneShotTransport()
        with patch.object(transport, "single_request", side_effect=OSError("lost response")) as call:
            with self.assertRaises(RuntimeError):
                transport.request("host", "/RPC2", b"request")
            self.assertEqual(call.call_count, 1)


class RobotTests(unittest.TestCase):
    def setUp(self):
        self.panel = controller.ControlPanel()
        self.panel.client = controller.DemoRobot()
        self.panel.gripper = controller.RS485Gripper(self.panel.client.robot, delay=0, write_verified=True,
                                                    writer=self.panel.client.write_registers)
        self.panel.poll()
        self.original = controller.panel
        controller.panel = self.panel
        self.client = controller.app.test_client()
        self.base = f"http://127.0.0.1:{controller.PORT}"

    def tearDown(self): controller.panel = self.original

    def post(self, path, data, token=True):
        return self.client.post("/api/"+path,json=data,base_url=self.base,
            headers={"X-Control-Token":controller.TOKEN} if token else {})

    def test_boot_locked_and_readonly(self):
        self.assertFalse(self.panel.snapshot()["robot"]["armed"])
        self.assertEqual(self.post("joint",dict(axis=0,delta=1)).status_code,400)
        self.assertEqual(self.panel.client.writes,[])

    def test_origin_host_token_guards(self):
        self.assertEqual(self.post("enable",{},token=False).status_code,403)
        self.assertEqual(self.client.post('/api/lock',json={},base_url=self.base,
            headers={'X-Control-Token':controller.TOKEN,'Origin':'https://bad.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/status',base_url='http://bad.example').status_code,403)

    def test_joint_nan_fractional_axis_and_bounds_rejected(self):
        self.panel.arm()
        for data in [dict(axis=0,delta='nan'),dict(axis=.5,delta=1),dict(axis=6,delta=1),dict(axis=0,delta=1000),
                     dict(axis=0,delta=1,vel=101),dict(axis=True,delta=1),dict(axis=0,delta=1,tool=15)]:
            self.assertEqual(self.post('joint',data).status_code,400)
        self.assertEqual(self.panel.client.joints[0],0)

    def test_http_joint_accepts_unrestricted_finite_increments(self):
        self.panel.arm();self.panel.client.MoveJ=Mock(return_value=0)
        for amount in [15,-15,.0001]:
            response=self.post('joint',dict(axis=0,delta=amount,vel=5))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['result']['target'][0],amount)
        self.assertEqual(self.panel.client.MoveJ.call_count,3)

    def test_stale_robot_and_gripper_hidden(self):
        self.panel.arm()
        self.panel.updated = time.monotonic()-4
        state=self.panel.snapshot()
        self.assertFalse(state['robot']['connected'])
        self.assertIsNone(state['gripper']['actual_position'])
        with self.assertRaises(RuntimeError): self.panel.require_enabled()

    def test_direct_speed_torque_write_and_measured_arrival(self):
        self.panel.arm()
        result=self.panel.grip(140,20,30)
        self.assertTrue(result['reached'])
        self.assertEqual(result['actual_position'],140)
        self.assertEqual(self.panel.client.writes,[(16,10,6,[140,20,30,40,40,1],1,1)])
        self.assertEqual(self.panel.snapshot()['gripper']['actual_position'],140)

    def test_robot_and_gripper_concurrent(self):
        self.panel.arm()
        started,release=threading.Event(),threading.Event()
        def move(target,**kwargs):
            started.set();release.wait(2);return 0
        self.panel.client.MoveJ=move
        worker=threading.Thread(target=self.panel.joint,args=(0,.5,5,0,0));worker.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(self.panel.grip(140,20,30)['reached'])
            self.assertTrue(self.panel.robot_busy)
        finally: release.set();worker.join(2)

    def test_native_write_precedes_idle_waiting_rpc_read_during_move(self):
        self.panel.arm()
        moving, written = threading.Event(), threading.Event()
        read = self.panel.client.ModbusRegRead
        write = self.panel.gripper.writer
        def move(target,**kwargs):
            moving.set()
            if not written.wait(2): raise RuntimeError('RS485 write was serialized behind arm motion')
            return 0
        def read_during_move(*args):
            if self.panel.robot_busy and not written.is_set():
                raise RuntimeError('idle-waiting read before concurrent write')
            return read(*args)
        def send(*args):
            result = write(*args)
            written.set()
            return result
        self.panel.client.MoveJ = move
        self.panel.client.ModbusRegRead = read_during_move
        self.panel.gripper.writer = send
        worker = threading.Thread(target=self.panel.joint,args=(0,.5))
        worker.start()
        try:
            self.assertTrue(moving.wait(1))
            self.assertTrue(self.panel.grip(140,20,30)['reached'])
            self.assertTrue(written.is_set())
        finally:
            written.set();worker.join(2)

    def test_ambiguous_move_latches_fault(self):
        self.panel.arm()
        self.panel.client.MoveJ=Mock(side_effect=RuntimeError('lost response'))
        with self.assertRaises(RuntimeError): self.panel.joint(0,.5,5,0,0)
        self.assertTrue(self.panel.robot_fault)
        with self.assertRaises(RuntimeError): self.panel.arm()
        self.panel.client.MoveJ.assert_called_once()

    def test_ambiguous_gripper_write_not_replayed(self):
        self.panel.arm()
        self.panel.client.ModbusRegWrite=Mock(side_effect=RuntimeError('lost response'))
        with self.assertRaises(RuntimeError): self.panel.grip(140,20,30)
        self.assertTrue(self.panel.gripper.uncertain)
        self.assertFalse(self.panel.snapshot()['robot']['armed'])
        self.panel.client.ModbusRegWrite.assert_called_once()

    def test_running_program_refuses_gripper_write(self):
        self.panel.arm()
        self.panel.client.GetProgramState=lambda:(0,2)
        with self.assertRaisesRegex(RuntimeError,'프로그램 실행 중'): self.panel.grip(140,20,30)
        self.assertEqual(self.panel.client.writes,[])

    def test_gripper_bad_bounds_never_write(self):
        self.panel.arm()
        for data in [dict(position=256,speed=20,torque=20),dict(position=127,speed=61,torque=20),
                     dict(position=127,speed=20,torque='nan')]:
            self.assertEqual(self.post('gripper',data).status_code,400)
        self.assertEqual(self.panel.client.writes,[])

    def test_no_lua_or_system_variables_needed(self):
        self.assertFalse(hasattr(self.panel.client,'GetSysVarValue'))
        self.assertFalse(hasattr(self.panel.client,'LuaUpload'))
        self.panel.arm()
        self.assertTrue(self.panel.grip(140,20,20)['reached'])

    def test_command_readback_mismatch_latches_fault(self):
        self.panel.arm()
        self.panel.client.write_registers = Mock(return_value=0)
        self.panel.gripper.writer = self.panel.client.write_registers
        with self.assertRaisesRegex(RuntimeError,'레지스터 불일치'):
            self.panel.grip(140,20,20)
        self.assertTrue(self.panel.gripper.uncertain)

    def test_coordinated_command_preflights_before_any_motion(self):
        self.panel.arm()
        self.panel.client.MoveJ = Mock()
        self.panel.client.GetProgramState = lambda:(0,2)
        with self.assertRaisesRegex(RuntimeError,'프로그램 실행 중'):
            self.panel.move_together(axis=0,delta=.5,position=140)
        self.panel.client.MoveJ.assert_not_called()
        self.assertEqual(self.panel.client.writes,[])

    def test_coordinated_api_validates_both_commands_before_dispatch(self):
        self.panel.arm()
        self.panel.client.MoveJ = Mock()
        response = self.post('together',dict(axis=0,delta=.5,position=140,speed=20,torque='nan'))
        self.assertEqual(response.status_code,400)
        self.panel.client.MoveJ.assert_not_called()
        self.assertEqual(self.panel.client.writes,[])

    def test_standalone_sdk_works_without_http_server(self):
        from omnipicker_ui.sdk import OmniPickerFR3
        with OmniPickerFR3(demo=True) as sdk:
            sdk.arm()
            result = sdk.move_together(axis=0,delta=.5,position=140,speed=20,torque=30)
            self.assertEqual(result['robot']['target'][0],.5)
            self.assertTrue(result['gripper']['reached'])
        self.assertFalse(sdk.snapshot()['robot']['connected'])

    def test_device_fault_blocks_write(self):
        original=self.panel.client.ModbusRegGetData
        def read(count,thread):
            if self.panel.client.read_address==20:return [0,29]
            return original(count,thread)
        self.panel.client.ModbusRegGetData=read
        self.panel.poll();self.panel.arm()
        with self.assertRaisesRegex(RuntimeError,'오류 코드: 29'):self.panel.grip(140,20,20)
        self.assertEqual(self.panel.client.writes,[])

    def test_stop_independent_of_move(self):
        self.panel.arm();self.panel.robot_busy=True;self.panel.stop()
        self.assertFalse(self.panel.snapshot()['robot']['armed'])
        self.assertTrue(self.panel.client.stopped.is_set())


class ModbusTests(unittest.TestCase):
    def test_unverified_hardware_write_blocked_before_rpc(self):
        driver=controller.RS485Gripper(Mock(),delay=0)
        with self.assertRaisesRegex(RuntimeError,'구동 차단'):
            driver.command(140,20,20,lambda:None)
        driver.rpc.ModbusRegWrite.assert_not_called()
    def test_decode_flat_and_nested(self):
        from omnipicker_ui.rs485 import decode_registers
        self.assertEqual(decode_registers([0,0.,0.,127.,0.,0.],5),[0,0,127,0,0])
        self.assertEqual(decode_registers([0,[335]],1),[335])

    def test_bad_frames_rejected(self):
        from omnipicker_ui.rs485 import decode_registers
        for data in [[1,335],[0,float('nan')],[0,1.5],[0,65536],[0,1,2]]:
            with self.assertRaises(RuntimeError):decode_registers(data,1)

    def test_read_delay_precedes_cached_data_fetch(self):
        from omnipicker_ui.rs485 import RS485Gripper
        order=[];rpc=Mock()
        rpc.ModbusRegRead.side_effect=lambda *args:order.append('read') or 0
        rpc.ModbusRegGetData.side_effect=lambda *args:order.append('get') or [0,335]
        with patch('omnipicker_ui.rs485.time.sleep',side_effect=lambda *args:order.append('wait')):
            self.assertEqual(RS485Gripper(rpc).read_registers(0,1),[335])
        self.assertEqual(order,['read','wait','get'])

    def test_stale_feedback_hidden(self):
        driver=controller.RS485Gripper(controller.DemoRobot(),delay=0)
        driver.refresh();self.assertTrue(driver.snapshot()['connected'])
        driver.updated=time.monotonic()-4
        self.assertIsNone(driver.snapshot()['actual_position'])


class USBTests(unittest.TestCase):
    def setUp(self):
        self.panel = setting.USBPanel()
        self.panel.device = DemoFibre()
        self.panel.refresh()
        self.original = setting.panel
        setting.panel = self.panel
        self.client = setting.app.test_client()
        self.base = f"http://127.0.0.1:{setting.PORT}"

    def tearDown(self):
        self.panel.close()
        setting.panel = self.original

    def post(self, path, data, token=True):
        return self.client.post("/api/" + path, json=data, base_url=self.base,
                                headers={"X-Control-Token": setting.TOKEN} if token else {})

    def test_usb_auth_and_locked_motion(self):
        self.assertEqual(self.post("save", {}, token=False).status_code, 403)
        self.assertEqual(self.post("move", dict(position=.5, kp=20, kd=1)).status_code, 400)
        self.assertIsNone(self.panel.target)

    def test_settings_apply_save_and_readback(self):
        values = self.panel.settings.copy()
        values.update(node_id=7, baudrate=230400, rs485_mode=True, modbus_mode=True)
        device = self.panel.device
        self.assertEqual(self.post("settings", dict(settings=values, confirm="APPLY_SETTINGS")).status_code, 200)
        self.assertTrue(self.panel.unsaved)
        self.assertEqual(self.panel.settings["node_id"], 7)
        self.assertEqual(self.post("save", dict(confirm="SAVE_FLASH")).status_code, 200)
        self.assertIsNone(self.panel.device)
        self.assertFalse(self.panel.unsaved)
        self.assertEqual(DemoFibre().settings(), values)
        self.assertEqual(device.values, values)

    def test_invalid_settings_do_not_write(self):
        device = self.panel.device
        device.write = Mock()
        for key, value in [("node_id", 0), ("node_id", 248), ("node_id", .5), ("baudrate", 12345),
                           ("stopbits", 3), ("rs485_mode", "true"), ("enable_on_boot", 1)]:
            values = self.panel.settings.copy()
            values[key] = value
            self.assertEqual(self.post("settings", dict(settings=values, confirm="APPLY_SETTINGS")).status_code, 400)
        device.write.assert_not_called()

    def test_lost_flash_reply_is_not_reported_as_saved(self):
        self.panel.unsaved = True
        self.panel.device.call = Mock(side_effect=RuntimeError("USB disconnected"))
        result = self.post("save", dict(confirm="SAVE_FLASH"))
        self.assertEqual(result.status_code, 400)
        self.assertIsNone(self.panel.device)
        self.assertTrue(self.panel.unsaved)

    def test_motion_checks_live_limits_and_error_before_enable(self):
        self.panel.enabled_until = time.monotonic() + 60
        self.panel.device.call = Mock(return_value={"result": True})
        response = self.post("move", dict(position=.5, kp=51, kd=1))
        self.assertEqual(response.status_code, 400)
        self.panel.device.call.assert_not_called()
        self.panel.device.info = lambda: dict(error_code=2, calibrated=True)
        response = self.post("move", dict(position=.5, kp=20, kd=1))
        self.assertEqual(response.status_code, 400)
        self.panel.device.call.assert_not_called()

    def test_usb_call_failure_disconnects_and_locks(self):
        self.panel.enabled_until = time.monotonic() + 60
        self.panel.device.call = Mock(side_effect=RuntimeError("USB lost"))
        result = self.post("move", dict(position=.5, kp=20, kd=1))
        self.assertEqual(result.status_code, 400)
        self.assertIsNone(self.panel.device)
        self.assertFalse(self.panel.snapshot()["armed"])


if __name__ == "__main__":
    unittest.main()
