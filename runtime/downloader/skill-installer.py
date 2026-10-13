#!/usr/bin/env python3
"""Install the self-contained 115-download skill with Python 3.8+ only.

One command:
  curl -fsSL https://raw.githubusercontent.com/devShuai/115-docker/main/runtime/downloader/skill-installer.py | python3 -
Offline from this project:
  python3 runtime/downloader/skill-installer.py --source .
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

NAME = '115-download'
REPOSITORY = 'devShuai/115-docker'
FILES = ('SKILL.md', 'agents/openai.yaml', 'scripts/115dl.py', 'references/api.md', 'references/openapi.json')
MAX_FILE = 1024 * 1024
MAX_TOTAL = 2 * MAX_FILE


def read_url(url, limit, accept='application/vnd.github+json'):
    request = urllib.request.Request(url, headers={'User-Agent': '115-download-skill-installer',
                                                 'Accept': accept})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds the skill size limit')
    return data


def github_source(ref):
    # Resolve once and use the immutable commit for every file, even if main changes during installation.
    url = 'https://api.github.com/repos/' + REPOSITORY + '/commits/' + urllib.parse.quote(ref, safe='')
    resolved = read_url(url, 128, 'application/vnd.github.sha').decode('ascii').strip()
    if not isinstance(resolved, str) or not re.fullmatch(r'[a-f0-9]{40}', resolved):
        raise ValueError('GitHub returned an invalid commit')
    base = 'https://raw.githubusercontent.com/' + REPOSITORY + '/' + resolved + '/skills/' + NAME + '/'
    return lambda name, limit: read_url(base + name, limit), 'github:' + resolved


def local_source(source):
    directory = Path(source).expanduser().resolve()
    if (directory / 'skills' / NAME / 'SKILL.md').is_file():
        directory = directory / 'skills' / NAME

    def read(name, limit):
        path = directory / name
        if path.is_symlink() or not path.is_file() or directory not in path.resolve().parents:
            raise ValueError('Missing or unsafe skill file: ' + name)
        if path.stat().st_size > limit:
            raise ValueError('Skill file exceeds size limit: ' + name)
        return path.read_bytes()

    return read, 'local'


def package(reader):
    raw = reader('manifest.json', 16 * 1024)
    manifest = json.loads(raw)
    if manifest.get('format') != 1 or manifest.get('name') != NAME or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported skill manifest')
    # Fixed members prevent a manifest from escaping the destination or adding executable installers.
    if set(manifest['files']) != set(FILES):
        raise ValueError('Unexpected or missing files in skill manifest')
    payload = {'manifest.json': raw}
    total = len(raw)
    for name in FILES:
        item = manifest['files'][name]
        if not isinstance(item, dict) or not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid checksum metadata: ' + name)
        size = item.get('size')
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE:
            raise ValueError('Invalid size metadata: ' + name)
        data = reader(name, MAX_FILE)
        if len(data) != size or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Checksum verification failed: ' + name)
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError('Skill package exceeds total size limit')
        payload[name] = data
    # Verify that the installed entry point belongs to the expected skill.
    front = payload['SKILL.md'].decode('utf-8').split('---', 2)
    if len(front) != 3 or not re.search(r'^name:\s*115-download\s*$', front[1], re.MULTILINE):
        raise ValueError('SKILL.md has an unexpected name')
    compile(payload['scripts/115dl.py'], '115dl.py', 'exec')
    spec = json.loads(payload['references/openapi.json'])
    if spec.get('openapi') != '3.1.0' or not isinstance(spec.get('paths'), dict):
        raise ValueError('Invalid OpenAPI reference')
    return payload


def install(payload, skills_dir, update=False):
    root = Path(skills_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.115-download-install.lock'
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError('Another installation is in progress; if it was interrupted, remove ' + str(lock)) from error
    try:
        os.close(descriptor)
        return install_locked(payload, root, update)
    finally:
        lock.unlink()


def install_locked(payload, root, update):
    target = root / NAME
    if target.is_symlink():
        raise ValueError('Destination skill is a symlink; choose a different skills directory')
    if target.exists():
        if not update:
            raise ValueError('Skill already exists; use --update to update it')
        old_manifest = target / 'manifest.json'
        if not target.is_dir() or not old_manifest.is_file() or old_manifest.is_symlink():
            raise ValueError('Existing destination is not a managed 115-download skill')
        old = json.loads(old_manifest.read_text(encoding='utf-8'))
        if old.get('name') != NAME or old.get('format') != 1:
            raise ValueError('Existing destination is not a managed 115-download skill')
    with tempfile.TemporaryDirectory(prefix='.115-download-install-', dir=str(root)) as temporary:
        stage = Path(temporary) / NAME
        previous = Path(temporary) / 'previous'
        if target.exists():
            # Preserve user-created files while replacing package-owned files.
            shutil.copytree(str(target), str(stage), symlinks=True)
        else:
            stage.mkdir(mode=0o700)
        for name, content in payload.items():
            file = stage / name
            if file.is_symlink() or any(parent.is_symlink() for parent in file.parents if parent != root):
                raise ValueError('Managed file uses a symlink: ' + name)
            file.parent.mkdir(parents=True, exist_ok=True)
            # Unlink copied files before writing so hard links in an existing skill cannot modify outside files.
            if file.exists():
                file.unlink()
            file.write_bytes(content)
            file.chmod(0o700 if name.startswith('scripts/') else 0o600)
        moved = False
        try:
            if target.exists():
                target.rename(previous)
                moved = True
            stage.rename(target)
        except OSError:
            if moved:
                previous.rename(target)
            raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install 115-download (Python 3.8+; no pip, git, Docker or NAS credentials needed)')
    parser.add_argument('--skills-dir', default=str(Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'skills'),
                        help='skill discovery directory (default: $CODEX_HOME/skills or ~/.codex/skills)')
    parser.add_argument('--source', help='offline project root or skill directory')
    parser.add_argument('--ref', default='main', help='GitHub branch/tag/commit, resolved to an immutable commit')
    parser.add_argument('--update', action='store_true', help='update an existing managed skill, preserving user-added files')
    args = parser.parse_args(argv)
    try:
        reader, origin = local_source(args.source) if args.source else github_source(args.ref)
        payload = package(reader)
        target = install(payload, args.skills_dir, args.update)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, urllib.error.URLError) as error:
        print('Installation failed: ' + str(error), file=sys.stderr)
        return 1
    print('Installed: ' + str(target))
    print('Source: ' + origin)
    print('Set DOWNLOAD_API_URL, DOWNLOAD_API_USER and DOWNLOAD_API_PASSWORD or DOWNLOAD_API_PASSWORD_FILE in the agent environment.')
    print('Open a new agent conversation if skill discovery needs refreshing, then use $115-download.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
