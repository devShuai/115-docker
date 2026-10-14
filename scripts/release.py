#!/usr/bin/env python3
"""Cut a release: add its notes to CHANGELOG.md, commit, tag vX.Y.Z and optionally push.

  python3 scripts/release.py patch               # or minor, major, 1.2.0, 1.2.0-rc.1
  python3 scripts/release.py minor --dry-run     # show the notes, change nothing
  python3 scripts/release.py patch --push        # push main and the tag to every remote

Notes are grouped from the commit subjects since the last stable release
(feat / fix / perf / docs, the rest under 其他), or taken from --notes FILE.
A new version must be above every existing release, so latest only moves
forward. Pushing the tag starts the release pipelines on OneDev and GitHub.
"""
import argparse
import datetime
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version  # noqa: E402

CHANGELOG = version.ROOT / "CHANGELOG.md"
HEADER = ("# 更新日志\n\n版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)：主版本号.次版本号.修订号。"
          "镜像标签的含义见 README 的“版本与发布”。\n")
GROUPS = (("feat", "新功能"), ("fix", "修复"), ("perf", "性能"), ("docs", "文档"))
SUBJECT = re.compile(r"(\w+)(?:\([^)]*\))?!?:\s*(.+)")


def git(*args):
    return subprocess.run(["git", *args], cwd=version.ROOT, check=True, capture_output=True, text=True).stdout.strip()


def releases(tags):
    """Release tags, oldest first."""
    return sorted((tag for tag in tags if version.parse(tag)), key=lambda tag: version.order(version.parse(tag)))


def next_version(spec, existing):
    """patch / minor / major from the newest stable release, or an explicit X.Y.Z[-rc.N]."""
    stable = [version.parse(tag) for tag in existing if not version.parse(tag)[3]]
    major, minor, patch, _ = max(stable, key=version.order) if stable else (0, 0, 0, "")
    bumped = {"major": (major + 1, 0, 0), "minor": (major, minor + 1, 0), "patch": (major, minor, patch + 1)}
    tag = "v%d.%d.%d" % bumped[spec] if spec in bumped else "v" + spec.removeprefix("v")
    parsed = version.parse(tag)
    if parsed is None:
        raise ValueError(f"版本号只能是 patch、minor、major，或 X.Y.Z、X.Y.Z-rc.N：{spec}")
    if tag in existing:
        raise ValueError(f"{tag} 已经发布过")
    if existing and version.order(parsed) <= version.order(version.parse(existing[-1])):
        raise ValueError(f"{tag} 必须高于已发布的最新版本 {existing[-1]}")
    return tag


def section(tag, day, subjects):
    """A CHANGELOG.md section from conventional commit subjects."""
    titles = dict(GROUPS)
    groups = {title: [] for title in [*titles.values(), "其他"]}
    for subject in subjects:
        match = SUBJECT.fullmatch(subject)
        kind, text = (match[1].lower(), match[2]) if match else ("", subject)
        if kind == "release" or subject.startswith("Merge "):
            continue
        groups[titles.get(kind, "其他")].append(text)
    lines = [f"## {tag} - {day}", ""]
    for title, items in groups.items():
        if items:
            lines += [f"### {title}", "", *(f"- {item}" for item in items), ""]
    return "\n".join(lines).rstrip() + "\n"


def insert(changelog, block):
    """The changelog with a new release section above the older ones."""
    text = changelog if changelog.strip() else HEADER
    at = text.find("\n## ")
    if at < 0:
        return text.rstrip() + "\n\n" + block
    return text[:at + 1] + block + "\n" + text[at + 1:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="patch、minor、major，或明确的 X.Y.Z / X.Y.Z-rc.N")
    parser.add_argument("--notes", type=Path, help="用这个文件的内容作为版本说明（默认按提交记录生成）")
    parser.add_argument("--trailer", action="append", default=[], help="追加到发版提交说明末尾，例如 Co-Authored-By")
    parser.add_argument("--push", action="store_true", help="把 main 和新标签一起推送到所有远端，触发发版流水线")
    parser.add_argument("--dry-run", action="store_true", help="只显示版本说明，不提交、不打标签")
    args = parser.parse_args(argv)
    try:
        if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
            raise ValueError("只能在 main 分支上发版")
        if git("status", "--porcelain"):
            raise ValueError("工作区有未提交的改动")
        remotes = git("remote").split()
        for remote in remotes:
            git("fetch", "--quiet", "--tags", remote)
            # Local commits not pushed yet are fine; commits that exist only on a remote are not.
            if subprocess.run(["git", "merge-base", "--is-ancestor", f"{remote}/main", "HEAD"],
                              cwd=version.ROOT).returncode:
                raise ValueError(f"{remote}/main 有本地没有的提交，请先同步")
        existing = releases(git("tag", "--list", "v*").split())
        tag = next_version(args.version, existing)
        stable = [item for item in existing if not version.parse(item)[3]]
        since = stable[-1] if stable else None
        subjects = git("log", "--format=%s", f"{since}..HEAD" if since else "HEAD").splitlines()
        if not subjects:
            raise ValueError(f"{since} 之后没有新的提交")
        day = datetime.date.today().isoformat()
        block = (f"## {tag} - {day}\n\n" + args.notes.read_text(encoding="utf-8").strip() + "\n"
                 if args.notes else section(tag, day, subjects))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or ""
        print("发版失败：" + (str(error) + ("\n" + detail.strip() if detail.strip() else "")), file=sys.stderr)
        return 2
    print(block)
    if args.dry_run:
        print(f"（预览）将提交 CHANGELOG.md 并创建标签 {tag}" + ("，推送到 " + "、".join(remotes) if args.push else ""))
        return 0
    CHANGELOG.write_text(insert(CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "", block),
                         encoding="utf-8")
    git("add", "CHANGELOG.md")
    message = ["-m", f"release: {tag}"] + (["-m", "\n".join(args.trailer)] if args.trailer else [])
    git("commit", "--quiet", *message)
    git("tag", "--annotate", tag, "--message", f"{tag}\n\n{version.notes(CHANGELOG.read_text(encoding='utf-8'), tag)}")
    if args.push:
        for remote in remotes:
            # Branch and tag in one push, so a tag trigger that requires main always sees the commit there.
            git("push", "--quiet", "--atomic", remote, "HEAD:refs/heads/main", f"refs/tags/{tag}")
        print(f"已发布 {tag}：已推送到 " + "、".join(remotes) + "，发版流水线会构建、验收并发布镜像")
    else:
        print(f"已创建 {tag}。确认无误后推送：" + "；".join(
            f"git push --atomic {remote} main refs/tags/{tag}" for remote in remotes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
