"""'Speel het huis' (E-321, chapter 25): play the demo house and see what HouseMind does.

Only a demo house has one: ``__main__`` makes a :class:`Play` and gives it to the server, which
ignores every play message without it (B-187). Everything goes through the simulated Home
Assistant, as a person, a wall switch, the weather or a sensor would in a real house; the
house's own clock (:class:`DemoClock`) jumps ahead or runs faster (B-188). The controls come
from what the house has – kinds and rooms, never fixed names (B-191).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.brain.proposals import Answer
from housemind.brain.readiness import readiness
from housemind.core.clock import DemoClock
from housemind.core.model import Entity, Source
from housemind.demo.energy_sim import EnergySim
from housemind.demo.houses import DemoHouse
from housemind.knowledge.area_roles import SLEEPING_ROLES
from housemind.runtime import HouseMind
from housemind.safety.emergency import Emergency
from housemind.server.logbook import logbook
from housemind.server.views import Viewer

_LOGGER = logging.getLogger(__name__)

# From twenty times slower to twenty times faster than the real time (the administrator's wish).
SPEEDS = (0.05, 0.1, 1.0, 10.0, 20.0)
KEEP_S = 10  # while it plays, how often the house's time is kept (real seconds)
JUMPS = ("morning", "evening", "night", "day", "week")
AT = {"morning": time(7, 0), "evening": time(19, 0), "night": time(23, 30)}
STEP_S = 300  # a jump goes in steps of 5 minutes; after each, the residents answer (B-189)
# A jump of an hour or more: the brain's round and the energy simulation every 5 minutes, like
# the release tests – otherwise a week of rounds every 30 seconds takes a minute (B-189).
COARSE = {"housemind-tick": 300.0, "demo-energy": 300.0}
LONG_S = 3600
YES = 0.95  # how often they say 'Doe het' – like the release tests
WEATHER = ("sunny", "cloudy", "rainy")
SKY = {"sunny": 1.0, "cloudy": 0.35, "rainy": 0.15}
OUTDOOR = (-5, 5, 15, 25, 32)
PRICES = {"dear": 0.45, "cheap": 0.03, "negative": -0.05}  # € per kWh, the next two hours
PRICE_HOURS = 2
PRESENCE = ("motion", "presence", "occupancy")
OPENINGS = ("door", "window", "garage_door", "opening")
SWITCHES = ("light", "switch", "fan", "cover")
EMERGENCY_KEYS = {
    Emergency.SMOKE: "smoke",
    Emergency.CARBON_MONOXIDE: "co",
    Emergency.GAS: "gas",
    Emergency.LEAK: "leak",
    Emergency.FROST: "frost",
}
HAPPENED = 12  # lines under 'Wat er net gebeurde'


def load_clock(store: Path) -> DemoClock:
    """The demo house's clock as it was left (B-188): its time, playing or stopped, and its
    speed. Its time never goes back: while it played, a little more than the last time kept."""
    try:
        kept = json.loads(store.read_text(encoding="utf-8"))
        if not isinstance(kept, dict):
            raise TypeError
    except (OSError, ValueError, TypeError):
        return DemoClock()
    now = datetime.now(UTC)
    running = kept.get("running", True) is not False
    speed = float(kept["speed"]) if kept.get("speed") in SPEEDS else 1.0
    try:
        if "at" in kept:
            at = datetime.fromisoformat(str(kept["at"]))
            if running:
                at += timedelta(seconds=2 * KEEP_S * speed)  # what it played after the last keep
        else:  # kept by the first version: how far ahead it was
            at = now + timedelta(seconds=max(0.0, float(kept.get("shift", 0))))
    except (TypeError, ValueError):
        return DemoClock()
    return DemoClock(at if at.tzinfo else at.replace(tzinfo=UTC), running, speed)


class Locked(Exception):
    """The house plays: a story or a jump waits for Stop (B-188). What you set yourself goes
    along at once while it plays (E-473)."""


class Busy(Exception):
    """A jump or a story is still going."""


class Play:
    def __init__(
        self,
        house: DemoHouse,
        clock: DemoClock,
        hm: HouseMind,
        time_zone: str,
        store: Path,
        sim: EnergySim | None = None,
        seed: int = 7,
    ) -> None:
        self.house = house
        self.ha = house.ha
        self.clock = clock
        self.hm = hm
        self.tz = ZoneInfo(time_zone)
        self.store = store
        self.sim = sim
        self.held = False  # 'Lang weg': the residents stay away until 'Iedereen thuis'
        self.busy: dict[str, Any] | None = None
        # A long jump goes on: what HouseMind does then is in Logboek, not a message per action
        # on the screen – a week of 'Ik heb … aangedaan' would pile up.
        self.forwarding = False
        self.last: dict[str, Any] | None = None  # the last tap: what, and since when
        self.on_change: Callable[[], Awaitable[None]] | None = None  # the screens
        self._rng = random.Random(seed)  # noqa: S311 - demo behaviour, not security
        self._task: asyncio.Task[None] | None = None
        self._weather_set: str | None = None  # a weather report without the energy simulation

    # -- what the house has ---------------------------------------------------------------

    def _entities(self, domain: str, classes: tuple[str, ...] = ()) -> list[Entity]:
        found = [
            e
            for e in self.ha.model.entities.values()
            if e.domain == domain
            and e.entity_id in self.ha.states
            and (not classes or e.device_class in classes)
        ]
        return sorted(found, key=lambda e: (self._room(e.area_id), e.name))

    def _room(self, area_id: str | None) -> str:
        area = self.ha.model.areas.get(area_id or "")
        return area.name if area else ""

    def _state(self, entity_id: str) -> str:
        state = self.ha.states.get(entity_id)
        return state.state if state else ""

    def _persons(self) -> list[Entity]:
        return self._entities("person")

    def _presence(self) -> dict[str, Entity]:
        """One movement or presence sensor per room."""
        rooms: dict[str, Entity] = {}
        for entity in self._entities("binary_sensor", PRESENCE):
            if entity.area_id and entity.area_id not in rooms:
                rooms[entity.area_id] = entity
        return rooms

    def _resident_user(self) -> str | None:
        for person in self._persons():
            user = self.ha.states[person.entity_id].attributes.get("user_id")
            if isinstance(user, str):
                return user
        return None

    def _emergencies(self) -> list[tuple[str, Entity]]:
        reactions = self.hm.emergencies
        found = []
        for entity in self._entities("binary_sensor"):
            kind = reactions.emergency_for(entity.entity_id) if reactions else None
            if kind in EMERGENCY_KEYS:
                found.append((EMERGENCY_KEYS[kind], entity))
        found += [("burglar", e) for e in self._entities("alarm_control_panel")]
        return found

    def _first(self, key: str) -> Entity | None:
        return next((e for k, e in self._emergencies() if k == key), None)

    def _weather(self) -> Entity | None:
        found = self._entities("weather")
        return found[0] if found else None

    def _temperatures(self) -> dict[str, list[str]]:
        """Per room: the thermostats and temperature sensors that tell its temperature."""
        rooms: dict[str, list[str]] = {}
        for entity in self._entities("climate"):
            if entity.area_id:
                rooms.setdefault(entity.area_id, []).append(entity.entity_id)
        for entity in self._entities("sensor", ("temperature",)):
            if entity.area_id and entity.area_id in rooms:
                rooms[entity.area_id].append(entity.entity_id)
        return rooms

    def _temperature(self, entity_id: str) -> float | None:
        state = self.ha.states[entity_id]
        raw: Any = (
            state.attributes.get("current_temperature")
            if entity_id.startswith("climate.")
            else state.state
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _has_prices(self) -> bool:
        return self.sim is not None and "sensor.electricity_price" in self.ha.states

    def _has_solar(self) -> bool:
        return self.sim is not None and any(
            e.device_class == "power" and "solar" in e.entity_id for e in self._entities("sensor")
        )

    def _walk(self, *roles: str) -> list[Entity]:
        """The sensors along a way through the house, by what the rooms are for – not by their
        names, which differ per language. A role counts once, in a room with a lamp."""
        lit = {e.area_id for e in self._entities("light")}
        way = []
        for role in roles:
            rooms = self._presence().items()
            found = (s for a, s in rooms if self.hm.area_roles.get(a) == role and a in lit)
            sensor = next(found, None)
            if sensor is not None:
                way.append(sensor)
        return way

    def _living(self) -> Entity:
        """The sensor of the living room, or of another room than the hall."""
        rooms = self._presence()
        living = [s for a, s in rooms.items() if self.hm.area_roles.get(a) == "living"]
        others = [s for a, s in rooms.items() if a != self._entry_area()]
        return (living or others or list(rooms.values()))[0]

    def _entry_area(self) -> str | None:
        door = self._front_door()
        return door.area_id if door else None

    def _front_door(self) -> Entity | None:
        doors = self._entities("binary_sensor", ("door",))
        hall = self._presence()
        return next((d for d in doors if d.area_id in hall), doors[0] if doors else None)

    def _same_room(self, domain: str, classes: tuple[str, ...], with_domain: str) -> Entity | None:
        rooms = {e.area_id for e in self._entities(with_domain) if e.area_id}
        return next((e for e in self._entities(domain, classes) if e.area_id in rooms), None)

    # -- the stories (B-190) -----------------------------------------------------------

    def stories(self) -> list[str]:
        has_people = bool(self._persons())
        rooms = self._presence()
        lit = {e.area_id for e in self._entities("light")}
        humid = self._same_room("sensor", ("humidity",), "fan")
        window = self._same_room("binary_sensor", ("window",), "climate")
        wanted = {
            "home_dark": has_people and any(a in lit for a in rooms),
            "leave": has_people and bool(rooms),
            "away_long": has_people and bool(self._entities("climate")),
            "wake": has_people and bool(rooms),
            "shower": humid is not None,
            "window": window is not None,
            "guests": has_people and bool(rooms),
            "smoke": self._first("smoke") is not None,
            "leak": self._first("leak") is not None,
            "frost": self._first("frost") is not None,
            "burglar": self._first("burglar") is not None,
            "price_negative": self._has_prices(),
            "sun_full": self._has_solar(),
            "update": bool(self.ha.config_entries_list),
        }
        return [key for key, ok in wanted.items() if ok]

    async def _story(self, key: str) -> None:
        story = getattr(self, f"_story_{key}")
        await story()

    def _begin(self, kind: str, key: str) -> None:
        """From here on, what HouseMind does shows under 'Wat er net gebeurde' (25.6)."""
        self.last = {"kind": kind, "key": key, "since": self.clock.now()}

    async def _set(
        self,
        entity_id: str | None,
        state: str,
        attributes: dict[str, Any] | None = None,
        by: Source = Source.PHYSICAL,
    ) -> None:
        if entity_id is not None and entity_id in self.ha.states:
            await self.ha.simulate(entity_id, state, attributes, by=by)

    async def _wait(self, minutes: float) -> None:
        await self.clock.advance(minutes * 60)

    async def _to(self, at: time) -> None:
        """To the next moment of the day at ``at`` – the residents live the hours between."""
        now = self.clock.now().astimezone(self.tz)
        target = datetime.combine(now.date(), at, tzinfo=self.tz)
        if target <= now:
            target = datetime.combine(now.date() + timedelta(days=1), at, tzinfo=self.tz)
        await self._forward((target - now).total_seconds(), "to")

    async def _to_dark(self) -> None:
        if not self._dark():
            await self._to(AT["evening"])
        for _ in range(12):  # summer: later than seven
            if self._dark():
                break
            await self._forward(1800, "to")

    def _dark(self) -> bool:
        world = self.hm.world
        return world is not None and world.snapshot(self.clock.now()).outside_dark

    async def _everyone_away(self) -> None:
        door = self._front_door()
        for sensor in self._presence().values():
            await self._set(sensor.entity_id, "off")
        await self._set(door.entity_id if door else None, "on")
        for person in self._persons():
            await self._set(person.entity_id, "not_home", by=Source.UNKNOWN)
        await self._set(door.entity_id if door else None, "off")

    async def _story_home_dark(self) -> None:
        await self._to_dark()
        if any(self._state(p.entity_id) == "home" for p in self._persons()):
            await self._everyone_away()
            await self._wait(15)  # the house is empty: Afwezig after ten minutes
        self._begin("story", "home_dark")
        door = self._front_door()
        rooms = self._presence()
        entry = rooms.get(door.area_id or "") if door else None
        entry = entry or next(iter(rooms.values()))
        await self._set(door.entity_id if door else None, "on")
        await self._set(self._persons()[0].entity_id, "home", by=Source.UNKNOWN)
        await self._wait(0.5)
        await self._set(door.entity_id if door else None, "off")
        await self._set(entry.entity_id, "on")
        await self._wait(2)
        living = self._living()
        if living is not entry:
            await self._set(entry.entity_id, "off")
            await self._set(living.entity_id, "on")
            await self._wait(2)

    async def _story_leave(self) -> None:
        """Someone sat in a room with the lamp on; then everyone leaves."""
        rooms = self._presence()
        lamps = [e for e in self._entities("light") if e.area_id in rooms]
        living = self._living().area_id  # now every room has a sensor: not the first by name
        lamp = next((e for e in lamps if e.area_id == living), None)
        lamp = lamp or next((e for e in lamps if e.area_id != self._entry_area()), None)
        lamp = lamp or (lamps[0] if lamps else None)
        if lamp is not None and lamp.area_id:
            await self._set(rooms[lamp.area_id].entity_id, "on")
            await self._switch(lamp.entity_id, True, Source.PHYSICAL)
            await self._wait(30)
        self._begin("story", "leave")
        await self._everyone_away()
        await self._wait(25)

    async def _story_away_long(self) -> None:
        self._begin("story", "away_long")
        await self._everyone_away()
        self.held = True  # they do not come home by themselves now
        await self._forward(25 * 3600, "away_long", answer=False)

    async def _story_wake(self) -> None:
        await self._to(time(6, 30))
        self._begin("story", "wake")
        rooms = self._presence()
        for person in self._persons():
            await self._set(person.entity_id, "home", by=Source.UNKNOWN)
        bedroom = next(
            (
                e
                for e in self._entities("light")
                if self.hm.area_roles.get(e.area_id or "") in SLEEPING_ROLES
            ),
            None,
        )
        here = rooms.get(bedroom.area_id or "") if bedroom else None
        await self._set(here.entity_id if here else None, "on")
        await self._set(bedroom.entity_id if bedroom else None, "on", by=Source.PERSON)
        humid = self._entities("sensor", ("humidity",))
        await self._wait(3)
        await self._set(humid[0].entity_id if humid else None, "84")
        await self._set(bedroom.entity_id if bedroom else None, "off", by=Source.PERSON)
        for sensor in self._walk("bathroom", "hall", "kitchen"):  # shower, then downstairs
            await self._set(here.entity_id if here else None, "off")
            await self._set(sensor.entity_id, "on")
            here = sensor
            await self._wait(1)
        await self._wait(10)

    async def _story_shower(self) -> None:
        self._begin("story", "shower")
        humid = self._same_room("sensor", ("humidity",), "fan")
        assert humid is not None
        motion = self._presence().get(humid.area_id or "")
        await self._set(motion.entity_id if motion else None, "on")
        await self._set(humid.entity_id, "84")
        await self._wait(10)
        await self._set(humid.entity_id, "72")
        await self._set(motion.entity_id if motion else None, "off")
        await self._wait(20)
        await self._set(humid.entity_id, "56")
        await self._wait(10)

    async def _story_window(self) -> None:
        self._begin("story", "window")
        window = self._same_room("binary_sensor", ("window",), "climate")
        assert window is not None
        await self._set(window.entity_id, "on")
        await self._wait(10)
        await self._set(window.entity_id, "off")
        await self._wait(5)

    async def _story_guests(self) -> None:
        if any(self._state(p.entity_id) == "home" for p in self._persons()):
            await self._everyone_away()
            await self._wait(15)
        self._begin("story", "guests")
        door = self._front_door()
        await self._set(door.entity_id if door else None, "on")
        await self._wait(0.5)
        await self._set(door.entity_id if door else None, "off")
        await self._set(self._living().entity_id, "on")
        await self._wait(5)

    async def _alarm(self, key: str) -> None:
        self._begin("story", key)
        sensor = self._first(key)
        assert sensor is not None
        await self._emergency(sensor.entity_id, True)
        await self._wait(1)

    async def _story_smoke(self) -> None:
        await self._alarm("smoke")

    async def _story_leak(self) -> None:
        await self._alarm("leak")

    async def _story_frost(self) -> None:
        await self._alarm("frost")

    async def _story_burglar(self) -> None:
        if any(self._state(p.entity_id) == "home" for p in self._persons()):
            await self._everyone_away()
            await self._wait(15)
        self._begin("story", "burglar")
        await self._alarm("burglar")

    async def _story_price_negative(self) -> None:
        self._begin("story", "price_negative")
        await self._price("negative")
        await self._wait(15)

    async def _story_update(self) -> None:
        """Home Assistant is updated; after it the smallest integration no longer starts and a
        lamp is away for a while (Home Assistant netjes, B-242). The first look is after 30
        minutes; what came back shows at the look after 2 hours."""
        await self.hm.tidy_round()  # what works now: the state from before the update
        self._begin("story", "update")
        owned: dict[str, list[str]] = {}
        for e in self.ha.model.entities.values():
            if e.config_entry_id and e.entity_id in self.ha.states:
                owned.setdefault(e.config_entry_id, []).append(e.entity_id)
        loaded = [
            x for x in self.ha.config_entries_list if x.state == "loaded" and x.entry_id in owned
        ]
        entry = min(loaded, key=lambda x: len(owned[x.entry_id]), default=None)
        lamps = [
            e for e in self._entities("light") if not e.config_entry_id and e.platform != "group"
        ]
        lamp = lamps[0].entity_id if lamps else None
        await self.ha.drop()
        version = self.ha.config.version
        major, _, patch = version.rpartition(".")
        newer = f"{major}.{int(patch) + 1}" if patch.isdigit() else f"{version}.1"
        if entry is not None:
            self.ha.config_entries_list = [
                replace(x, state="setup_retry") if x is entry else x
                for x in self.ha.config_entries_list
            ]
        for entity_id in [*(owned[entry.entry_id] if entry else []), *([lamp] if lamp else [])]:
            self.ha.states[entity_id] = replace(self.ha.states[entity_id], state="unavailable")
        await self._wait(1)
        await self.ha.come_back(version=newer)
        await self._wait(50)  # the round after 10 minutes, the first look after 30
        if lamp is not None:
            await self._set(lamp, "off")

    async def _story_sun_full(self) -> None:
        hour = self.clock.now().astimezone(self.tz).hour
        if not 10 <= hour < 15:
            await self._to(time(12, 0))
        self._begin("story", "sun_full")
        await self._sky("sunny")
        await self._wait(15)

    # -- the time (B-188, B-189) --------------------------------------------------------

    async def _forward(self, seconds: float, what: str, answer: bool = True) -> None:
        """Jump ahead in steps of 5 minutes; after each, the residents answer the questions
        like someone with their rhythm would (mostly 'Doe het')."""
        end = self.clock.now() + timedelta(seconds=seconds)
        shown = -1.0
        if seconds >= LONG_S:
            self.clock.coarse = dict(COARSE)
            self.forwarding = True
        try:
            while (left := (end - self.clock.now()).total_seconds()) > 0:
                await self.clock.advance(min(STEP_S, left))
                if answer:
                    await self._answer()
                done = round(1 - max(0.0, (end - self.clock.now()).total_seconds()) / seconds, 2)
                if self.busy is not None and done - shown >= 0.05:
                    shown = done
                    self.busy = {**self.busy, "done": done}
                    await self._changed()
        finally:
            self.clock.relax()
            self.forwarding = False
        self.save()

    async def _answer(self) -> None:
        user = self._resident_user()
        for proposal in await self.hm.open_proposals():
            choice = Answer.DO if self._rng.random() < YES else Answer.NOT_NOW
            await self.hm.answer(proposal.id, choice, user)

    async def _jump(self, to: str) -> None:
        self._begin("jump", to)
        if to in AT:
            await self._to(AT[to])
        else:
            await self._forward(7 * 86400 if to == "week" else 86400, to)

    def save(self) -> None:
        """Keep the house's time, playing or stopped, and the speed – as it was left (B-188)."""
        kept = {
            "at": self.clock.now().isoformat(),
            "running": self.clock.running,
            "speed": self.clock.speed,
        }
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(kept), encoding="utf-8")
        except OSError:
            _LOGGER.warning("Could not keep the demo house's time")

    async def settle(self) -> None:
        """At the start of a demo house: an emergency HouseMind still holds open from before the
        restart, while its sensor is off again in the fresh demo house, closes (the
        administrator's wish, 22-10-2026). Such a sensor reports again, as a device does when it
        comes back after a restart; one that is still on stays an emergency."""
        open_topics = {
            str(n.get("topic"))
            for n in await self.hm.notifications()
            if n.get("kind") == "emergency"
        }
        for _, entity in self._emergencies():
            state = self._state(entity.entity_id)
            if state in ("on", "triggered"):
                continue
            if not any(t.endswith(f":{entity.entity_id}") for t in open_topics):
                continue
            await self._set(entity.entity_id, "unknown")
            await self._set(entity.entity_id, state)

    async def keep(self) -> None:
        """While the house plays, keep its time every few seconds (B-188)."""
        while True:
            await asyncio.sleep(KEEP_S)
            if self.clock.running:
                self.save()

    # -- the controls (B-191) -----------------------------------------------------------

    async def _emergency(self, entity_id: str, on: bool) -> None:
        if entity_id.startswith("alarm_control_panel."):
            if on:
                if self._state(entity_id) == "disarmed":
                    await self._set(entity_id, "armed_away", by=Source.PERSON)
                await self._set(entity_id, "triggered")
            else:
                await self._set(entity_id, "disarmed", by=Source.PERSON)
            return
        await self._set(entity_id, "on" if on else "off")

    async def _price(self, level: str | None) -> None:
        if self.sim is None:
            return
        if level in PRICES:
            local = self.clock.now().astimezone(self.tz)
            start = local.replace(minute=local.minute - local.minute % 15, second=0, microsecond=0)
            self.sim.price = (start, start + timedelta(hours=PRICE_HOURS), PRICES[level])
        else:
            self.sim.price = None
        self.sim.forget_prices()
        await self.sim.prices_now(self.clock.now())

    async def _sky(self, condition: str | None) -> None:
        if self.sim is not None:
            self.sim.sky = SKY.get(condition or "")
            self.sim.forget_prices()  # the sun moves the prices too

    async def _weather_now(self) -> None:
        weather = self._weather()
        if weather is None:
            return
        attrs: dict[str, Any] = {}
        if self.sim is not None and self.sim.outdoor is not None:
            attrs["temperature"] = self.sim.outdoor
        condition = self.sim.condition() if self.sim is not None else self._weather_set
        await self._set(weather.entity_id, condition or self._state(weather.entity_id), attrs)

    async def control(self, data: dict[str, Any]) -> None:
        """One tap on a control. Raises :class:`Busy` while a jump or story goes on, and
        :class:`Locked` for a story or jump while the house plays: those wait for Stop (B-188).
        What you set yourself goes along at once, also while it plays (E-473)."""
        do = data.get("do")
        if do == "speed":
            value = data.get("value")
            if value in SPEEDS:
                self.clock.set_speed(float(value))
                self.save()
                await self._changed()  # every screen shows the new speed at once
            return
        if do == "stop" and self.busy is not None:
            await self._halt()  # Stop also halts a story or jump that goes on (wish, 22-10)
            return
        if self.busy is not None:
            raise Busy
        if do in ("play", "stop"):
            if do == "play":
                self.clock.play()
            else:
                self.clock.stop()
            self.save()
            await self._changed()  # every screen shows Play or Stop at once
            return
        if do in ("jump", "story"):
            if self.clock.running:
                raise Locked  # chosen at Stop: it plays at once, the clock jumps (B-188)
            key = data.get("to") if do == "jump" else data.get("key")
            if (do == "jump" and key in JUMPS) or (do == "story" and key in self.stories()):
                self._start(str(do), str(key))
            return
        self._begin("control", str(do))
        target = data.get("id")
        target = target if isinstance(target, str) and target in self.ha.states else None
        on = data.get("on") is True
        if do == "presence" and target is not None:
            await self._set(target, "on" if on else "off")
        elif do == "resident" and target is not None and target.startswith("person."):
            await self._set(target, "home" if on else "not_home", by=Source.UNKNOWN)
            if on:
                self.held = False
        elif do == "everyone":
            if on:
                self.held = False
                for person in self._persons():
                    await self._set(person.entity_id, "home", by=Source.UNKNOWN)
            else:
                await self._everyone_away()
        elif do == "switch" and target is not None and target.split(".")[0] in SWITCHES:
            by = Source.PERSON if data.get("by") == "person" else Source.PHYSICAL
            await self._switch(target, on, by)
        elif do == "opening" and target is not None and target.startswith("binary_sensor."):
            await self._set(target, "on" if on else "off")
        elif do == "weather" and self._weather() is not None:
            condition = data.get("value")
            condition = condition if condition in WEATHER else None
            if self.sim is not None:
                self.sim.weather = condition
                await self._sky(condition)
            else:
                self._weather_set = condition
            await self._weather_now()
        elif do == "outdoor" and self._weather() is not None and self.sim is not None:
            value = data.get("value")
            self.sim.outdoor = float(value) if value in OUTDOOR else None
            await self._weather_now()
        elif do == "room_temp":
            await self._room_temp(data.get("area"), data.get("delta"))
        elif do == "emergency" and target is not None:
            if any(e.entity_id == target for _, e in self._emergencies()):
                await self._emergency(target, on)
        elif do == "safe":
            for _, entity in self._emergencies():
                await self._emergency(entity.entity_id, False)
            await self.settle()  # also what was still open from before a restart
        elif do == "price" and self._has_prices():
            value = data.get("value")
            await self._price(value if value in PRICES else None)
        elif do == "sky" and self._has_solar():
            value = data.get("value")
            await self._sky(value if value in SKY else None)

    async def _switch(self, entity_id: str, on: bool, by: Source) -> None:
        state = self.ha.states[entity_id]
        attrs = dict(state.attributes)
        if entity_id.startswith("cover."):
            attrs["current_position"] = 100 if on else 0
            await self._set(entity_id, "open" if on else "closed", attrs, by=by)
            return
        if not on:
            attrs.pop("brightness", None)
        elif entity_id.startswith("light.") and "brightness" not in attrs:
            modes = attrs.get("supported_color_modes") or []
            if any(m != "onoff" for m in modes):
                attrs["brightness"] = 255
        await self.ha.simulate(entity_id, "on" if on else "off", attrs, by=by)

    async def _room_temp(self, area: Any, delta: Any) -> None:
        if not isinstance(area, str) or delta not in (-0.5, 0.5):
            return
        for entity_id in self._temperatures().get(area, []):
            current = self._temperature(entity_id)
            if current is None:
                continue
            value = round(current + float(delta), 1)
            if entity_id.startswith("climate."):
                await self._set(entity_id, self._state(entity_id), {"current_temperature": value})
            else:
                await self._set(entity_id, f"{value:.1f}")

    def _start(self, kind: str, key: str) -> None:
        self.busy = {"what": key, "kind": kind, "done": 0.0}

        async def run() -> None:
            try:
                if kind == "jump":
                    await self._jump(key)
                else:
                    await self._story(key)
            except Exception:
                _LOGGER.exception("Demo %s %s failed", kind, key)
            finally:
                self.busy = None
                self.save()
                await self._changed()

        self._task = asyncio.get_running_loop().create_task(run(), name=f"demo-{kind}")

    async def _halt(self) -> None:
        """Stop during a story or jump: it ends where it got to, and the time stands still
        there. On a slow machine a week ahead takes minutes."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.clock.stop()
        self.save()
        await self._changed()

    async def wait(self) -> None:
        """Until the jump or story that runs now is done (tests)."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _changed(self) -> None:
        if self.on_change is not None:
            await self.on_change()

    # -- what the screens get -------------------------------------------------------------

    def live(self) -> dict[str, Any]:
        """The bar at the top: the house's time and speed, and a jump that goes on."""
        return {
            "now": self.clock.now().isoformat(),
            "speed": self.clock.speed,
            "running": self.clock.running,
            "busy": self.busy,
            "held": self.held,
        }

    def controls(self) -> dict[str, Any]:
        persons = self._persons()
        rooms = self._presence()
        ignored = self.hm.world.occupancy.ignored if self.hm.world is not None else set()
        weather = self._weather()
        temps = self._temperatures()
        sim = self.sim
        return {
            "house": self.house.name,
            "speeds": list(SPEEDS),
            "jumps": list(JUMPS),
            "stories": self.stories(),
            "rooms": [
                {
                    "id": s.entity_id,
                    "area": a,
                    "name": self._room(a),
                    "on": self._state(s.entity_id) == "on",
                    # Only a presence sensor sees that someone left; after motion the room
                    # stays taken a few minutes, someone may sit still.
                    "kind": "presence"
                    if s.device_class in ("presence", "occupancy") and s.entity_id not in ignored
                    else "motion",
                }
                for a, s in sorted(rooms.items(), key=lambda r: self._room(r[0]))
            ],
            "residents": [
                {"id": p.entity_id, "name": p.name, "home": self._state(p.entity_id) == "home"}
                for p in persons
            ],
            "switches": [
                {
                    "id": e.entity_id,
                    "name": e.name,
                    "room": self._room(e.area_id),
                    "kind": e.domain,
                    "on": self._state(e.entity_id) in ("on", "open", "opening"),
                }
                for domain in SWITCHES
                for e in self._entities(domain)
            ],
            "openings": [
                {
                    "id": e.entity_id,
                    "name": e.name,
                    "kind": e.device_class,
                    "open": self._state(e.entity_id) == "on",
                }
                for e in self._entities("binary_sensor", OPENINGS)
            ],
            "weather": None
            if weather is None
            else {
                "condition": self._state(weather.entity_id),
                "temperature": self.ha.states[weather.entity_id].attributes.get("temperature"),
                "set": (sim.weather if sim is not None else self._weather_set),
                "outdoorSet": sim.outdoor if sim is not None else None,
                "outdoor": list(OUTDOOR) if sim is not None else [],
                "choices": list(WEATHER),
            },
            "temperatures": [
                {"area": a, "name": self._room(a), "value": self._temperature(ids[0])}
                for a, ids in sorted(temps.items(), key=lambda r: self._room(r[0]))
            ],
            "emergencies": [
                {
                    "key": key,
                    "id": e.entity_id,
                    "room": self._room(e.area_id),
                    "on": self._state(e.entity_id) in ("on", "triggered"),
                }
                for key, e in self._emergencies()
            ],
            "energy": None
            if sim is None
            else {
                "prices": self._has_prices(),
                "price": next(
                    (k for k, v in PRICES.items() if sim.price and sim.price[2] == v), None
                )
                if sim.price is not None
                else None,
                "solar": self._has_solar(),
                "sky": next((k for k, v in SKY.items() if sim.sky == v), None),
            },
            "held": self.held,
            "learning": self.hm.learning_allowed,
        }

    async def view(self, viewer: Viewer, user_id: str, lang: str) -> dict[str, Any]:
        """Everything for the panel: the controls, the steps so far and what happened."""
        out = self.controls()
        out["live"] = self.live()
        parts = await readiness(self.hm, lang, 2, True)
        steps = {"observe": 0, "propose": 0, "autonomous": 0}
        for part in parts:
            for key, count in part.get("counts", {}).items():
                steps[key] = steps.get(key, 0) + int(count)
        out["steps"] = steps
        out["last"] = None
        out["happened"] = []
        if self.last is not None:
            since: datetime = self.last["since"]
            out["last"] = {**self.last, "since": since.isoformat()}
            out["happened"] = await happened(self.hm, viewer, user_id, lang, since)
        return out


async def happened(
    hm: HouseMind, viewer: Viewer, user_id: str, lang: str, since: datetime
) -> list[dict[str, Any]]:
    """What HouseMind did, asked or left since the tap – the same lines as in Logboek (B-180),
    at least as much as level 2 shows (also what it only watched on Observeren)."""
    seen = replace(viewer, level=max(viewer.level, 2))
    log = await logbook(hm, seen, user_id, lang)
    lines = []
    for item in log["items"]:
        if datetime.fromisoformat(item["when"]) < since or item["type"] not in ("done", "notice"):
            continue
        if item["type"] == "notice" and item.get("proposal") is not None:
            continue  # the question itself: 'Ik vroeg: …' already says it
        lines.append(
            {
                "at": item["at"],
                "day": item["day"],
                "text": item["text"],
                "type": item["type"],
                "kind": item.get("kind"),
                "outcome": item.get("outcome"),
            }
        )
    return lines[:HAPPENED]
