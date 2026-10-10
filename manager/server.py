#!/usr/bin/env python3
"""Authenticated host maintenance UI. No arbitrary Docker API is exposed."""
import argparse
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit

from engine import Engine


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config):
        self.engine = Engine(config)
        self.config = config
        self.sessions = {}
        self.attempts = {}
        self.session_lock = threading.Lock()
        super().__init__((config.get('bind', '127.0.0.1'), config.get('port', 11502)), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = '115-Manager'
    sys_version = ''

    def log_message(self, format, *args):
        # Never log request bodies, cookies, credentials or user-supplied URLs.
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8', cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def host_allowed(self):
        try:
            host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        except ValueError:
            return False
        allowed = self.server.config.get('allowed_hosts', ['127.0.0.1', 'localhost'])
        return host in allowed

    def same_origin(self):
        origin = self.headers.get('Origin')
        if not origin:
            return False
        try:
            parts = urlsplit(origin)
            return parts.scheme in ('http', 'https') and parts.netloc == self.headers.get('Host') and not parts.path
        except ValueError:
            return False

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['115_session'].value
        except (KeyError, ValueError):
            return None
        with self.server.session_lock:
            session = self.server.sessions.get(token)
            if session and session['expires'] > time.monotonic():
                return session
            self.server.sessions.pop(token, None)
        return None

    def do_GET(self):
        if not self.host_allowed():
            return self.send(403, {'error': '不允许此主机名'})
        path = urlsplit(self.path).path
        if path == '/api/status':
            session = self.session()
            if not session:
                return self.send(401, {'error': '请使用桌面账号登录'})
            result = self.server.engine.status()
            result['csrf'] = session['csrf']
            return self.send(200, result)
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            return self.send(404, {'error': '页面不存在'})
        name, mime = files[path]
        self.send(200, (Path(__file__).parent / name).read_bytes(), mime)

    def do_POST(self):
        if not self.host_allowed() or not self.same_origin():
            return self.send(403, {'error': '请求来源不匹配'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError()
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, OSError):
            return self.send(400, {'error': '请求格式无效'})
        path = urlsplit(self.path).path
        if path == '/api/login':
            return self.login(data)
        session = self.session()
        if not session:
            return self.send(401, {'error': '登录已过期'})
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
            return self.send(403, {'error': '请求令牌无效，请刷新页面'})
        if path == '/api/logout':
            with self.server.session_lock:
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v is not session}
            return self.send(200, {'ok': True}, cookie='115_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        actions = {'/api/check': ('check', None), '/api/update': ('update', data.get('image_id')),
                   '/api/rollback': ('rollback', data.get('id'))}
        if path not in actions:
            return self.send(404, {'error': '操作不存在'})
        try:
            self.server.engine.submit(*actions[path])
            self.send(202, {'ok': True})
        except ValueError as error:
            self.send(409, {'error': str(error)})

    def login(self, data):
        address = self.client_address[0]
        now = time.monotonic()
        with self.server.session_lock:
            attempts = [t for t in self.server.attempts.get(address, []) if now - t < 60]
            if len(attempts) >= 5:
                return self.send(429, {'error': '登录尝试过多，请稍后重试'})
            attempts.append(now)
            self.server.attempts[address] = attempts
        try:
            username, password = self.server.engine.credentials()
        except Exception:
            return self.send(503, {'error': '暂时无法读取桌面账号，请稍后重试'})
        supplied_user, supplied_password = data.get('username'), data.get('password')
        if not isinstance(supplied_user, str) or not isinstance(supplied_password, str):
            return self.send(400, {'error': '请输入账号和密码'})
        matches = hmac.compare_digest(supplied_user.encode(), username.encode())
        matches &= hmac.compare_digest(hashlib.sha256(supplied_password.encode()).digest(), hashlib.sha256(password.encode()).digest())
        if not matches:
            return self.send(401, {'error': '账号或密码错误'})
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.server.session_lock:
            self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
            self.server.sessions[token] = {'csrf': csrf, 'expires': now + 8 * 3600}
            self.server.attempts.pop(address, None)
        secure = '; Secure' if self.server.config.get('secure_cookie', False) else ''
        self.send(200, {'ok': True, 'csrf': csrf},
                  cookie='115_session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800' + secure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='root 管理的 JSON 配置文件')
    args = parser.parse_args()
    os.umask(0o077)
    server = Server(json.loads(Path(args.config).read_text()))
    def stop(signum, frame):
        threading.Thread(target=server.shutdown).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if server.engine.worker:
            server.engine.worker.join()


if __name__ == '__main__':
    main()
