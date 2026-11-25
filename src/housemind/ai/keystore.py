"""Encrypted keys for AI providers (B-19, E-86).

Keys are stored encrypted in the App data, so they travel with the (encrypted) Home Assistant
backup and a restore on new hardware just works. They never appear in logs, exports or the
diagnostics package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class KeyStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._key_file = directory / "key"
        self._store_file = directory / "store"

    def __repr__(self) -> str:
        return "KeyStore(…)"

    def _fernet(self) -> Fernet:
        self._dir.mkdir(parents=True, exist_ok=True)
        if not self._key_file.exists():
            self._key_file.write_bytes(Fernet.generate_key())
            os.chmod(self._key_file, 0o600)
        return Fernet(self._key_file.read_bytes())

    def _load(self) -> dict[str, str]:
        if not self._store_file.exists():
            return {}
        try:
            raw = self._fernet().decrypt(self._store_file.read_bytes())
        except InvalidToken:
            return {}
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()}

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def set(self, name: str, value: str | None) -> None:
        data = self._load()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        token = self._fernet().encrypt(json.dumps(data).encode())
        self._store_file.write_bytes(token)
        os.chmod(self._store_file, 0o600)

    def has(self, name: str) -> bool:
        return name in self._load()
