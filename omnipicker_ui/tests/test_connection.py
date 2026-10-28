"""Bus recovery only restores serial connectivity; never sends actuator targets."""
import unittest
from unittest.mock import Mock,call
from omnipicker_ui.native import NativeTCP
from omnipicker_ui.sdk import OmniPickerFR3,DemoRobot
from omnipicker_ui.rs485 import RS485Gripper

VERSION='FR3-V1-001(V6.0),v3.9.8,V3.9.35-QX'
class ConnectionTests(unittest.TestCase):
    def sdk(self):
        sdk=OmniPickerFR3(demo=True);sdk.client=DemoRobot()
        sdk.gripper=RS485Gripper(sdk.client,delay=0,write_verified=True,writer=sdk.client.write_registers);sdk.poll()
        sdk.native=Mock();sdk.native.version.return_value=VERSION;sdk.native.reopen_rtu.return_value=[0,7,8,0,1,1]
        return sdk

    def test_reopen_uses_only_identical_validated_serial_settings(self):
        n=NativeTCP('127.0.0.1');n._request=Mock(side_effect=[VERSION,'0,7,8,0,1,1','1','0,7,8,0,1,1'])
        self.assertEqual(n.reopen_rtu(),[0,7,8,0,1,1])
        self.assertEqual(n._request.call_args_list,[call(905,'GetSoftwareVersion()'),call(1151,'ModbusSlaveGetParam_RTU()'),call(1150,'ModbusSlaveSetParam_RTU(0,7,8,0,1,1)'),call(1151,'ModbusSlaveGetParam_RTU()')])

    def test_void_setter_reply_is_verified_by_readback_without_replay(self):
        n=NativeTCP('127.0.0.1');n._request=Mock(side_effect=[VERSION,'0,7,8,0,1,1','284,-236,601,-148,51,-133','0,7,8,0,1,1'])
        self.assertEqual(n.reopen_rtu(),[0,7,8,0,1,1]);self.assertFalse(n.last_reopen_acknowledged)
        self.assertEqual(sum(c.args[0]==1150 for c in n._request.call_args_list),1)

    def test_unknown_version_or_config_never_writes(self):
        for replies in [['model,v4,other'],[VERSION,'0,6,8,0,1,1'],[VERSION,'bad']]:
            n=NativeTCP('127.0.0.1');n._request=Mock(side_effect=replies)
            with self.assertRaises(RuntimeError):n.reopen_rtu()
            self.assertFalse(any(c.args[0]==1150 for c in n._request.call_args_list))

    def test_lost_reopen_ack_is_never_repeated(self):
        n=NativeTCP('127.0.0.1');n._request=Mock(side_effect=[VERSION,'0,7,8,0,1,1',TimeoutError('lost')])
        with self.assertRaises(TimeoutError):n.reopen_rtu()
        self.assertEqual(sum(c.args[0]==1150 for c in n._request.call_args_list),1)

    def test_unknown_firmware_is_pending_and_reverification_restores_writer(self):
        s=self.sdk();s.demo=False;s.gripper.firmware=None
        s.verify_gripper_transport(refresh_version=True)
        self.assertIn('조회 대기',s.transport_error);self.assertFalse(s.gripper.write_verified)
        s.gripper.firmware=335;s.verify_gripper_transport()
        self.assertEqual(s.transport_error,'');self.assertTrue(s.gripper.write_verified)
        self.assertEqual(s.gripper.writer,s.native.write_registers)
        s.gripper.firmware=336;s.verify_gripper_transport()
        self.assertFalse(s.gripper.write_verified);self.assertIn('336',s.transport_error)

    def test_recover_disarms_and_never_moves_any_device(self):
        s=self.sdk();s.arm();s.demo=False;s.client.MoveJ=Mock();s.client.MoveL=Mock()
        state=s.recover_rs485()['state'];self.assertTrue(state['feedback_valid']);self.assertTrue(state['writable'])
        s.client.MoveJ.assert_not_called();s.client.MoveL.assert_not_called();self.assertEqual(s.client.writes,[])
        s.native.write_registers.assert_not_called();s.native.reopen_rtu.assert_called_once()
        self.assertFalse(s.snapshot()['robot']['armed'])

    def test_recovery_refuses_motion_or_running_program(self):
        s=self.sdk();s.robot_busy=True
        with self.assertRaises(RuntimeError):s.recover_rs485()
        s.robot_busy=False;s.client.GetProgramState=lambda:(0,2)
        with self.assertRaises(RuntimeError):s.recover_rs485()
        s.native.reopen_rtu.assert_not_called();self.assertEqual(s.client.writes,[])

    def test_preview_reports_reference_and_target_pose_without_motion(self):
        s=self.sdk();s.client.MoveL=Mock()
        reply=s.preview_robot('tool',delta=[2,0,0,0,0,0])
        self.assertEqual(len(reply['reference_pose']),6)
        self.assertGreater(sum((a-b)**2 for a,b in zip(reply['pose'][:3],reply['reference_pose'][:3])),3.99)
        s.client.MoveL.assert_not_called();self.assertEqual(s.client.writes,[])
