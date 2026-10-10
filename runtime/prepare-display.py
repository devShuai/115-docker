"""Remove only stale X11 state left in a container's writable layer after stop."""
import errno
import os
from pathlib import Path
import re
import socket


def prepare(display, temporary=Path('/tmp'), proc=Path('/proc')):
    if not re.fullmatch(r':[0-9]+', display):
        raise ValueError('Invalid X11 display')
    number = str(int(display[1:]))
    address = temporary / '.X11-unix' / ('X' + number)
    # A PID from a previous container start can have been reused by an unrelated
    # process. Inspect X server command lines and both X11 socket namespaces.
    for path in proc.glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().split(b'\0')
            name = args[0].split(b'/')[-1]
            if name in (b'Xkasmvnc', b'Xtigervnc', b'Xvnc', b'Xorg', b'X') and display.encode() in args:
                raise RuntimeError('X11 display is already running; refusing to remove its state')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    for endpoint in (str(address), '\0' + str(address)):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            try:
                connection.connect(endpoint)
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EINVAL, errno.EAFNOSUPPORT):
                    raise RuntimeError('Cannot verify X11 socket state; refusing cleanup') from error
            else:
                raise RuntimeError('X11 display accepts connections; refusing to remove its state')
    for path in (temporary / ('.X' + number + '-lock'), address):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    prepare(os.environ.get('DISPLAY', ':115'))
