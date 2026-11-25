"""Knowledge updates apart from App releases (E-154, H5.5).

Once a day HouseMind looks whether a newer knowledge bundle is published (device kinds,
countries, languages …). Only fetching: nothing about the house goes out (E-14). A bundle is
used only when
1. its signature is right (Ed25519, with the key that ships with the App),
2. every file matches the fingerprint in its manifest,
3. it fits this App version, and
4. its files load as knowledge.
Then it replaces the previous one in one go; the previous one is kept to go back to. The new
knowledge is used from the next start of HouseMind. An administrator can turn this off.

A bundle is a zip with ``manifest.json`` (``version``, ``min_app``, ``max_app``, ``files``:
path → sha256) and ``manifest.sig`` (the signature of manifest.json, hex).
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from housemind.i18n.catalog import load_languages
from housemind.knowledge.loader import KnowledgeError, load_catalog

# Where bundles are published and the key they are signed with. Both are set before the first
# Beta (B-43); until then nothing is fetched.
INDEX_URL: str | None = None
PUBLIC_KEY_HEX: str | None = None
ALLOWED = ("catalog/", "countries/", "languages/", "energy/", "skills/", "scenes/", "tested_ha")
MAX_BUNDLE = 5 * 1024 * 1024


class BundleError(Exception):
    """The bundle cannot be trusted or used; the reason is in plain words."""


@dataclass(frozen=True, slots=True)
class Installed:
    version: str
    kinds: int
    languages: tuple[str, ...]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def verify(data: bytes, public_key_hex: str, app_version: str) -> dict[str, Any]:
    """Check a bundle completely; returns its manifest, or raises BundleError."""
    if len(data) > MAX_BUNDLE:
        raise BundleError("too_big")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        manifest_raw = archive.read("manifest.json")
        signature = bytes.fromhex(archive.read("manifest.sig").decode().strip())
    except (zipfile.BadZipFile, KeyError, ValueError) as err:
        raise BundleError("not_a_bundle") from err
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            signature, manifest_raw
        )
    except (InvalidSignature, ValueError) as err:
        raise BundleError("bad_signature") from err
    manifest: dict[str, Any] = json.loads(manifest_raw)
    files = manifest.get("files")
    if not isinstance(files, dict) or not isinstance(manifest.get("version"), str):
        raise BundleError("bad_manifest")
    names = {n for n in archive.namelist() if not n.endswith("/")} - {
        "manifest.json",
        "manifest.sig",
    }
    if names != set(files):
        raise BundleError("files_differ")
    for name, digest in files.items():
        if not name.startswith(ALLOWED) or ".." in Path(name).parts:
            raise BundleError("bad_path")
        if hashlib.sha256(archive.read(name)).hexdigest() != digest:
            raise BundleError("files_differ")
    app = _version(app_version)
    if _version(str(manifest.get("min_app", "0"))) > app:
        raise BundleError("app_too_old")
    if manifest.get("max_app") and _version(str(manifest["max_app"])) < app:
        raise BundleError("app_too_new")
    return manifest


def install(data: bytes, manifest: dict[str, Any], target: Path) -> Installed:
    """Unpack into a fresh folder, check it loads as knowledge, then swap in one go. A bundle
    that is not newer than the installed one is refused: an old signed bundle can never bring
    back what a later one made stricter (QA 2, B-168)."""
    current = installed_version(target)
    if current is not None and _version(str(manifest["version"])) <= _version(current):
        raise BundleError("not_newer")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        staging = Path(tmp) / "bundle"
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in manifest["files"]:
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
        (staging / "manifest.json").write_text(json.dumps(manifest))
        try:
            catalog, _ = load_catalog([staging / "catalog"])
            languages = load_languages(staging / "languages")
        except KnowledgeError as err:
            raise BundleError("does_not_load") from err
        previous = target.with_name(target.name + ".prev")
        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.rename(previous)
        staging.rename(target)
    return Installed(
        str(manifest["version"]), len(catalog.kinds), tuple(sorted(languages.languages))
    )


def installed_version(target: Path) -> str | None:
    try:
        return str(json.loads((target / "manifest.json").read_text())["version"])
    except (OSError, ValueError, KeyError):
        return None


async def fetch(index_url: str) -> tuple[str, bytes] | None:
    """The newest bundle: the index says {"version", "url"}. Only fetching (E-14)."""
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(index_url) as response:
            if response.status != 200:
                return None
            index = await response.json(content_type=None)
        if not isinstance(index, dict) or not isinstance(index.get("url"), str):
            return None
        async with session.get(index["url"]) as response:
            if response.status != 200:
                return None
            return str(index.get("version", "")), await response.read()
