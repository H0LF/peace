"""Checking a release before it runs. Each one is signed with a key kept only on the maintainer's computer; the matching
public key below ships inside PEACE, so a download is trusted because of who signed it, not where it came from.

The signature covers the version too, so an old signed release cannot be passed off as a new one.
    python -m peace.release keygen <private key file>      (once, by the maintainer)
    python -m peace.release sign <bundle> <version> <private key file>
    python -m peace.release unpack <bundle> <signature> <version> <empty directory>
"""

import base64
import hashlib
import os
import re
import sys
import tarfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

PUBLIC_KEY = "X3LAetJPv2svcQhYzmu3VSJJQhD44OoYTeOKsDLeIYI="
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class BadRelease(Exception):
    """Do not install this."""


def _message(bundle: Path, version: str) -> bytes:
    return f"peace-release-v1:{version}:{hashlib.sha256(bundle.read_bytes()).hexdigest()}".encode()


def sign(bundle: Path, version: str, key_file: Path) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_file.read_text()))
    return base64.b64encode(key.sign(_message(bundle, version)))


def verify(bundle: Path, signature: Path, version: str, public_key: str | None = None) -> None:
    if not VERSION.match(version):
        raise BadRelease(f"invalid version: {version}")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or PUBLIC_KEY))
        key.verify(base64.b64decode(signature.read_text()), _message(bundle, version))
    except (InvalidSignature, ValueError, OSError) as e:
        raise BadRelease("signature does not match") from e


def unpack(bundle: Path, signature: Path, version: str, dest: Path, public_key: str | None = None) -> None:
    """Verify first, then extract one top-level folder's contents into `dest`, refusing anything that climbs out of it."""
    verify(bundle, signature, version, public_key)
    with tarfile.open(bundle) as tar:
        tar.extractall(dest, filter="data")  # the "data" filter raises on absolute paths, ../ and links leaving dest
    meta = dest / "pyproject.toml"
    if not meta.is_file() or f'version = "{version}"' not in meta.read_text():
        raise BadRelease("bundle is not this version")


def keygen(key_file: Path) -> str:
    if key_file.exists():
        raise SystemExit(f"{key_file} already exists: replacing it would strand every installed PEACE")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    key_file.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(base64.b64encode(raw).decode())
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def main(argv: list[str]) -> int:
    match argv:
        case ["keygen", key]:
            print(keygen(Path(key)))
        case ["sign", bundle, version, key]:
            sys.stdout.write(sign(Path(bundle), version, Path(key)).decode())
        case ["unpack", bundle, signature, version, dest]:
            try:
                unpack(Path(bundle), Path(signature), version, Path(dest))
            except (BadRelease, tarfile.TarError) as e:
                print(f"release refused: {e}", file=sys.stderr)
                return 1
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
