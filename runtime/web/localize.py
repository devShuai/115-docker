"""Load zh-default.js first in a web client page and mark the page as Chinese."""
from pathlib import Path
import sys

MARKER = '<script src="zh-default.js"></script>'


def localize(html):
    if MARKER in html:
        return html
    # Both vendored clients ship exactly one <head>; refuse anything else so an
    # upstream layout change fails the image build instead of silently skipping.
    if html.count("<head>") != 1 or '<html lang="en"' not in html:
        raise ValueError("Unexpected web client page layout")
    html = html.replace('<html lang="en"', '<html lang="zh-CN"', 1)
    return html.replace("<head>", "<head>" + MARKER + '<script src="update-config.js" defer></script><script src="update-link.js" defer></script>', 1)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        path = Path(name)
        path.write_text(localize(path.read_text(encoding="utf-8")), encoding="utf-8")
