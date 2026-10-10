import importlib.util
from pathlib import Path
import socket
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_display', Path(__file__).resolve().parents[1] / 'runtime/prepare-display.py')
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.X11-unix').mkdir()
        self.proc = self.root / 'proc'; self.proc.mkdir()
        self.lock = self.root / '.X115-lock'; self.lock.write_text('22')
        self.address = self.root / '.X11-unix/X115'

    def test_stale_lock_and_dead_socket_are_removed(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.bind(str(self.address))
        display.prepare(':115', self.root, self.proc)
        self.assertFalse(self.lock.exists()); self.assertFalse(self.address.exists())

    def test_active_socket_is_not_removed(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.bind(str(self.address)); peer.listen(1)
            with self.assertRaisesRegex(RuntimeError, 'accepts connections'):
                display.prepare(':115', self.root, self.proc)
            self.assertTrue(self.lock.exists()); self.assertTrue(self.address.exists())

    def test_active_x_server_is_not_removed_even_before_listening(self):
        process = self.proc / '22'; process.mkdir()
        (process / 'cmdline').write_bytes(b'/usr/bin/Xkasmvnc\0:115\0')
        with self.assertRaisesRegex(RuntimeError, 'already running'):
            display.prepare(':115', self.root, self.proc)
        self.assertTrue(self.lock.exists())

    def test_reused_non_x_pid_is_not_treated_as_live_display(self):
        process = self.proc / '22'; process.mkdir()
        (process / 'cmdline').write_bytes(b'/usr/bin/python3\0other.py\0')
        display.prepare(':115', self.root, self.proc)
        self.assertFalse(self.lock.exists())
