#!/usr/bin/env python3
"""Verify local archives, or restore them from the URLs in the pinned manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="download missing archives")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "vendor/manifest.json").read_text())
    for asset in manifest["assets"]:
        target = ROOT / "vendor" / asset["name"]
        if not target.exists() and args.fetch:
            temporary = target.with_suffix(target.suffix + ".part")
            try:
                request = urllib.request.Request(asset["url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                if checksum(temporary) != asset["sha256"]:
                    raise ValueError("Downloaded checksum mismatch: " + asset["name"])
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if not target.is_file() or checksum(target) != asset["sha256"]:
            raise SystemExit("Missing or invalid archive: " + asset["name"])
        print("Verified " + asset["name"])
    expected = "".join(asset["sha256"] + "  " + asset["name"] + "\n"
                       for asset in manifest["assets"])
    if (ROOT / "vendor/SHA256SUMS").read_text() != expected:
        raise SystemExit("SHA256SUMS does not match manifest.json")


if __name__ == "__main__":
    main()
