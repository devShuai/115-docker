#!/usr/bin/env python3
"""Version and image tags of a build, and a release's notes.

A release is an annotated tag vMAJOR.MINOR.PATCH (or a pre-release such as
v1.2.0-rc.1) on main. A release publishes the image as X.Y.Z; a stable release
also moves X.Y, X and latest. Builds of main without a release tag are edge
builds: version edge-<commit>, image tag edge.

  python3 scripts/version.py                          # version of this checkout
  python3 scripts/version.py --tag v1.2.0 --image REPO --write release
  python3 scripts/version.py --notes v1.2.0           # its section of CHANGELOG.md
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")


def parse(tag):
    """'v1.2.3' -> (1, 2, 3, ''), 'v1.2.3-rc.1' -> (1, 2, 3, 'rc.1'); anything else -> None."""
    match = TAG.fullmatch(tag or "")
    return (int(match[1]), int(match[2]), int(match[3]), match[4] or "") if match else None


def order(parsed):
    """Semantic-version precedence: pre-releases sort before their release."""
    major, minor, patch, pre = parsed
    fields = [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split(".")] if pre else []
    return major, minor, patch, not pre, fields


def build(tag="", commit=""):
    """{version, channel, tags} of a release build (tag) or an edge build of commit."""
    if not tag:
        return {"version": f"edge-{commit[:7]}" if commit else "dev", "channel": "edge", "tags": ["edge"]}
    parsed = parse(tag)
    if parsed is None:
        raise ValueError(f"发版标签必须是 vX.Y.Z 或 vX.Y.Z-rc.N 这样的格式：{tag}")
    major, minor, _, pre = parsed
    version = tag[1:]
    if pre:
        return {"version": version, "channel": "prerelease", "tags": [version]}
    return {"version": version, "channel": "stable", "tags": [version, f"{major}.{minor}", str(major), "latest"]}


def notes(changelog, tag):
    """The body of '## <tag> ...' in CHANGELOG.md text, without its heading."""
    lines, body = changelog.splitlines(), None
    for line in lines:
        if line.startswith("## "):
            if body is not None:
                break
            if line[3:].split(" ", 1)[0] == tag:
                body = []
            continue
        if body is not None:
            body.append(line)
    if body is None:
        raise ValueError(f"CHANGELOG.md 里没有 {tag} 的说明")
    return "\n".join(body).strip() + "\n"


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def current_tag():
    """The release tag on HEAD, if any (local builds)."""
    try:
        tags = _git("tag", "--points-at", "HEAD").split()
    except (OSError, subprocess.CalledProcessError):
        return ""
    releases = sorted((tag for tag in tags if parse(tag)), key=lambda tag: order(parse(tag)))
    return releases[-1] if releases else ""


def current_commit():
    try:
        return _git("rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="发版标签；空字符串表示 main 的 edge 构建（默认读取 HEAD 上的标签）")
    parser.add_argument("--commit", help="提交哈希（默认 HEAD）")
    parser.add_argument("--image", help="镜像仓库，例如 ghcr.io/devshuai/115-docker")
    parser.add_argument("--write", metavar="NAME", help="写出 NAME.version 与 NAME.tags，供 OneDev 的 @file:NAME.version@ 变量使用")
    parser.add_argument("--github-output", action="store_true", help="写入 $GITHUB_OUTPUT")
    parser.add_argument("--notes", metavar="TAG", help="输出 CHANGELOG.md 中该版本的说明")
    args = parser.parse_args(argv)
    try:
        if args.notes:
            sys.stdout.write(notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), args.notes))
            return 0
        tag = current_tag() if args.tag is None else args.tag.strip()
        info = build(tag, args.commit if args.commit is not None else current_commit())
    except (OSError, ValueError) as error:
        print("错误：" + str(error), file=sys.stderr)
        return 2
    references = [f"{args.image}:{name}" for name in info["tags"]] if args.image else info["tags"]
    if args.write:
        # Single lines without a newline: OneDev pastes file content into step fields as is.
        Path(args.write + ".version").write_text(info["version"], encoding="utf-8")
        Path(args.write + ".tags").write_text(" ".join("-t " + reference for reference in references), encoding="utf-8")
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"version={info['version']}\nchannel={info['channel']}\ntags={' '.join(references)}\n")
    print(info["version"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
