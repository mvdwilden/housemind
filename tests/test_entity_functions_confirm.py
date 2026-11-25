"""Every entity's function, part D: confirm per device in one go (E-432, design 51.7). On "Wat
HouseMind van je huis weet" what the AI says each entity is shows per device – "Oven:
instelling (2)" – with one Klopt for all of it, and per entity something else to choose. Each
test fails without the build; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.functions import FUNCTIONS, counts_for_acting
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity
from housemind.ha.fake import FakeHomeAssistant
from housemind.server import views
from housemind.server.web import create_app
from tests.knowledge_page import by_device, to_confirm
from tests.test_ai import enable
from tests.test_entity_functions_ai import LOCK, POWER, SAID, ReadingAI, _oven
from tests.test_house_knowledge import _page, _ws
from tests.test_house_knowledge_derived import _add, _start

PREHEAT = "switch.oven_fast_preheat"


async def _oven_more(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """The oven of part B, with a second setting: fast preheating."""
    await _oven(ha, clock)
    preheat = Entity(
        PREHEAT, "r4", "Snel voorverwarmen", device_id="device.oven", original_name="Fast Preheat"
    )
    await _add(ha, clock, preheat)


async def _read(tmp_path: Path) -> Any:
    SAID["Fast Preheat"] = ("setting", 0.85)
    hm, _clock, _ha = await _start(tmp_path, setup=_oven_more)
    await enable(hm, ReadingAI())
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    return hm


async def test_what_each_entity_is_shows_per_device(tmp_path: Path) -> None:
    """The oven's child lock and fast preheating stand together under the oven, in short; the
    relay under its own device. Not one by one among the rest."""
    hm = await _read(tmp_path)
    page = views.house_knowledge(hm, "nl")
    oven = next(g for g in by_device(page) if g["name"] == "Oven")
    # Its type beside its name, and what is in it: two settings (E-432, folded in E-524).
    assert {r["deviceModel"] for r in oven["items"]} == {"Bake 1"}
    assert [r["what"] for r in oven["items"]] == ["instelling", "instelling"]
    assert set(oven["keys"]) == {f"entity:{LOCK}:function", f"entity:{PREHEAT}:function"}
    assert not any(r["field"] == "function" for r in to_confirm(page))
    assert any(g["keys"] == [f"entity:{POWER}:function"] for g in by_device(page))
    choices = {c["name"] for c in page["functions"]}  # Anders…: every one but "not known yet"
    assert len(choices) == len(FUNCTIONS) - 1 and "nog onbekend" not in choices
    await hm.stop()


async def test_one_klopt_confirms_everything_of_a_device(tmp_path: Path) -> None:
    """Klopt, alles: both settings of the oven are the administrator's from then on, and the
    oven is gone from the page."""
    hm = await _read(tmp_path)
    assert hm.world is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        oven = next(g for g in by_device(views.house_knowledge(hm, "nl")) if g["name"] == "Oven")
        await ws.send_json({"type": "knowledge", "do": "device_yes", "ids": oven["keys"]})
        page = await _page(ws)
        assert not any(g["name"] == "Oven" for g in by_device(page))
        await ws.close()
    assert hm.world.function_sources[LOCK] == hm.world.function_sources[PREHEAT] == "person"
    await hm.stop()


async def test_something_else_for_one_entity(tmp_path: Path) -> None:
    """The AI thought the relay powers an appliance; the administrator says it is a lamp. From
    then on it is a lamp – the room says so, and it may be used as one."""
    hm = await _read(tmp_path)
    assert hm.world is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        key = f"entity:{POWER}:function"
        await ws.send_json({"type": "knowledge", "do": "role", "id": key, "role": "lamp"})
        await _page(ws)
        await ws.close()
    assert hm.world.functions[POWER] == "lamp"
    assert counts_for_acting("lamp", hm.world.function_sources[POWER])
    room = next(r for r in views.rooms(hm, "nl", 3) if r["id"] == "garage")
    relay = next(i for i in room["devices"] if i["id"] == POWER)
    assert relay["function"] == "lamp" and not relay["fromAi"]
    await hm.stop()
