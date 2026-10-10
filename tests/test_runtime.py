from html.parser import HTMLParser
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure", ROOT / "runtime/configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
spec = importlib.util.spec_from_file_location("localize", ROOT / "runtime/web/localize.py")
localize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(localize)
BASH = os.environ.get("TEST_BASH", shutil.which("bash"))
version = subprocess.check_output([BASH, "-c", "printf '%s' \"$BASH_VERSION\""], text=True)
MODERN_BASH = tuple(int(v) for v in version.split(".")[:2]) >= (4, 3)
KASM_DEB = ROOT / "vendor/kasmvncserver-trixie-1.5.0-amd64.deb"
KASM_WEB = ROOT / "vendor/kasmvnc-web-475ecfa53565.tar.gz"


def deb_member(path, member):
    """Read one file from a .deb (an ar archive holding data.tar.xz)."""
    data = path.read_bytes()
    offset = 8
    while offset < len(data):
        header = data[offset:offset + 60]
        size = int(header[48:58])
        if header[:16].decode().strip().rstrip("/").startswith("data.tar"):
            body = data[offset + 60:offset + 60 + size]
            with tarfile.open(fileobj=io.BytesIO(body)) as archive:
                return archive.extractfile(member).read().decode()
        offset += 60 + size + (size & 1)
    raise FileNotFoundError(member)


