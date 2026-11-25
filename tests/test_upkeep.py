"""Looking after HouseMind: signed knowledge bundles (E-154) and the diagnostics package (E-98)."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from housemind import __version__
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge import updates
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from housemind.support import diagnostics

ROOT = Path(__file__).resolve().parents[1]


def _tool() -> Any:
    spec = importlib.util.spec_from_file_location("sign_bundle", ROOT / "tools" / "sign_bundle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def keypair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    ).hex()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    return private, public


def test_only_a_correctly_signed_complete_bundle_is_used(tmp_path: Path) -> None:
    private, public = keypair()
    bundle = _tool().build(ROOT / "knowledge", "2026.10.1", private)
    manifest = updates.verify(bundle, public, __version__)
    assert manifest["version"] == "2026.10.1" and any(
        n.startswith("catalog/") for n in manifest["files"]
    )
    _, other = keypair()
    with pytest.raises(updates.BundleError, match="bad_signature"):
        updates.verify(bundle, other, __version__)
    # A changed file inside a correctly signed manifest.
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bundle)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "languages/nl.yaml":
                data += b"\nextra: true\n"
            dst.writestr(name, data)
    with pytest.raises(updates.BundleError, match="files_differ"):
        updates.verify(out.getvalue(), public, __version__)
    newer = _tool().build(ROOT / "knowledge", "2026.10.2", private)
    newer_manifest = json.loads(zipfile.ZipFile(io.BytesIO(newer)).read("manifest.json"))
    assert newer_manifest["min_app"] == "0.1.0"
    # Installed in one go; the previous one is kept to go back to.
    target = tmp_path / "knowledge-update"
    first = updates.install(bundle, manifest, target)
    assert first.version == "2026.10.1" and first.kinds > 40 and "nl" in first.languages
    updates.install(newer, updates.verify(newer, public, __version__), target)
    assert updates.installed_version(target) == "2026.10.2"
    assert updates.installed_version(target.with_name("knowledge-update.prev")) == "2026.10.1"
    # An older signed bundle never comes back over a newer one (QA 2, B-168).
    with pytest.raises(updates.BundleError, match="not_newer"):
        updates.install(bundle, manifest, target)
    assert updates.installed_version(target) == "2026.10.2"


async def test_the_daily_check_installs_and_tells_administrators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = keypair()
    bundle = _tool().build(ROOT / "knowledge", "2026.10.1", private)

    async def fake_fetch(url: str) -> tuple[str, bytes]:
        return "2026.10.1", bundle

    monkeypatch.setattr(updates, "fetch", fake_fetch)
    clock = SimulatedClock(datetime(2026, 10, 19, 8, 0, tzinfo=UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.world is not None
    snapshot = hm.world.snapshot(clock.now())
    assert await hm.check_knowledge("https://example.invalid/index.json", public, snapshot) == (
        "installed"
    )
    assert updates.installed_version(hm.knowledge_target) == "2026.10.1"
    assert await hm.check_knowledge("https://example.invalid/index.json", public, snapshot) == (
        "current"
    )
    assert any(str(n["topic"]).startswith("knowledge:") for n in await hm.notifications())
    # Until bundles are published, the daily check fetches nothing at all.
    assert updates.INDEX_URL is None
    await hm.stop()


def test_anonymising_logs() -> None:
    line = (
        "Light light.kitchen_ceiling in Woonkamer by Anna at 192.168.1.20 "
        "see https://x.example/y mail me@example.com; housemind.runtime failed"
    )
    out = diagnostics.anonymise(line, ["Woonkamer", "Anna"])
    assert "kitchen_ceiling" not in out and "light.device_1" in out
    assert "Woonkamer" not in out and "Anna" not in out
    assert "<address>" in out and "<link>" in out and "<email>" in out
    assert "housemind.runtime" in out


async def test_the_diagnostics_package_is_for_administrators(tmp_path: Path) -> None:
    clock = SimulatedClock(datetime(2026, 10, 19, 8, 0, tzinfo=UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    import logging

    logging.getLogger("housemind.test").warning("Could not reach light.living_ceiling")
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics", headers={"X-Remote-User-Id": "demo-user-2"})
        assert response.status == 403
        response = await client.get("/api/diagnostics", headers={"X-Remote-User-Id": "demo-user-1"})
        assert response.status == 200
        package = await response.json()
    assert package["housemind"] == __version__ and package["entities_per_domain"]["light"] >= 1
    # For a trial in a real house: what was recognised and what the self-check found.
    lights = package["recognised"]["light"]
    assert lights[1] >= 1 and 0 <= lights[0] <= lights[1]
    assert package["self_check"]["broken_actions"] == [] and package["self_check"]["connected"]
    assert package["people"]["persons_in_ha"] >= 1
    text = json.dumps(package)
    assert "living_ceiling" not in text and "Woonkamer" not in text
    assert any("light.device_" in line for line in package["log"])
    await hm.stop()
