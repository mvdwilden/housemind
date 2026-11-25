"""Straks, in Brein › Ritme, from the user's side. The administrator (14-11-2026): "Wat doet
eigenlijk 'Wat ik nu zou doen'? Ik vind het een niet veelzeggende pagina." – it showed what
HouseMind would do this very moment, which on a calm moment is nothing or one line it may not
do anyway. Their choice: the page goes, and under the rhythm comes what HouseMind will do at
the next moments of the day ("die mockup vind ik geweldig; zo kan je zien wat er
waarschijnlijk gaat gebeuren"). Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ai.lookups import HouseLookups
from housemind.brain.later import later
from housemind.brain.rules import HouseRule
from housemind.runtime import HouseMind
from housemind.safety.emergency import Emergency
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode
from tests.test_real_house_counts import _house
from tests.test_rhythm import local

H, S = HouseMode.HOME, HouseMode.SLEEP
TUESDAY_AFTERNOON = datetime(2026, 11, 10, 13, 20, tzinfo=UTC)  # 14:20 in the house


async def _ahead(
    tmp_path: Path, start: datetime = TUESDAY_AFTERNOON, rhythm: bool = True
) -> HouseMind:
    """The family house, where Home Assistant knows the place (the sun), set up and running;
    with ``rhythm``: a week of nights to bed at 23:10 and up at 6:50."""
    hm, _clock, _ha = await _house(tmp_path, start=start, located=True)
    assert hm.gate is not None
    hm.activated = True
    hm.gate.state.inactive = False
    hm.rhythm.wake.clear()
    hm.rhythm.bed.clear()
    if rhythm:
        for day in range(1, 8):
            for kind in (DayType.WORKDAY, DayType.FREE):
                hm.rhythm.record(S, H, local(day, 6, 50), kind, kind)
                hm.rhythm.record(H, S, local(day, 23, 10), kind, kind)
    return hm


def _moment(view: dict[str, Any], key: str) -> dict[str, Any]:
    return next(m for m in view["moments"] if m["key"] == key)


def _texts(moment: dict[str, Any]) -> list[str]:
    return [line["text"] for line in moment["lines"]]


def _states(hm: HouseMind) -> dict[str, str]:
    return {e: s.state for e, s in hm.ha.states.items()}  # type: ignore[attr-defined]


async def test_the_day_ahead_moment_by_moment(tmp_path: Path) -> None:
    """Tuesday 14:20 in November, in a house that knows its rhythm: Straks shows it gets dark
    (around 16:30), the house goes to sleep (23:10, as usual), getting up tomorrow (6:50, a
    workday) and it gets light (around 8:25), in that order. At dusk the curtains close and the
    garden lamp goes on; at bedtime the lamps in the living room go to Gedimd and then out, the
    thermostat to 17 °C and the garden lamp out again; getting up, the bedroom lamp comes on. A
    new house only watches: 'Zou ik'. Nothing is switched, learned or kept, the house mode
    stays, and today's wake-up time is not fixed by looking ahead."""
    hm = await _ahead(tmp_path)
    assert hm.world is not None and hm.learning is not None
    before = _states(hm)
    kept = copy.deepcopy((hm.learning.counts, hm.learning.values, dict(hm.learning.steps)))
    frozen, mode, recent = dict(hm.rhythm.frozen), hm.world.modes.mode, dict(hm.recent)
    view = later(hm, "nl")
    assert view["at"] == "14:20" and view["unknown"] == [] and view["blocked"] is None
    assert [(m["key"], m["title"]) for m in view["moments"]] == [
        ("dark", "Het wordt donker"),
        ("bed", "Het huis gaat slapen"),
        ("wake", "Morgen opstaan"),
        ("light", "Het wordt licht"),
    ]
    dark, bed, wake, light = view["moments"]
    assert dark["at"].startswith("16:") and light["at"].startswith("08:")
    assert (bed["at"], bed["how"]) == ("23:10", "zoals meestal")
    assert (wake["at"], wake["how"]) == ("06:50", "werkdag")
    assert "Woonkamer gordijnen sluiten." in _texts(dark)
    assert "Tuin lamp aandoen." in _texts(dark)
    assert "Woonkamer plafondlamp op Gedimd zetten." in _texts(bed)
    assert "Woonkamer plafondlamp uitdoen." in _texts(bed)
    assert "Woonkamer thermostaat op 17 °C zetten." in _texts(bed)
    assert "Tuin lamp uitdoen." in _texts(bed)  # what it did at dusk is how the night begins
    assert "Slaapkamer lamp aandoen." in _texts(wake)
    assert "Woonkamer gordijnen openen." in _texts(light)
    lines = [line for m in view["moments"] for line in m["lines"]]
    assert {line["chip"] for line in lines} == {"Zou ik"}
    assert all(line.get("icon") for line in lines)
    assert _states(hm) == before
    assert copy.deepcopy((hm.learning.counts, hm.learning.values, dict(hm.learning.steps))) == kept
    assert hm.rhythm.frozen == frozen and hm.world.modes.mode is mode and hm.recent == recent
    await hm.stop()