class VisibleStrings(HTMLParser):
    """Strings that the web client's l10n.translateDOM() looks up."""
    ATTRIBUTES = {"title": None, "alt": {"area", "img", "input"},
                  "placeholder": {"input", "textarea"},
                  "label": {"menuitem", "menu", "optgroup", "option", "track"}}
    VOID = {"input", "img", "meta", "link", "br", "hr", "source", "area"}

    def __init__(self):
        super().__init__()
        self.strings, self.stack, self.in_body, self.code = set(), [], False, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.in_body = self.in_body or tag == "body"
        self.code += tag in ("script", "style")
        parent = self.stack[-1][1] if self.stack else True
        enabled = {"no": False, "": True, "yes": True}.get(attrs.get("translate"), parent)
        if tag not in self.VOID:
            self.stack.append((tag, enabled))
        if self.in_body and enabled and not self.code:
            for name, tags in self.ATTRIBUTES.items():
                if attrs.get(name) and (tags is None or tag in tags):
                    self.strings.add(attrs[name].strip())
            if tag == "input" and attrs.get("type") in ("reset", "button", "submit") and attrs.get("value"):
                self.strings.add(attrs["value"].strip())

    def handle_endtag(self, tag):
        self.code -= tag in ("script", "style")
        while self.stack and self.stack.pop()[0] != tag:
            pass

    def handle_data(self, data):
        enabled = all(flag for _, flag in self.stack)
        if self.in_body and enabled and not self.code and data.strip():
            self.strings.add(data.strip())


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.profile = self.directory / "profile"
        self.run = self.directory / "run"
        self.environment = {
            "PROFILE_DIR_IN_CONTAINER": str(self.profile),
            "RUNTIME_DIR": str(self.run),
            "ENABLE_COOKIE_EXTENSION": "false",
        }

    def execute(self, **values):
        with patch.dict(os.environ, {**self.environment, **values}, clear=True):
            configure.configure()

    def test_new_profile_has_persistent_download_directory(self):
        self.execute()
        path = self.profile / "Default/Preferences"
        self.assertEqual(json.loads(path.read_text())["download"]["default_directory"], "/opt/Downloads")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.run / "115Cookie").exists())

    def test_existing_preferences_and_download_choice_are_preserved(self):
        path = self.profile / "Default/Preferences"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"download": {"default_directory": "/chosen"}, "custom": [1, 2]}))
        self.execute()
        content = json.loads(path.read_text())
        self.assertEqual(content["download"]["default_directory"], "/chosen")
        self.assertEqual(content["custom"], [1, 2])

    def test_invalid_preferences_are_not_overwritten(self):
        path = self.profile / "Default/Preferences"
        path.parent.mkdir(parents=True)
        path.write_text("broken profile")
        with self.assertRaises(json.JSONDecodeError):
            self.execute()
        self.assertEqual(path.read_text(), "broken profile")

    def test_cookie_values_are_encoded_and_removed_when_disabled(self):
        value = "a'\"\\\n|b"
        self.execute(ENABLE_COOKIE_EXTENSION="true", COOKIE_UID=value)
        path = self.run / "115Cookie/config.js"
        payload = path.read_text().removeprefix("const COOKIE_VALUES = ").removesuffix(";\n")
        self.assertEqual(json.loads(payload)["UID"], value)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.execute()
        self.assertFalse(path.parent.exists())

    def test_cookie_extension_requires_values(self):
        with self.assertRaisesRegex(ValueError, "no COOKIE values"):
            self.execute(ENABLE_COOKIE_EXTENSION="true")

    def test_invalid_password_is_rejected_before_starting_services(self):
        for value in ("", "123456", "morethan8", "1234\n567"):
            for script in ("start.sh", "entrypoint.sh"):
                result = subprocess.run([BASH, str(ROOT / "runtime" / script)],
                                        env={**os.environ, **self.environment, "REMOTE_BACKEND": "legacy", "PASSWORD": value},
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exactly 8", result.stderr)
                self.assertNotIn("dbus", result.stderr)
                self.assertFalse(self.run.exists())

    def test_entrypoint_prepares_private_runtime_directory_before_dbus(self):
        mock_bin = self.directory / "bin"
        mock_bin.mkdir()
        runner = mock_bin / "dbus-run-session"
        runner.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, sys\n"
            "p = pathlib.Path(os.environ['XDG_RUNTIME_DIR'])\n"
            "print(json.dumps({'mode': p.stat().st_mode & 0o777, 'args': sys.argv[1:],\n"
            "'profile': pathlib.Path(os.environ['PROFILE_DIR_IN_CONTAINER']).is_dir()}))\n"
        )
        runner.chmod(0o755)
        environment = {**os.environ, **self.environment, "PASSWORD": "Check123",
                       "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run([BASH, str(ROOT / "runtime/entrypoint.sh")],
                                env=environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = json.loads(result.stdout)
        self.assertEqual(content["mode"], 0o700)
        self.assertTrue(content["profile"])
        self.assertEqual(content["args"], ["--", str(ROOT / "runtime/start.sh")])
        # Existing runtime directories must also have their permissions corrected.
        self.run.chmod(0o755)
        result = subprocess.run([BASH, str(ROOT / "runtime/entrypoint.sh")],
                                env=environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(json.loads(result.stdout)["mode"], 0o700)

    def test_time_zone_defaults_to_shanghai_and_rejects_unknown_names(self):
        mock_bin = self.directory / "bin"
        mock_bin.mkdir()
        runner = mock_bin / "dbus-run-session"
        runner.write_text("#!" + sys.executable + "\nimport os\nprint(os.environ['TZ'])\n")
        runner.chmod(0o755)
        base = {key: value for key, value in os.environ.items() if key != "TZ"}
        base.update(self.environment, PASSWORD="Check123",
                    PATH=str(mock_bin) + os.pathsep + os.environ["PATH"])

        def entrypoint(**values):
            return subprocess.run([BASH, str(ROOT / "runtime/entrypoint.sh")], env={**base, **values},
                                  capture_output=True, text=True, timeout=5)

        # Container UIs may omit TZ or pass it empty; both must not become UTC.
        for values, expected in (({}, "Asia/Shanghai"), ({"TZ": ""}, "Asia/Shanghai"), ({"TZ": "UTC"}, "UTC")):
            result = entrypoint(**values)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        shutil.rmtree(self.run)
        for value in ("../../etc/passwd", "Asia/Nowhere", "Asia/Shanghai\n", "/etc/localtime"):
            result = entrypoint(TZ=value)
            self.assertNotEqual(result.returncode, 0, value)
            self.assertIn("IANA time zone", result.stderr)
            self.assertFalse(self.run.exists())

    def test_vendored_novnc_supports_mandatory_resize(self):
        mandatory = json.loads((ROOT / "runtime/novnc/mandatory.json").read_text())
        self.assertEqual(mandatory["resize"], "remote")
        with tarfile.open(ROOT / "vendor/novnc-1.6.0.tar.gz") as archive:
            html = archive.extractfile("noVNC-1.6.0/vnc.html").read().decode()
            ui = archive.extractfile("noVNC-1.6.0/app/ui.js").read().decode()
        self.assertIn("mandatory.json", html)
        self.assertIn("mandatory: mandatory", html)
        self.assertIn("UI.customSettings.mandatory", ui)

    def test_theme_and_openbox_xml_are_present(self):
        for name in ("rc.xml", "menu.xml"):
            ET.parse(ROOT / "rootfs/opt/.config/openbox" / name)
        self.assertTrue((ROOT / "rootfs/usr/share/themes/115Dark/openbox-3/themerc").is_file())
        ET.parse(ROOT / "rootfs/usr/share/backgrounds/115/desktop.svg")


class LocalizationTests(unittest.TestCase):
    # Technical names that the client shows unchanged in every language.
    UNTRANSLATED = {"Canvas2D", "WebGL", "Ctrl+Shift+"}

    def test_web_clients_load_chinese_defaults_first(self):
        with tarfile.open(ROOT / "vendor/novnc-1.6.0.tar.gz") as archive:
            novnc = archive.extractfile("noVNC-1.6.0/vnc.html").read().decode()
        kasm = deb_member(KASM_DEB, "./usr/share/kasmvnc/www/vnc.html")
        for name, page in (("noVNC", novnc), ("KasmVNC", kasm)):
            result = localize.localize(page)
            self.assertEqual(result.count(localize.MARKER), 1, name)
            # Classic scripts run before the clients' deferred module scripts.
            self.assertEqual(result.index("<script"), result.index(localize.MARKER), name)
            self.assertIn('<html lang="zh-CN"', result, name)
            self.assertEqual(localize.localize(result), result, name)
        with self.assertRaises(ValueError):
            localize.localize('<html lang="en"><head></head><body><head></head></body></html>')
        script = (ROOT / "runtime/web/zh-default.js").read_text()
        self.assertIn('"zh-CN"', script)
        self.assertIn('getItem("enable_ime") === null', script)

    def test_kasm_chinese_translation_covers_visible_ui(self):
        with tarfile.open(KASM_WEB) as archive:
            root = archive.getnames()[0].split("/")[0]
            page = archive.extractfile(f"{root}/index.html").read().decode()
            ui = archive.extractfile(f"{root}/app/ui.js").read().decode()
            stock = json.load(archive.extractfile(f"{root}/app/locale/zh_CN.json"))
        parser = VisibleStrings()
        parser.feed(page)
        wanted = {text for text in parser.strings | set(re.findall(r'\b_\("([^"]+)"\)', ui))
                  if re.search(r"[A-Za-z]{2}", text)}
        overlay = json.loads((ROOT / "runtime/kasmvnc/zh_CN.json").read_text())
        self.assertLessEqual(set(stock), set(overlay), "the overlay must keep every upstream entry")
        self.assertEqual({text for text in wanted if text not in overlay}, self.UNTRANSLATED)
        for source, target in overlay.items():
            self.assertTrue(target.strip(), source)
            self.assertNotIn("「", target, source)
        self.assertIn("IME Input Mode", wanted)

    def test_disconnected_page_is_chinese_and_reconnects(self):
        page = (ROOT / "runtime/kasmvnc/disconnected.html").read_text()
        self.assertIn('<html lang="zh-CN">', page)
        self.assertIn('href="/"', page)
        self.assertIsNone(re.search(r">[^<]*[A-Za-z]{4,}[^<]*<", page.split("</style>")[1]))


@unittest.skipUnless(MODERN_BASH, "process management needs Bash 4.3 or later")
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        blocker = self.directory / "service.py"
        blocker.write_text(
            "#!" + sys.executable + "\n"
            "import os, pathlib, signal, sys, time\n"
            "name = pathlib.Path(sys.argv[0]).name\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "(pathlib.Path(os.environ['PID_DIRECTORY']) / (name + '.pid')).write_text(str(os.getpid()))\n"
            "if os.environ.get('FAIL_SERVICE') == name:\n"
            "    time.sleep(0.3); sys.exit(7)\n"
            "while True: time.sleep(0.05)\n"
        )
        blocker.chmod(0o755)
        for command in ("Xtigervnc", "Xkasmvnc", "openbox", "pcmanfm", "tint2"):
            (self.bin / command).symlink_to(blocker)
        for command, source in {"tigervncpasswd": "#!/bin/sh\ncat\n", "kasmvncpasswd": '#!/bin/sh\ncat > "$6"\n', "xdpyinfo": "#!/bin/sh\nexit 0\n"}.items():
            path = self.bin / command
            path.write_text(source)
            path.chmod(0o755)
        self.environment = {
            **os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PASSWORD": "Check123", "REMOTE_BACKEND": "legacy", "DISPLAY": ":9915", "AUTO_START": "false",
            "ENABLE_COOKIE_EXTENSION": "false", "PID_DIRECTORY": str(self.directory),
            "RUNTIME_DIR": str(self.directory / "run"),
            "PROFILE_DIR_IN_CONTAINER": str(self.directory / "profile"),
            "CONFIGURE_SCRIPT": str(ROOT / "runtime/configure.py"),
            "WEBSOCKIFY_RUN": str(blocker),
        }

    def launch(self, **environment):
        process = subprocess.Popen([BASH, str(ROOT / "runtime/start.sh")],
                                   env={**self.environment, **environment},
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def assert_children_stopped(self):
        for path in self.directory.glob("*.pid"):
            pid = int(path.read_text())
            with self.assertRaises(ProcessLookupError, msg=path.name):
                os.kill(pid, 0)

    def test_critical_service_failure_exits_and_cleans_up(self):
        process = self.launch(FAIL_SERVICE="openbox")
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 1, output)
        self.assertIn("shutting down", output)
        self.assert_children_stopped()

    def test_sigterm_stops_all_services_cleanly(self):
        process = self.launch()
        deadline = time.monotonic() + 5
        while len(list(self.directory.glob("*.pid"))) < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(list(self.directory.glob("*.pid"))), 5)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, output)
        self.assert_children_stopped()

    def test_kasm_does_not_start_websockify_and_cleans_up(self):
        process = self.launch(REMOTE_BACKEND="kasm", FAIL_SERVICE="openbox")
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 1, output)
        self.assertIn("Started KasmVNC", output)
        self.assertNotIn("Started noVNC", output)
        self.assert_children_stopped()


if __name__ == "__main__":
    unittest.main()
