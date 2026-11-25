"""Colour schemes as files (E-266, E-267, B-113, B-114), from the user's side: the four shipped
schemes pass the readability check, an own file comes in by itself without a restart, a file
that is not readable stays out with the reason for the administrator, and Vesta has one name,
also in Home Assistant and Assist (E-265). Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.themes import RULES, contrast, load_schemes
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_round5_retest import EVENING
from tests.test_vesta_asks import _until

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "knowledge" / "themes"

GOOD = """
id: zee
naam: { nl: Zee, en: Sea }
uitleg: { nl: "Blauw en fris." }
basis: donker
niveau: AA
kleuren:
  inkt: "#06121C"
  vlak: "#0B1A28"
  verhoogd: "#132638"
  lijn: "#22384F"
  rand: "#5F7D99"
  tekst: "#E6F0F8"
  gedempt: "#9FB6CC"
  accent: "#4FC3F7"
  op-accent: "#04202E"
  nood: "#FF6B7A"
  belangrijk: "#FF9E5E"
  leren: "#F2C46B"
vesta:
  gloed: 0.7
"""


async def _house(tmp_path: Path, brain_name: str | None = None) -> tuple[HouseMind, Path]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    own = tmp_path / "own"
    (own / "themes").mkdir(parents=True)
    data = tmp_path / "data"
    folders = [ROOT / "knowledge", tmp_path / "update", own]
    hm = HouseMind(clock, house.ha, Paths(folders, data), set())
    if brain_name is not None:  # a name given to the brain for Assist before (E-170)
        await hm.db.open()
        await hm.db.set_setting("brain_name", brain_name)
        await hm.db.close()
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, own / "themes"


def test_the_four_shipped_schemes_pass_the_check() -> None:
    """Diep (the default), Licht, Hoog contrast (AAA: text 7:1) and Nacht, each readable: every
    rule on every background it names, in their own order."""
    schemes = load_schemes([SHIPPED])
    assert not schemes.problems
    assert [s.id for s in schemes.ordered()] == ["diep", "licht", "contrast", "nacht"]
    assert schemes.by_id["contrast"].level == "AAA"
    assert schemes.by_id["licht"].base == "light" and schemes.by_id["nacht"].glow < 1
    for scheme in schemes.ordered():
        for rule in RULES:
            need = rule.aaa if scheme.level == "AAA" else rule.aa
            for on in rule.on:
                assert contrast(scheme.colours[rule.role], scheme.colours[on]) >= need
        assert scheme.name("nl") and scheme.name("de") and scheme.hint("fr")


def test_a_scheme_that_is_hard_to_read_stays_out_with_the_reason(tmp_path: Path) -> None:
    """Quiet text that is too dark, a colour that is missing, a file that is not YAML: none of
    them comes into the list, and each says why (which colour, on what, how much, what is
    needed), so an administrator can fix it."""
    (tmp_path / "bos.yaml").write_text(GOOD.replace("zee", "bos").replace("#9FB6CC", "#3A4A5A"))
    half = GOOD.replace("zee", "half").replace('  rand: "#5F7D99"\n', "")
    (tmp_path / "half.yaml").write_text(half)
    (tmp_path / "kapot.yaml").write_text("kleuren: [niet: goed")
    schemes = load_schemes([SHIPPED, tmp_path], own=tmp_path)
    assert "bos" not in schemes.by_id and "half" not in schemes.by_id
    problems = {(p.file, p.code, p.role, p.on) for p in schemes.problems}
    assert ("own/bos.yaml", "contrast", "gedempt", "vlak") in problems
    assert ("own/half.yaml", "missing", "rand", None) in problems
    assert ("own/kapot.yaml", "unreadable", None, None) in problems
    bos = next(p for p in schemes.problems if p.file == "own/bos.yaml" and p.on == "vlak")
    assert bos.ratio is not None and bos.ratio < 4.5 and bos.need == 4.5
    assert len(schemes.by_id) == 4  # the shipped ones stay


async def test_an_own_scheme_comes_in_by_itself_without_a_restart(tmp_path: Path) -> None:
    """A new file in the own folder is in every app within a minute; an own file with the id of
    a shipped one goes first; a removed file goes out again."""
    hm, own = await _house(tmp_path)
    seen: list[int] = []

    async def changed() -> None:
        seen.append(len(hm.schemes.by_id))

    hm.on_update(changed)
    assert [s.id for s in hm.schemes.ordered()] == ["diep", "licht", "contrast", "nacht"]
    (own / "zee.yaml").write_text(GOOD)
    own_night = GOOD.replace("id: zee", "id: nacht").replace("Zee", "Eigen nacht")
    (own / "nacht.yaml").write_text(own_night)
    await hm.tick()
    assert "zee" not in hm.schemes.by_id  # not every tick: once a minute (real time)
    hm._schemes_checked -= 61
    await hm.tick()
    assert "zee" in hm.schemes.by_id and hm.schemes.by_id["zee"].own
    assert hm.schemes.by_id["nacht"].name("nl") == "Eigen nacht" and hm.schemes.by_id["nacht"].own
    assert seen and seen[-1] == 5
    (own / "zee.yaml").unlink()
    hm._schemes_checked -= 61
    await hm.tick()
    assert "zee" not in hm.schemes.by_id
    await hm.stop()


async def test_the_app_gets_the_schemes_and_administrators_see_why(tmp_path: Path) -> None:
    """Every app gets the schemes that passed (with their colours and Vesta's glow) to choose
    one per device; the House settings show every scheme, also the ones left out and why."""
    hm, own = await _house(tmp_path)
    (own / "bos.yaml").write_text(GOOD.replace("zee", "bos").replace("#9FB6CC", "#3A4A5A"))
    hm.load_schemes()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        snap: dict[str, Any] = (await _until(ws, "snapshot"))["snapshot"]["snapshot"]
        themes = {t["id"]: t for t in snap["themes"]}
        assert list(themes) == ["diep", "licht", "contrast", "nacht"]
        assert themes["licht"]["name"] == "Licht" and themes["licht"]["base"] == "light"
        assert themes["diep"]["colours"]["accent"] == "#5ED4C4"
        assert themes["nacht"]["glow"] == 0.5
        await ws.send_json({"type": "get", "what": "settings"})
        settings = (await _until(ws, "data"))["data"]["data"]
        assert [t["id"] for t in settings["themes"]["ok"]] == list(themes)
        problem = settings["themes"]["problems"][0]
        assert problem["file"] == "own/bos.yaml" and problem["code"] == "contrast"
        await ws.close()
    await hm.stop()


async def test_vesta_has_one_name_also_in_assist(tmp_path: Path) -> None:
    """The name in Home Assistant and Assist is hers: Vesta, or the one the administrator gives
    her. A name once given to the brain for Assist becomes her name, once."""
    hm, _own = await _house(tmp_path)
    assert (await hm.integration_status())["name"] == "Vesta"
    await hm.set_house_setting("vesta_name", "Juno")
    assert (await hm.integration_status())["name"] == "Juno"
    await hm.stop()
    old, _own = await _house(tmp_path / "old", brain_name="Jarvis")
    assert old.vesta_name("nl") == "Jarvis"
    assert (await old.integration_status())["name"] == "Jarvis"
    assert await old.db.get_setting("brain_name") is None
    await old.stop()
