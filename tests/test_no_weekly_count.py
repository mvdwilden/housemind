"""The anonymous weekly count is gone (E-442). The administrator's wish: "zullen we deze
functionaliteit eruit halen?" – it never had an address, so it never sent anything, and to send
it HouseMind would need a server that receives data from other houses. Now there is no tick box,
no preview, no weekly try, and no word about it in the privacy statement, Help, the manual or
the website, in any language. A house that once said yes keeps nothing of it."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

from housemind.memory.database import Database
from housemind.server import views
from housemind.support.outbound import FLOWS
from tests.test_screens import ROOT, start

LANGS = ("nl", "en", "de", "fr")
# The words each language used for it.
WORDS = re.compile(
    r"anonieme telling|anonymous (weekly )?count|anonymen? Zählung|comptage anonyme", re.IGNORECASE
)


async def test_house_settings_have_no_count_any_more(tmp_path: Path) -> None:
    """Onderhoud en privacy: no tick box, no preview of what would go out, and a yes is refused."""
    hm, _clock, _ha = await start(tmp_path)
    shown = await views.house_settings(hm, "nl")
    assert not {"counter", "counterPreview", "counterPublished"} & set(shown)
    assert await hm.set_house_setting("counter", True) is False
    assert await hm.db.get_setting("counter") is None
    await hm.stop()


async def test_a_house_that_said_yes_keeps_nothing_of_it(tmp_path: Path) -> None:
    """After the update the old yes and when it last counted are gone."""
    db = Database(tmp_path / "housemind.db")
    await db.open()
    await db.set_setting("counter", True)
    await db.set_setting("counted_at", "2026-10-30T08:00:00+00:00")
    await db.execute("PRAGMA user_version=22")
    await db.close()
    hm, _clock, _ha = await start(tmp_path)
    assert await hm.db.get_setting("counter") is None
    assert await hm.db.get_setting("counted_at") is None
    await hm.stop()


def test_nothing_counts_and_nothing_can_send_it() -> None:
    assert "counter" not in [f.key for f in FLOWS]
    assert importlib.util.find_spec("housemind.support.counter") is None


def test_no_word_about_it_anywhere_a_resident_reads() -> None:
    """The app's words (privacy statement, Help, About), the manual, the website and the App's
    documentation in Home Assistant."""
    files = [ROOT / "knowledge" / "languages" / f"{lang}.yaml" for lang in LANGS]
    files += [ROOT / "knowledge" / "manual" / f"{lang}.html" for lang in LANGS]
    files += [ROOT / "docs" / "index.html"]
    files += [ROOT / "docs" / lang / "index.html" for lang in ("nl", "de", "fr")]
    files += [ROOT / "ha_app_stable" / "DOCS.md", ROOT / "docs" / "app" / "DOCS.md"]
    found = [str(f.relative_to(ROOT)) for f in files if WORDS.search(f.read_text())]
    assert found == []
    for lang in LANGS:
        words = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        upkeep = words["ui"]["settings"]["upkeep"]
        assert not {"counter", "counter_what", "counter_none"} & set(upkeep), lang
