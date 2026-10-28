"""Original SDK dispatch and transport coverage; never connects to hardware."""
import ast
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from omnipicker_ui import OmniPickerFR3, OmniPickerUSB, UnsupportedFeature, GripperCommand, GripperLimits
from omnipicker_ui.api import vendor_catalog
from omnipicker_ui.fibre import FibreDevice, flatten
from omnipicker_ui.sdk import IndependentRPC


class VendorAPITests(unittest.TestCase):
    def test_catalog_matches_entire_original_rpc_class(self):
        catalog=vendor_catalog()['fr3']
        self.assertEqual(len(catalog['methods']),594)
        path=Path(catalog['source'])
        if path.exists():
            cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='RPC')
            source={n.name for n in cls.body if isinstance(n,ast.FunctionDef) and not n.name.startswith('_')}
            self.assertEqual({m['name'] for m in catalog['methods']},source)

    def test_every_vendor_method_preserves_arguments_and_return_identity(self):
        sdk=OmniPickerFR3(demo=True)
        sentinel=object();calls=[]
        def create(name):
            def method(*args,**kwargs):
                calls.append((name,args,kwargs));return sentinel
            return method
        methods=vendor_catalog()['fr3']['methods']
        sdk.client=type('Vendor',(),{m['name']:staticmethod(create(m['name'])) for m in methods})()
        callback=lambda *args: None
        for spec in methods:
            with self.subTest(method=spec['name']):
                result=getattr(sdk.fr3,spec['name'])(sentinel,callback=callback)
                self.assertIs(result,sentinel)
                self.assertEqual(calls[-1],(spec['name'],(sentinel,),dict(callback=callback)))
        self.assertIs(sdk.fr3.vendor,sdk.client)

    def test_no_fake_success_for_unimplemented_demo_methods(self):
        with OmniPickerFR3(demo=True) as sdk:
            with self.assertRaises(UnsupportedFeature): sdk.fr3.MoveC

    def test_scoped_rpc_timeout_restores_and_is_thread_local(self):
        rpc=IndependentRPC('127.0.0.1')
        with rpc.timeout(30):
            self.assertEqual(rpc.local.timeout,30)
            values=[]
            thread=threading.Thread(target=lambda:values.append(getattr(rpc.local,'timeout',3)))
            thread.start();thread.join()
            self.assertEqual(values,[3])
            with self.assertRaises(ValueError):
                with rpc.timeout(10): raise ValueError('caller failed')
            self.assertEqual(rpc.local.timeout,30)
        self.assertEqual(rpc.local.timeout,3)

    def test_usb_and_can_apis_fail_before_any_rs485_access(self):
        sdk=OmniPickerFR3(demo=True)
        sdk.gripper=Mock()
        for method in ('StartMotorCalibration','UpdateFirmwareViaFlash','SendFrameSyncByCanfd','SetCanNodeId'):
            with self.subTest(method=method), self.assertRaises(UnsupportedFeature):
                getattr(sdk.omnipicker,method)()
        with self.assertRaises(UnsupportedFeature): sdk.omnipicker.read('motor.config.pos_gain')
        sdk.gripper.assert_not_called()
        self.assertEqual(sdk.gripper.mock_calls,[])

    def test_fibre_inventory_has_no_hardcoded_endpoint_ids(self):
        features=vendor_catalog()['omnipicker_fibre']['features']
        self.assertEqual(sum(f['kind']=='function' for f in features),13)
        self.assertEqual(sum(f['kind']=='property' for f in features),62)
        self.assertFalse(any('id' in f for f in features))


