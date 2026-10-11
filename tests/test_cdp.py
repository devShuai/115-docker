import importlib.util
import json
from pathlib import Path
import socket
import struct
import threading
import unittest

spec = importlib.util.spec_from_file_location('cdp', Path(__file__).resolve().parents[1]/'runtime/downloader/cdp.py')
cdp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdp)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.client_socket, self.peer = socket.socketpair()
        self.addCleanup(self.client_socket.close)
        self.addCleanup(self.peer.close)
        self.client = cdp.CDP.__new__(cdp.CDP)
        self.client.socket = self.client_socket
        self.client.replies = {}
        self.client.handlers = []

    def test_fragmented_message_with_interleaved_ping(self):
        payload = json.dumps({'id':1, 'result':{'value':'hello'}}).encode()
        split = 15
        wire = bytes([1,split])+payload[:split]+b'\x89\x02ok'+bytes([128,len(payload)-split])+payload[split:]
        self.peer.sendall(wire)
        self.assertEqual(self.client.receive()['result']['value'], 'hello')
        pong = self.peer.recv(64)
        self.assertEqual(pong[:2], b'\x8a\x82')
        self.assertEqual(bytes(v ^ pong[2+i%4] for i,v in enumerate(pong[6:])), b'ok')

    def test_large_binary_payload_and_event_dispatch(self):
        message = {'method':'event', 'params':{'text':'x'*70000}}
        payload = json.dumps(message).encode()
        writer = threading.Thread(target=self.peer.sendall, args=(b'\x82\x7f'+struct.pack('!Q', len(payload))+payload,))
        writer.start()
        events = []
        self.client.handlers.append(events.append)
        self.assertEqual(self.client.receive(), message)
        self.assertEqual(events, [message])
        writer.join(timeout=2)
        self.assertFalse(writer.is_alive())

    def test_closed_connection_is_not_a_hang(self):
        self.peer.close()
        with self.assertRaisesRegex(RuntimeError,'closed'):
            self.client.receive()
