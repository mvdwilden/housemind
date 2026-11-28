from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_app_config_is_safe() -> None:
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text(encoding="utf-8"))
    assert config["ingress"] is True
    assert "ports" not in config  # no open ports (E-86)
    assert config.get("host_network") is not True
    assert config.get("full_access") is not True
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["backup_pre"].startswith("python3 -m housemind.memory.checkpoint")
    assert set(config["options"]) == set(config["schema"])
    assert config["discovery"] == ["housemind"]  # the integration announces itself (B-06)


@pytest.mark.skipif(
    not (ROOT / "frontend" / "dist" / "index.html").exists(), reason="frontend not built"
)
def test_local_app_folder(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "make_local_app", ROOT / "tools" / "make_local_app.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = module.build(tmp_path / "housemind")
    config = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    assert "image" not in config and config["slug"] == "housemind_local"
    assert config["name"] == "HouseMind (local)"
    assert "url" not in config  # no link to a page that is not public yet
    assert (out / "frontend" / "index.html").exists()
    assert (out / "knowledge" / "catalog" / "lighting.yaml").exists()
    assert (out / "src" / "housemind" / "__main__.py").exists()
    # The small integration travels with the App, to be placed after a yes (B-06).
    assert (out / "integration" / "custom_components" / "housemind" / "manifest.json").exists()
    assert "COPY integration/ /app/integration/" in (out / "Dockerfile").read_text()
    assert "COPY integration/ /app/integration/" in (ROOT / "Dockerfile").read_text()
    # The change list travels along: Expert reads every change in "what is new" (E-150).
    assert "COPY CHANGELOG.md /app/CHANGELOG.md" in (out / "Dockerfile").read_text()
    assert (out / "CHANGELOG.md").exists()
    assert "COPY ha_app_stable/CHANGELOG.md /app/CHANGELOG.md" in (ROOT / "Dockerfile").read_text()
    # HA builds a local App on the device: it must start from a base image with Python, the
    # same exact one on every architecture – in the Dockerfile, as HA's build.yaml takes no
    # digest (QA 3, gat 7).
    first = (out / "Dockerfile").read_text().splitlines()[0]
    assert first.startswith("FROM ghcr.io/home-assistant/base-python:") and "@sha256:" in first
    assert not (out / "build.yaml").exists()
    assert not (out / "tests").exists() and not (out / "frontend" / "node_modules").exists()
    # The info page HA shows is for the people who use it: no parts for developers.
    info = (out / "README.md").read_text(encoding="utf-8")
    assert info == (ROOT / "ha_app_stable" / "README.md").read_text(encoding="utf-8")
    assert "Development" not in info and "uv run" not in info and "Repository layout" not in info


def test_supervisor_token_also_from_the_container_environment(tmp_path: Path) -> None:
    from housemind.__main__ import supervisor_token

    assert supervisor_token({"SUPERVISOR_TOKEN": "abc"}, tmp_path) == "abc"
    assert supervisor_token({}, tmp_path) is None
    (tmp_path / "SUPERVISOR_TOKEN").write_text("xyz\n", encoding="utf-8")
    assert supervisor_token({}, tmp_path) == "xyz"


def test_the_app_has_its_icon_and_logo() -> None:
    """E-166: the Orb is the logo; HA shows icon.png (1:1) and logo.png (wide) in the store."""
    import struct

    def size(path: Path) -> tuple[int, int]:
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        return struct.unpack(">II", data[16:24])

    width, height = size(ROOT / "ha_app_stable" / "icon.png")
    assert width == height >= 128
    width, height = size(ROOT / "ha_app_stable" / "logo.png")
    assert 2 <= width / height <= 3
    assert (ROOT / "frontend" / "public" / "favicon.svg").exists()


def test_the_image_installs_exactly_uv_lock_with_hashes() -> None:
    """QA 2, M-33: the image installed open version ranges. Now it installs requirements.lock,
    every version as in uv.lock and checked by its hash, in both images."""
    import tomllib

    locked = {
        p["name"]: p["version"]
        for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    }
    pins = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith((" ", "#")):
            name, version = line.split(" ")[0].split("==")
            pins[name] = version
    assert pins and all(locked.get(name) == version for name, version in pins.items()), pins
    for dockerfile in (ROOT / "Dockerfile", ROOT / "tools" / "make_local_app.py"):
        text = dockerfile.read_text(encoding="utf-8")
        assert "--require-hashes -r requirements.lock" in text and "--no-deps ." in text
    assert (ROOT / ".github" / "dependabot.yml").exists()


def test_the_screens_are_built_on_the_build_machine() -> None:
    """The aarch64 release once hung for six hours on npm ci under emulation. The screens are
    plain files, the same on every architecture, so their stage runs on the machine that
    builds; only the App itself is built for the target, and it takes just those files."""
    stages: dict[str, list[str]] = {}
    name = ""
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM "):
            name = line.split(" AS ")[1] if " AS " in line else "app"
            stages[name] = [line]
        elif name and line.strip() and not line.startswith("#"):
            stages[name].append(line)
    assert set(stages) == {"frontend", "app"}, list(stages)
    first = stages["frontend"][0]
    assert first.startswith("FROM --platform=$BUILDPLATFORM node:") and "@sha256:" in first
    assert stages["app"][0] == "FROM ${BUILD_FROM}"  # the App itself: the target platform
    taken = [line for line in stages["app"] if "--from=" in line]
    assert taken == ["COPY --from=frontend /build/dist/ /app/frontend/"]
    assert stages["frontend"][-1] == "RUN npm run build"


def test_home_assistant_watches_the_app_and_limits_it() -> None:
    """QA 2, M-34: no watchdog and no AppArmor profile. Now HA asks /api/health and starts
    the App again when it does not answer, and the App has its own profile."""
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text(encoding="utf-8"))
    assert config["watchdog"] == "http://[HOST]:[PORT:8099]/api/health"
    profile = (ROOT / "ha_app_stable" / "apparmor.txt").read_text(encoding="utf-8")
    assert "profile housemind" in profile and "deny ptrace" in profile
    assert '"apparmor.txt"' in (ROOT / "tools" / "make_local_app.py").read_text(encoding="utf-8")


def test_the_version_is_the_same_everywhere() -> None:
    """QA 2, L12: the version lived in five places, set by hand. Now one tool sets them all,
    and this test sees that they agree."""
    spec = importlib.util.spec_from_file_location("set_version", ROOT / "tools" / "set_version.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = module.versions()
    assert len(found) == 5 and len(set(found.values())) == 1 and "" not in found.values(), found
    import json

    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == lock["packages"][""]["version"] == next(iter(found.values()))