class GripperAPITests(unittest.TestCase):
    def setUp(self):
        self.sdk=OmniPickerFR3(demo=True).connect()
        self.sdk.arm()

    def tearDown(self): self.sdk.close()

    def test_stage_without_motion_then_explicit_trigger_and_wait(self):
        result=self.sdk.omnipicker.stage(140,20,30,acceleration=50,deceleration=60)
        self.assertTrue(result['staged']);self.assertFalse(result['trigger_sent'])
        self.assertEqual(self.sdk.client.grip_position,127)
        self.assertIsNone(self.sdk.gripper.target)
        self.assertEqual(self.sdk.omnipicker.read_registers(10,6),[140,20,30,50,60,0])
        result=self.sdk.omnipicker.trigger(wait=False)
        self.assertFalse(result['completed']);self.assertTrue(result['readback_verified'])
        self.assertTrue(self.sdk.omnipicker.wait()['reached'])
        self.assertEqual(self.sdk.gripper.acceleration,50)
        self.assertEqual(self.sdk.gripper.deceleration,60)

    def test_undefined_registers_never_reach_bus(self):
        self.sdk.client.ModbusRegRead=Mock()
        for address,count in [(1,1),(15,2),(19,2),(-1,1),(10,0)]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.sdk.omnipicker.read_registers(address,count)
        self.sdk.client.ModbusRegRead.assert_not_called()

    def test_individual_parameter_write_does_not_trigger_motion(self):
        result=self.sdk.omnipicker.write('target_speed',25)
        self.assertFalse(result['trigger_sent'])
        self.assertEqual(self.sdk.omnipicker.read('target_speed'),25)
        self.assertEqual(self.sdk.client.grip_position,127)
        self.assertEqual(self.sdk.client.writes[-1][1:4],(11,1,[25]))
        with self.assertRaises(ValueError): self.sdk.omnipicker.write('actual_position',140)

    def test_monitor_does_not_take_idle_waiting_rs485_lock_during_motion(self):
        self.sdk.robot_busy=True
        self.sdk.gripper.refresh=Mock()
        self.sdk.poll()
        self.sdk.gripper.refresh.assert_not_called()
        self.sdk.robot_busy=False

    def test_vendor_program_can_own_modbus_without_monitor_interference(self):
        self.sdk.client.GetProgramState=lambda:(0,2)
        self.sdk.gripper.refresh=Mock()
        self.sdk.poll()
        self.sdk.gripper.refresh.assert_not_called()
        self.assertFalse(self.sdk.gripper.snapshot()['connected'])
        with self.assertRaises(RuntimeError): self.sdk.omnipicker.read('firmware_version')

    def test_invalid_acceleration_or_completion_options_never_write(self):
        for options in [dict(acceleration=0),dict(deceleration=256),dict(timeout=float('nan')),
                        dict(wait=1),dict(accept_contact='yes')]:
            self.sdk.arm()
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.sdk.omnipicker.move(140,20,20,**options)
        self.assertEqual(self.sdk.client.writes,[])

    def test_contact_is_an_explicit_completion_policy_not_arrival(self):
        original=self.sdk.client.ModbusRegGetData
        def read(count,thread):
            if self.sdk.client.read_address==21:return [0,2]
            if self.sdk.client.read_address==22:return [0,130]
            return original(count,thread)
        self.sdk.client.ModbusRegGetData=read
        result=self.sdk.omnipicker.move(140,20,20,accept_contact=True)
        self.assertTrue(result['completed']);self.assertTrue(result['contact'])
        self.assertFalse(result['reached'])
        self.assertTrue(self.sdk.gripper.snapshot()['contact_completed'])
        self.assertEqual(self.sdk.gripper.snapshot()['reason'],'접촉 완료')

    def test_contact_defaults_to_fault_and_never_retries(self):
        original=self.sdk.client.ModbusRegGetData
        def read(count,thread):
            if self.sdk.client.read_address==21:return [0,2]
            return original(count,thread)
        self.sdk.client.ModbusRegGetData=read
        with self.assertRaisesRegex(RuntimeError,'정체'):
            self.sdk.omnipicker.move(140,20,20)
        self.assertTrue(self.sdk.gripper.uncertain)
        self.assertFalse(self.sdk.gripper.snapshot()['contact_completed'])
        self.assertEqual(len(self.sdk.client.writes),1)

    def test_gestures_are_normalized_documented_targets(self):
        self.assertEqual([self.sdk.omnipicker.gesture_target(x) for x in ('closed','half_open','open')],[0,127,255])
        self.assertEqual(self.sdk.omnipicker.GetDoA(),1)

    def test_full_protocol_range_requires_explicit_limits(self):
        with self.assertRaises(ValueError): self.sdk.omnipicker.move(140,255,255)
        with OmniPickerFR3(demo=True,gripper_limits=GripperLimits.full_range()) as sdk:
            sdk.arm()
            result=sdk.omnipicker.move(140,255,255,acceleration=255,deceleration=1)
            self.assertTrue(result['reached'])
            self.assertEqual(sdk.client.writes[-1][3],[140,255,255,255,1,1])

    def test_original_movel_and_gripper_can_overlap(self):
        written=threading.Event()
        calls=[];send=self.sdk.gripper.writer
        def write(*args):
            reply=send(*args);written.set();return reply
        def move(*args,**kwargs):
            calls.append((args,kwargs))
            if not written.wait(2):raise RuntimeError('gripper serialized behind MoveL')
            return 0
        self.sdk.client.MoveL=move
        self.sdk.gripper.writer=write
        pose=[300,0,400,180,0,90]
        result=self.sdk.parallel('MoveL',args=(pose,0,0),kwargs=dict(vel=5),
                                  gripper=GripperCommand(140,20,20,50,60))
        self.assertEqual(calls,[((pose,0,0),dict(vel=5))])
        self.assertEqual(result['robot'],0);self.assertTrue(result['gripper']['reached'])

    def test_parallel_rejects_missing_vendor_arguments_before_motion(self):
        self.sdk.client.MoveL=Mock()
        with self.assertRaises(TypeError):
            self.sdk.parallel('MoveL',args=(),gripper=GripperCommand(140,20,20))
        self.sdk.client.MoveL.assert_not_called()
        self.assertEqual(self.sdk.client.writes,[])


class USBAPITests(unittest.TestCase):
    def test_generic_access_uses_live_fibre_endpoints(self):
        device=FibreDevice.__new__(FibreDevice)
        device.endpoints=flatten([dict(name='motor.config.pos_gain',id=501,type='float',access='rw')])
        device.exchange=Mock(return_value=b'')
        usb=OmniPickerUSB(device)
        usb.write('motor.config.pos_gain',20)
        self.assertEqual(device.exchange.call_args.args[0],501)
        device.exchange.reset_mock()
        with self.assertRaises(ValueError): usb.write('motor.config.pos_gain',float('nan'))
        device.exchange.assert_not_called()

    def test_no_implicit_usb_discovery(self):
        with patch('omnipicker_ui.fibre.usb_modules') as discover:
            usb=OmniPickerUSB()
            self.assertEqual(len(usb.features()),75)
            with self.assertRaises(RuntimeError):usb.read('can_node_id')
            discover.assert_not_called()


if __name__=='__main__':unittest.main()
