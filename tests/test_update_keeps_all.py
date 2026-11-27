"""An update keeps everything (E-44, E-578), as the administrator of the real house asked:
"datgene wat hij al geleerd heeft: alle instellingen moeten behouden blijven, en alleen de
nieuwe versie moet overschreven worden". In Home Assistant an update replaces the App and
keeps its data folder; HouseMind then starts on the same database. From the user's side, over
the same websocket the screens use. What it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind import __version__
from housemind.brain import setting_up
from housemind.brain.automations import Status
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_automations import HALL, ROOT, start
from tests.test_start_over import _hello, _snapshot, _taken_over

LAMP = "light.kitchen"
RESIDENT = {"X-Remote-User-Id": "user-update"}


async def test_an_update_keeps_what_was_learned_and_set_and_only_says_what_is_new(
    tmp_path: Path,
) -> None:
    """A house that is set up and learned, with its own settings, standen and a wait before
    off, gets a new version. After it: HouseMind is on at once, the setup does not come back,
    the introduction does not come back, and everything is still there – what it learned, the
    automation it took over, the settings, the standen and the wait. The only new thing on the
    screen is "Wat is nieuw", once, until it is read."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None and await setting_up.state(hm) == setting_up.DONE
    await _taken_over(hm, clock)
    await hm.learning.grant("light_on_entry", "hall", clock.now(), "yes")  # a yes is kept
    await ha.simulate(LAMP, "on")
    await hm.set_house_setting("pet_areas", ["hall"])
    assert await hm.levels_save(LAMP, {"pct": [10, 30, 55, 90]})
    assert await hm.off_wait_save(LAMP, 4)
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, preferences, consents,"
        " created) VALUES(?,?,?,?,?,?)",
        (
            "user-update",
            "Bewoner",
            3,
            json.dumps({"onboarded": True, "seen_version": "0.4.0"}),
            "{}",
            "2026-10-01",
        ),
    )
    counts = dict(hm.learning.counts)
    assert counts
    await hm.stop()

    # The update: the new App starts on the same data folder.
    new = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), {"hall"})
    await new.start(autorun=False)
    assert new.learning is not None
    assert new.activated and new.orb_state() != "inactive"
    assert await setting_up.state(new) == setting_up.DONE
    assert new.learning_allowed and new.pet_areas == {"hall"}
    assert new.learning.step("light_on_entry", "hall") is Step.AUTONOMOUS
    assert dict(new.learning.counts) == counts
    assert new.automations.status[HALL] is Status.TURNED_OFF
    assert new.light_levels.own[LAMP].pct == (10, 30, 55, 90)
    assert new.light_levels.off_wait(LAMP) == 4

    async with TestClient(TestServer(create_app(new, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, RESIDENT)
        snap = await _snapshot(ws)
        assert snap["setup"]["state"] == setting_up.DONE
        assert not snap["onboarding"]["person"]
        assert snap["news"] is not None and snap["news"]["version"] == __version__
        await ws.send_json({"type": "set_pref", "key": "seen_version", "value": __version__})
        await _snapshot(ws, lambda s: s["news"] is None)
        await ws.close()
    await new.stop()