async def test_each_moment_judged_at_its_own_time(tmp_path: Path) -> None:
    """A house rule that holds at a moment says no there, with the reason: 'never a lamp on in
    the garden from 16:00 to 18:00' keeps the garden lamp off at dusk, so at bedtime there is
    none to put out. A rule that holds only now holds nothing back later: 'never above 18 °C
    from 14:00 to 15:00' does not stop the living room from warming up when the house gets up.
    The same refusal is not said again at every moment."""
    hm = await _ahead(tmp_path)
    await hm.save_rule(
        HouseRule("area", "garden", "never", "on", time_from=time(16), time_to=time(18)),
        "demo-user-1",
    )
    await hm.save_rule(
        HouseRule("house", "*", "limit", "heat", 18.0, time_from=time(14), time_to=time(15)),
        "demo-user-1",
    )
    view = later(hm, "nl")
    garden = next(
        line for line in _moment(view, "dark")["lines"] if line["text"].startswith("Tuin")
    )
    assert garden["outcome"] == "denied"
    assert garden["chip"] == "Niet: Een huisregel zegt dat het niet mag"
    assert "Tuin lamp uitdoen." not in _texts(_moment(view, "bed"))
    assert "Woonkamer thermostaat op 20,5 °C zetten." in _texts(_moment(view, "wake"))
    refusals = [
        line["text"] for m in view["moments"] for line in m["lines"] if line["outcome"] == "denied"
    ]
    assert len(refusals) == len(set(refusals))
    await hm.stop()


async def test_still_learning_says_so(tmp_path: Path) -> None:
    """A house whose rhythm HouseMind does not know yet: Straks still shows when it gets dark
    and light, and says honestly what it is still learning instead of guessing a time."""
    hm = await _ahead(tmp_path, rhythm=False)
    view = later(hm, "nl")
    assert [m["key"] for m in view["moments"]] == ["dark", "light"]
    assert view["unknown"] == [
        "Wanneer het huis gaat slapen, leer ik nog.",
        "Wanneer het huis opstaat, leer ik nog.",
    ]
    await hm.stop()


async def test_late_in_the_evening_and_in_an_emergency(tmp_path: Path) -> None:
    """At 23:40, still up after the usual bedtime: the house goes to sleep 'later dan
    meestal', soon; it is dark already, so the next dusk is tomorrow's, after getting up and
    the light. In an alarm the emergency reactions come first: Straks says so and looks no
    further."""
    hm = await _ahead(tmp_path, start=datetime(2026, 11, 10, 22, 40, tzinfo=UTC))
    view = later(hm, "nl")
    assert [m["key"] for m in view["moments"]] == ["bed", "wake", "light", "dark"]
    assert _moment(view, "bed")["how"] == "later dan meestal"
    assert _moment(view, "bed")["at"] == "23:45"
    hm.status.emergency = Emergency.SMOKE
    view = later(hm, "nl")
    assert view["moments"] == [] and view["blocked"].startswith("Er is een noodgeval")
    await hm.stop()


async def test_from_involved_and_the_old_page_is_gone(tmp_path: Path) -> None:
    """Over the connection 'later' answers from Betrokken (level 3) on, not on Geïnformeerd.
    'simulate', the page 'Wat ik nu zou doen', answers nothing any more, and Brein keeps it as
    a tab no longer."""
    hm = await _ahead(tmp_path)

    async def answer(ws: Any, what: str) -> dict[str, Any] | None:
        await ws.send_json({"type": "get", "what": what})
        await ws.send_json({"type": "orb_tap"})  # Vesta's answer: everything before is handled
        found = None
        while (message := await ws.receive_json(timeout=5))["type"] != "say":
            if message["type"] == "data" and message["what"] == what:
                found = message["data"]
        return found

    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "set_level", "level": 2})
        assert await answer(ws, "later") is None
        await ws.send_json({"type": "set_level", "level": 3})
        view = await answer(ws, "later")
        assert view is not None and _moment(view, "bed")["title"] == "Het huis gaat slapen"
        await ws.send_json({"type": "set_level", "level": 4})
        assert await answer(ws, "simulate") is None
        await ws.close()
    assert "simulate" not in views.PREF_CHOICES["brain_tab"]
    for lang in ("nl", "en", "de", "fr"):
        assert hm.languages.text(lang, "ui.brain.tab.simulate") == "ui.brain.tab.simulate"
        assert hm.languages.text(lang, "later.title") != "later.title"
    await hm.stop()


async def test_vesta_knows_what_comes(tmp_path: Path) -> None:
    """Asked what she will do tonight, Vesta looks it up: the lookup 'plan' carries the moments
    ahead with what HouseMind would then do and whether it may – the same list as Straks."""
    hm = await _ahead(tmp_path)
    plan = await HouseLookups(hm, "nl", 10).plan({})
    assert [m["moment"] for m in plan["later"]][:2] == ["Het wordt donker", "Het huis gaat slapen"]
    assert "Woonkamer gordijnen sluiten. (Zou ik)" in plan["later"][0]["would"]
    await hm.stop()
