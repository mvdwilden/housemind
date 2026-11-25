"""Make and sign a knowledge bundle (E-154), for the people who maintain HouseMind.

    python tools/sign_bundle.py keygen <private-key-file>
    python tools/sign_bundle.py build <knowledge-dir> <version> <private-key-file> <out.zip>

The private key never goes into the repository; the public key goes into
``housemind/knowledge/updates.py`` (PUBLIC_KEY_HEX).
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FOLDERS = ("catalog", "countries", "languages", "energy", "skills", "scenes")


def keygen(path: Path) -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    path.write_text(raw.hex())
    path.chmod(0o600)
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return public.hex()


def build(root: Path, version: str, key_hex: str, min_app: str = "0.1.0") -> bytes:
    files: dict[str, bytes] = {}
    for folder in FOLDERS:
        for path in sorted((root / folder).rglob("*.yaml")) if (root / folder).exists() else []:
            files[str(path.relative_to(root))] = path.read_bytes()
    manifest = {
        "version": version,
        "min_app": min_app,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    raw = json.dumps(manifest, sort_keys=True).encode()
    signature = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex)).sign(raw)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", raw)
        archive.writestr("manifest.sig", signature.hex())
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


if __name__ == "__main__":
    if sys.argv[1:2] == ["keygen"]:
        print(f"Public key: {keygen(Path(sys.argv[2]))}")
    elif sys.argv[1:2] == ["build"]:
        knowledge, version, key_file, target = sys.argv[2:6]
        data = build(Path(knowledge), version, Path(key_file).read_text().strip())
        Path(target).write_bytes(data)
        print(f"Bundle {version}: {target}")
    else:
        raise SystemExit(__doc__)
