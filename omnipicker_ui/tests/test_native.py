"""Wire fixtures from the vendor command protocol; no hardware access."""
import socket
import unittest
from unittest.mock import Mock, patch

from omnipicker_ui.native import NativeTCP, frame


class NativeTests(unittest.TestCase):
    def connection(self, *chunks):
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        conn.recv.side_effect = chunks
        return conn

    def test_documented_version_fixture_with_fragmented_reply(self):
        conn = self.connection(b'/f/bIII1III905III', b'5IIImodelIII/b/f')
        with patch('omnipicker_ui.native.socket.create_connection', return_value=conn):
            self.assertEqual(NativeTCP('127.0.0.1').version(), 'model')
        conn.sendall.assert_called_once_with(b'/f/bIII1III905III20IIIGetSoftwareVersion()III/b/f')

    def test_wrong_count_command_and_length_rejected(self):
        for data in [b'/f/bIII2III905III1III1III/b/f',
                     b'/f/bIII1III1165III1III1III/b/f',
                     b'/f/bIII1III905III2III1III/b/f']:
            with self.subTest(data=data), patch('omnipicker_ui.native.socket.create_connection', return_value=self.connection(data)):
                with self.assertRaises(RuntimeError): NativeTCP('127.0.0.1').version()

    def test_ambiguous_write_never_reconnects_or_replays(self):
        conn = self.connection()
        conn.recv.side_effect = socket.timeout('lost acknowledgment')
        with patch('omnipicker_ui.native.socket.create_connection', return_value=conn) as connect:
            with self.assertRaises(socket.timeout): NativeTCP('127.0.0.1').write_registers(10,[127,10,10,40,40,1])
        connect.assert_called_once()
        conn.sendall.assert_called_once()

    def test_invalid_gripper_values_never_open_socket(self):
        with patch('omnipicker_ui.native.socket.create_connection') as connect:
            for address,values in [(9,[0]),(15,[1,2]),(10,[]),(10,[True]),(10,[float('nan')]),(10,[256])]:
                with self.subTest(address=address,values=values), self.assertRaises(ValueError):
                    NativeTCP('127.0.0.1').write_registers(address,values)
        connect.assert_not_called()


if __name__ == '__main__': unittest.main()
