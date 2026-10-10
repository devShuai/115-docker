"""Apply the narrow-screen DPI fix to the pinned KasmVNC 1.5.0 web bundle."""
from pathlib import Path
import sys

# Upstream treats every high-DPI viewport narrower than 1000 CSS pixels as a
# small physical device. A desktop window or browser zoom can meet that rule,
# too, producing an unexpected resolution even with Native Resolution off.
# Keep explicit HiDPI support; skip only this automatic mobile heuristic.
ORIGINAL = (
    "else if(((m=this._renderer)==null?void 0:m.antiAliasing)===0"
    "&&this._screens[u].pixelRatio>1&&h<1e3&h>0)"
)
REPLACEMENT = "else if(false/* 115: use explicit HiDPI, never infer it from viewport width */)"


def patch_bundle(source):
    if source.count(REPLACEMENT) == 1 and ORIGINAL not in source:
        return source
    if source.count(ORIGINAL) != 1 or REPLACEMENT in source:
        raise ValueError("Unexpected KasmVNC DPI implementation; review the pinned web bundle")
    return source.replace(ORIGINAL, REPLACEMENT, 1)


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    bundles = list((directory / "assets").glob("ui-*.js"))
    if len(bundles) != 1:
        raise ValueError("Expected exactly one KasmVNC web UI bundle")
    path = bundles[0]
    path.write_text(patch_bundle(path.read_text(encoding="utf-8")), encoding="utf-8")
