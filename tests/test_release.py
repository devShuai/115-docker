import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import version  # noqa: E402

spec = importlib.util.spec_from_file_location("release", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

CHANGELOG = """# 更新日志

说明。

## v1.1.0 - 2026-11-01

### 修复

- b

## v1.0.0 - 2026-10-14

首个正式版本。
"""


class VersionTests(unittest.TestCase):
    def test_release_tags(self):
        self.assertEqual(version.parse("v1.2.3"), (1, 2, 3, ""))
        self.assertEqual(version.parse("v1.2.3-rc.1"), (1, 2, 3, "rc.1"))
        for tag in ("1.2.3", "v1.2", "v01.2.3", "v1.2.3-", "v1.2.3 ", "release-1", ""):
            self.assertIsNone(version.parse(tag), tag)

    def test_semantic_precedence(self):
        tags = ["v1.0.0", "v1.1.0-rc.10", "v1.0.1", "v1.1.0-rc.2", "v1.1.0", "v1.1.0-beta", "v0.9.0"]
        self.assertEqual(sorted(tags, key=lambda tag: version.order(version.parse(tag))),
                         ["v0.9.0", "v1.0.0", "v1.0.1", "v1.1.0-beta", "v1.1.0-rc.2", "v1.1.0-rc.10", "v1.1.0"])

    def test_image_tags_per_channel(self):
        self.assertEqual(version.build("v1.2.3"), {"version": "1.2.3", "channel": "stable",
                                                   "tags": ["1.2.3", "1.2", "1", "latest"]})
        self.assertEqual(version.build("v2.0.0-rc.1"), {"version": "2.0.0-rc.1", "channel": "prerelease",
                                                        "tags": ["2.0.0-rc.1"]})
        self.assertEqual(version.build("", "0123456789abcdef"), {"version": "edge-0123456", "channel": "edge",
                                                                 "tags": ["edge"]})
        self.assertEqual(version.build("")["version"], "dev")
        with self.assertRaisesRegex(ValueError, "vX.Y.Z"):
            version.build("latest")

    def test_files_for_onedev_and_outputs_for_github(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix = os.path.join(folder, "release")
            output = Path(folder) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(version.main(["--tag", "v1.2.3", "--commit", "abc", "--image", "repo/app",
                                               "--write", prefix, "--github-output"]), 0)
            self.assertEqual(Path(prefix + ".version").read_text(), "1.2.3")
            self.assertEqual(Path(prefix + ".tags").read_text(),
                             "-t repo/app:1.2.3 -t repo/app:1.2 -t repo/app:1 -t repo/app:latest")
            self.assertEqual(output.read_text(), "version=1.2.3\nchannel=stable\n"
                                                 "tags=repo/app:1.2.3 repo/app:1.2 repo/app:1 repo/app:latest\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(version.main(["--tag", "v1"]), 2)

    def test_release_notes_come_from_the_changelog(self):
        self.assertEqual(version.notes(CHANGELOG, "v1.1.0"), "### 修复\n\n- b\n")
        self.assertEqual(version.notes(CHANGELOG, "v1.0.0"), "首个正式版本。\n")
        with self.assertRaisesRegex(ValueError, "v2.0.0"):
            version.notes(CHANGELOG, "v2.0.0")


class ReleaseTests(unittest.TestCase):
    def test_next_version(self):
        self.assertEqual(release.next_version("1.0.0", []), "v1.0.0")
        self.assertEqual(release.next_version("patch", []), "v0.0.1")
        existing = ["v1.0.0", "v1.0.1"]
        for spec, expected in (("patch", "v1.0.2"), ("minor", "v1.1.0"), ("major", "v2.0.0"), ("v1.1.0-rc.1", "v1.1.0-rc.1")):
            self.assertEqual(release.next_version(spec, existing), expected)
        self.assertEqual(release.next_version("minor", ["v1.0.0", "v1.1.0-rc.1"]), "v1.1.0", "after its pre-release")
        for spec, existing, error in (("1.0.1", ["v1.0.1"], "已经发布过"), ("1.0.0", ["v1.0.1"], "必须高于"),
                                      ("patch", ["v1.0.0", "v1.1.0-rc.1"], "必须高于"), ("next", [], "patch")):
            with self.assertRaisesRegex(ValueError, error):
                release.next_version(spec, existing)

    def test_notes_grouped_from_commit_subjects(self):
        subjects = ["fix(cloud): read every list", "feat: versions", "docs: readme", "release: v0.9.0",
                    "Merge branch 'x'", "perf: faster", "chore: tidy", "plain subject", "feat!: breaking"]
        self.assertEqual(release.section("v1.0.0", "2026-10-14", subjects),
                         "## v1.0.0 - 2026-10-14\n\n### 新功能\n\n- versions\n- breaking\n\n### 修复\n\n- read every list\n\n"
                         "### 性能\n\n- faster\n\n### 文档\n\n- readme\n\n### 其他\n\n- tidy\n- plain subject\n")

    def test_new_sections_go_above_older_ones(self):
        first = release.insert("", "## v1.0.0 - d\n\n- a\n")
        self.assertTrue(first.startswith(release.HEADER))
        self.assertTrue(first.endswith("\n\n## v1.0.0 - d\n\n- a\n"))
        second = release.insert(first, "## v1.0.1 - e\n\n- b\n")
        self.assertLess(second.index("## v1.0.1"), second.index("## v1.0.0"))
        self.assertEqual(version.notes(second, "v1.0.1"), "- b\n")
        self.assertEqual(version.notes(second, "v1.0.0"), "- a\n")


@unittest.skipUnless(shutil.which("git"), "needs git")
class ReleaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.remote, self.work = root / "origin.git", root / "work"
        subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote)], check=True)
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(self.work)], check=True)
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"),
                           ("tag.gpgsign", "false")):
            self.git("config", key, value)
        self.git("remote", "add", "origin", str(self.remote))
        self.commit("feat: first feature")
        self.commit("fix: first fix")
        self.git("push", "--quiet", "origin", "main")
        patches = (patch.object(version, "ROOT", self.work), patch.object(release, "CHANGELOG", self.work / "CHANGELOG.md"))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, subject):
        (self.work / "file.txt").write_text(subject, encoding="utf-8")
        self.git("add", "file.txt")
        self.git("commit", "--quiet", "-m", subject)

    def run_release(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_release_commits_tags_and_pushes(self):
        code, output = self.run_release("1.0.0", "--push", "--trailer", "Co-Authored-By: Someone <x@example.invalid>")
        self.assertEqual(code, 0, output)
        changelog = (self.work / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## v1.0.0 - ", changelog)
        self.assertIn("### 新功能\n\n- first feature", changelog)
        self.assertEqual(self.git("log", "-1", "--format=%B"),
                         "release: v1.0.0\n\nCo-Authored-By: Someone <x@example.invalid>")
        self.assertIn("- first fix", self.git("tag", "-l", "--format=%(contents)", "v1.0.0"))
        remote = subprocess.run(["git", "ls-remote", str(self.remote)], check=True, capture_output=True, text=True).stdout
        head = self.git("rev-parse", "HEAD")
        self.assertIn(f"{head}\trefs/heads/main", remote)
        self.assertIn("refs/tags/v1.0.0", remote)
        code, output = self.run_release("patch")
        self.assertEqual(code, 2)
        self.assertIn("没有新的提交", output)

    def test_dry_run_and_guards(self):
        self.commit("fix: second fix")
        code, output = self.run_release("minor", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("## v0.1.0", output)
        self.assertEqual(self.git("tag", "-l"), "", "a dry run creates nothing")
        (self.work / "file.txt").write_text("dirty", encoding="utf-8")
        self.assertIn("未提交", self.run_release("patch")[1])
        self.git("checkout", "--quiet", "--", "file.txt")
        self.git("checkout", "--quiet", "-b", "topic")
        self.assertIn("main 分支", self.run_release("patch")[1])


if __name__ == "__main__":
    unittest.main()
