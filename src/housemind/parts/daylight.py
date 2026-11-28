"""The day's rhythm of the light in the house (E-465 – E-468, design 68): reading the settings,
taking over on Autonomous and giving back, keeping lamps that burn on the rhythm, and letting go
of a lamp someone chose for. The working out itself is in ``brain/daylight.py``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.daylight import (
    OWN,
    READ_EVERY,
    SAME_KELVIN,
    SAME_PCT,
    DaylightBook,
    Following,
    Level,
    chosen,
    fitted,
    is_own,
    lamp_now,
    request_data,
    with_transition,
)
from housemind.brain.notify import Kind
from housemind.brain.skills import ARRIVAL, EVENING
from housemind.core.model import Source, StateChange, daypart
from housemind.ha.adapter import HALightRhythm
from housemind.knowledge.matcher import recognise
from housemind.parts import Part
from housemind.safety.gate import ActionRequest, Layer, watched

if TYPE_CHECKING:
    from housemind.brain.learning import Learning
    from housemind.brain.skills import SkillEngine
    from housemind.core.clock import Clock
    from housemind.core.model import HouseModel
    from housemind.ha.adapter import HomeAssistant
    from housemind.knowledge.catalog import Catalog
    from housemind.memory.database import Database
    from housemind.safety.gate import Decision, SafetyGate
    from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)

SETTING = "day_rhythm"
# The lamps of one "on" report one by one: look a moment later, and send one message.
PLAIN_WAIT_S = 1.0
ON_THE_RHYTHM = frozenset({"light_on_entry", EVENING, ARRIVAL})  # skills whose "on" follows it
# A room's lamps are HouseMind's as soon as one of its light rules there is on Autonomous
# (E-579): from then on nothing else gives them their colour through the day.
ROOM_LIGHT = (
    "light_on_entry",
    "light_off_leave",
    "light_off_sleep",
    "night_light",
    "bedtime_dim",
    "wake_light",
)


class DaylightPart(Part):
    """Who gives the light its colour now, and the rounds that keep it."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        activated: bool
        catalog: Catalog
        clock: Clock
        daylight: DaylightBook
        db: Database
        gate: SafetyGate | None
        ha: HomeAssistant
        learning: Learning | None
        model: HouseModel
        parts_off: dict[str, dict[str, str]]
        skills: SkillEngine | None
        world: World | None
        _daylight_plain: set[str]
        _daylight_task: asyncio.Task[Any] | None
        _daylight_saving: set[asyncio.Task[Any]]
        _daylight_own_kept: dict[str, tuple[datetime, HALightRhythm]]
        skill_params: dict[str, dict[str, Any]]

        def autonomous_here(self, skill: str, area_id: str) -> bool: ...
        def _expect_followups(self, entity_id: str) -> None: ...
        def _learns_now(self) -> bool: ...
        async def _raise(
            self,
            kind: Kind,
            topic: str,
            text_key: str,
            params: dict[str, Any],
            snapshot: WorldState,
            concerns: str = "home",
        ) -> Any: ...
        async def _record(self, decision: Decision, previous: Any = None) -> int: ...

    async def _daylight_start(self, now: datetime) -> None:
        """At the start: what HouseMind had taken over, and the settings as they are now."""
        assert self.skills is not None and self.world is not None
        book = self.daylight
        book.model = self.model
        book.states = self.world.states  # what is on now, before the first round (B-502)
        self.skills.daylight = book
        book.zone = self.world.tz
        book.own_for = self._daylight_own
        saved = await self.db.get_setting(SETTING) or {}
        book.taken = {str(e) for e in saved.get("taken", ())}
        book.kept_on = {str(e) for e in saved.get("kept_on", ())}
        book.paused = {str(e) for e in saved.get("paused", ())}
        await self._read_daylight(now)
        for entry_id in list(book.taken):
            rhythm = book.rhythms.get(entry_id)
            if rhythm is not None and book.is_on(rhythm):
                # Put back on while HouseMind was away: that stays (E-468).
                book.taken.discard(entry_id)
                book.kept_on.add(entry_id)
        for entry_id in list(book.paused):
            rhythm = book.rhythms.get(entry_id)
            if rhythm is None or book.is_on(rhythm):
                book.paused.discard(entry_id)  # gone, or someone put it on: theirs
        self._daylight_switches()
        for entry_id in book.taken:
            if (rhythm := book.rhythms.get(entry_id)) is not None:
                self._daylight_follow_what_burns(rhythm, now)
        if book.paused:
            # HouseMind stopped while a lamp was being set: no window holds it now.
            await self._daylight_resume(now, everything=True)

    def _daylight_own(self, lamp: str) -> HALightRhythm | None:
        """A house without that integration (E-471, B-385): the same line over the day, between
        what this room learned for the evening and for the day, else the start knowledge. A
        house that always uses one colour keeps it. Worked out once an hour."""
        area = self.model.area_of(lamp)
        if area is None or self.learning is None:
            return None
        stands = self.daylight.stands
        if stands is not None and not stands.follows_rhythm(lamp):
            return None  # this lamp does not follow the day's rhythm (E-496)
        now = self.clock.now()
        kept = self._daylight_own_kept.get(area.id)
        if kept is not None and now - kept[0] < READ_EVERY:
            return kept[1]
        params = self.skill_params.get("light_on_entry", {})

        def limits(key: str, fallback: float) -> tuple[float, float]:
            assert self.learning is not None
            start = params.get(key)
            start = start if isinstance(start, dict) else {}
            found = [
                self.learning.part_value("light_on_entry", area.id, part, key)
                or float(start.get(part, fallback))
                for part in ("evening", "day")
            ]
            return min(found), max(found)

        low, high = limits("brightness_pct", 80.0)
        warm, white = limits("color_temp_kelvin", 3000.0)
        rhythm = HALightRhythm(
            f"{OWN}{area.id}",
            "",
            min_brightness=low,
            max_brightness=high,
            min_kelvin=warm,
            max_kelvin=white,
            interval_s=300.0,
            transition_s=60.0,
            brightness_mode="linear",
        )
        self._daylight_own_kept[area.id] = (now, rhythm)
        return rhythm

    async def _read_daylight(self, now: datetime) -> None:
        book = self.daylight
        assert self.world is not None
        book.states = self.world.states
        book.place = self.world.location
        try:
            found = await self.ha.light_rhythms()
        except (ConnectionError, TimeoutError) as err:
            # What was read before stays; a group never loses its lamps because of a bad moment.
            _LOGGER.warning("Could not read the day's rhythm of the light: %s", err)
            book.read_at = now
            return
        book.read(found, now)

    def _daylight_switches(self) -> None:
        assert self.gate is not None
        book = self.daylight
        self.gate.state.day_rhythm_taken = {
            switch
            for entry_id in book.taken | book.paused
            if (rhythm := book.rhythms.get(entry_id)) is not None
            and (switch := book.main_switch(rhythm)) is not None
        }

    async def _daylight_save(self) -> None:
        book = self.daylight
        self._daylight_switches()
        await self.db.set_setting(
            SETTING,
            {
                "taken": sorted(book.taken),
                "kept_on": sorted(book.kept_on),
                "paused": sorted(book.paused),
            },
        )

    async def _daylight_round(self, snapshot: WorldState) -> None:
        """In the tick: new settings, who does the work, and the lamps that burn."""
        book = self.daylight
        now = snapshot.now
        assert self.world is not None
        book.states = self.world.states
        if book.read_at is None or now - book.read_at >= READ_EVERY:
            await self._read_daylight(now)
        await self._daylight_hand_over(snapshot)
        if book.paused:
            await self._daylight_resume(now)
        await self._daylight_keep_up(snapshot)

    def _rhythm_skill(self, area_id: str) -> str | None:
        """The light rule of this room that HouseMind does on Autonomous, under which the day's
        colour goes too (E-579): the room's lamps are its own then. None while none is."""
        for skill in ROOM_LIGHT:
            if skill in self.skill_params and self.autonomous_here(skill, area_id):
                return skill
        return None

    def _daylight_wanted(self, rhythm: HALightRhythm) -> bool:
        """HouseMind itself steers all lamps of this group once the light of each of their rooms
        is its own on Autonomous (E-468, E-579): nothing else steers them then, and none is
        left without a colour."""
        assert self.gate is not None
        book = self.daylight
        lamps = book.lamps_of(rhythm)
        if not lamps or book.place is None or not self.activated or "lighting" in self.parts_off:
            return False
        for unit in self._daylight_units(rhythm):
            area = self.model.area_of(unit)
            if area is None or self._rhythm_skill(area.id) is None:
                return False
            members = self.model.groups.get(unit, ())
            if watched(self.gate.state.other_system, unit, members):
                return False
        return True

    async def _daylight_hand_over(self, snapshot: WorldState) -> None:
        book = self.daylight
        back_off = bool(await self.db.get_setting("given_back", False))
        given: list[str] = []
        for rhythm in sorted(book.rhythms.values(), key=lambda r: r.title):
            wanted = not back_off and self._daylight_wanted(rhythm)
            if book.ours(rhythm) and not wanted:
                if await self._daylight_give_back(rhythm, "step_down", snapshot.now):
                    given.append(rhythm.title)
            elif (
                wanted
                and not book.ours(rhythm)
                and rhythm.entry_id not in book.kept_on
                and book.is_on(rhythm)
            ):
                await self._daylight_take(rhythm, snapshot.now)
        if given:
            await self._raise(
                Kind.INFO,
                f"given_back:{snapshot.now.isoformat()}",
                "notify.automation_given_back",
                {"names": ", ".join(f"‘{n}’" for n in given), "count": len(given)},
                snapshot,
                concerns="admins",
            )

    async def _daylight_take(self, rhythm: HALightRhythm, now: datetime) -> None:
        assert self.gate is not None
        book = self.daylight
        switch = book.main_switch(rhythm)
        if switch is None:
            return
        state = book.states.get(switch)
        by_hand = state.attributes.get("manual_control") if state is not None else None
        decision = await self.gate.submit(
            ActionRequest(
                Layer.PERSON_ANSWER,  # the choice for Autonomous is the yes (E-468)
                switch,
                "switch.turn_off",
                reason="take_over",
                created_at=now,
                facts={"day_rhythm_group": rhythm.title},
            )
        )
        await self._record(decision)
        if not decision.executed:
            return
        book.taken.add(rhythm.entry_id)
        await self._daylight_save()
        self._daylight_follow_what_burns(
            rhythm, now, set(by_hand) if isinstance(by_hand, list) else set()
        )

    async def _daylight_give_back(self, rhythm: HALightRhythm, why: str, now: datetime) -> bool:
        assert self.gate is not None
        book = self.daylight
        switch = book.main_switch(rhythm)
        if switch is None:  # the group is gone from Home Assistant: nothing to put back
            book.taken.discard(rhythm.entry_id)
            await self._daylight_save()
            return False
        decision = await self.gate.submit(
            ActionRequest(Layer.RESTORE, switch, "switch.turn_on", reason=why, created_at=now)
        )
        await self._record(decision)
        if not decision.executed:
            return False
        book.taken.discard(rhythm.entry_id)
        for lamp in self._daylight_units(rhythm):
            book.following.pop(lamp, None)
        await self._daylight_save()
        return True

    async def daylight_all_on(self, why: str = "give_back") -> list[str]:
        """Everything HouseMind took over goes back on (E-112, E-468): what could not."""
        book = self.daylight
        failed: list[str] = []
        now = self.clock.now()
        for entry_id in list(book.taken):
            rhythm = book.rhythms.get(entry_id)
            if rhythm is not None and not await self._daylight_give_back(rhythm, why, now):
                failed.append(book.main_switch(rhythm) or entry_id)
        await self._daylight_resume(now, everything=True)
        return failed

    # -- while the administrator sets a lamp's standen (E-579) -----------------------------

    def _daylight_held(self, rhythm: HALightRhythm, now: datetime) -> bool:
        """A lamp of this group is held by an open Standen window."""
        assert self.gate is not None
        lamps = set(self.daylight.lamps_of(rhythm)) | self._daylight_units(rhythm)
        for lamp in list(lamps):
            lamps.update(self.model.groups.get(lamp, ()))
        return any(self.gate.state.setting(lamp, now) for lamp in lamps)

    async def daylight_hold(self, lamp: str) -> None:
        """The Standen window holds this lamp: the group of that integration that still gives
        it its colour rests until the window closes, so only what the administrator tries is
        on the lamp. Quiet, as the Proef itself: no line in the Logboek."""
        if self.gate is None:
            return
        book = self.daylight
        now = self.clock.now()
        changed = False
        for one in (lamp, *self.model.groups.get(lamp, ())):
            rhythm = book.group_of(one)
            if (
                rhythm is None
                or is_own(rhythm)
                or book.ours(rhythm)
                or rhythm.entry_id in book.paused
                or not book.is_on(rhythm)
                or (switch := book.main_switch(rhythm)) is None
            ):
                continue
            # Kept first, then switched off (B-502): a stop in between still finds it at the
            # next start and puts the group back on.
            book.paused.add(rhythm.entry_id)
            await self._daylight_save()
            decision = await self.gate.submit(
                ActionRequest(
                    Layer.PERSON_ANSWER,  # the administrator opened the window: their choice
                    switch,
                    "switch.turn_off",
                    reason="setting_stands",
                    created_at=now,
                    facts={"day_rhythm_group": rhythm.title},
                )
            )
            if not decision.executed:
                book.paused.discard(rhythm.entry_id)
                changed = True
        if changed:
            await self._daylight_save()

    async def _daylight_resume(self, now: datetime, everything: bool = False) -> None:
        """A group that rested while a lamp of it was being set goes on again once no window
        holds a lamp of it – or becomes HouseMind's, if by now it may steer it (E-579)."""
        assert self.gate is not None
        book = self.daylight
        back_off = bool(await self.db.get_setting("given_back", False))
        changed = False
        for entry_id in sorted(book.paused):
            rhythm = book.rhythms.get(entry_id)
            switch = book.main_switch(rhythm) if rhythm is not None else None
            if rhythm is None or switch is None:
                book.paused.discard(entry_id)
                changed = True
                continue
            if not everything and self._daylight_held(rhythm, now):
                continue
            if not everything and not back_off and self._daylight_wanted(rhythm):
                book.paused.discard(entry_id)
                book.taken.add(entry_id)  # it stays off: HouseMind does the work now
                self._daylight_follow_what_burns(rhythm, now)
                changed = True
                continue
            decision = await self.gate.submit(
                ActionRequest(
                    Layer.RESTORE, switch, "switch.turn_on", reason="setting_done", created_at=now
                )
            )
            if decision.executed:
                book.paused.discard(entry_id)
                changed = True
        if changed:
            await self._daylight_save()

    def _daylight_units(self, rhythm: HALightRhythm) -> set[str]:
        """What HouseMind addresses of this group: a group that steers its lamps, or the lamps
        of a group that only gathers them (E-458)."""
        units: set[str] = set()
        for lamp in self.daylight.lamps_of(rhythm):
            units.update(self.model.units([lamp, *self.model.groups.get(lamp, ())]))
        return units

    def _daylight_follow_what_burns(
        self, rhythm: HALightRhythm, now: datetime, by_hand: set[str] | None = None
    ) -> None:
        """Lamps that burn when HouseMind starts doing the work: on the rhythm from the next
        round, unless someone chose for them."""
        assert self.gate is not None
        book = self.daylight
        for unit in self._daylight_units(rhythm):
            state = book.states.get(unit)
            if state is None or state.state != "on":
                continue
            lamps = {unit, *self.model.groups.get(unit, ())}
            win = self.gate.state.people_win_until
            if (by_hand and lamps & by_hand) or any(
                (until := win.get(lamp)) is not None and now < until for lamp in lamps
            ):
                continue
            pct, warm = lamp_now(state)
            book.following[unit] = Following(pct, warm, now - timedelta(seconds=rhythm.interval_s))

    def _daylight_lit(self, request: ActionRequest, now: datetime) -> None:
        """HouseMind just put a lamp on with the day's values: it follows from here."""
        book = self.daylight
        if (
            request.skill not in ON_THE_RHYTHM
            or request.action != "light.turn_on"
            or not request.data
            or request.facts.get("warning")
            or request.facts.get("restored")
        ):
            return
        rhythm = book.group_of(request.entity_id)
        if rhythm is not None and book.ours(rhythm):
            book.following[request.entity_id] = Following(
                request.data.get("brightness_pct"), request.data.get("color_temp_kelvin"), now
            )

    def _daylight_may_tell(self, lamp: str) -> set[str]:
        """What this kind of lamp may be told when it goes on."""
        assert self.gate is not None
        entity = self.model.entities.get(lamp)
        if entity is None:
            return set()
        kind = recognise(self.catalog, entity, self.gate.state.roles.get(lamp)).kind
        allowed = kind.allowed("light.turn_on") if kind is not None else None
        return set(allowed.values) if allowed is not None else set()

    async def _daylight_send(
        self,
        lamp: str,
        rhythm: HALightRhythm,
        transition: float,
        snapshot: WorldState,
    ) -> Following | None:
        """Send one lamp the day's values, through the gate and quietly: a round of the rhythm is
        not a thought or a line in the Logboek. None when there was nothing to send."""
        assert self.gate is not None
        book = self.daylight
        area = self.model.area_of(lamp)
        now = snapshot.now
        part = snapshot.part or daypart(snapshot.local)
        if is_own(rhythm) and part == "night":
            if lamp in book.following:
                book.following[lamp].at = now
            return book.following.get(lamp)  # the night keeps what it learned (E-471)
        pct, warm = book.level(
            rhythm,
            now,
            lambda key: (
                self.learning.day_shift(area.id, part, key)
                if self.learning is not None and area is not None and not is_own(rhythm)
                else None
            ),
        )
        skill = (self._rhythm_skill(area.id) if area is not None else None) or "light_on_entry"
        allowed = self._daylight_may_tell(lamp)
        stand = book.stand_data(lamp, allowed, pct, warm)  # one of its four standen (E-576)
        warm = fitted(lamp, book.states, warm)
        if stand is not None:
            # Compared as the stand is: while the rhythm stays nearest the same stand, nothing.
            pct, warm = stand.get("brightness_pct"), stand.get("color_temp_kelvin")
        elif pct is not None and book.stands is not None:
            pct = float(book.stands.real(lamp, pct))  # its own standen, before comparing (B-409)
        left = book.following.get(lamp)
        if (
            left is not None
            and (pct is None or left.pct is None or abs(pct - left.pct) < SAME_PCT)
            and (warm is None or left.kelvin is None or abs(warm - left.kelvin) < SAME_KELVIN)
        ):
            left.at = now  # nobody can see this: no radio for it
            return left
        if stand is not None:
            data = with_transition(stand, allowed, transition)
        else:
            data = request_data(allowed, pct, warm, transition)
        if not data:
            return None
        decision = await self.gate.submit(
            ActionRequest(
                Layer.REFLEX,
                lamp,
                "light.turn_on",
                data,
                reason=f"skill:{skill}",
                skill=skill,
                area_id=area.id if area is not None else None,
                created_at=now,
                facts={"day_rhythm": True},
            )
        )
        if not decision.executed:
            if left is not None:
                left.at = now  # paused, a house rule, a signal: the next round looks again
            return left
        self._expect_followups(lamp)
        followed = Following(data.get("brightness_pct", pct), data.get("color_temp_kelvin"), now)
        book.following[lamp] = followed
        return followed

    async def _daylight_keep_up(self, snapshot: WorldState) -> None:
        """The lamps that burn on the rhythm, each as often and as softly as its group says
        (E-466). A lamp no longer where HouseMind left it is someone's choice (B-383)."""
        assert self.skills is not None
        book = self.daylight
        now = snapshot.now
        for unit, at in list(book.chosen.items()):
            if now - at < self._daylight_hand_holds(unit):
                continue
            # Half an hour after someone chose (E-584): the day's light again, from this round.
            del book.chosen[unit]
            state = book.states.get(unit)
            rhythm = book.group_of(unit)
            if state is None or state.state != "on" or rhythm is None or not book.ours(rhythm):
                continue
            pct, warm = lamp_now(state)
            book.following[unit] = Following(pct, warm, now - timedelta(seconds=rhythm.interval_s))
        for lamp in list(book.following):
            state = book.states.get(lamp)
            rhythm = book.group_of(lamp)
            if state is None or state.state != "on" or rhythm is None or not book.ours(rhythm):
                book.following.pop(lamp, None)
                continue
            left = book.following[lamp]
            if rhythm.only_once or (now - left.at).total_seconds() < rhythm.interval_s:
                continue
            area = self.model.area_of(lamp)
            if area is not None and self.skills.dimming(area.id):
                continue  # a dim warning: the lamp comes back to where it was, or goes out
            if not book.still_there(lamp, state):
                book.following.pop(lamp, None)
                continue
            await self._daylight_send(lamp, rhythm, rhythm.transition_s, snapshot)

    def _daylight_hand_holds(self, unit: str) -> timedelta:
        """How long someone's own choice for a lamp holds before the rhythm takes it again: as
        long as the catalogue lets a person's hand go first for that kind of lamp (E-584)."""
        entity = self.model.entities.get(unit)
        roles = self.gate.state.roles if self.gate is not None else {}
        kind = recognise(self.catalog, entity, roles.get(unit)).kind if entity else None
        return timedelta(hours=kind.people_win_max_hours if kind is not None else 0.5)

    def _daylight_saw(self, change: StateChange, now: datetime) -> None:
        """What happens to its lamps and its switches (E-467, E-468)."""
        if change.new is None:
            return
        if change.entity_id.startswith("switch."):
            self._daylight_switched(change)
        elif change.entity_id.startswith("light."):
            self._daylight_lamp(change, now)

    def _daylight_switched(self, change: StateChange) -> None:
        """Someone else put a group back on: it stays, and HouseMind asks nothing. Someone else
        put it off: theirs, and from then on it may come to HouseMind again if put on."""
        book = self.daylight
        if change.source is Source.HOUSEMIND or change.new is None:
            return
        for entry_id in book.rhythms:
            rhythm = book.rhythms[entry_id]
            if book.main_switch(rhythm) != change.entity_id:
                continue
            if change.new.state == "on" and entry_id in book.paused:
                book.paused.discard(entry_id)  # put on while it rested: theirs again
            elif change.new.state == "on" and entry_id in book.taken:
                book.taken.discard(entry_id)
                book.kept_on.add(entry_id)
                for lamp in self._daylight_units(rhythm):
                    book.following.pop(lamp, None)
            elif change.new.state == "off" and entry_id in book.kept_on:
                book.kept_on.discard(entry_id)
            else:
                return
            self._daylight_switches()
            self._daylight_later(self._daylight_save())
            return

    def _daylight_later(self, work: Any) -> None:
        with_loop = asyncio.get_running_loop()
        task = with_loop.create_task(work, name="housemind-day-rhythm")
        self._daylight_saving.add(task)
        task.add_done_callback(self._daylight_saving.discard)

    def _daylight_lamp(self, change: StateChange, now: datetime) -> None:
        book = self.daylight
        new = change.new
        assert new is not None
        lamp = change.entity_id
        if new.state != "on":
            if new.state == "off":
                book.following.pop(lamp, None)
                book.chosen.pop(lamp, None)
                self._daylight_plain.discard(lamp)
            return
        level = lamp_now(new)
        before = book.last_seen.get(lamp)
        if level != (None, None):
            book.last_seen[lamp] = level
        if change.source is Source.HOUSEMIND:
            return
        rhythm = book.group_of(lamp)
        if rhythm is None or is_own(rhythm) or not book.ours(rhythm):
            return  # its own rhythm only keeps what HouseMind lit; the round sees a choice
        unit = self.model.unit(lamp) or lamp
        if change.old is not None and change.old.state == "off":
            if before is None or not chosen(level, before):
                # A plain "on", from a button or an automation: back where it last was, so
                # nobody chose a colour and it gets the day's (E-467).
                if lamp not in self.model.groups:
                    self._daylight_plain.add(lamp)
                    if self._daylight_task is None:
                        self._daylight_task = asyncio.get_running_loop().create_task(
                            self._daylight_plain_round(), name="housemind-day-rhythm"
                        )
                return
        else:
            left = book.following.get(unit)
            if left is None or not chosen(level, (left.pct, left.kelvin)):
                return
        # Someone chose: it stays – half an hour at most, or until the lamp goes out (E-467,
        # E-584) – and it teaches.
        book.following.pop(unit, None)
        book.chosen[unit] = now
        self._daylight_plain.discard(lamp)
        if change.source in (Source.PERSON, Source.PHYSICAL):
            self._daylight_learn(rhythm, lamp, level, now)

    def _daylight_learn(
        self, rhythm: HALightRhythm, lamp: str, level: Level, now: datetime
    ) -> None:
        assert self.world is not None
        area = self.model.area_of(lamp)
        if area is None or self.learning is None or not self._learns_now():
            return
        snapshot = self.world.snapshot(now)
        part = snapshot.part or daypart(snapshot.local)
        pct, warm = self.daylight.level(rhythm, now)
        if level[0] is not None and pct is not None:
            stands = self.daylight.stands
            felt = stands.felt(lamp, level[0]) if stands is not None else level[0]
            self.learning.chose_light(area.id, part, "brightness_pct", felt - pct, now)
        if level[1] is not None and warm is not None:
            self.learning.chose_light(area.id, part, "color_temp_kelvin", level[1] - warm, now)

    async def _daylight_plain_round(self) -> None:
        """The lamps that just went on plainly: their group as one message when all of it did,
        else each by itself – never a lamp on that nobody asked for."""
        assert self.world is not None
        await self.clock.sleep(PLAIN_WAIT_S)
        book = self.daylight
        lamps, self._daylight_plain = self._daylight_plain, set()
        self._daylight_task = None
        snapshot = self.world.snapshot(self.clock.now())
        targets: set[str] = set()
        for lamp in lamps:
            unit = self.model.unit(lamp) or lamp
            members = self.model.groups.get(unit, ())
            whole = bool(members) and all(
                (state := book.states.get(m)) is not None and state.state == "on" for m in members
            )
            targets.add(unit if whole else lamp)
        for target in sorted(targets):
            rhythm = book.group_of(target)
            if rhythm is not None and book.ours(rhythm):
                await self._daylight_send(target, rhythm, rhythm.initial_transition_s, snapshot)

    async def _daylight_stop(self) -> None:
        for task in (self._daylight_task, *self._daylight_saving):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._daylight_task = None
