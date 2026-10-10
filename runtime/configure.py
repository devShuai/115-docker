"""Prepare a persistent profile and optional private cookie extension."""
import json
import os
from pathlib import Path
import shutil


def write_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def configure():
    profile = Path(os.environ.get("PROFILE_DIR_IN_CONTAINER", "/etc/115"))
    preferences = profile / "Default" / "Preferences"
    content = json.loads(preferences.read_text()) if preferences.exists() else {}
    downloads = content.setdefault("download", {})
    downloads.setdefault("default_directory", "/opt/Downloads")
    downloads.setdefault("prompt_for_download", False)
    write_private_json(preferences, content)

    target = Path(os.environ.get("RUNTIME_DIR", "/run/115")) / "115Cookie"
    if target.exists():
        shutil.rmtree(target)
    if os.environ.get("ENABLE_COOKIE_EXTENSION", "false") != "true":
        return
    values = {name: os.environ.get("COOKIE_" + name, "")
              for name in ("CID", "SEID", "UID", "KID")}
    if not any(values.values()):
        raise ValueError("Cookie extension enabled but no COOKIE values supplied")
    shutil.copytree(Path(__file__).with_name("115Cookie"), target)
    config = target / "config.js"
    config.write_text("const COOKIE_VALUES = " + json.dumps(values) + ";\n")
    config.chmod(0o600)


if __name__ == "__main__":
    configure()
