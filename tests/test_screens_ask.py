"""Less on the screens (QA 3, schermen 14 – 19, 20, 21, 26 and 28) – from the side of the
person who looks: a device says only what asks something of you, Klaar voor autonomie? says
how often it went well without a score, and what no screen shows is not sent or kept. The
screens themselves are checked in a real browser in tests/test_ui_house_in_motion.py,
test_ui_access.py, test_ui_icons.py, test_ui_live_devices.py and test_ui_system_ring.py. Each
test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")
GONE = (
    "ui.devices.icon_from.ha",
    "ui.devices.other_icon",
    "ui.network.last",
    "ui.network.quiet",
    "ui.settings.themes.own",
    "ui.settings.upkeep.not_published",
    "ui.stands.filter.title",
)


async def _house(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(datetime(2026, 11, 25, 19, 0, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path / "data"))
    await hm.start(autorun=False)
    return hm


async def test_a_device_is_sent_without_where_its_icon_came_from(tmp_path: Path) -> None:
    """Brein › Apparaten and a room show each device with its icon, and no line with where
    that icon came from: that is not sent any more. The icon still shows the lamp as it is."""
    hm = await _house(tmp_path)
    rows = views.devices(hm, "nl")
    assert rows and all("icon" in row and "iconFrom" not in row for row in rows)
    room = next(r for r in views.rooms(hm, "nl", 4) if r["devices"])
    assert all("iconFrom" not in device for device in room["devices"])
    await hm.stop()


def test_ready_for_autonomy_says_how_often_without_a_score() -> None:
    """Under a part of Klaar voor autonomie?: how often it went well, otherwise or was put
    back, and since when – no score and no 'zekerheid', in every language."""
    languages = load_languages([ROOT / "knowledge" / "languages"])
    for lang in ("nl", "en", "de", "fr"):
        line = languages.text(
            lang, "ui.ready.numbers", hits=14, misses=2, corrections=1, since="1-11"
        )
        assert "14" in line and "2" in line and "1-11" in line, line
        assert "{" not in line and "%" not in line, line
    nl = languages.text("nl", "ui.ready.numbers", hits=1, misses=0, corrections=0, since="x")
    assert "zekerheid" not in nl and "score" not in nl


def test_what_no_screen_shows_is_in_no_language() -> None:
    """The texts of the lines that went – the code line, Laatst om, the quiet line, the
    filter on Standen – are in no language file any more, so no screen can show them."""
    languages = load_languages([ROOT / "knowledge" / "languages"])
    for lang in ("nl", "en", "de", "fr"):
        assert not [key for key in GONE if key in languages.languages[lang]], lang
