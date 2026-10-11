"""PBKDF2 credentials for the download API. The clear password stays in the supervisor.

Usage: printf '%s' "$PASSWORD" | python3 auth.py USER > api-auth
"""
import hashlib
import hmac
import os
from pathlib import Path
import sys

ITERATIONS = 200_000


def encode(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify(password, encoded):
    try:
        scheme, iterations, salt, expected = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def load(path):
    user, _, encoded = Path(path).read_text(encoding="utf-8").strip().partition(":")
    if not user or not encoded:
        raise ValueError("Invalid download API credential file")
    return user, encoded


if __name__ == "__main__":
    print(sys.argv[1] + ":" + encode(sys.stdin.read()))
