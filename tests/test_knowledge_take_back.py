"""Leren en stappen kort, Wat ik weet in Brein (E-523 – E-525, B-448, B-449, design 88), from
the user's side: everything HouseMind knows of the house can be taken back – also after Klopt
or Klopt niet –, a person's own choice keeps what HouseMind thought, and what moved out of
Huisinstellingen is in Brein. Each test fails without the build; what it checks is its
docstring."""

from __future__ import annotations

from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.readiness import readiness, readiness_others
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from tests.knowledge_page import confirmed, to_confirm
from tests.test_house_knowledge import BEDTIME, PLUG, ROOT, _house, _page, _read, _ws
from tests.test_knowing import _said

ROOM = "area:hobby:role"


def _row(page: dict, key: str) -> dict:
    return next(r for r in page["rows"] if r["id"] == key)


async def test_klopt_toch_niet_gives_the_room_the_kind_of_its_name_again(tmp_path: Path) -> None:
    """A room AI called a study, confirmed with Klopt: "Klopt toch niet" makes it the kind its
    name gives again ('other' here, so its question may come once), not the study, also after
    a restart; the line stays in sight under Klopt niet, with its room."""
    hm, _clock, ha = await _house(tmp_path)
    await _read(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "knowledge", "do": "yes", "id": ROOM})
        page = await _page(ws)
        assert _row(page, ROOM)["state"] == "yes" and hm.area_roles["hobby"] == "office"
        await ws.send_json({"type": "knowledge", "do": "no", "id": ROOM})
        page = await _page(ws)
        await ws.close()
    line = _row(page, ROOM)
    assert (line["state"], line["what"], line["room"]) == ("no", "Werkkamer", "Hobbyhoek van Emma")
    assert hm.area_roles["hobby"] == "other"
    row = await hm.db.fetchone("SELECT role_source FROM areas WHERE area_id = 'hobby'")
    assert row is not None and row["role_source"] == "auto"
    await hm.stop()
    again = HouseMind(hm.clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.area_roles["hobby"] == "other"
    await again.stop()


async def test_back_to_to_confirm_waits_again_and_the_rules_hold(tmp_path: Path) -> None:
    """ "Terug naar te bevestigen" after Klopt: AI's study waits again for a Klopt, marked "van
    AI"; until then the room is what its name says, and the tab counts it as waiting."""
    hm, _clock, _ha = await _house(tmp_path)
    await _read(hm)
    assert await hm.answer_knowledge(ROOM, True)
    waiting = views.knowledge_waiting(hm)
    assert await hm.reopen_knowledge(ROOM)
    line = _row(views.house_knowledge(hm, "nl"), ROOM)
    assert (line["state"], line["source"], line["fromAI"]) == ("open", "ai", True)
    assert line in to_confirm(views.house_knowledge(hm, "nl"))
    assert hm.area_roles["hobby"] == "other"
    assert views.knowledge_waiting(hm) == waiting + 1
    assert not await hm.reopen_knowledge(ROOM)  # it waits already: nothing to take back
    await hm.stop()


async def test_something_else_keeps_what_housemind_thought_and_can_go_back(tmp_path: Path) -> None:
    """ "Iets anders…": the person's own kind counts at once, and the line says what HouseMind
    thought itself. Taken back, HouseMind's own guess waits again – not the person's choice,
    and not nothing."""
    hm, _clock, _ha = await _house(tmp_path)
    await _read(hm)
    assert await hm.answer_knowledge(ROOM, True)
    assert await hm.answer_knowledge(ROOM, True, "utility")  # another kind, after Klopt
    assert await hm.answer_knowledge(ROOM, True, "kitchen")  # and again: the guess stays
    assert hm.area_roles["hobby"] == "kitchen"
    line = _row(views.house_knowledge(hm, "nl"), ROOM)
    assert (line["state"], line["source"], line["what"]) == ("yes", "person", "Keuken")
    assert line["was"] == "Werkkamer"
    assert await hm.reopen_knowledge(ROOM)
    line = _row(views.house_knowledge(hm, "nl"), ROOM)
    assert (line["state"], line["source"], line["what"]) == ("open", "ai", "Werkkamer")
    assert line["certainty"] == 80 and "was" not in line
    assert hm.area_roles["hobby"] == "other"
    await hm.stop()


async def test_klopt_toch_wel_after_klopt_niet(tmp_path: Path) -> None:
    """Klopt niet is no longer the end: "Klopt toch wel" makes it the study after all."""
    hm, _clock, _ha = await _house(tmp_path)
    await _read(hm)
    assert await hm.answer_knowledge(ROOM, False)
    assert hm.area_roles["hobby"] == "other"
    assert await hm.answer_knowledge(ROOM, True)
    assert hm.area_roles["hobby"] == "office"
    assert [r["id"] for r in confirmed(views.house_knowledge(hm, "nl"))] == [ROOM]
    await hm.stop()


async def test_a_switch_taken_back_is_only_watched_again(tmp_path: Path) -> None:
    """A plug AI called a lamp, confirmed: HouseMind may switch it as a lamp. "Klopt toch
    niet": it is no lamp any more. Back to Te bevestigen: watched as a lamp, marked "van AI",
    never switched until a Klopt (50.8)."""
    hm, _clock, _ha = await _house(tmp_path)
    await _read(hm)
    key = f"entity:{PLUG}:role"
    assert await hm.answer_knowledge(key, True)
    assert hm.gate is not None and hm.gate.state.roles.get(PLUG) == "light"
    assert await hm.answer_knowledge(key, False)
    assert PLUG not in hm.gate.state.roles
    assert await hm.reopen_knowledge(key)
    finding = hm.knowledge.get("entity", PLUG, "role")
    assert finding is not None and finding.from_ai
    line = _row(views.house_knowledge(hm, "nl"), key)
    assert line["state"] == "open" and line["fromAI"]
    await hm.stop()


async def test_only_an_administrator_from_betrokken_takes_back(tmp_path: Path) -> None:
    """On Geïnformeerd there is no Wat ik weet: a message to take back changes nothing."""
    hm, _clock, _ha = await _house(tmp_path, level=2)
    await _read(hm)
    assert await hm.answer_knowledge(ROOM, True)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "knowledge", "do": "back", "id": ROOM})
        await ws.send_json({"type": "get", "what": "settings"})
        await ws.close()
    assert hm.area_roles["hobby"] == "office"
    await hm.stop()


