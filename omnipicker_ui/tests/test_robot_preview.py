"""Preview/application, local TCP transforms and settings boundary tests. No hardware."""
import copy
import time
import unittest
from unittest.mock import Mock,patch
from werkzeug.test import Client
from werkzeug.wrappers import Response
from omnipicker_ui import controller,setting
from omnipicker_ui.sdk import OmniPickerFR3,DemoRobot
from omnipicker_ui.rs485 import RS485Gripper
from omnipicker_ui.kinematics import tool_target,transform,forward,inverse
from omnipicker_ui.fibre import DemoFibre,FibreDevice,flatten


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.sdk=OmniPickerFR3(demo=True)
        self.sdk.client=DemoRobot()
        self.sdk.gripper=RS485Gripper(self.sdk.client,delay=0,write_verified=True,writer=self.sdk.client.write_registers)
        self.sdk.poll()
        self.start=self.sdk.joints[:]
        self.sdk.client.MoveJ=Mock(return_value=0)
        self.sdk.client.MoveL=Mock(return_value=0)

    def preview(self):
        target=self.start[:];target[0]+=1
        return self.sdk.preview_robot('joint',joints=target)

    def test_preview_is_readonly_and_apply_requires_arm_and_is_once(self):
        plan=self.preview()
        self.sdk.client.MoveJ.assert_not_called()
        self.assertEqual(self.sdk.client.writes,[])
        with self.assertRaises(RuntimeError):self.sdk.apply_robot_plan(plan['id'])
        self.sdk.arm();self.sdk.apply_robot_plan(plan['id'],37)
        self.sdk.client.MoveJ.assert_called_once_with(plan['joints'],tool=0,user=0,vel=37,blendT=-1.)
        with self.assertRaises(RuntimeError):self.sdk.apply_robot_plan(plan['id'])
        self.sdk.client.MoveJ.assert_called_once()

    def test_sdk_caller_cannot_mutate_stored_plan(self):
        plan=self.preview();expected=copy.deepcopy(plan['joints']);plan['joints'][0]=150
        self.sdk.arm();result=self.sdk.apply_robot_plan(plan['id'])
        self.assertEqual(result['target'],expected)

    def test_drift_lock_expiry_and_invalid_limits_never_dispatch(self):
        for kind in ['drift','lock','expiry','limit']:
            with self.subTest(kind=kind):
                self.sdk.client.joints=self.start[:];self.sdk.poll();self.sdk.arm();plan=self.preview()
                if kind=='drift':self.sdk.client.joints[0]+=1
                if kind=='lock':self.sdk.disarm();self.sdk.arm()
                if kind=='expiry':self.sdk.plans[plan['id']]['expires']=time.monotonic()-1
                if kind=='limit':
                    self.sdk.client.GetJointSoftLimitDeg=lambda:(0,[-.1,.1,-270,90,-165,165,-270,90,-180,180,-360,360])
                with self.assertRaises((ValueError,RuntimeError)):self.sdk.apply_robot_plan(plan['id'])
                self.sdk.client.MoveJ.assert_not_called()

    def test_invalid_target_nan_and_bad_velocity_never_dispatch(self):
        for target in [[float('nan')]*6,[1000]*6,[0,1]]:
            with self.assertRaises((ValueError,RuntimeError)):self.sdk.preview_robot('joint',joints=target)
        self.sdk.arm();plan=self.preview()
        for speed in [0,101,float('nan')]:
            with self.assertRaises(ValueError):self.sdk.apply_robot_plan(plan['id'],speed)
        self.sdk.client.MoveJ.assert_not_called()

    def test_failed_motion_is_not_replayed_and_latches_fault(self):
        self.sdk.arm();plan=self.preview();self.sdk.client.MoveJ.side_effect=RuntimeError('lost')
        with self.assertRaises(RuntimeError):self.sdk.apply_robot_plan(plan['id'])
        self.assertTrue(self.sdk.robot_fault)
        self.assertFalse(self.sdk.snapshot()['robot']['armed'])
        with self.assertRaises(RuntimeError):self.sdk.apply_robot_plan(plan['id'])
        self.sdk.client.MoveJ.assert_called_once()

    def test_large_and_sub_degree_jog_increments_use_only_soft_limits(self):
        self.sdk.arm()
        for amount in [15,-25,.0001]:
            result=self.sdk.joint(0,amount,velocity=5)
            self.assertAlmostEqual(result['target'][0],amount)
        self.assertEqual(self.sdk.client.MoveJ.call_count,3)
        self.sdk.client.MoveJ.reset_mock()
        for amount in [0,1000,float('inf'),float('nan')]:
            self.sdk.arm()
            with self.assertRaises((ValueError,RuntimeError)):self.sdk.joint(0,amount)
        self.sdk.client.MoveJ.assert_not_called()

    def test_large_increment_together_preflights_target_before_either_motion(self):
        self.sdk.arm()
        with self.assertRaises(ValueError):self.sdk.move_together(axis=0,delta=1000,position=140)
        self.sdk.client.MoveJ.assert_not_called();self.assertEqual(self.sdk.client.writes,[])
        self.sdk.move_together(axis=0,delta=15,position=140,speed=20,torque=20)
        self.sdk.client.MoveJ.assert_called_once()
        self.assertEqual(self.sdk.client.MoveJ.call_args.args[0][0],15)

    def test_tool_preview_has_no_arbitrary_angle_or_translation_cap(self):
        self.sdk.client.GetInverseKinRef=Mock(return_value=(0,self.start[:]))
        for delta in [[150,0,0,0,0,0],[0,0,0,45,0,0],[0,0,0,0,-80,0]]:
            self.sdk.preview_robot('tool',delta=delta)
        self.assertEqual(self.sdk.client.GetInverseKinRef.call_count,3)
        self.sdk.client.MoveL.assert_not_called()
        self.assertEqual(self.sdk.motion_limits()['rotation_step'],[0,None])

    def test_tool_frame_rotation_not_base_translation(self):
        target=tool_target([10,20,30,0,0,90],[5,0,0,0,0,0])
        for got,want in zip(target[:3],[10,25,30]):self.assertAlmostEqual(got,want)
        target=tool_target([0,0,0,90,0,0],[0,0,0,0,10,0])
        # Local pitch at roll 90 becomes base Z rotation, via matrix product.
        expected=transform([0,0,0,90,0,10]);actual=transform(target)
        for a,b in zip(sum(actual,[]),sum(expected,[])):self.assertAlmostEqual(a,b)

    def test_demo_ik_matches_tcp_target(self):
        reference=forward(self.start)
        for delta in [[1,0,0,0,0,0],[0,1,0,0,0,0],[0,0,1,0,0,0],[0,0,0,.5,0,0],[0,0,0,0,.5,0],[0,0,0,0,0,.5]]:
            target=tool_target(reference,delta);q=inverse(target,self.start)
            a,b=transform(forward(q)),transform(target)
            self.assertLess(max(abs(a[i][3]-b[i][3]) for i in range(3)),.06)
            self.assertLess(max(abs(a[i][j]-b[i][j]) for i in range(3) for j in range(3)),.0005)

    def test_tool_uses_movel_and_checks_active_tcp_and_offset(self):
        self.sdk.arm();plan=self.sdk.preview_robot('tool',delta=[1,0,0,0,0,0]);self.sdk.apply_robot_plan(plan['id'],12)
        self.sdk.client.MoveJ.assert_not_called();self.sdk.client.MoveL.assert_called_once()
        self.assertEqual(self.sdk.client.MoveL.call_args.kwargs['joint_pos'],plan['joints'])
        with self.assertRaisesRegex(RuntimeError,'활성 툴'):self.sdk.preview_robot('tool',delta=[1,0,0,0,0,0],tool=1)
        plan=self.sdk.preview_robot('tool',delta=[1,0,0,0,0,0]);self.sdk.client.GetTCPOffset=lambda:(0,[0,0,20,0,0,0])
        with self.assertRaisesRegex(RuntimeError,'툴/좌표계 변경'):self.sdk.apply_robot_plan(plan['id'])
        self.sdk.client.MoveL.assert_called_once()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.usb=setting.USBPanel();self.usb.device=DemoFibre();self.usb.refresh()
        self.original=setting.panel;setting.panel=self.usb
        self.client=setting.app.test_client();self.base=f'http://127.0.0.1:{setting.PORT}'
    def tearDown(self):setting.panel=self.original
    def post(self,path,data):return self.client.post('/api/'+path,json=data,base_url=self.base,headers={'X-Control-Token':setting.TOKEN})

    def test_all_usb_features_and_generic_typed_access(self):
        features=self.client.get('/api/endpoints',base_url=self.base).json['features'];self.assertEqual(len(features),75)
        data=dict(path='motor.config.pos_gain',value=3.5,confirm='EXECUTE_USB_ENDPOINT')
        self.assertEqual(self.post('endpoint/execute',data).status_code,400)
        self.usb.enabled_until=time.monotonic()+60
        self.assertEqual(self.post('endpoint/execute',data).status_code,200)
        self.assertEqual(self.post('endpoint/read',dict(path=data['path'])).json['value'],3.5)

    def test_rs485_custom_range_changes_move_validation_without_sending_a_command(self):
        sdk=OmniPickerFR3(demo=True)
        sdk.client=DemoRobot();sdk.gripper=RS485Gripper(sdk.client,delay=0,write_verified=True,writer=sdk.client.write_registers);sdk.poll()
        original=controller.panel;controller.panel=sdk
        try:
            client=controller.app.test_client();base=f'http://127.0.0.1:{controller.PORT}'
            headers={'X-Control-Token':controller.TOKEN}
            registers=client.get('/api/gripper/registers',base_url=base).json['registers']
            self.assertEqual([r['address'] for r in registers],[0,10,11,12,13,14,15,20,21,22,23,24])
            sdk.arm()
            response=client.post('/api/gripper/limits',json=dict(speed_min=0,speed_max=255,torque_min=0,torque_max=255),base_url=base,headers=headers)
            self.assertEqual(response.status_code,200);self.assertEqual(sdk.client.writes,[])
            self.assertFalse(sdk.snapshot()['robot']['armed'])
            sdk.arm()
            response=client.post('/api/gripper',json=dict(position=127,speed=70,torque=80),base_url=base,headers=headers)
            self.assertEqual(response.status_code,200);self.assertEqual(sdk.client.writes[0][3],[127,70,80,40,40,1])
        finally:controller.panel=original

    def test_bad_function_arg_does_not_write_partial_arguments(self):
        fibre=object.__new__(FibreDevice)
        fibre.endpoints=flatten([dict(name='f',type='function',id=1,inputs=[dict(name='a',type='float',access='rw',id=2),dict(name='b',type='uint8',access='rw',id=3)])])
        fibre.exchange=Mock()
        with self.assertRaises(ValueError):fibre.call('f',a=1,b=256)
        fibre.exchange.assert_not_called()

    def test_usb_mount_prefix_and_no_usb_claim_on_open(self):
        client=Client(controller.app,Response)
        before=setting.panel.device
        response=client.get('/gripper/usb/',base_url=f'http://127.0.0.1:{controller.PORT}')
        self.assertEqual(response.status_code,200)
        self.assertIn('window.API_BASE="/gripper/usb/api/"',response.text)
        self.assertEqual(response.headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertIs(setting.panel.device,before)
        self.assertEqual(client.get('/gripper/usb/api/status',base_url=f'http://127.0.0.1:{controller.PORT}').status_code,200)

    def test_usb_pause_blocks_grip_and_arm_and_rs485_reads(self):
        sdk=OmniPickerFR3(demo=True)
        sdk.client=DemoRobot();sdk.gripper=RS485Gripper(sdk.client,delay=0,write_verified=True,writer=sdk.client.write_registers);sdk.poll();sdk.arm()
        sdk.external_bus_pause=True;sdk.gripper.suspended=True
        for action in [lambda:sdk.grip(127,20,20),lambda:sdk.probe_gripper(),lambda:sdk.omnipicker.read('actual_position'),lambda:sdk.arm()]:
            with self.assertRaises(RuntimeError):action()
        self.assertEqual(sdk.client.writes,[])


if __name__=='__main__':unittest.main()
