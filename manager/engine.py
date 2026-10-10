"""Fixed-target Docker maintenance; all persistent records are private to the host."""
import copy
import datetime
import fcntl
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import uuid

spec = importlib.util.spec_from_file_location('image_checker', Path(__file__).resolve().parents[1] / 'scripts/check-update.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write_json(path, value):
    tmp = Path(str(path) + '.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.chmod(0o600)
    os.replace(str(tmp), str(path))


def envmap(values):
    return dict(value.split('=', 1) for value in values or [])


def replacement_config(old, old_image, new_image, image):
    keys = ('Hostname', 'Domainname', 'User', 'ExposedPorts', 'Tty', 'OpenStdin',
            'StdinOnce', 'Cmd', 'Healthcheck', 'Volumes', 'WorkingDir', 'Entrypoint',
            'Labels', 'StopSignal', 'StopTimeout', 'Shell')
    config = {k: copy.deepcopy(v) for k, v in new_image['Config'].items() if k in keys}
    labels = {k: v for k, v in (old['Config'].get('Labels') or {}).items()
              if not k.startswith('org.opencontainers.image.')}
    labels.update(new_image['Config'].get('Labels') or {})
    config['Labels'] = labels
    defaults = envmap(old_image['Config'].get('Env'))
    env = envmap(new_image['Config'].get('Env'))
    env.update({k: v for k, v in envmap(old['Config'].get('Env')).items() if defaults.get(k) != v})
    for key in ('User', 'WorkingDir', 'Tty', 'OpenStdin', 'StdinOnce'):
        config[key] = old['Config'].get(key)
    config.update(Image=image, Env=[k + '=' + v for k, v in env.items()],
                  HostConfig=copy.deepcopy(old['HostConfig']))
    config['HostConfig']['RestartPolicy'] = old['HostConfig']['RestartPolicy']
    return config


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


class Engine:
    def __init__(self, config):
        self.config = config
        self.docker_path = config.get('docker', 'docker')
        self.container = config.get('container', '115-docker')
        self.image = config.get('image', checker.DEFAULT_IMAGE)
        self.root = Path(config['state_dir'])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.guard = (self.root / 'manager.lock').open('a')
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.guard.close()
            raise
        self.lock = threading.Lock()
        self.job = {'busy': False, 'phase': '就绪', 'error': ''}
        self.checked = None
        self.worker = None
        # A reboot must never pretend an unfinished update succeeded.
        for record in self.records():
            if record['status'] == 'pending':
                record['status'] = 'interrupted'
                self.save(record)

    def docker(self, *args, timeout=120):
        try:
            return subprocess.check_output([self.docker_path, *args], stderr=subprocess.PIPE,
                                           timeout=timeout).decode().strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('Docker 操作失败，请检查服务、仓库登录及网络') from error

    def inspect(self, target=None):
        return json.loads(self.docker('container', 'inspect', target or self.container))[0]

    def image_info(self, target):
        return json.loads(self.docker('image', 'inspect', target))[0]

    def create(self, config):
        conn = UnixConnection('localhost', timeout=120)
        try:
            conn.request('POST', '/containers/create?name=' + self.container,
                         body=json.dumps(config).encode(), headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            body = response.read()
            if response.status != 201:
                raise RuntimeError('创建容器失败（Docker API %s）' % response.status)
            return json.loads(body)['Id']
        finally:
            conn.close()

    def credentials(self):
        env = envmap(self.inspect()['Config'].get('Env'))
        if not env.get('PASSWORD'):
            raise RuntimeError('桌面容器未设置登录密码')
        return env.get('REMOTE_USER', '115'), env['PASSWORD']

    def record_dir(self, record):
        return self.root / record['id']

    def save(self, record):
        write_json(self.record_dir(record) / 'record.json', record)

    def records(self):
        return sorted([json.loads(p.read_text()) for p in self.root.glob('*/record.json')],
                      key=lambda r: r['created'], reverse=True)

    def history(self, current_id=None):
        result = []
        for r in self.records():
            result.append({k: r.get(k, '') for k in ('id', 'created', 'status', 'old_image', 'old_revision', 'new_revision', 'rollback_name')})
            result[-1]['can_rollback'] = (r['status'] in ('ready', 'interrupted')
                                          and (current_id in (r['old_id'], r.get('new_id'))
                                               or r['status'] == 'interrupted'))
        return result

    def status(self):
        deployed = {}
        current_id = None
        try:
            c = self.inspect()
            current_id = c['Id']
            i = self.image_info(c['Image'])
            deployed = {'tag': c['Config']['Image'], 'image_id': c['Image'],
                        'revision': (i['Config'].get('Labels') or {}).get('org.opencontainers.image.revision', ''),
                        'health': c['State'].get('Health', {}).get('Status', 'unknown'),
                        'running': c['State']['Running']}
        except RuntimeError:
            deployed = {'running': False, 'health': '容器暂时不可用'}
        with self.lock:
            return {'job': dict(self.job), 'checked': copy.deepcopy(self.checked),
                    'deployed': deployed, 'history': self.history(current_id),
                    'desktop_url': self.config['desktop_url']}

    def phase(self, message):
        with self.lock:
            self.job['phase'] = message

    def submit(self, action, value=None):
        with self.lock:
            if self.job['busy']:
                raise ValueError('已有操作正在执行，请等待完成')
            if action == 'update' and (not self.checked or self.checked['status'] != 'update_available'
                                       or value != self.checked['latest_image_id']):
                raise ValueError('请先检查更新，再更新所显示的版本')
            if action not in ('check', 'update', 'rollback'):
                raise ValueError('不支持的操作')
            self.job = {'busy': True, 'action': action, 'phase': '开始操作', 'error': ''}
            self.worker = threading.Thread(target=self.run_job, args=(action, value))
            self.worker.start()

    def run_job(self, action, value):
        try:
            if action == 'check':
                self.phase('正在查询镜像仓库')
                result = checker.check_update(self.docker_path, self.container, self.image)
            elif action == 'update':
                self.update(value)
                result = checker.check_update(self.docker_path, self.container, self.image)
            else:
                self.rollback(value)
                result = None
            with self.lock:
                self.checked = result
                self.job.update(busy=False, phase='操作完成', error='')
        except Exception as error:
            # Never send raw Docker stderr, inspect output or credentials to clients.
            message = str(error) if isinstance(error, (ValueError, RuntimeError)) else '操作失败，请检查 NAS 上的管理服务和备份'
            with self.lock:
                if action == 'check':
                    self.checked = None
                self.job.update(busy=False, phase='操作失败', error=message)

    def manager_path(self):
        directory = self.config.get('synology_manager_dir')
        return Path(directory) / (self.container + '.config') if directory else None

    def sync_manager(self, old, new_id, image, old_name):
        path = self.manager_path()
        if path and old:
            saved = copy.deepcopy(old)
            saved.update(name=old_name, enabled=False)
            write_json(path.parent / (old_name + '.config'), saved)
            current = copy.deepcopy(old)
            current.update(name=self.container, id=new_id, image=image, enabled=True)
            write_json(path, current)

    def wait_healthy(self, target):
        until = time.monotonic() + self.config.get('health_timeout', 150)
        while time.monotonic() < until:
            state = self.inspect(target)['State']
            if state.get('Health', {}).get('Status') == 'healthy':
                return
            if not state['Running'] or state.get('Health', {}).get('Status') == 'unhealthy':
                break
            time.sleep(2)
        raise RuntimeError('容器未通过健康检查')

    def prepare_saved_container(self, record):
        # Earlier project images left X lock/socket files in the writable layer.
        # Backport just the safe startup cleanup to that preserved container;
        # retain its original entrypoint and all of its desktop implementation.
        directory = self.record_dir(record)
        probe = directory / 'entrypoint-probe.sh'
        self.docker('cp', record['old_id'] + ':/opt/115/entrypoint.sh', str(probe))
        source = probe.read_text()
        marker = 'python3 "$script_dir/prepare-display.py"'
        if marker in source:
            return
        anchor = 'source "$script_dir/environment.sh"\n'
        if source.count(anchor) != 1 or 'exec dbus-run-session -- "$script_dir/start.sh"' not in source:
            raise RuntimeError('旧容器启动脚本不是已知版本，拒绝自动修改')
        original = directory / 'entrypoint-before.sh'
        if not original.exists():
            shutil.copy2(str(probe), str(original))
            original.chmod(0o600)
        patched = directory / 'entrypoint-compatible.sh'
        patched.write_text(source.replace(anchor, anchor + marker + '\n', 1))
        patched.chmod(0o755)
        helper = Path(__file__).resolve().parents[1] / 'runtime/prepare-display.py'
        self.docker('cp', str(helper), record['old_id'] + ':/opt/115/prepare-display.py')
        self.docker('cp', str(patched), record['old_id'] + ':/opt/115/entrypoint.sh')
        record['startup_compatibility_backport'] = True
        self.save(record)

    def update(self, expected):
        old = self.inspect()
        if not old['State']['Running']:
            raise ValueError('正式容器未运行，不能开始更新')
        # Pull before stopping the desktop; reject a latest tag changed since check.
        self.phase('正在拉取新镜像，桌面继续运行')
        self.docker('pull', self.image, timeout=1800)
        new_image = self.image_info(self.image)
        if new_image['Id'] != expected:
            raise ValueError('latest 已发生变化，请重新检查更新')
        if new_image['Id'] == old['Image']:
            raise ValueError('当前已运行此镜像')
        if not new_image['Config'].get('Healthcheck'):
            raise ValueError('候选镜像缺少健康检查，拒绝更新')
        mounts = [m for m in old['Mounts'] if m['Destination'] == '/etc/115' and m['Type'] == 'bind']
        if len(mounts) != 1 or not Path(mounts[0]['Source']).is_dir():
            raise ValueError('需要独立的 /etc/115 主机绑定目录才能备份和回退')
        profile = Path(mounts[0]['Source']).resolve()
        if any(m['Destination'].startswith('/etc/115/') for m in old['Mounts']):
            raise ValueError('浏览器数据目录内存在额外挂载，无法完整备份')
        if profile == self.root or profile in self.root.parents or self.root in profile.parents:
            raise ValueError('浏览器数据和备份目录不能互相包含')
        if profile.stat().st_dev != self.root.stat().st_dev:
            raise ValueError('备份目录需要与浏览器数据位于同一文件系统，以便原子恢复')
        size = sum(p.stat().st_size for p in profile.rglob('*') if p.is_file() and not p.is_symlink())
        if shutil.disk_usage(self.root).free < size * 2 + 100 * 1024**2:
            raise ValueError('备份磁盘空间不足，未停止桌面')
        old_image = self.image_info(old['Image'])
        ident = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        directory = self.root / ident
        directory.mkdir(mode=0o700)
        manager = self.manager_path()
        manager_old = json.loads(manager.read_text()) if manager and manager.exists() else None
        record = {'id': ident, 'created': datetime.datetime.now().isoformat(timespec='seconds'),
                  'status': 'pending', 'old_id': old['Id'], 'new_id': None,
                  'old_image': old['Config']['Image'], 'old_image_id': old['Image'], 'new_image_id': expected,
                  'old_revision': (old_image['Config'].get('Labels') or {}).get('org.opencontainers.image.revision', ''),
                  'new_revision': (new_image['Config'].get('Labels') or {}).get('org.opencontainers.image.revision', ''),
                  'rollback_name': self.container + '-before-' + ident,
                  'profile': str(profile), 'profile_saved': False, 'old_renamed': False}
        write_json(directory / 'container-before.json', old)
        write_json(directory / 'manager-before.json', manager_old)
        self.save(record)
        try:
            self.phase('停止桌面并备份浏览器数据')
            self.docker('stop', '--time', '30', old['Id'])
            shutil.copytree(str(profile), str(directory / 'profile'), symlinks=True)
            record['profile_saved'] = True
            self.save(record)
            self.docker('rename', old['Id'], record['rollback_name'])
            record['old_renamed'] = True
            self.save(record)
            self.docker('update', '--restart=no', old['Id'])
            self.phase('创建新容器并验证桌面健康')
            config = replacement_config(old, old_image, new_image, self.image)
            config['Labels']['io.devshuai.115-update.record'] = ident
            if self.config.get('public_url'):
                env = envmap(config['Env'])
                env['UPDATE_URL'] = self.config['public_url']
                config['Env'] = [k + '=' + v for k, v in env.items()]
            # Record the candidate identity before starting its application.
            record['new_id'] = self.create(config)
            self.save(record)
            if self.inspect(record['new_id'])['Image'] != expected:
                raise RuntimeError('镜像标签被其他操作改变，拒绝启动不匹配的镜像')
            self.docker('start', record['new_id'])
            self.wait_healthy(record['new_id'])
            self.sync_manager(manager_old, record['new_id'], self.image, record['rollback_name'])
            record['status'] = 'ready'
            self.save(record)
        except Exception as error:
            self.phase('更新失败，正在恢复旧容器和数据')
            try:
                self.restore(record)
                record['status'] = 'failed_restored'
                self.save(record)
            except Exception:
                record['status'] = 'interrupted'
                self.save(record)
                raise RuntimeError('更新中断，自动恢复未完成；请使用备份记录的回退按钮') from error
            raise RuntimeError('更新失败，已恢复旧容器及升级前的浏览器数据') from error

    def restore(self, record):
        directory = self.record_dir(record)
        old = self.inspect(record['old_id'])
        # Inspect by name, not mutable image tags; never stop an unrelated replacement.
        try:
            current = self.inspect()
        except RuntimeError:
            current = None
        allowed = {record['old_id'], record.get('new_id')}
        if current and not record.get('new_id') and current['Id'] != record['old_id']:
            labels = current['Config'].get('Labels') or {}
            if labels.get('io.devshuai.115-update.record') == record['id'] and current['Image'] == record['new_image_id']:
                record['new_id'] = current['Id']
                self.save(record)
                allowed.add(current['Id'])
        if current and current['Id'] not in allowed:
            raise ValueError('正式容器已被其他方式替换，拒绝覆盖；请人工检查备份')
        profile = Path(record['profile'])
        restoring = None
        if record['profile_saved']:
            source = directory / 'profile'
            size = sum(p.stat().st_size for p in source.rglob('*') if p.is_file() and not p.is_symlink())
            if shutil.disk_usage(profile.parent).free < size + 100 * 1024**2:
                raise ValueError('恢复数据所需的磁盘空间不足')
            self.phase('准备配套浏览器数据')
            restoring = profile.parent / (profile.name + '-restore-' + uuid.uuid4().hex[:8])
            shutil.copytree(str(source), str(restoring), symlinks=True)
        if old['State']['Running']:
            self.docker('stop', '--time', '30', old['Id'])
        saved_name = self.container + '-after-' + record['id'] + '-' + uuid.uuid4().hex[:6]
        if current and current['Id'] != record['old_id']:
            self.docker('stop', '--time', '30', current['Id'])
            self.docker('update', '--restart=no', current['Id'])
            self.docker('rename', current['Id'], saved_name)
        elif record.get('new_id'):
            # Candidate may have another name after a prior interrupted restore.
            candidate = self.inspect(record['new_id'])
            if candidate['State']['Running']:
                self.docker('stop', '--time', '30', candidate['Id'])
            self.docker('update', '--restart=no', candidate['Id'])
            saved_name = candidate['Name'].lstrip('/')
        if record['profile_saved']:
            self.phase('恢复配套浏览器数据，保留当前数据副本')
            saved_profile = directory / ('profile-after-' + uuid.uuid4().hex[:8])
            record['last_saved_profile'] = str(saved_profile)
            self.save(record)
            if profile.exists():
                os.replace(str(profile), str(saved_profile))
            os.replace(str(restoring), str(profile))
        if old['Name'].lstrip('/') != self.container:
            self.docker('rename', old['Id'], self.container)
        before = json.loads((directory / 'container-before.json').read_text())
        policy = before['HostConfig']['RestartPolicy']
        restart = policy['Name']
        if restart == 'on-failure' and policy.get('MaximumRetryCount'):
            restart += ':' + str(policy['MaximumRetryCount'])
        self.docker('update', '--restart=' + restart, old['Id'])
        self.prepare_saved_container(record)
        self.docker('start', old['Id'])
        manager_old = json.loads((directory / 'manager-before.json').read_text())
        manager = self.manager_path()
        if manager and manager_old:
            if record.get('new_id'):
                candidate_manager = copy.deepcopy(manager_old)
                candidate_manager.update(name=saved_name, id=record['new_id'], image=self.image, enabled=False)
                write_json(manager.parent / (saved_name + '.config'), candidate_manager)
            write_json(manager, manager_old)
            stale = manager.parent / (record['rollback_name'] + '.config')
            if stale.exists():
                stale.unlink()
        self.wait_healthy(old['Id'])

    def rollback(self, ident):
        matches = [r for r in self.records() if r['id'] == ident and r['status'] in ('ready', 'interrupted')]
        if len(matches) != 1:
            raise ValueError('此备份不可回退')
        record = matches[0]
        current = self.inspect() if record['status'] == 'ready' else None
        manager = self.manager_path()
        current_manager = json.loads(manager.read_text()) if manager and manager.exists() else None
        record['status'] = 'pending'
        self.save(record)
        self.phase('正在回退容器')
        try:
            self.restore(record)
        except Exception as error:
            # If the old version cannot start, retain its profile and restore the
            # version that was running when the rollback was requested.
            saved = Path(record.get('last_saved_profile', '/nonexistent'))
            if current and current['Id'] != record['old_id'] and saved.is_dir():
                try:
                    self.phase('回退验证失败，正在恢复回退前的版本')
                    old = self.inspect(record['old_id'])
                    self.docker('stop', '--time', '30', old['Id'])
                    if old['Name'].lstrip('/') == self.container:
                        self.docker('rename', old['Id'], record['rollback_name'])
                    self.docker('update', '--restart=no', old['Id'])
                    profile = Path(record['profile'])
                    prepared = profile.parent / (profile.name + '-forward-' + uuid.uuid4().hex[:8])
                    shutil.copytree(str(saved), str(prepared), symlinks=True)
                    if profile.exists():
                        os.replace(str(profile), str(self.record_dir(record) / ('profile-failed-rollback-' + uuid.uuid4().hex[:8])))
                    os.replace(str(prepared), str(profile))
                    self.docker('rename', current['Id'], self.container)
                    restart = current['HostConfig']['RestartPolicy']['Name']
                    self.docker('update', '--restart=' + restart, current['Id'])
                    self.docker('start', current['Id'])
                    self.wait_healthy(current['Id'])
                    if manager and current_manager:
                        write_json(manager, current_manager)
                        self.sync_manager(json.loads((self.record_dir(record) / 'manager-before.json').read_text()), current['Id'], self.image, record['rollback_name'])
                    record['status'] = 'ready'
                    self.save(record)
                except Exception:
                    record['status'] = 'interrupted'
                    self.save(record)
                    raise RuntimeError('回退未完成，请使用备份记录恢复或检查 NAS') from error
                raise RuntimeError('旧版本未通过回退验证，已恢复回退前的容器和数据') from error
            record['status'] = 'interrupted'
            self.save(record)
            raise
        record['status'] = 'rolled_back'
        self.save(record)
