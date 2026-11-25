"""What the manual drew is in the app itself now (E-403 – E-411, B-329 – B-338), from the
user's side: what you get, how long HouseMind keeps what, how a step works, when a pause ends,
how far the measuring of energy is, and what Vesta calls a device. Each test fails without it;
what it checks is its docstring."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.runtime import HouseMind
from housemind.server import views
from housemind.server.web import create_app
from tests.test_energy import start as energy_house
from tests.test_safety_gaps import _house
from tests.test_start_over import ADMIN, _hello, _next, _snapshot


async def _person(hm: HouseMind, level: int, consents: dict[str, Any] | None = None) -> None:
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, consents, created) "
        "VALUES(?,?,?,?,?)",
        (
            "demo-user-1",
            "Resident 1",
            level,
            json.dumps(consents or {}),
            hm.clock.now().isoformat(),
        ),
    )
    await hm._refresh_people()


async def _snap(hm: HouseMind, tmp_path: Path) -> dict[str, Any]:
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        snap = await _snapshot(ws)
        await ws.close()
    return snap


async def test_on_expert_you_see_what_you_get_and_how_long_it_is_kept(tmp_path: Path) -> None:
    """Mijn voorkeuren says what this person gets – from their own level – and Mijn gegevens
    how long HouseMind keeps what, with the terms it really cleans up by; Brein how a step
    works, with the house's limits; Pauze until when "Tot morgen" lasts."""
    hm, _ha = await _house(tmp_path)
    await _person(hm, 4)
    snap = await _snap(hm, tmp_path)
    assert snap["youGet"] == [
        "Bij nood: altijd, ook op je telefoon en in je stille uren.",
        "Hooguit 20 andere meldingen per dag.",
        "Hooguit 5 vragen per dag.",
        "Het overzicht van de dag om 19:00.",
    ]
    assert [line.split(":", 1)[0] for line in snap["keepTerms"]] == [
        "7 dagen",
        "30 dagen",
        "90 dagen",
        "2 jaar",
        "Tot je het wist",
        "Opnames van je stem",
    ]
    assert "stepRules" not in snap  # how a step works is in the manual now (QA 3)
    assert snap["paused"]["morning"] == "06:00"
    assert snap["paused"]["hour"] == views._local(hm, hm.clock.now() + timedelta(hours=1)).strftime(
        "%H:%M"
    )
    await hm.stop()


async def test_on_carefree_without_a_yes_only_emergencies_and_no_rules(tmp_path: Path) -> None:
    """Ontzorgd, no yes to messages in danger: emergencies in the app, no other notices – and
    no explanation of steps, which is for Betrokken and Expert."""
    hm, _ha = await _house(tmp_path)
    await _person(hm, 1)
    snap = await _snap(hm, tmp_path)
    assert snap["youGet"][:2] == [
        "Bij nood: in de app; op je telefoon alleen als je ja zei op berichten bij gevaar.",
        "Verder geen meldingen.",
    ]
    assert "stepRules" not in snap
    await hm.stop()


async def test_expert_sees_what_vesta_calls_each_device(tmp_path: Path) -> None:
    """Apparaten en entiteiten has a column with what Vesta says – "de … in de …" – never
    the name from Home Assistant."""
    hm, _ha = await _house(tmp_path)
    await _person(hm, 4)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        await _snapshot(ws)
        await ws.send_json({"type": "get", "what": "devices"})
        while (message := await _next(ws, "data"))["what"] != "devices":
            pass
        await ws.close()
    lamps = [d for d in message["data"] if d["typeKey"] == "light" and d["area"]]
    assert lamps and all(d["vesta"].startswith(("de ", "het ", "een ")) for d in lamps)
    await hm.stop()


async def test_energy_says_which_day_of_the_measuring_it_is(tmp_path: Path) -> None:
    """The first weeks Energie says "Dag 1 van 14", not only how many days are left."""
    hm, _clock, _ha = await energy_house(tmp_path)
    view = await views.energy(hm, "nl", 3, True)
    assert view is not None and view["measuringDay"] == {"day": 1, "of": 14}
    await hm.stop()
