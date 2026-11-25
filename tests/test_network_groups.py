"""A group is one dot for its devices in the network (E-515), from the server's side: each room
gives its groups with their members, as Apparaten en entiteiten knows them (E-312), so the
screen can show the group and leave its devices out. The administrator's wish (14-11-2026):
"als er een groep is, zoals gedefinieerd in apparaten, dan wil ik dat je maar één apparaat laat
zien." Fails without the change: the rooms had no members."""

from __future__ import annotations

from pathlib import Path

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.test_mind import EVENING

ROOT = Path(__file__).resolve().parents[1]


async def test_each_room_gives_its_groups_with_their_members(tmp_path: Path) -> None:
    clock = SimulatedClock(EVENING)
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    rooms = {r["id"]: r for r in views.rooms(hm, "nl", 4)}
    living = {d["id"]: d for d in rooms["living"]["devices"]}
    assert sorted(living["light.living_room"]["members"]) == [
        "light.living_ceiling",
        "light.living_mood",
    ]
    assert living["light.living_room"]["radio"] is True  # a Hue room
    assert "members" not in living["light.living_ceiling"]
    dining = {d["id"]: d for d in rooms["dining"]["devices"]}
    assert dining["light.dining_table"]["radio"] is True  # a Zigbee2MQTT group
    await hm.stop()
