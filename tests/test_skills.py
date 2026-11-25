from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from housemind.brain.skills import SkillEngine, load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, StateChange
from housemind.demo.houses import DemoHouse, load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.catalog import Catalog
from housemind.safety.gate import ActionRequest
from housemind.world.house_mode import HouseMode
from housemind.world.world import World

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"
# Monday 19 October 2026, 19:00 UTC = 21:00 in Amsterdam (dark, evening).
EVENING = datetime(2026, 10, 19, 19, 0, tzinfo=UTC)


class Rig:
    def __init__(self, catalog: Catalog, house_key: str, start: datetime) -> None:
        self.clock = SimulatedClock(start)
        self.house: DemoHouse = load_demo_house(house_key, self.clock, "nl")
        words = role_words(load_languages(KNOWLEDGE / "languages"))
        roles = {a.id: area_role(a, words) for a in self.house.ha.model.areas.values()}
        self.world = World(
            self.house.ha.model, "Europe/Amsterdam", "NL", roles, set(self.house.pet_areas)
        )
        self.world.load_states(self.house.ha.states)
        self.engine = SkillEngine(
            catalog, self.house.ha.model, roles, load_skill_params([KNOWLEDGE / "skills"])
        )

    async def set(
        self, entity_id: str, state: str, by: Source = Source.PHYSICAL, **attrs: object
    ) -> None:
        old = self.house.ha.states.get(entity_id)
        await self.house.ha.simulate(entity_id, state, dict(attrs), by=by)
        new = self.house.ha.states[entity_id]
        self.world.on_change(StateChange(entity_id, old, new, by), self.clock.now())

    def run(self) -> list[ActionRequest]:
        snap = self.world.snapshot(self.clock.now())
        return self.engine.evaluate(snap, self.house.ha.states)

    def run_as(self, mode: HouseMode, hours: float) -> list[ActionRequest]:
        """As if the house were in this mode for so many hours (E-315)."""
        snap = self.world.snapshot(self.clock.now())
        since = snap.local - timedelta(hours=hours)
        snap = replace(snap, mode=mode, mode_since=since)
        return self.engine.evaluate(snap, self.house.ha.states)


def _by(requests: list[ActionRequest], entity_id: str) -> list[ActionRequest]:
    return [r for r in requests if r.entity_id == entity_id]


async def test_light_on_when_entering_in_the_dark(catalog: Catalog) -> None:
    rig = Rig(catalog, "apartment", EVENING)
    await rig.set("binary_sensor.living_presence", "on")
    requests = rig.run()
    ceiling = _by(requests, "light.living_ceiling")
    assert ceiling and ceiling[0].action == "light.turn_on"
    assert ceiling[0].data == {"brightness_pct": 35, "color_temp_kelvin": 2700}  # Normaal
    assert ceiling[0].skill == "light_on_entry" and ceiling[0].area_id == "living"
    assert ceiling[0].facts["context"] == "workday|21:00"


async def test_never_dark_suddenly_dim_first_then_off(catalog: Catalog) -> None:
    rig = Rig(catalog, "apartment", EVENING)
    await rig.house.ha.simulate("light.living_ceiling", "on", {"brightness": 200})
    rig.world.load_states(rig.house.ha.states)
    await rig.set("binary_sensor.living_presence", "on")
    await rig.set("binary_sensor.living_presence", "off")
    await rig.clock.advance(5 * 60)
    assert not _by(rig.run(), "light.living_ceiling")  # still within hold time
    await rig.clock.advance(20 * 60)
    first = _by(rig.run(), "light.living_ceiling")
    assert first and first[0].data == {"brightness_pct": 15} and first[0].facts.get("warning")
    await rig.clock.advance(70)
    second = _by(rig.run(), "light.living_ceiling")
    assert second and second[0].action == "light.turn_off"


