"""Vesta zoekt het op (E-419, design 50.4, B-351): what an AI may look up in the house while it
answers a question. Only reading, never doing, and only with labels instead of names (E-15):

- ``room``         one room now: occupied, dark, its climate, what is open, its devices
- ``why``          what HouseMind did or left there lately, and why – the decision trace
- ``history``      what a device or room did in the last days, at most seven, summed up per
                   day – never the raw history
- ``plan``         what HouseMind would do now, room by room, and when it expects the house
                   to wake up and to go to sleep
- ``house_rules``  the house rules, numbered as the AI got them, to change one (E-420)
- ``energy``       energy now and today, and the cheapest time in the coming hours

The AI service runs at most five of them for one question, each within its time limit, and
sends what they give through the privacy filter once more. Security devices, people and where
they are, calendars and media never come along: only devices with a label (privacy.Labels).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from housemind.ai.providers import Tool
from housemind.ai.service import DAYS, Lookup
from housemind.brain.later import later
from housemind.brain.rules import rule_text
from housemind.energy.tips import cheapest_block

if TYPE_CHECKING:
    from housemind.ha.adapter import HistoryChange
    from housemind.runtime import HouseMind

HISTORY_DAYS = 7  # at most a week back (E-419)
MAX_DECISIONS = 5  # the newest decisions for one device or room
MAX_HISTORY = 8  # devices of one room in one history
# What counts as "on" for a summary per day: the time it was on and how often it went on.
ACTIVE = frozenset({"on", "open", "opening", "playing", "cleaning", "heat", "cool", "heat_cool"})

TARGET = {"type": "string", "description": "the label of a room or a device, e.g. bathroom_1"}
NOTHING: dict[str, Any] = {"type": "object", "properties": {}}


class HouseLookups:
    """The lookups for one question, in the language of the person who asked."""

    def __init__(self, hm: HouseMind, lang: str, rules_max: int) -> None:
        self.hm, self.lang, self.rules_max = hm, lang, rules_max

    def all(self) -> list[Lookup]:
        def takes(**properties: Any) -> dict[str, Any]:
            first = next(iter(properties))
            return {"type": "object", "properties": properties, "required": [first]}

        return [
            Lookup(
                Tool(
                    "room",
                    "One room now: whether someone is there, dark, its climate, what is open, "
                    "and all its devices with their state.",
                    takes(room={"type": "string", "description": "the label of the room"}),
                ),
                self.room,
            ),
            Lookup(
                Tool(
                    "why",
                    "What the home did, proposed or left undone lately for a device or a room, "
                    "and why (its decision trace), newest first.",
                    takes(about=TARGET),
                ),
                self.why,
            ),
            Lookup(
                Tool(
                    "history",
                    f"What a device or a room did in the last days (1 to {HISTORY_DAYS}), summed "
                    "up per day: temperatures as lowest, highest and average; devices as how "
                    "often and how many hours they were on, and who did it.",
                    takes(
                        about=TARGET,
                        days={"type": "integer", "minimum": 1, "maximum": HISTORY_DAYS},
                    ),
                ),
                self.history,
            ),
            Lookup(
                Tool(
                    "plan",
                    "What the home would do now, room by room; the moments ahead (getting "
                    "dark, going to sleep, getting up, getting light) with what it would then "
                    "do; and when it expects the house to wake up and to go to sleep.",
                    NOTHING,
                ),
                self.plan,
            ),
            Lookup(
                Tool(
                    "house_rules",
                    "The house rules, numbered as in the data, each with its fields.",
                    NOTHING,
                ),
                self.house_rules,
            ),
            Lookup(
                Tool(
                    "energy",
                    "Energy now and today: the grid, solar, the home battery, what today cost, "
                    "and the cheapest time in the next twelve hours.",
                    NOTHING,
                ),
                self.energy,
            ),
        ]

    # -- helpers ----------------------------------------------------------------------------

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.hm.config.time_zone if self.hm.config else "UTC")

    def _when(self, at: datetime) -> dict[str, Any]:
        """A moment as days ago and the time: never a date, which the filter would take for a
        number."""
        local = at.astimezone(self._tz)
        today = self.hm.clock.now().astimezone(self._tz).date()
        return {"days_ago": (today - local.date()).days, "time": local.strftime("%H:%M")}

    def _target(self, label: str) -> tuple[list[str], str | None]:
        """The devices a label stands for – one device, or those of a room – and the room.
        Only devices an AI may know (E-15)."""
        labels = self.hm.ai.labels
        area_id = labels.area_id(label)
        if area_id is not None:
            found = [
                e
                for e in sorted(labels.to_label)
                if not e.startswith("area:")
                and (area := self.hm.model.area_of(e)) is not None
                and area.id == area_id
            ]
            return found, area_id
        entity_id = labels.entity_id(label)
        return ([entity_id] if entity_id else []), None

    # -- the lookups ------------------------------------------------------------------------

    async def room(self, args: dict[str, Any]) -> dict[str, Any]:
        label = str(args.get("room", ""))
        area_id = self.hm.ai.labels.area_id(label)
        if area_id is None or area_id not in self.hm.model.areas:
            return {"error": "no such room"}
        facts = self.hm.house_facts(also=(area_id,))  # all its devices, also those off
        return {"room": label, **facts["rooms"].get(label, {})}

    async def why(self, args: dict[str, Any]) -> dict[str, Any]:
        label = str(args.get("about", ""))
        entities, _area = self._target(label)
        if not entities:
            return {"error": "unknown label"}
        since = (self.hm.clock.now() - timedelta(days=HISTORY_DAYS)).isoformat()
        marks = ",".join("?" * len(entities))
        rows = await self.hm.db.fetchall(
            f"SELECT id FROM decisions WHERE entity_id IN ({marks}) AND at >= ? "  # noqa: S608 – only ?
            "ORDER BY at DESC, id DESC LIMIT ?",
            (*entities, since, MAX_DECISIONS),
        )
        labels = self.hm.ai.labels
        decisions = []
        for row in rows:
            decision = await self.hm.stored_decision(int(row["id"]))
            if decision is None:
                continue
            decisions.append(
                {
                    **self._when(decision.at),
                    "device": labels.to_label.get(decision.request.entity_id, "?"),
                    "action": decision.request.action,
                    "outcome": decision.outcome.value,
                    # Names from Home Assistant, which the filter turns into labels.
                    "why": " ".join(self.hm.explain(decision, 2, self.lang)),
                }
            )
        left = [
            {
                **self._when(self.hm.last_not_done[e].at),
                "device": labels.to_label[e],
                "why": self.hm.why_not(e, self.lang),
            }
            for e in entities
            if e in self.hm.last_not_done
        ]
        left.sort(key=lambda item: (item["days_ago"], item["time"]))
        return {"about": label, "decisions": decisions, "left_undone": left[:MAX_DECISIONS]}

    async def history(self, args: dict[str, Any]) -> dict[str, Any]:
        label = str(args.get("about", ""))
        entities, _area = self._target(label)
        if not entities:
            return {"error": "unknown label"}
        try:
            days = max(1, min(HISTORY_DAYS, int(args.get("days") or 1)))
        except (TypeError, ValueError):
            days = 1
        now = self.hm.clock.now()
        local = now.astimezone(self._tz)
        start = (local - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wanted = [e for e in entities if self._summed(e)][:MAX_HISTORY]
        if not wanted:
            return {"about": label, "days": days, "devices": {}}
        changes = await self.hm.ha.history(wanted, start, now)
        per: dict[str, list[HistoryChange]] = defaultdict(list)
        for change in sorted(changes, key=lambda c: c.at):
            per[change.entity_id].append(change)
        labels = self.hm.ai.labels
        devices = {
            labels.to_label[e]: self._sum(e, per.get(e, []), start, now)
            for e in wanted
            if e in labels.to_label
        }
        return {"about": label, "days": days, "devices": devices}

    def _summed(self, entity_id: str) -> bool:
        """What a summary per day says something about: a climate value, a thermostat, or a
        device that is on or off – not a power meter of each socket."""
        entity = self.hm.model.entities.get(entity_id)
        if entity is None or entity.hidden:
            return False
        if entity.domain == "sensor":
            return entity.device_class in ("temperature", "humidity", "carbon_dioxide")
        return True

    def _sum(
        self, entity_id: str, changes: list[HistoryChange], start: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """One device per day, newest first: values as lowest, highest and average – for a
        thermostat also what it was set to; states as how often it went on, the hours it was
        on, and who did it."""
        domain = entity_id.split(".", 1)[0]
        days: dict[int, dict[str, Any]] = {}
        today = now.astimezone(self._tz).date()
        for i, change in enumerate(changes):
            until = changes[i + 1].at if i + 1 < len(changes) else now
            at = max(change.at, start)
            day = (today - at.astimezone(self._tz).date()).days
            item = days.setdefault(day, {"days_ago": day})
            if domain in ("sensor", "climate"):
                value = (
                    change.attributes.get("current_temperature")
                    if domain == "climate"
                    else change.state
                )
                number = _number(value)
                if number is not None:
                    item.setdefault("_values", []).append(number)
            if domain == "climate":
                setpoint = _number(change.attributes.get("temperature"))
                if setpoint is not None:
                    item.setdefault("_set", []).append(setpoint)
            if domain == "sensor":
                continue
            active = change.state in ACTIVE
            if active and change.at >= start and (i == 0 or changes[i - 1].state not in ACTIVE):
                item["went_on"] = item.get("went_on", 0) + 1
                by = item.setdefault("by", {})
                by[change.source.value] = by.get(change.source.value, 0) + 1
            if active:
                self._hours(days, at, until, today)
        out = []
        for day in sorted(days):
            item = days[day]
            values = item.pop("_values", [])
            if values:
                item |= {
                    "lowest": round(min(values), 1),
                    "highest": round(max(values), 1),
                    "average": round(sum(values) / len(values), 1),
                }
            setpoints = item.pop("_set", [])
            if setpoints:
                item["set_to"] = sorted({round(v, 1) for v in setpoints})
            if "hours_on" in item:
                item["hours_on"] = round(item["hours_on"], 1)
            out.append(item)
        return out

    def _hours(
        self, days: dict[int, dict[str, Any]], at: datetime, until: datetime, today: date
    ) -> None:
        """The time a device was on, split over the days it falls in."""
        moment = at
        while moment < until:
            local = moment.astimezone(self._tz)
            midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            end = min(until, midnight.replace(microsecond=0))
            day = (today - local.date()).days
            item = days.setdefault(day, {"days_ago": day})
            item["hours_on"] = item.get("hours_on", 0.0) + (end - moment).total_seconds() / 3600
            moment = end

    async def plan(self, args: dict[str, Any]) -> dict[str, Any]:
        hm = self.hm
        out: dict[str, Any] = {"house_mode": hm.world.modes.mode.value if hm.world else "home"}
        try:
            now = hm.simulate(self.lang)  # what it would do now; nothing is switched or kept
        except AssertionError:
            now = {"rooms": []}
        out["now"] = [
            {"room": room["area"], "would": [i["text"] for i in room["items"]][:4]}
            for room in now["rooms"][:8]
        ]
        try:
            ahead = later(hm, self.lang)  # Straks: what it would do at the moments ahead
        except AssertionError:
            ahead = {"moments": []}
        out["later"] = [
            {
                "time": moment["at"],
                "moment": moment["title"],
                "would": [f"{line['text']} ({line['chip']})" for line in moment["lines"]][:6],
            }
            for moment in ahead["moments"][:5]
        ]
        if hm.world is not None:
            snapshot = hm.world.snapshot(hm.clock.now())
            wake = hm._wake_at(snapshot)
            bed = hm._bed_at(snapshot)
            if wake is not None:
                out["wake_up"] = wake[0].astimezone(self._tz).strftime("%H:%M")
            if bed is not None:
                out["bedtime"] = bed.astimezone(self._tz).strftime("%H:%M")
        paused = hm.paused_until()
        if paused is not None:
            out["paused_until"] = paused.astimezone(self._tz).strftime("%H:%M")
        return out

    async def house_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        labels = self.hm.ai.labels
        rules = []
        for number, rule in enumerate(self.hm.rules.rules[: self.rules_max], start=1):
            scope = (
                labels.area(rule.scope_id)
                if rule.scope_type == "area"
                else labels.to_label.get(rule.scope_id)
                if rule.scope_type == "entity"
                else "*"
            )
            item: dict[str, Any] = {
                "number": number,
                "text": rule_text(rule, self.lang, self.hm.languages, self.hm.model),
                "scope_type": rule.scope_type,
                "scope": scope or "?",
                "effect": rule.effect,
            }
            for key in ("category", "value", "skill"):
                if getattr(rule, key) is not None:
                    item[key] = getattr(rule, key)
            for key in ("time_from", "time_to"):
                if getattr(rule, key) is not None:
                    item[key] = getattr(rule, key).strftime("%H:%M")
            if rule.days:
                item["days"] = [DAYS[d] for d in rule.days]
            rules.append(item)
        return {"house_rules": rules}

    async def energy(self, args: dict[str, Any]) -> dict[str, Any]:
        hm = self.hm
        if hm.energy is None or hm.world is None or not hm.energy.setup.has_any:
            return {"error": "no energy meters in this home"}
        now, states = hm.clock.now(), hm.world.states
        facts = hm.house_facts().get("energy", {})
        today = await hm.energy.today(now)
        out: dict[str, Any] = {
            "now": facts,
            "today_kwh": {
                key.removesuffix("_kwh"): round(today.get(key, 0.0), 2)
                for key in ("import_kwh", "export_kwh", "solar_kwh")
            },
            "today_cost": round(today.get("cost", 0.0), 2),
            "currency": hm.energy.country.currency,
        }
        plan = hm.energy.plan
        slots = plan.slots if plan is not None else hm.energy.forecast(states, now)
        current = next((s for s in slots if s.start <= now < s.start + timedelta(minutes=15)), None)
        if current is not None and current.buy is not None:
            out["price_now"] = round(current.buy, 3)
        block = cheapest_block(slots, now, now + timedelta(hours=12))
        if block is not None:
            begin, end, price = block
            out["cheapest"] = {
                "from": begin.astimezone(self._tz).strftime("%H:%M"),
                "to": end.astimezone(self._tz).strftime("%H:%M"),
                "price": round(price, 3),
            }
        return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
