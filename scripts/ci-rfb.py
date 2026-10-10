"""Exercise real RFB authentication and SetDesktopSize in the CI container."""
import os
import re
import socket
import struct
import subprocess
import time


def read_exact(connection, size):
    result = bytearray()
    while len(result) < size:
        data = connection.recv(size - len(result))
        if not data:
            raise RuntimeError('RFB connection closed unexpectedly')
        result.extend(data)
    return bytes(result)


def authenticate(connection, password):
    version = read_exact(connection, 12)
    if version != b'RFB 003.008\n':
        raise RuntimeError(f'Unexpected RFB version: {version!r}')
    connection.sendall(version)
    count = read_exact(connection, 1)[0]
    if not count or 2 not in read_exact(connection, count):
        raise RuntimeError('Server does not offer VNC password authentication')
    connection.sendall(b'\x02')
    challenge = read_exact(connection, 16)
    # VNC auth reverses bits in each password byte before DES ECB encryption.
    key = bytes(int(f'{value:08b}'[::-1], 2) for value in password.encode('ascii')[:8])
    response = subprocess.check_output([
        'openssl', 'enc', '-des-ecb', '-K', key.hex(), '-nopad', '-nosalt',
        '-provider', 'legacy', '-provider', 'default',
    ], input=challenge)
    connection.sendall(response)
    if struct.unpack('!I', read_exact(connection, 4))[0] != 0:
        raise RuntimeError('RFB authentication failed')
    connection.sendall(b'\x01')  # Share the desktop with the health check/client.
    header = read_exact(connection, 24)
    bits_per_pixel = header[4]
    name_size = struct.unpack('!I', header[20:24])[0]
    if name_size > 65536:
        raise RuntimeError('Invalid RFB server name length')
    read_exact(connection, name_size)
    return bits_per_pixel


def get_screen(connection, bits_per_pixel):
    # Advertise raw pixels plus ExtendedDesktopSize (-308), as noVNC does.
    connection.sendall(struct.pack('!BBHii', 2, 0, 2, 0, -308))
    connection.sendall(struct.pack('!BBHHHH', 3, 0, 0, 0, 1, 1))
    for _ in range(20):
        message = read_exact(connection, 1)[0]
        if message == 2:  # Bell
            continue
        if message == 3:  # ServerCutText
            header = read_exact(connection, 7)
            length = struct.unpack('!I', header[3:])[0]
            if length > 1048576:
                raise RuntimeError('Unexpected clipboard size')
            read_exact(connection, length)
            continue
        if message != 0:
            raise RuntimeError(f'Unexpected RFB message: {message}')
        rectangles = struct.unpack('!BH', read_exact(connection, 3))[1]
        screen = None
        for _ in range(rectangles):
            x, y, width, height, encoding = struct.unpack('!HHHHi', read_exact(connection, 12))
            if encoding == -308:
                count = read_exact(connection, 4)[0]
                for number in range(count):
                    screen_id, _, _, _, _, flags = struct.unpack('!IHHHHI', read_exact(connection, 16))
                    if number == 0:
                        screen = (screen_id, flags)
            elif encoding == 0:
                size = width * height * (bits_per_pixel // 8)
                if size > 16777216:
                    raise RuntimeError('Unexpected initial frame size')
                read_exact(connection, size)
            else:
                raise RuntimeError(f'Unexpected RFB encoding: {encoding}')
        if screen is not None:
            return screen
    raise RuntimeError('Server did not advertise ExtendedDesktopSize')


def verify_resize(connection, width, height, screen):
    screen_id, flags = screen
    connection.sendall(struct.pack('!BBHHBBIHHHHI', 251, 0, width, height, 1, 0,
                                   screen_id, 0, 0, width, height, flags))
    for _ in range(50):
        output = subprocess.check_output(['xrandr', '--current'], text=True)
        if re.search(rf'current {width} x {height}\b', output):
            print(f'PASS: RFB SetDesktopSize -> {width}x{height}')
            return
        time.sleep(0.1)
    raise RuntimeError(f'Remote resize failed: expected {width}x{height}\n{output}')


def main():
    port = 5900 + int(os.environ.get('DISPLAY', ':115').removeprefix(':'))
    with socket.create_connection(('127.0.0.1', port), timeout=10) as connection:
        bpp = authenticate(connection, os.environ['PASSWORD'])
        screen = get_screen(connection, bpp)
        for width, height in ((1280, 720), (1920, 1080), (1600, 900)):
            verify_resize(connection, width, height, screen)


if __name__ == '__main__':
    main()