async def test_the_automations_and_the_thresholds_are_in_brein(tmp_path: Path) -> None:
    """The one list of automations (E-523): an automation no part lists (a flag and a script)
    is there too, under Overig, with what starts it; Uitzetten and Weer aanzetten only for an
    administrator. The thresholds are in the card of a part, for an administrator on Expert
    only."""
    hm, _clock, _ha = await _house(tmp_path)
    await _read(hm)
    parts = await readiness(hm, "nl", 3, True)
    others = await readiness_others(hm, "nl", 3, True, parts)
    bedtime = next(a for a in others if a["id"] == BEDTIME)
    assert bedtime["causes"] and "canTurnOff" in bedtime and "canTurnOn" in bedtime
    assert bedtime["does"]["text"].startswith("Bij beweging")  # from AI, only to read
    listed = {a["id"] for p in parts for a in p.get("automations", [])}
    assert not listed & {a["id"] for a in others}  # every automation once
    resident = await readiness_others(hm, "nl", 3, False, parts)
    assert "canTurnOff" not in next(a for a in resident if a["id"] == BEDTIME)
    assert not any("thresholds" in p for p in parts)  # Betrokken
    # The thresholds are no screen any more, only in the settings file (QA 3).
    assert not any("thresholds" in p for p in await readiness(hm, "nl", 4, True))
    await hm.stop()


async def test_vesta_points_to_brein_for_the_automations(tmp_path: Path) -> None:
    """ "Waar vind ik de automatiseringen?": in Brein, under Klaar voor autonomie?, and a yes opens
    it there – no longer in Huisinstellingen."""
    hm, _clock, _ha = await _house(tmp_path)
    reply = await hm.knowing.answer(
        "Waar vind ik de automatiseringen?", "nl", 3, "demo-user-1", True
    )
    assert reply is not None
    assert reply["open"] == {"sheet": "readiness", "section": "readiness"}
    said = _said("know_setting_where", sheet="Brein", section="Klaar voor autonomie?")
    assert reply["text"] in said
    await hm.stop()
