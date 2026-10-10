#!/usr/bin/env python3
"""Check a running container against a registry tag without pulling image layers."""

import argparse
import json
import re
import subprocess
import sys

DEFAULT_IMAGE = 'registry.devshuai.com/shuai-repo/115-docker:latest'


def image_config_digest(manifests, platform):
    """Select the real platform image, excluding BuildKit attestation manifests."""
    candidates = manifests if isinstance(manifests, list) else [manifests]
    matches = []
    for item in candidates:
        descriptor = item.get('Descriptor', {})
        remote_platform = descriptor.get('platform', {})
        if any(remote_platform.get(k, '') != platform.get(k, '')
               for k in ('os', 'architecture', 'variant')):
            continue
        manifest = item.get('OCIManifest') or item.get('SchemaV2Manifest') or {}
        digest = manifest.get('config', {}).get('digest', '')
        if manifest.get('artifactType') or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            continue
        matches.append(digest)
    if len(set(matches)) != 1:
        raise ValueError('仓库未返回唯一匹配的平台镜像，无法确认更新状态')
    return matches[0]


def docker_json(docker, *args):
    try:
        result = subprocess.run([docker, *args], check=True, capture_output=True,
                                text=True, timeout=90)
    except subprocess.CalledProcessError as error:
        # Do not echo registry responses or credential-bearing command output.
        raise RuntimeError('Docker 查询失败（退出码 %s），请检查容器名称、仓库登录和网络' % error.returncode) from error
    return json.loads(result.stdout)


def check_update(docker, container, image):
    running = docker_json(docker, 'container', 'inspect', container)[0]
    if not running.get('State', {}).get('Running'):
        raise ValueError('目标容器未运行，无法确认正式部署状态')
    local = docker_json(docker, 'image', 'inspect', running['Image'])[0]
    platform = {'os': local['Os'], 'architecture': local['Architecture'],
                'variant': local.get('Variant', '')}
    remote = docker_json(docker, 'manifest', 'inspect', '--verbose', image)
    remote_id = image_config_digest(remote, platform)
    return {'status': 'update_available' if running['Image'] != remote_id else 'up_to_date',
            'container': container, 'image': image,
            'deployed_tag': running['Config']['Image'], 'platform': platform,
            'running_image_id': running['Image'], 'latest_image_id': remote_id,
            'running_revision': (local.get('Config', {}).get('Labels') or {}).get(
                'org.opencontainers.image.revision', '')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='115-docker', help='正在运行的容器名称或 ID')
    parser.add_argument('--image', default=DEFAULT_IMAGE, help='要跟踪的仓库标签（默认 latest）')
    parser.add_argument('--docker', default='docker', help='Docker CLI 路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)
    try:
        result = check_update(args.docker, args.container, args.image)
    except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.TimeoutExpired) as error:
        result = {'status': 'error', 'message': str(error)}
        print(json.dumps(result, ensure_ascii=False) if args.json else '检查失败：' + str(error))
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(('发现更新' if result['status'] == 'update_available' else '已是最新镜像')
              + '：' + args.image)
        print('运行镜像：' + result['running_image_id'])
        print('仓库镜像：' + result['latest_image_id'])
    return 10 if result['status'] == 'update_available' else 0


if __name__ == '__main__':
    sys.exit(main())
