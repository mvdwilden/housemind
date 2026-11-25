"""QA 2, group 1, part 3 – what goes to an AI, and care (M-7, M-8, M-11). Each test fails
without its fix; what it checks is its docstring."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.server import views
from housemind.server.web import create_app
from tests.test_safety_gaps import _house
from tests.test_start_over import _hello, _next, _snapshot

RESIDENT_2 = {"X-Remote-User-Id": "demo-user-2"}


async def test_an_ai_question_carries_only_what_is_on_and_the_room_it_asks_about(
    tmp_path: Path,
) -> None:
    """Every lamp that was off went along with every AI question. Now only devices that are
    on go along – and all of a room the question names, so "is the kitchen lamp on?" can be
    answered."""
    hm, ha = await _house(tmp_path)
    await ha.simulate("light.living_reading", "on")
    await ha.simulate("light.kitchen", "off")
    labels = hm.ai.labels
    living, kitchen = labels.area("living"), labels.area("kitchen")
    lamp_living = labels.to_label["light.living_reading"]
    lamp_kitchen = labels.to_label["light.kitchen"]
    facts = hm.house_facts("Wat staat er aan?")
    assert lamp_living in facts["rooms"][living].get("devices", {})
    assert lamp_kitchen not in facts["rooms"][kitchen].get("devices", {})
    asked = hm.house_facts("Is de lamp in de keuken aan?")
    assert lamp_kitchen in asked["rooms"][kitchen]["devices"]
    await hm.stop()


async def test_the_ai_choice_shows_what_goes_and_warns_for_an_address_outside(
    tmp_path: Path,
) -> None:
    """Before the yes: an example of one of Vesta's sentences besides a house question (the
    task that goes most often), and a warning when an own AI address is outside the home
    network."""
    hm, _ha = await _house(tmp_path)
    shown = json.loads(await views.ai_preview(hm, "nl", "demo-user-1"))
    assert set(shown) == {"ask", "vesta"}
    assert shown["vesta"]["messages"][0]["facts"]["text"]
    for address, remote in (("http://192.168.1.20:11434", False), ("https://ai.example.com", True)):
        await hm.ai.configure(replace(hm.ai.config, provider="ollama", base_url=address))
        settings = await views.house_settings(hm, "nl")
        assert settings["ai"]["remote"] is remote, address
    await hm.stop()


async def test_care_needs_the_yes_of_the_person_it_is_about(tmp_path: Path) -> None:
    """Care (unusual quiet, told to a contact) was switched on by the administrator's tick.
    Now the person it is about – who has an account – gets the question and says yes
    themselves; until then it stays off, and they can take it back in Mijn gegevens."""
    hm, _ha = await _house(tmp_path)
    for i in (1, 2):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", 3, json.dumps({}), hm.clock.now().isoformat()),
        )
    await hm._refresh_people()
    await hm.set_house_setting("experimental", ["care"])
    await hm.set_house_setting(
        "care", {"person": "person.resident_2", "contact": "demo-user-1", "consent": True}
    )
    shown = (await views.house_settings(hm, "nl"))["care"]
    assert shown["ownAccount"] and not shown["agreed"]  # the administrator's tick is not it
    row = await hm.db.fetchone(
        "SELECT id, concerns FROM notifications "
        "WHERE topic = 'care_consent' AND resolved_at IS NULL"
    )
    assert row is not None and row["concerns"] == "user:demo-user-2"
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, RESIDENT_2)
        await _snapshot(ws)
        await ws.send_json({"type": "notification", "id": row["id"], "action": "yes"})
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
        await _next(ws, "saved")
        assert (await views.house_settings(hm, "nl"))["care"]["agreed"]
        await ws.send_json({"type": "care_withdraw"})
        await _next(ws, "saved")
        await ws.close()
    assert not (await views.house_settings(hm, "nl"))["care"]["agreed"]
    await hm.stop()
