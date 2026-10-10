import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('check_update', Path(__file__).resolve().parents[1] / 'scripts/check-update.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

OLD = 'sha256:' + 'a' * 64
NEW = 'sha256:' + 'b' * 64
PLATFORM = {'os': 'linux', 'architecture': 'amd64', 'variant': ''}


def manifest(digest=NEW, kind='OCIManifest', arch='amd64'):
    return {'Descriptor': {'platform': {'os': 'linux', 'architecture': arch}},
            kind: {'config': {'digest': digest}}}


class UpdateTests(unittest.TestCase):
    def test_selects_platform_and_excludes_attestations(self):
        attestation = manifest(OLD, arch='unknown')
        attestation['Descriptor']['platform']['os'] = 'unknown'
        self.assertEqual(checker.image_config_digest([attestation, manifest(), manifest(OLD, arch='arm64')], PLATFORM), NEW)

    def test_single_schema2_manifest(self):
        self.assertEqual(checker.image_config_digest(manifest(kind='SchemaV2Manifest'), PLATFORM), NEW)

    def test_missing_ambiguous_and_invalid_digests_fail_closed(self):
        for value in ([], {}, [manifest(), manifest(OLD)], manifest('invalid'), manifest(arch='arm64')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checker.image_config_digest(value, PLATFORM)

    def test_compares_running_image_even_if_latest_is_already_cached(self):
        container = {'Image': OLD, 'State': {'Running': True}, 'Config': {'Image': 'image:latest'}}
        image = {'Os': 'linux', 'Architecture': 'amd64', 'Config': {'Labels': {}}}
        with patch.object(checker, 'docker_json', side_effect=[[container], [image], [manifest()]]) as call:
            result = checker.check_update('docker', '115-docker', 'image:latest')
            self.assertEqual(result['status'], 'update_available')
            self.assertEqual(call.call_args_list[1].args, ('docker', 'image', 'inspect', OLD))

    def test_same_config_digest_is_up_to_date(self):
        container = {'Image': NEW, 'State': {'Running': True}, 'Config': {'Image': 'image:latest'}}
        image = {'Os': 'linux', 'Architecture': 'amd64', 'Config': {'Labels': {}}}
        with patch.object(checker, 'docker_json', side_effect=[[container], [image], manifest()]):
            self.assertEqual(checker.check_update('docker', '115-docker', 'image:latest')['status'], 'up_to_date')

    def test_registry_failure_and_timeout_are_not_up_to_date(self):
        for error in (RuntimeError('registry unavailable'), subprocess.TimeoutExpired('docker', 90)):
            with patch.object(checker, 'check_update', side_effect=error), patch('builtins.print'):
                self.assertEqual(checker.main(['--json']), 1)

    def test_status_exit_codes(self):
        for status, expected in [('update_available', 10), ('up_to_date', 0)]:
            with patch.object(checker, 'check_update', return_value={'status': status}), patch('builtins.print'):
                self.assertEqual(checker.main(['--json']), expected)

    def test_stopped_container_fails(self):
        with patch.object(checker, 'docker_json', return_value=[{'State': {'Running': False}}]):
            with self.assertRaisesRegex(ValueError, '未运行'):
                checker.check_update('docker', '115-docker', 'image:latest')
