"""How HouseMind looks in Home Assistant. E-235: its own logo, not "logo not available" – a custom
integration may bring its own brand images since HA 2026.3. E-251: the App options in four
languages, each naming the demo houses as HA shows them; the values stay, because a changed value
would make an existing choice invalid and HA would not start the App. Each test fails without
it; what it checks is its docstring."""

from __future__ import annotations

import re
import struct
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "integration" / "custom_components" / "housemind" / "brand"
APP = ROOT / "ha_app_stable"


def _size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def test_the_integration_brings_its_own_logo() -> None:
    """A square icon of 256 pixels and a logo whose short side is between 128 and 256."""
    assert _size(BRAND / "icon.png") == (256, 256)
    width, height = _size(BRAND / "logo.png")
    assert 128 <= min(width, height) <= 256


def test_the_app_options_speak_four_languages_and_keep_their_values() -> None:
    """Every option has a name and a description in Dutch, English, German and French, and the
    demo house's description names each value exactly as HA shows it."""
    config = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
    assert config["schema"]["demo_home"] == "list(simple|average|extended)"
    values = re.findall(r"[a-z]+", config["schema"]["demo_home"][5:-1])
    for code in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((APP / "translations" / f"{code}.yaml").read_text(encoding="utf-8"))
        for option in config["schema"]:
            item = texts["configuration"][option]
            assert item["name"] and item["description"], (code, option)
        description = texts["configuration"]["demo_home"]["description"]
        assert all(value in description for value in values), code
