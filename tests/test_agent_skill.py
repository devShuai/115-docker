import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('skill_installer', ROOT / 'runtime/downloader/skill-installer.py')
builder = load('skill_builder', ROOT / 'scripts/build-agent-skill.py')


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / 'skills'
        reader, _ = installer.local_source(ROOT)
        self.payload = installer.package(reader)

    def run_installer(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = installer.main(['--source', str(ROOT), '--skills-dir', str(self.target), *args])
        return code, out.getvalue(), err.getvalue()

    def test_sources_are_synchronized_and_zip_is_portable(self):
        archive = self.root / 'skill.zip'
        builder.build(check=True, archive=archive)
        import zipfile
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(set(zipped.namelist()), {'115-download/' + name for name in self.payload})
            for name, data in self.payload.items():
                self.assertEqual(zipped.read('115-download/' + name), data)
        self.assertEqual(self.payload['scripts/115dl.py'], (ROOT / 'runtime/downloader/cli.py').read_bytes())
        guide = self.payload['references/api.md'].decode()
        self.assertNotIn('安装与非交互调用', guide)
        self.assertIn('python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json', guide)
        for endpoint in builder.apidocs.ENDPOINTS:
            self.assertIn(endpoint['method'] + ' ' + endpoint['path'], guide)

    def test_install_update_preserves_custom_files_and_refuses_unmanaged_destinations(self):
        self.assertEqual(self.run_installer()[0], 0)
        directory = self.target / installer.NAME
        for name, data in self.payload.items():
            self.assertEqual((directory / name).read_bytes(), data)
        note = directory / 'private-settings.txt'
        note.write_text('keep this local file')
        self.assertEqual(self.run_installer()[0], 1)
        self.assertEqual(self.run_installer('--update')[0], 0)
        self.assertEqual(note.read_text(), 'keep this local file')
        self.assertFalse(list(self.target.glob('.115-download-install-*')))
        (directory / 'manifest.json').unlink()
        self.assertEqual(self.run_installer('--update')[0], 1)
        self.assertTrue(note.exists())

    def test_bad_checksum_never_changes_existing_skill(self):
        self.assertEqual(self.run_installer()[0], 0)
        current = self.target / installer.NAME / 'SKILL.md'
        before = current.read_bytes()
        def bad_reader(name, limit):
            return b'changed' if name == 'scripts/115dl.py' else self.payload[name]
        with patch.object(installer, 'local_source', return_value=(bad_reader, 'local')):
            code, _, err = self.run_installer('--update')
        self.assertEqual(code, 1)
        self.assertIn('Checksum', err)
        self.assertEqual(current.read_bytes(), before)

    def test_parallel_install_lock_preserves_current_skill(self):
        self.assertEqual(self.run_installer()[0], 0)
        lock = self.target / '.115-download-install.lock'
        lock.write_text('another installer')
        before = (self.target / installer.NAME / 'SKILL.md').read_bytes()
        code, _, err = self.run_installer('--update')
        self.assertEqual(code, 1)
        self.assertIn('in progress', err)
        self.assertEqual((self.target / installer.NAME / 'SKILL.md').read_bytes(), before)
        self.assertEqual(lock.read_text(), 'another installer')

    def test_manifest_cannot_escape_directory_and_symlinks_are_not_followed(self):
        manifest = json.loads(self.payload['manifest.json'])
        manifest['files']['../escape.py'] = manifest['files'].pop('SKILL.md')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            installer.package(lambda name, limit: json.dumps(manifest).encode())
        self.target.mkdir()
        outside = self.root / 'outside'; outside.mkdir()
        (self.target / installer.NAME).symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            installer.install(self.payload, self.target, update=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_github_downloads_use_one_pinned_commit_for_all_files(self):
        sha = 'a' * 40
        requested = []
        def fake_get(url, limit, accept=None):
            requested.append(url)
            if url.startswith('https://api.github.com/'):
                self.assertEqual(accept, 'application/vnd.github.sha')
                return (sha + '\n').encode()
            base = 'https://raw.githubusercontent.com/devShuai/115-docker/' + sha + '/skills/115-download/'
            self.assertTrue(url.startswith(base), url)
            return self.payload[url[len(base):]]
        with patch.object(installer, 'read_url', side_effect=fake_get):
            reader, origin = installer.github_source('feature/test')
            payload = installer.package(reader)
        self.assertEqual(payload, self.payload)
        self.assertEqual(origin, 'github:' + sha)
        self.assertEqual(requested[0], 'https://api.github.com/repos/devShuai/115-docker/commits/feature%2Ftest')
        self.assertEqual(len(requested), len(self.payload) + 1)

    def test_default_directory_respects_codex_home_without_reading_credentials(self):
        home = self.root / 'codex-home'
        with patch.dict(os.environ, {'CODEX_HOME': str(home)}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.main(['--source', str(ROOT)]), 0)
        self.assertTrue((home / 'skills/115-download/SKILL.md').is_file())

    def test_installed_client_reaches_fake_api_without_project_imports(self):
        # Real HTTP request from the installed copy, using synthetic credentials and data only.
        from test_downloader import ServerCloud, FakeWatcher
        import auth
        import server
        app = server.App(ServerCloud(), FakeWatcher(), '115', auth.encode('Check123'), str(self.root / 'profile'))
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.handler_for(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
        self.assertEqual(self.run_installer()[0], 0)
        script = self.target / installer.NAME / 'scripts/115dl.py'
        env = dict(os.environ, DOWNLOAD_API_URL=f'http://127.0.0.1:{httpd.server_address[1]}',
                   DOWNLOAD_API_USER='115', DOWNLOAD_API_PASSWORD='Check123')
        env.pop('DOWNLOAD_API_PASSWORD_FILE', None)
        result = subprocess.run([sys.executable, '-I', str(script), '--no-input', '--json', 'browse', '--cid', '0'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['kind'], 'drive')


if __name__ == '__main__':
    unittest.main()