async def test_coming_back_during_warning_restores_light(catalog: Catalog) -> None:
    rig = Rig(catalog, "apartment", EVENING)
    await rig.house.ha.simulate("light.living_ceiling", "on", {"brightness": 200})
    rig.world.load_states(rig.house.ha.states)
    await rig.set("binary_sensor.living_presence", "on")
    await rig.set("binary_sensor.living_presence", "off")
    await rig.clock.advance(26 * 60)
    assert _by(rig.run(), "light.living_ceiling")[0].facts.get("warning")
    await rig.set("light.living_ceiling", "on", Source.HOUSEMIND, brightness=77)  # dimmed to 30%
    await rig.set("binary_sensor.living_presence", "on")
    restore = _by(rig.run(), "light.living_ceiling")
    assert (
        restore and restore[0].data == {"brightness_pct": 78} and restore[0].facts.get("restored")
    )


async def test_coming_back_when_nothing_was_dimmed_puts_nothing_back(catalog: Catalog) -> None:
    """On Observe or Propose the warning dims nothing: coming back then puts nothing back."""
    rig = Rig(catalog, "apartment", EVENING)
    await rig.house.ha.simulate("light.living_ceiling", "on", {"brightness": 200})
    rig.world.load_states(rig.house.ha.states)
    await rig.set("binary_sensor.living_presence", "on")
    await rig.set("binary_sensor.living_presence", "off")
    await rig.clock.advance(26 * 60)
    assert _by(rig.run(), "light.living_ceiling")[0].facts.get("warning")
    await rig.set("binary_sensor.living_presence", "on")
    assert not _by(rig.run(), "light.living_ceiling")


async def test_night_light_in_hall_but_no_light_in_bedroom(catalog: Catalog) -> None:
    rig = Rig(catalog, "family_home", datetime(2026, 10, 20, 0, 30, tzinfo=UTC))  # 02:30 local
    rig.world.modes.set_manual(HouseMode.SLEEP, rig.clock.now())
    await rig.set("binary_sensor.bathroom_motion", "on")
    bathroom = _by(rig.run(), "light.bathroom")
    assert bathroom and bathroom[0].skill == "night_light"
    assert bathroom[0].data == {"brightness_pct": 15}  # Gedimd (E-494)
    # The hall is a pet area: motion alone while the house sleeps is perhaps the dog (E-90) –
    # not right after someone left the bathroom, which is a person walking (E-529).
    await rig.clock.advance(3 * 60)
    await rig.set("binary_sensor.hall_motion", "on")
    assert not _by(rig.run(), "light.hall")
    rig2 = Rig(catalog, "family_home", datetime(2026, 10, 20, 0, 30, tzinfo=UTC))
    await rig2.set("light.bedroom", "off")
    await rig2.set("binary_sensor.bathroom_motion", "on")
    assert not _by(rig2.run(), "light.bedroom")


async def test_heating_follows_house_mode_and_open_window(catalog: Catalog) -> None:
    rig = Rig(catalog, "family_home", EVENING)
    # Away for a few hours: the heating stays as it is – they will be back (E-315).
    assert not _by(rig.run_as(HouseMode.AWAY, 23), "climate.living")
    # Away for over a day (a vacation, a business trip): lower.
    living = _by(rig.run_as(HouseMode.AWAY, 25), "climate.living")
    assert living and living[0].data == {"temperature": 17.5} and living[0].skill == "setback_away"
    # Vacation chosen by a person: lower at once.
    living = _by(rig.run_as(HouseMode.VACATION, 0), "climate.living")
    assert living and living[0].data == {"temperature": 17.5} and living[0].skill == "setback_away"
    rig.world.modes.set_manual(HouseMode.HOME, rig.clock.now())
    await rig.set("binary_sensor.bedroom_window", "on")
    rig.run()
    await rig.clock.advance(4 * 60)
    bedroom = _by(rig.run(), "climate.bedroom")
    assert (
        bedroom
        and bedroom[0].skill == "window_open_setback"
        and bedroom[0].data == {"temperature": 12.0}
    )


