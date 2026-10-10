import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_rfb', ROOT / 'scripts/ci-rfb.py')
rfb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rfb)


class FragmentedConnection:
    def __init__(self, incoming):
        self.incoming = incoming
        self.sent = bytearray()

    def recv(self, size):
        data = self.incoming[:min(size, 2)]
        self.incoming = self.incoming[len(data):]
        return data

    def sendall(self, data):
        self.sent.extend(data)


class RFBProtocolTests(unittest.TestCase):
    def test_partial_reads_and_unexpected_disconnect(self):
        connection = FragmentedConnection(b'fragmented')
        self.assertEqual(rfb.read_exact(connection, 10), b'fragmented')
        with self.assertRaisesRegex(RuntimeError, 'closed unexpectedly'):
            rfb.read_exact(connection, 1)

    def test_extended_desktop_screen_is_parsed_with_raw_rectangle(self):
        screen_id, flags = 127, 3
        connection = FragmentedConnection(
            struct.pack('!BBH', 0, 0, 2)
            + struct.pack('!HHHHi', 0, 0, 1600, 900, -308)
            + struct.pack('!BBBBIHHHHI', 1, 0, 0, 0, screen_id, 0, 0, 1600, 900, flags)
            + struct.pack('!HHHHi', 0, 0, 1, 1, 0) + bytes(4))
        self.assertEqual(rfb.get_screen(connection, 32), (screen_id, flags))
        self.assertEqual(connection.incoming, b'')

    def test_authentication_uses_expected_vnc_des_response(self):
        openssl = shutil.which('openssl')
        if not openssl:
            self.skipTest('OpenSSL is unavailable')
        version = subprocess.check_output([openssl, 'version'], text=True)
        if not version.startswith('OpenSSL 3.'):
            self.skipTest('OpenSSL 3 legacy provider is required')
        server_init = struct.pack('!HH', 1600, 900) + bytes([32]) + bytes(15) + struct.pack('!I', 3)
        connection = FragmentedConnection(
            b'RFB 003.008\n\x01\x02' + bytes(range(16)) + bytes(4) + server_init + b'115')
        self.assertEqual(rfb.authenticate(connection, 'Check123'), 32)
        self.assertEqual(bytes(connection.sent), b'RFB 003.008\n\x02'
                         + bytes.fromhex('53da7852372f0c5918ec93e5315af613') + b'\x01')
        self.assertEqual(connection.incoming, b'')


if __name__ == '__main__':
    unittest.main()
