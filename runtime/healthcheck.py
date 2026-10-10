"""Check HTTP, the RFB greeting, X11, and the window manager."""
import os
from pathlib import Path
import socket
import subprocess
import urllib.request


def check():
    display = os.environ.get("DISPLAY", ":115")
    port = 5900 + int(display[1:])
    with urllib.request.urlopen("http://127.0.0.1:1150/vnc.html", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("noVNC HTTP failed")
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        if not connection.recv(12).startswith(b"RFB "):
            raise RuntimeError("VNC did not send an RFB greeting")
    subprocess.run(["xdpyinfo", "-display", display], check=True,
                   timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for entry in Path("/proc").glob("[0-9]*/comm"):
        try:
            if entry.read_text().strip() == "openbox":
                return
        except OSError:
            continue
    raise RuntimeError("Openbox is not running")


if __name__ == "__main__":
    check()
