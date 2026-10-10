import copy
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'manager'))
import engine
import server

OLD, NEW = 'sha256:' + '1' * 64, 'sha256:' + '2' * 64


class FakeEngine(engine.Engine):
    def __init__(self, base):
        self.profile = base / 'profile'
        self.profile.mkdir()
        (self.profile / 'preferences').write_text('before')
        manager = base / 'manager'
        manager.mkdir()
        (manager / '115-docker.config').write_text(json.dumps({'name': '115-docker', 'id': 'old', 'image': 'image:build-12', 'enabled': True}))
        super().__init__({'state_dir': str(base / 'state'), 'container': '115-docker', 'image': 'image:latest',
                          'desktop_url': 'http://localhost:11501/', 'synology_manager_dir': str(manager)})
        self.commands = []
        self.fail_health = False
        self.fail_old_health = False
        self.entrypoints = {'old': '#!/bin/sh\nsource "$script_dir/environment.sh"\nexec dbus-run-session -- "$script_dir/start.sh" "$@"\n'}
        self.containers = {'old': {'Id': 'old', 'Name': '/115-docker', 'Image': OLD,
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Mounts': [{'Destination': '/etc/115', 'Type': 'bind', 'Source': str(self.profile)}],
            'Config': {'Image': 'image:build-12', 'Env': ['PASSWORD=Private1', 'DISPLAY_WIDTH=1600'],
                       'Labels': {'com.docker.compose.service': 'browser115'}, 'User': '0', 'WorkingDir': '/opt'},
            'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                           'PortBindings': {'1150/tcp': [{'HostPort': '11501'}]}}}}
        self.images = {OLD: {'Id': OLD, 'Config': {'Env': ['DISPLAY_WIDTH=1600'], 'Labels': {'org.opencontainers.image.revision': 'old'}}},
                       'image:latest': {'Id': NEW, 'Config': {'Env': ['DISPLAY_WIDTH=1800'],
                         'Healthcheck': {'Test': ['CMD', 'true']}, 'Labels': {'org.opencontainers.image.revision': 'new'}}}}

    def inspect(self, target=None):
        target = target or self.container
        for c in self.containers.values():
            if target in (c['Id'], c['Name'].lstrip('/')):
                return copy.deepcopy(c)
        raise RuntimeError('Missing container')

    def image_info(self, target):
        return copy.deepcopy(self.images[target])

    def docker(self, *args, **kwargs):
        self.commands.append(args)
        if args[0] == 'pull':
            return ''
        if args[0] == 'cp':
            if ':/' in args[1]:
                Path(args[2]).write_text(self.entrypoints[args[1].split(':', 1)[0]])
            elif args[2].endswith('/entrypoint.sh'):
                self.entrypoints[args[2].split(':', 1)[0]] = Path(args[1]).read_text()
            return ''
        c = self.containers[self.inspect(args[-1] if args[0] in ('start', 'stop', 'update') else args[1])['Id']]
        if args[0] == 'stop': c['State']['Running'] = False
        if args[0] == 'start':
            c['State']['Running'] = True
            if c['Id'] == 'new': (self.profile / 'preferences').write_text('migrated')
        if args[0] == 'update': c['HostConfig']['RestartPolicy']['Name'] = args[1].split('=', 1)[1]
        if args[0] == 'rename': c['Name'] = '/' + args[2]
        return ''

    def create(self, config):
        self.containers['new'] = {'Id': 'new', 'Name': '/115-docker', 'Image': NEW,
            'Config': copy.deepcopy(config), 'HostConfig': copy.deepcopy(config['HostConfig']),
            'State': {'Running': False, 'Health': {'Status': 'healthy'}}, 'Mounts': []}
        return 'new'

    def wait_healthy(self, target):
        if target == 'new' and self.fail_health:
            raise RuntimeError('candidate failed health')
        if target == 'old' and self.fail_old_health:
            raise RuntimeError('old failed health')


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = FakeEngine(Path(self.temp.name))
        self.addCleanup(self.engine.guard.close)

    def test_success_retains_container_data_and_synology_registration(self):
        self.engine.update(NEW)
        r = self.engine.records()[0]
        self.assertEqual(r['status'], 'ready')
        self.assertFalse(self.engine.inspect('old')['State']['Running'])
        self.assertEqual(self.engine.inspect('old')['HostConfig']['RestartPolicy']['Name'], 'no')
        self.assertEqual((self.engine.record_dir(r) / 'profile/preferences').read_text(), 'before')
        cfg = self.engine.inspect('new')['Config']
        self.assertEqual(engine.envmap(cfg['Env']), {'PASSWORD': 'Private1', 'DISPLAY_WIDTH': '1800'})
        self.assertEqual(cfg['Labels']['org.opencontainers.image.revision'], 'new')
        self.assertEqual(cfg['Labels']['com.docker.compose.service'], 'browser115')
        self.assertEqual(json.loads(self.engine.manager_path().read_text())['id'], 'new')
        self.assertEqual((self.engine.record_dir(r) / 'container-before.json').stat().st_mode & 0o777, 0o600)

    def test_health_failure_restores_old_profile_and_current_name(self):
        self.engine.fail_health = True
        with self.assertRaisesRegex(RuntimeError, '已恢复旧容器'):
            self.engine.update(NEW)
        self.assertEqual(self.engine.inspect()['Id'], 'old')
        self.assertTrue(self.engine.inspect()['State']['Running'])
        self.assertEqual((self.engine.profile / 'preferences').read_text(), 'before')
        r = self.engine.records()[0]
        self.assertEqual(r['status'], 'failed_restored')
        self.assertEqual(json.loads(self.engine.manager_path().read_text())['id'], 'old')
        self.assertEqual(next(self.engine.record_dir(r).glob('profile-after-*/preferences')).read_text(), 'migrated')

    def test_rollback_keeps_current_profile_and_restores_original(self):
        self.engine.update(NEW)
        r = self.engine.records()[0]
        self.engine.rollback(r['id'])
        self.assertEqual(self.engine.inspect()['Id'], 'old')
        self.assertEqual((self.engine.profile / 'preferences').read_text(), 'before')
        self.assertEqual(self.engine.records()[0]['status'], 'rolled_back')
        self.assertEqual(next(self.engine.record_dir(r).glob('profile-after-*/preferences')).read_text(), 'migrated')
        self.assertIn('prepare-display.py', self.engine.entrypoints['old'])
        self.assertNotIn('prepare-display.py', (self.engine.record_dir(r) / 'entrypoint-before.sh').read_text())

    def test_latest_race_leaves_running_container_untouched(self):
        with self.assertRaisesRegex(ValueError, 'latest 已发生变化'):
            self.engine.update(OLD)
        self.assertTrue(self.engine.inspect()['State']['Running'])
        self.assertEqual(self.engine.commands, [('pull', 'image:latest')])
        self.assertEqual(self.engine.records(), [])

    def test_failed_rollback_restores_the_pre_rollback_version_and_data(self):
        self.engine.update(NEW)
        record = self.engine.records()[0]
        self.engine.fail_old_health = True
        with self.assertRaisesRegex(RuntimeError, '已恢复回退前'):
            self.engine.rollback(record['id'])
        self.assertEqual(self.engine.inspect()['Id'], 'new')
        self.assertEqual((self.engine.profile / 'preferences').read_text(), 'migrated')
        self.assertEqual(self.engine.records()[0]['status'], 'ready')

    def test_cannot_overwrite_an_unrelated_container_on_rollback(self):
        self.engine.update(NEW)
        record = self.engine.records()[0]
        self.engine.containers['new']['Name'] = '/elsewhere'
        alien = copy.deepcopy(self.engine.containers['old'])
        alien.update(Id='alien', Name='/115-docker')
        self.engine.containers['alien'] = alien
        before = len(self.engine.commands)
        with self.assertRaisesRegex(ValueError, '其他方式替换'):
            self.engine.rollback(record['id'])
        self.assertEqual(len(self.engine.commands), before)

    def test_lock_rejects_a_second_manager(self):
        with self.assertRaises(BlockingIOError):
            engine.Engine(self.engine.config)

    def test_update_requires_check_and_cannot_run_concurrently(self):
        with self.assertRaisesRegex(ValueError, '先检查更新'):
            self.engine.submit('update', NEW)
        self.engine.job['busy'] = True
        with self.assertRaisesRegex(ValueError, '正在执行'):
            self.engine.submit('check')


class FakeWebEngine:
    def __init__(self, config): self.submitted = []
    def credentials(self): return '115', 'Private1'
    def status(self): return {'job': {'busy': False}}
    def submit(self, *args): self.submitted.append(args)


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        with patch.object(server, 'Engine', FakeWebEngine):
            self.server = server.Server({'bind': '127.0.0.1', 'port': 0, 'allowed_hosts': ['127.0.0.1']})
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.finish)
        self.port = self.server.server_port
        self.origin = 'http://127.0.0.1:' + str(self.port)

    def finish(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, data=None, cookie=None, csrf=None, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if method == 'POST': headers['Origin'] = origin or self.origin
        if cookie: headers['Cookie'] = cookie
        if csrf: headers['X-CSRF-Token'] = csrf
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        r = conn.getresponse(); body = r.read(); status = r.status; cookie = r.getheader('Set-Cookie')
        conn.close()
        return status, json.loads(body) if path.startswith('/api/') else body, cookie

    def login(self):
        status, result, cookie = self.request('POST', '/api/login', {'username': '115', 'password': 'Private1'})
        self.assertEqual(status, 200)
        return cookie.split(';')[0], result['csrf']

    def test_unauthenticated_read_and_update_are_denied(self):
        self.assertEqual(self.request('GET', '/api/status')[0], 401)
        self.assertEqual(self.request('POST', '/api/update', {})[0], 401)
        self.assertFalse(self.server.engine.submitted)

    def test_csrf_and_cross_origin_rejected_but_normal_operation_works(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request('POST', '/api/update', {}, cookie=cookie)[0], 403)
        self.assertEqual(self.request('POST', '/api/update', {}, cookie=cookie, csrf=csrf, origin='http://evil.example')[0], 403)
        self.assertEqual(self.request('POST', '/api/check', {}, cookie=cookie, csrf=csrf)[0], 202)
        self.assertEqual(self.server.engine.submitted, [('check', None)])

    def test_paths_cannot_expose_host_state_and_logout_invalidates_session(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request('GET', '/../../state/record.json')[0], 404)
        status, result, _ = self.request('GET', '/api/status', cookie=cookie)
        self.assertEqual(status, 200); self.assertEqual(result['csrf'], csrf)
        self.assertEqual(self.request('POST', '/api/docker', {}, cookie=cookie, csrf=csrf)[0], 404)
        self.assertEqual(self.request('POST', '/api/logout', {}, cookie=cookie, csrf=csrf)[0], 200)
        self.assertEqual(self.request('GET', '/api/status', cookie=cookie)[0], 401)

    def test_password_failures_are_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.request('POST', '/api/login', {'username': '115', 'password': 'wrong'})[0], 401)
        self.assertEqual(self.request('POST', '/api/login', {'username': '115', 'password': 'Private1'})[0], 429)