async def test_leaving_a_room_leaves_the_heating(catalog: Catalog) -> None:
    """Someone walking out of a room is somewhere else in the house (E-315)."""
    rig = Rig(catalog, "family_home", EVENING - timedelta(hours=4))  # 17:00 local
    await rig.set("binary_sensor.living_presence", "on")
    rig.run()
    await rig.clock.advance(10 * 60)
    await rig.set("binary_sensor.living_presence", "off")
    await rig.clock.advance(60 * 60)
    assert not _by(rig.run(), "climate.living")


async def test_the_room_itself_reaches_the_wish(catalog: Catalog) -> None:
    """A thermostat that measures warmer than the room: half a degree more at a time, at most
    once an hour; warm enough stays; a whole degree too warm goes half a degree back (E-315)."""
    rig = Rig(catalog, "family_home", EVENING - timedelta(hours=3))  # 18:00 local, at home
    room = {"living": 19.5}
    rig.engine.room_air = lambda area_id, states: room.get(area_id)
    await rig.set("climate.living", "heat", temperature=20.5, current_temperature=20.4)
    up = _by(rig.run(), "climate.living")
    assert up and up[0].data == {"temperature": 21.0} and up[0].facts["variant"] == "reach"
    await rig.set("climate.living", "heat", temperature=21.0, current_temperature=21.0)
    await rig.clock.advance(30 * 60)
    assert not _by(rig.run(), "climate.living")  # not again within the hour
    room["living"] = 20.6  # warm enough: it stays, no back and forth
    await rig.clock.advance(60 * 60)
    assert not _by(rig.run(), "climate.living")
    # Cooling in summer does not take over the half degree of the winter.
    await rig.set("climate.living", "cool", temperature=24.0, current_temperature=24.0)
    room["living"] = 24.2
    assert not _by(rig.run(), "climate.living")
    await rig.set("climate.living", "heat", temperature=21.0, current_temperature=21.0)
    room["living"] = 21.6  # a whole degree too warm: half a degree back
    back = _by(rig.run(), "climate.living")
    assert back and back[0].data == {"temperature": 20.5} and back[0].facts["variant"] == "ease"
    # Without a sensor of its own the room is not measured apart: the wish as it is.
    plain = Rig(catalog, "family_home", EVENING - timedelta(hours=3))
    await plain.set("climate.living", "heat", temperature=20.5, current_temperature=19.0)
    assert not _by(plain.run(), "climate.living")


