"""Small, dependency-free CDP client used only by the isolated CI desktop tests."""
import base64
import hashlib
import json
import os
import socket
import struct
import time
from urllib.parse import urlsplit


class CDP:
    def __init__(self, url):
        parsed = urlsplit(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        self.socket.sendall((f"GET {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
                             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            part = self.socket.recv(1)
            if not part:
                raise RuntimeError("CDP closed during WebSocket handshake")
            header.extend(part)
            if len(header) > 16384:
                raise RuntimeError("Invalid WebSocket handshake")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b" 101 " not in header or expected not in header:
            raise RuntimeError("CDP WebSocket handshake failed")
        self.sequence = 0
        self.replies = {}
        self.handlers = []

    def close(self):
        self.socket.close()

    def send_frame(self, payload, opcode=1):
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        self.socket.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))

    def send(self, method, params=None):
        self.sequence += 1
        self.send_frame(json.dumps({"id": self.sequence, "method": method, "params": params or {}}).encode())
        return self.sequence

    def read(self, count):
        data = bytearray()
        while len(data) < count:
            block = self.socket.recv(count - len(data))
            if not block:
                raise RuntimeError("CDP connection closed")
            data.extend(block)
        return bytes(data)

    def receive(self, timeout=10):
        self.socket.settimeout(timeout)
        chunks = bytearray()
        while True:
            first, second = self.read(2)
            opcode = first & 15
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read(8))[0]
            mask = self.read(4) if second & 128 else None
            payload = self.read(length)
            if mask:
                payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
            if opcode == 8:
                raise RuntimeError("CDP connection closed")
            if opcode == 9:
                self.send_frame(payload, 10)
                continue
            if opcode == 10:
                continue
            chunks.extend(payload)
            if first & 128:
                message = json.loads(chunks)
                if "id" in message:
                    self.replies[message["id"]] = message
                else:
                    for handler in self.handlers:
                        handler(message)
                return message

    def call(self, method, params=None, timeout=15):
        sequence = self.send(method, params)
        deadline = time.monotonic() + timeout
        while sequence not in self.replies:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP command timed out: {method}")
            self.receive(remaining)
        reply = self.replies.pop(sequence)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.receive(max(0.001, deadline - time.monotonic()))
            except socket.timeout:
                return