async def test_a_heat_pump_that_heats_and_cools(catalog: Catalog) -> None:
    """E-318 (B-185): one setpoint for heating and cooling – the wish a person set, reached in
    the room in both directions; a day away uses its own "away", and back home its usual mode.
    Before, a heat pump on "heat and cool" was left alone altogether."""
    rig = Rig(catalog, "family_home", EVENING - timedelta(hours=3))  # 18:00 local, at home
    room: dict[str, float] = {}
    before: dict[str, str] = {}
    rig.engine.room_air = lambda area_id, states: room.get(area_id)
    rig.engine.hc_wish = lambda entity_id, state: 19.5
    rig.engine.hc_before = lambda entity_id: before.get(entity_id)
    pump = {"preset_modes": ["away", "none"], "preset_mode": "none"}
    await rig.set("climate.living", "heat_cool", temperature=19.5, current_temperature=19.4, **pump)
    assert not _by(rig.run(), "climate.living")  # no room sensor of its own: as it is
    room["living"] = 18.8
    up = _by(rig.run(), "climate.living")
    assert up and up[0].skill == "keep_temperature" and up[0].data == {"temperature": 20.0}
    assert up[0].facts["variant"] == "warmer"
    # Too warm while it measures its own setting: half a degree down.
    cool = Rig(catalog, "family_home", EVENING - timedelta(hours=3))
    cool.engine.room_air = lambda area_id, states: {"living": 20.3}.get(area_id)
    cool.engine.hc_wish = lambda entity_id, state: 19.5
    await cool.set("climate.living", "heat_cool", temperature=19.5, current_temperature=19.6,
                   **pump)  # fmt: skip
    down = _by(cool.run(), "climate.living")
    assert down and down[0].data == {"temperature": 19.0} and down[0].facts["variant"] == "cooler"
    # Away: nothing the first day; then its own "away", remembering what it was.
    assert not _by(rig.run_as(HouseMode.AWAY, 5), "climate.living")
    away = _by(rig.run_as(HouseMode.AWAY, 25), "climate.living")
    assert away and away[0].skill == "setback_away" and away[0].action == "climate.set_preset_mode"
    assert away[0].data == {"preset_mode": "away"} and away[0].facts["before"] == "none"
    # Back home: its usual mode again – but only when HouseMind chose "away" itself.
    await rig.set("climate.living", "heat_cool", temperature=19.5, current_temperature=19.4,
                  preset_modes=["away", "none"], preset_mode="away")  # fmt: skip
    assert not _by(rig.run(), "climate.living")  # a person chose "away"
    before["climate.living"] = "none"
    back = _by(rig.run(), "climate.living")
    assert back and back[0].data == {"preset_mode": "none"} and back[0].facts["variant"] == "home"
    # With a lower and an upper limit it stays as people set it.
    two = Rig(catalog, "family_home", EVENING - timedelta(hours=3))
    two.engine.room_air = lambda area_id, states: {"living": 17.0}.get(area_id)
    two.engine.hc_wish = lambda entity_id, state: 19.5
    await two.set("climate.living", "heat_cool", temperature=None, target_temp_low=19.0,
                  target_temp_high=24.0, current_temperature=19.0)  # fmt: skip
    assert not _by(two.run(), "climate.living")


async def test_cooling_reaches_the_wish_and_saves_when_long_away(catalog: Catalog) -> None:
    rig = Rig(catalog, "family_home", EVENING - timedelta(hours=3))
    await rig.set("climate.living", "cool", temperature=26.0, current_temperature=26.5)
    cool = _by(rig.run(), "climate.living")
    assert cool and cool[0].skill == "cool_on_presence" and cool[0].data == {"temperature": 24.0}
    assert not _by(rig.run_as(HouseMode.AWAY, 2), "climate.living")
    away = _by(rig.run_as(HouseMode.AWAY, 30), "climate.living")
    assert away and away[0].skill == "setback_away" and away[0].data == {"temperature": 27.0}
    # An open window changes nothing for cooling (no 12 °C on an airco).
    await rig.set("binary_sensor.bedroom_window", "on")
    await rig.set("climate.bedroom", "cool", temperature=25.0, current_temperature=25.0)
    rig.run()
    await rig.clock.advance(4 * 60)
    assert not _by(rig.run(), "climate.bedroom")


async def test_covers_close_once_when_dark(catalog: Catalog) -> None:
    rig = Rig(catalog, "apartment", EVENING)
    first = _by(rig.run(), "cover.living_curtain")
    assert first and first[0].action == "cover.close_cover"
    await rig.clock.advance(60)
    assert not _by(rig.run(), "cover.living_curtain")  # once per day
    morning = Rig(catalog, "apartment", datetime(2026, 10, 20, 6, 30, tzinfo=UTC))  # 08:30 local
    await morning.house.ha.simulate("cover.living_curtain", "closed")
    morning.world.load_states(morning.house.ha.states)
    morning.world.states["sun.sun"] = morning.world.states["light.hall"].__class__(
        "sun.sun", "above_horizon", {"elevation": 12.0}, morning.clock.now()
    )
    opened = _by(morning.run(), "cover.living_curtain")
    assert opened and opened[0].action == "cover.open_cover"


async def test_no_request_when_state_already_right(catalog: Catalog) -> None:
    rig = Rig(catalog, "apartment", EVENING + timedelta(hours=-6))  # 15:00 local, daylight guess
    await rig.set("binary_sensor.living_presence", "on")
    assert not _by(rig.run(), "light.living_ceiling")
