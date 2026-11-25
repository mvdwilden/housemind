"""Home Assistant tidy (E-351 – E-360, B-235 – B-244): the rounds that look at what is broken
in Home Assistant. A round runs every hour and on 'Nu nakijken', never on the way from a sensor
to an action, and only 10 minutes after Home Assistant (or HouseMind) started. It reads the
registries, states, automations and scripts HouseMind already has, plus the integrations.
Since when each finding was first seen is kept in the setting ``tidy_seen``.

After an administrator's yes it switches an entity or an automation off – through the safety
gate, never deleting, always back (B-243, setting ``tidy_off``). 'Bewust uit' (``tidy_marks``)
and the choices 'Laat zo' and 'Geen dubbele' (``tidy_choices``) keep a finding quiet (B-239,
B-244).

Part D (E-361, B-246): only administrators hear about it. Important at once: a backup 7 days
old (or none), a disk 95 % full, an integration that has not started for an hour while
something depends on it. Information in the day's bundle: at most one line a day about the
heaviest new thing (``tidy_told``), the first time one line with how many there are. What is
solved goes by itself; no message asks to switch something off.

Part C (B-240 – B-242): automations that have not run for a long time, in the same round; the
system (backup, database, disk) once a night at 03:30 and on 'Nu nakijken'; and after an update
of Home Assistant, its operating system or the Supervisor, a look at 30 minutes, 2, 6 and 24
hours. Settings: ``tidy_db`` (nightly sizes), ``tidy_busy``, ``tidy_versions``, ``tidy_before``
(what worked, each hour) and ``tidy_update``.

Part E (E-397, B-314): backups and a full disk. The margin counts in backups that still fit,
the backup tile says what Home Assistant keeps (``backup/config/info``, never its password),
and old loose backups Home Assistant does not clear itself are one card. HouseMind makes and
deletes no backups.
"""

from __future__ import annotations

import asyncio
import logging
import time as timer
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import tidy, tidy_system
from housemind.brain.automations import TURN_OFF, TURN_ON, Status
from housemind.brain.notify import Kind
from housemind.brain.rhythm import daily_words
from housemind.parts import Part
from housemind.safety.gate import ActionRequest, Decision, Layer
from housemind.world.house_mode import HouseMode
from housemind.world.sun import elevation

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from housemind.brain.automations import Automations
    from housemind.core.clock import Clock
    from housemind.core.model import HouseModel, State, StateChange
    from housemind.ha.adapter import HAConfig, HAConfigEntry, HAScript, HomeAssistant
    from housemind.i18n.catalog import Languages
    from housemind.memory.database import Database
    from housemind.safety.gate import SafetyGate
    from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)

ROUND_EVERY = timedelta(hours=1)
AFTER_START = timedelta(minutes=10)  # B-238: let Home Assistant settle first
CHECK_GAP = timedelta(minutes=1)  # 'Nu nakijken' at most once a minute
SEED_HISTORY = timedelta(days=2)  # how far back a meter's history is read once (B-265)
PATTERN_DAYS = timedelta(days=7)  # Home Assistant's history read for 'Bewust?' (B-239)
PATTERN_MAX = 20  # findings a round looks at for a pattern
GONE_FOR = timedelta(days=90)  # a choice about something gone this long goes (B-244)
# What can be switched off after a yes (B-243): entities by these findings, automations by a
# reference to what is gone.
OFF_ENTITY = frozenset({"duplicate", "not_provided", "not_provided_group"})
NIGHT_AT = time(3, 30)  # the night round: the system, and the database's size (B-241)
BACKUP_KEY = "backup:home_assistant"  # 'Ik maak back-ups buiten Home Assistant' (B-244)
KEEP_ALL_KEY = "backup_keep_all:home_assistant"  # 'Laat zo': Home Assistant keeps them all
LOOSE_KEY = "backup_loose:local"  # 'Bewust houden': the loose backups there are now (E-397)
DOWN_TELLS = timedelta(hours=1)  # an integration that does not start: important after this
VIEW = (("view", "notify.answer.view"),)  # 'Bekijken' opens Home Assistant netjes (B-245)


class TidyPart(Part):
    """Home Assistant tidy (E-351 – E-354): the rounds and what they found."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        clock: Clock
        ha: HomeAssistant
        db: Database

        async def setting(self, key: str) -> Any: ...

        model: HouseModel
        world: World | None
        automations: Automations
        scripts: list[HAScript]
        config: HAConfig | None
        _started_at: datetime
        languages: Languages
        gate: SafetyGate | None

        def _followed(self) -> set[str]: ...
        def _beside(self, work: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]: ...
        async def _record(self, decision: Decision, previous: Any = None) -> int: ...
        async def _raise(
            self,
            kind: Kind,
            topic: str,
            text_key: str,
            params: dict[str, Any],
            snapshot: WorldState,
            concerns: str = "home",
            actions: tuple[tuple[str, str], ...] = (),
            safety: bool = False,
            push: dict[str, str] | None = None,
        ) -> int | None: ...
        async def _resolve(self, topic: str) -> None: ...
        def _tracked(self, *prefixes: str) -> list[str]: ...

        _raised: dict[str, tuple[Kind, str, datetime]]

    tidy_findings: list[tidy.Finding]
    tidy_checked: datetime | None
    _tidy_seen: dict[str, str] | None
    _tidy_valued: set[str]
    _tidy_count_from: datetime | None
    _tidy_lock: asyncio.Lock
    _tidy_looking: asyncio.Task[Any] | None
    _tidy_meters: dict[str, tuple[float, str]] | None
    _tidy_marks: dict[str, tidy.Mark]
    _tidy_choices: dict[str, dict[str, Any]]
    _tidy_off: dict[str, dict[str, Any]]
    _tidy_loaded: bool
    tidy_system: dict[str, Any] | None
    tidy_update: dict[str, Any] | None
    _tidy_backups: list[dict[str, Any]] | None
    _tidy_backup_config: dict[str, Any] | None
    _tidy_health: dict[str, Any]
    _tidy_counts: Counter[str]
    _tidy_counts_since: datetime
    _tidy_busy: list[dict[str, Any]]
    _tidy_connected_at: datetime | None
    _tidy_fresh: bool
    _tidy_c_loaded: bool
    tidy_entry_names: dict[str, tuple[str, str]]
    tidy_took: tuple[float, float] | None

    def _tidy_setup(self) -> None:
        """The starting values (from ``HouseMind.__init__``)."""
        self.tidy_findings = []
        self.tidy_checked = None
        self._tidy_seen = None
        self._tidy_valued = set()
        self._tidy_count_from = None
        self._tidy_lock = asyncio.Lock()
        self._tidy_looking = None  # the quarter-hour look, beside the core
        self._tidy_meters = None  # each meter's value and since when (B-265)
        self._tidy_marks = {}  # 'Bewust uit' by target (B-239)
        self._tidy_choices = {}  # 'Laat zo', 'Geen dubbele' … by finding key (B-244)
        self._tidy_off = {}  # what HouseMind switched off, by target (B-243)
        self._tidy_loaded = False
        self.tidy_system = None  # the three tiles, from the last night or 'Nu nakijken'
        self.tidy_update = None  # the last update and what changed (B-242)
        self._tidy_backups = None
        self._tidy_backup_config = None  # only the schedule and what it keeps (E-397)
        self._tidy_health = {}
        self._tidy_counts = Counter()  # state changes per entity since the night (B-241)
        self._tidy_counts_since = self.clock.now()
        self._tidy_busy = []  # the busiest of the last whole day
        self._tidy_connected_at = None
        self._tidy_fresh = False
        self._tidy_c_loaded = False
        self.tidy_entry_names = {}  # integration: its title and domain, from the last round
        self.tidy_took = None  # the last round: ms on the clock and ms of computing (B-247)

    def tidy_connected(self) -> None:
        """Home Assistant is (back) there: findings count from 10 minutes later (B-238). What
        was already seen keeps its time: a lost connection does not reset it."""
        self._tidy_count_from = self.clock.now() + AFTER_START
        self._tidy_connected_at = self.clock.now()  # after an update: the looks count from here
        self._tidy_fresh = True  # a round as soon as the 10 minutes are over: maybe an update

    async def _tidy_count(self, change: StateChange) -> None:
        """Every state change Home Assistant sends: to name what changes most often (B-241)."""
        self._tidy_counts[change.entity_id] += 1

    async def _tidy_beside(self, snapshot: WorldState) -> None:
        """The quarter-hour look reads Home Assistant – the integrations, the recorder's
        history, the system – and that can take long in a big house: it runs beside the core,
        which never waits for it (E-85). One at a time: a look still waiting for Home
        Assistant is not joined by a second."""
        if self._tidy_looking is not None and not self._tidy_looking.done():
            return
        self._tidy_looking = self._beside(self._tidy_review(snapshot), "tidy")

    async def _tidy_review(self, snapshot: WorldState) -> None:
        """Every quarter of an hour: a round when one is due, the night round at 03:30, and a
        look after an update when one is due."""
        now = snapshot.now
        if self._tidy_count_from is None:  # since HouseMind started
            self._tidy_count_from = self._started_at + AFTER_START
        if now < self._tidy_count_from:
            return
        await self._tidy_c_load()
        if self.world is not None:
            local = now.astimezone(self.world.tz)
            night = await self.db.get_setting("tidy_night")
            if local.time() >= NIGHT_AT and night != local.date().isoformat():
                await self.db.set_setting("tidy_night", local.date().isoformat())
                await self._tidy_read_system(now, night=True)
            elif self.tidy_system is None:  # just started: the tiles once, not empty all day
                await self._tidy_read_system(now, night=False)
        if self._tidy_fresh or self.tidy_checked is None or now - self.tidy_checked >= ROUND_EVERY:
            self._tidy_fresh = False
            await self.tidy_round()
        await self._tidy_look_after_update(now)

    async def tidy_check(self) -> bool:
        """'Nu nakijken' (B-235): a round now, at most once a minute, with the system too.
        False: not now."""
        now = self.clock.now()
        if self.tidy_checked is not None and now - self.tidy_checked < CHECK_GAP:
            return False
        await self._tidy_c_load()
        await self._tidy_read_system(now, night=False)
        await self.tidy_round()
        return True

    async def tidy_round(self) -> None:
        """One round: read the integrations, apply the rules, keep since when (B-235)."""
        if self.world is None or self._tidy_lock.locked():
            return
        async with self._tidy_lock:
            began, computed = timer.perf_counter(), timer.process_time()
            await self._tidy_round(began, computed)

    async def _tidy_round(self, began: float, computed: float) -> None:
        """The round itself, timed for the meter on Expert (B-247)."""
        assert self.world is not None
        if self._tidy_seen is None:
            stored = await self.db.get_setting("tidy_seen", {})
            self._tidy_seen = (
                {str(k): str(v) for k, v in stored.items()} if isinstance(stored, dict) else {}
            )
        try:
            entries = await self.ha.config_entries()
        except (ConnectionError, TimeoutError):
            _LOGGER.warning("Could not read the integrations from Home Assistant")
            entries = None
        if entries is not None:
            self.tidy_entry_names = {e.entry_id: (e.title or e.domain, e.domain) for e in entries}
        await self._tidy_load()
        states = dict(self.world.states)
        now = self.clock.now()
        await self._tidy_someone_back(states, now)
        extra = await self._tidy_energy(states, entries, now)
        found = tidy.find(
            self.model,
            states,
            entries,
            self._tidy_configured(states),
            self._followed(),
            now,
            self._tidy_seen,
            self._tidy_valued,
            extra,
            frozenset(self._tidy_choices),
        )
        self._tidy_valued = tidy.valued_next(self._tidy_valued, self.model, states)
        await self._tidy_forget_gone(found.seen, now)
        local = now.astimezone(self.world.tz)
        shown = tidy.apply_marks(
            found.findings, self._tidy_marks, self.model, now, local, self._tidy_sun_down()
        )
        unused = await self._tidy_unused(states, now, found.findings)
        self.tidy_findings = await self._tidy_suggest(shown, now) + unused
        self.tidy_checked = now
        await self._tidy_versions(states, entries, now)
        if found.seen != self._tidy_seen:
            self._tidy_seen = found.seen
            await self.db.set_setting("tidy_seen", found.seen)
        self.tidy_took = (
            (timer.perf_counter() - began) * 1000,
            (timer.process_time() - computed) * 1000,
        )
        await self._tidy_tell(now)

    # -- part D: who hears about it (E-361, B-246) -------------------------------------------

    async def _tidy_tell(self, now: datetime) -> None:
        """Only administrators; important at once, the rest one line a day in the bundle."""
        assert self.world is not None
        snapshot = self.world.snapshot(now)
        current: set[str] = set()
        system = self.tidy_system or {}
        backup = system.get("backup") or {}
        if backup.get("state") in ("old", "none"):
            old = backup.get("state") == "old"
            key = "notify.tidy.backup_old" if old else "notify.tidy.backup_none"
            current.add("tidy:backup")
            await self._tidy_raise(
                Kind.IMPORTANT, "tidy:backup", key, {"count": int(backup.get("days") or 0)}
            )
        disk = system.get("disk") or {}
        if disk.get("state") == "full":
            current.add("tidy:disk")
            params: dict[str, Any]
            if _short(disk, tidy_system.ROOM_IMPORTANT):  # the next backup may not fit (E-397)
                key = "notify.tidy.disk_room"
                params = {
                    "free": _size(disk["free"], self.languages),
                    "size": _size(disk["backup"], self.languages),
                }
            else:
                key, params = "notify.tidy.disk_full", {"pct": _pct(disk)}
            await self._tidy_raise(Kind.IMPORTANT, "tidy:disk", key, params)
        lost = set((self.tidy_update or {}).get("lost_entries") or [])
        for f in self.tidy_findings:
            if f.kind != "integration_down" or not (f.depends or f.used):
                continue
            if now - f.since < DOWN_TELLS:
                continue
            topic = f"tidy:down:{f.target}"
            current.add(topic)
            after = f.target in lost  # since the update (B-242)
            await self._tidy_raise(
                Kind.IMPORTANT,
                topic,
                "notify.tidy.down_update" if after else "notify.tidy.down",
                {"name": f.name, "count": len(f.follows)},
            )
        current |= await self._tidy_daily(snapshot.local.date().isoformat())
        for topic in self._tracked("tidy:"):
            if topic not in current:
                await self._resolve(topic)

    async def _tidy_raise(
        self, kind: Kind, topic: str, text_key: str, params: dict[str, Any]
    ) -> None:
        if topic in self._raised or self.world is None:
            return  # once; it goes by itself when it is solved
        snapshot = self.world.snapshot(self.clock.now())
        await self._raise(kind, topic, text_key, params, snapshot, "admins", VIEW)

    def _tidy_attention(self) -> dict[str, tuple[tuple[int, ...], str, dict[str, Any]]]:
        """Everything on the page that may be news, with how heavy it is and its words."""
        out: dict[str, tuple[tuple[int, ...], str, dict[str, Any]]] = {}
        for f in self.tidy_findings:
            count = (
                int(f.detail.get("days") or 0) if f.kind.startswith("unused") else len(f.follows)
            )
            weight = (int(f.heavy), int(f.used > 0), len(f.depends), len(f.follows))
            out[f.key] = (weight, f"notify.tidy.found.{f.kind}", {"name": f.name, "count": count})
        system = self.tidy_system or {}
        disk = system.get("disk") or {}
        if disk.get("state") == "filling":
            if _short(disk, tidy_system.ROOM_INFO):
                out["system:disk"] = (
                    (0,),
                    "notify.tidy.found.disk_room",
                    {"count": int(disk["room"])},
                )
            else:
                out["system:disk"] = ((0,), "notify.tidy.found.disk_filling", {"pct": _pct(disk)})
        if system.get("keepAll"):
            out["system:keep_all"] = ((0,), "notify.tidy.found.keep_all", {})
        loose = system.get("loose") or []
        if loose:  # a new loose backup makes it news again
            out[f"system:loose:{loose[-1]['id']}"] = (
                (0,),
                "notify.tidy.found.loose",
                {"count": len(loose), "size": _size(sum(b["size"] for b in loose), self.languages)},
            )
        database = system.get("database") or {}
        if database.get("state") == "grows":
            size = f"{round(float(database.get('growth') or 0) / 2**20)} MB"
            out["system:database"] = ((0,), "notify.tidy.found.db_grows", {"size": size})
        update = self.tidy_update or {}
        if update.get("lost"):
            out[f"update:{update.get('at')}"] = (
                (2,),
                "notify.tidy.found.update_lost",
                {"count": len(update["lost"])},
            )
        return out

    async def _tidy_daily(self, day: str) -> set[str]:
        """One line a day about the heaviest new thing (B-246); the first time one line with
        how many there are, so 28 known things do not become 28 messages."""
        items = self._tidy_attention()
        stored = await self.db.get_setting("tidy_told")
        if not isinstance(stored, dict):
            if self.tidy_checked is None:
                return set()
            topic = "tidy:overview" if items else ""
            if items:
                await self._tidy_raise(
                    Kind.INFO, topic, "notify.tidy.overview", {"count": len(items)}
                )
            told = {"keys": sorted(items), "day": day, "topic": topic}
            await self.db.set_setting("tidy_told", told)
            return {topic} if topic else set()
        keys = [k for k in stored.get("keys") or [] if k in items]  # gone: news again later
        topic = str(stored.get("topic") or "")
        new = [k for k in items if k not in keys]
        if new and stored.get("day") != day:
            heaviest = max(new, key=lambda k: (items[k][0], k))
            _, text_key, params = items[heaviest]
            topic = f"tidy:new:{heaviest}"
            await self._tidy_raise(Kind.INFO, topic, text_key, params)
            stored = {"keys": sorted(items), "day": day, "topic": topic}
        else:
            stored = {**stored, "keys": keys}
        await self.db.set_setting("tidy_told", stored)
        still = topic == "tidy:overview" and stored.get("day") == day
        still = still or (topic.startswith("tidy:new:") and topic[9:] in items)
        return {topic} if still else set()

    async def _tidy_energy(
        self, states: dict[str, State], entries: list[HAConfigEntry] | None, now: datetime
    ) -> list[tidy.Finding]:
        """The meters of the energy settings that stand still, and the energy settings
        themselves (B-265, B-267). The settings are read the way the energy part reads them;
        nothing in them changes."""
        try:
            prefs = await self.ha.energy_preferences()
        except Exception:  # an older HA or a hiccup: no energy findings now
            prefs = {}
        if not prefs:
            return []  # not read, or no energy settings at all: nothing to say for sure
        meters = tidy.meters_of(prefs)
        if self._tidy_meters is None:
            stored = await self.db.get_setting("tidy_meters", {})
            self._tidy_meters = {
                str(k): (float(v[0]), str(v[1]))
                for k, v in (stored.items() if isinstance(stored, dict) else [])
                if isinstance(v, list) and len(v) == 2
            }
        await self._tidy_seed_meters(meters, states, now)
        memory = tidy.meters_memory(meters, states, self._tidy_meters, now)
        if memory != self._tidy_meters:
            self._tidy_meters = memory
            await self.db.set_setting("tidy_meters", {k: list(v) for k, v in memory.items()})
        try:
            forecast = await self.ha.solar_forecast()
        except Exception:  # no forecast: the hours of sun count instead
            forecast = {}
        try:
            validation = await self.ha.energy_validate()
        except (ConnectionError, TimeoutError):
            validation = {}
        assert self.world is not None
        mode = self.world.snapshot(now).mode
        found = tidy.meters_still(
            meters,
            self.model,
            states,
            memory,
            now,
            entries,
            forecast,
            self._tidy_sun_hours if self.world.location is not None else None,
            away=mode in (HouseMode.AWAY, HouseMode.VACATION),
        )
        return found + tidy.energy_settings(
            prefs,
            validation,
            self.model,
            states,
            daily_words(self.languages, "grid_meter"),
            daily_words(self.languages, "not_grid_meter"),
            now,
        )

    async def _tidy_seed_meters(
        self, meters: list[tidy.Meter], states: dict[str, State], now: datetime
    ) -> None:
        """A meter HouseMind has no memory of yet: since when it has had this value, from
        Home Assistant's history of the last two days, so a restart does not start over."""
        assert self._tidy_meters is not None
        new = [
            m.entity_id
            for m in meters
            if m.entity_id not in self._tidy_meters and m.entity_id in states
        ]
        if not new:
            return
        try:
            history = await self.ha.history(new, now - SEED_HISTORY, now)
        except (ConnectionError, TimeoutError):
            history = []
        for entity_id in new:
            state = states[entity_id]
            try:
                value = float(state.state)
            except ValueError:
                continue
            changes = sorted((c.at, c.state) for c in history if c.entity_id == entity_id)
            # Only the state at the start of the period says nothing about the time in between:
            # then Home Assistant's own time of the last change counts (the careful one).
            found = tidy.still_since(changes, value) if len(changes) > 1 else None
            since = min(found or state.last_changed, state.last_changed, now)
            self._tidy_meters[entity_id] = (value, since.isoformat())

    def _tidy_sun_hours(self, start: datetime, end: datetime) -> float:
        """How many hours the sun was up between two moments (every quarter of an hour)."""
        assert self.world is not None and self.world.location is not None
        quarters = 0
        at = start
        while at < end:
            if elevation(*self.world.location, at) > 0:
                quarters += 1
            at += timedelta(minutes=15)
        return quarters / 4

    # -- part C: automations, the system, after an update (B-240 – B-242) ----------------------

    async def _tidy_c_load(self) -> None:
        if self._tidy_c_loaded:
            return
        self._tidy_c_loaded = True
        update = await self.db.get_setting("tidy_update")
        self.tidy_update = update if isinstance(update, dict) else None
        busy = await self.db.get_setting("tidy_busy", [])
        self._tidy_busy = [b for b in busy or [] if isinstance(b, dict)]

    async def _tidy_unused(
        self, states: dict[str, State], now: datetime, broken: list[tidy.Finding]
    ) -> list[tidy.Finding]:
        """Automations that have not run for a long time (B-240). Not the ones with a question
        at Bestaande automatiseringen or switched off there (E-42), not what HouseMind switched
        off here, and not one that is already a card for pointing to what is gone."""
        skip = {e for e, s in self.automations.status.items() if s in _ASKED}
        skip |= {t for t, x in self._tidy_off.items() if x.get("status") == "off"}
        skip |= {f.target for f in broken if f.kind == "missing_ref"}
        rows = await self.db.fetchall("SELECT entity_id, first_seen FROM automations")
        first = {str(r["entity_id"]): r["first_seen"] for r in rows}
        autos = []
        for entity_id, reading in self.automations.readings.items():
            state = states.get(entity_id)
            if state is None or state.state not in ("on", "off"):
                continue
            autos.append(
                tidy_system.Auto(
                    entity_id,
                    reading.automation.name,
                    state.state == "on",
                    _moment(state.attributes.get("last_triggered")),
                    _moment(first.get(entity_id)),
                    reading.automation.config_id,
                    self._tidy_words(entity_id, reading.automation.name),
                )
            )
        found = tidy_system.unused(autos, now, daily_words(self.languages, "season"), skip)
        return [f for f in found if f.key not in self._tidy_choices]

    def _tidy_words(self, entity_id: str, name: str) -> str:
        """Its name, labels and categories (E-345): where a season shows."""
        words = [name]
        entity = self.model.entities.get(entity_id)
        if entity is not None:
            words += [self.model.labels[x].name for x in entity.labels if x in self.model.labels]
            words += [
                self.model.categories[x].name
                for x in entity.categories.values()
                if x in self.model.categories
            ]
        return " ".join(words).lower()

    async def _tidy_read_system(self, now: datetime, night: bool) -> None:
        """The system (B-241): Home Assistant's system health and its backups. At night also
        the database's size for the growth, and the count of changes starts again."""
        try:
            self._tidy_health = await self.ha.system_health()
        except (ConnectionError, TimeoutError):
            self._tidy_health = {}
        try:
            self._tidy_backups = await self.ha.backups()
        except (ConnectionError, TimeoutError):
            self._tidy_backups = None
        try:
            self._tidy_backup_config = await self.ha.backup_config()
        except (ConnectionError, TimeoutError):
            self._tidy_backup_config = None
        await self._tidy_kept_gone()
        sizes = await self.db.get_setting("tidy_db", {}) or {}
        size = tidy_system.size_bytes(
            ((self._tidy_health.get("recorder") or {}).get("info") or {}).get("estimated_db_size")
        )
        if size is not None and self.world is not None:
            day = now.astimezone(self.world.tz).date().isoformat()
            if night or day not in sizes:  # one size a night; the first day the first read
                sizes[day] = size
                sizes = dict(sorted(sizes.items())[-tidy_system.DB_KEEP :])
                await self.db.set_setting("tidy_db", sizes)
        if night:
            seconds = (now - self._tidy_counts_since).total_seconds()
            self._tidy_busy = tidy_system.busiest(dict(self._tidy_counts), seconds)
            await self.db.set_setting("tidy_busy", self._tidy_busy)
            self._tidy_counts = Counter()
            self._tidy_counts_since = now
        self._tidy_tiles(now, sizes)

    def _tidy_tiles(self, now: datetime, sizes: dict[str, float] | None = None) -> None:
        if sizes is None:
            sizes = (self.tidy_system or {}).get("sizes") or {}
        busy = self._tidy_busy
        seconds = (now - self._tidy_counts_since).total_seconds()
        if not busy or seconds >= 6 * 3600:  # no whole day yet: what HouseMind counted so far
            busy = tidy_system.busiest(dict(self._tidy_counts), seconds) or busy
        backups = self._tidy_backups
        backup = tidy_system.backup_tile(backups, now, BACKUP_KEY in self._tidy_choices)
        keep = tidy_system.retention(self._tidy_backup_config)
        if keep is not None:
            backup["keep"] = keep  # 'Home Assistant … bewaart de laatste 3' (E-397)
        disk = tidy_system.disk_tile(self._tidy_health, tidy_system.backup_size(backups))
        old = tidy_system.loose(backups, now)
        if old and disk.get("state") in ("filling", "full"):
            disk["looseBytes"] = sum(b["size"] for b in old)  # what the old loose ones take
        if disk.get("state") != "unknown" and (together := tidy_system.on_device(backups)):
            disk["backups"] = together  # all backups on this device, for the ring (E-451)
        kept = (self._tidy_choices.get(LOOSE_KEY) or {}).get("ids") or []
        self.tidy_system = {
            "at": now.isoformat(),
            "backup": backup,
            "database": tidy_system.database_tile(self._tidy_health, sizes, busy),
            "disk": disk,
            "keepAll": tidy_system.keeps_all(keep) and KEEP_ALL_KEY not in self._tidy_choices,
            "loose": tidy_system.loose(backups, now, kept),
            "sizes": sizes,
        }

    async def _tidy_kept_gone(self) -> None:
        """A backup kept on purpose that is no longer there leaves the choice (E-397); with none
        left the choice goes."""
        choice = self._tidy_choices.get(LOOSE_KEY)
        if choice is None or self._tidy_backups is None:
            return
        there = {str(b.get("backup_id")) for b in self._tidy_backups}
        ids = [i for i in choice.get("ids") or [] if i in there]
        if ids == choice.get("ids"):
            return
        if ids:
            choice["ids"] = ids
        else:
            del self._tidy_choices[LOOSE_KEY]
        await self._tidy_save()

    async def _tidy_versions(
        self, states: dict[str, State], entries: list[HAConfigEntry] | None, now: datetime
    ) -> None:
        """After every round: an update when a version changed (B-242). Otherwise what works
        now is kept, as the state from before a next update."""
        await self._tidy_c_load()
        found = tidy_system.versions(self.config.version if self.config else None, states)
        if not found:
            return
        stored = await self.db.get_setting("tidy_versions") or {}
        parts = tidy_system.changed(stored, found)
        if parts:
            before = await self.db.get_setting("tidy_before") or {}
            self.tidy_update = {
                "from": {p: stored.get(p) for p in parts},
                "to": {p: found.get(p) for p in parts},
                "at": (self._tidy_connected_at or now).isoformat(),
                "before": before if before.get("working") else None,
                "checks": [],
            }
            await self.db.set_setting("tidy_update", self.tidy_update)
        elif entries is not None:
            entities, loaded = tidy_system.working(self.model, states, entries)
            await self.db.set_setting(
                "tidy_before", {"at": now.isoformat(), "working": entities, "loaded": loaded}
            )
        if found != stored:
            await self.db.set_setting("tidy_versions", found)
        if (
            self.tidy_update is not None
            and now - datetime.fromisoformat(str(self.tidy_update["at"])) >= tidy_system.UPDATE_KEEP
        ):
            self.tidy_update = None
            await self.db.set_setting("tidy_update", None)

    async def _tidy_look_after_update(self, now: datetime) -> None:
        """A look at 30 minutes, 2, 6 and 24 hours after the update (B-242)."""
        record = self.tidy_update
        if record is None or self.world is None or not tidy_system.check_due(record, now):
            return
        try:
            entries = await self.ha.config_entries()
        except (ConnectionError, TimeoutError):
            return
        await self._tidy_load()
        lost, lost_entries = tidy_system.compare(
            record["before"], self.model, dict(self.world.states), entries, set(self._tidy_marks)
        )
        self.tidy_update = tidy_system.check(record, lost, lost_entries, now)
        await self.db.set_setting("tidy_update", self.tidy_update)

    # -- marks, choices and switching off (B-239, B-243, B-244) ------------------------------

    async def _tidy_load(self) -> None:
        if self._tidy_loaded:
            return
        marks = await self.setting("tidy_marks")
        self._tidy_marks = {
            str(k): tidy.Mark(**v) for k, v in (marks or {}).items() if isinstance(v, dict)
        }
        choices = await self.setting("tidy_choices")
        self._tidy_choices = {str(k): dict(v) for k, v in (choices or {}).items()}
        off = await self.db.get_setting("tidy_off", {})
        self._tidy_off = {str(k): dict(v) for k, v in (off or {}).items()}
        self._tidy_gate_sync()
        self._tidy_loaded = True

    async def _tidy_save(self) -> None:
        await self.db.set_setting(
            "tidy_marks", {k: asdict(m) for k, m in sorted(self._tidy_marks.items())}
        )
        await self.db.set_setting("tidy_choices", dict(sorted(self._tidy_choices.items())))
        await self.db.set_setting("tidy_off", dict(sorted(self._tidy_off.items())))
        self._tidy_gate_sync()

    def _tidy_gate_sync(self) -> None:
        if self.gate is not None:
            self.gate.state.tidy_off = {
                t
                for t, e in self._tidy_off.items()
                if e.get("kind") == "entity" and e.get("status") == "off"
            }

    def _tidy_sun_down(self) -> Any:
        if self.world is None or self.world.location is None:
            return None
        location = self.world.location
        return lambda moment: elevation(*location, moment) < 0

    async def _tidy_someone_back(self, states: dict[str, State], now: datetime) -> None:
        """Someone switched back on in Home Assistant what HouseMind switched off: it stays on,
        and HouseMind asks no more – a choice of its own (B-243, 'De mens wint')."""
        changed = False
        for target, entry in self._tidy_off.items():
            if entry.get("status") != "off":
                continue
            entity = self.model.entities.get(target)
            if entry.get("kind") == "entity":
                back = entity is not None and not entity.disabled
            else:
                state = states.get(target)
                back = state is not None and state.state == "on"
            if back:
                entry.update(status="someone", back_at=now.isoformat())
                reason = entry.get("reason")
                kind = "unused_on" if reason == "unused" else reason
                self._tidy_choices[f"{kind}:{target}"] = {
                    "choice": "someone_on",
                    "at": now.isoformat(),
                    "by": None,
                }
                changed = True
        if changed:
            await self._tidy_save()

    async def _tidy_forget_gone(self, seen: dict[str, str], now: datetime) -> None:
        """A choice about something that has not been there for 90 days goes by itself."""
        changed = False
        for key, choice in list(self._tidy_choices.items()):
            target = key.partition(":")[2]
            if (
                target in self.model.entities
                or target in self.model.devices
                or key in seen
                or key in (BACKUP_KEY, KEEP_ALL_KEY, LOOSE_KEY)  # about the system, not a thing
            ):
                if choice.pop("gone_since", None) is not None:
                    changed = True
                continue
            gone = choice.setdefault("gone_since", now.isoformat())
            changed = True
            if now - datetime.fromisoformat(str(gone)) >= GONE_FOR:
                del self._tidy_choices[key]
        if changed:
            await self.db.set_setting("tidy_choices", dict(sorted(self._tidy_choices.items())))

    async def _tidy_suggest(
        self, findings: list[tidy.Finding], now: datetime
    ) -> list[tidy.Finding]:
        """Away at about the same time most days: 'Meestal uit tussen … – bewust?' with those
        hours ready, from Home Assistant's history of the last week (B-239). Never chosen by
        HouseMind itself."""
        wanted = [
            f
            for f in findings
            if f.kind in ("unavailable", "device_down") and "review" not in f.detail
        ][:PATTERN_MAX]
        if not wanted or self.world is None:
            return findings
        probe = {
            f.key: f.target if f.kind == "unavailable" else f.follows[0] for f in wanted if f.target
        }
        try:
            history = await self.ha.history(sorted(set(probe.values())), now - PATTERN_DAYS, now)
        except (ConnectionError, TimeoutError):
            return findings
        out: list[tidy.Finding] = []
        for f in findings:
            entity_id = probe.get(f.key)
            if entity_id is not None:
                changes = [(c.at, c.state) for c in history if c.entity_id == entity_id]
                found = tidy.suggest_window(changes, self.world.tz, now, self._tidy_sun_down())
                if found is not None:
                    f = replace(f, detail={**f.detail, "suggest_mark": found})
            out.append(f)
        return out

    def _tidy_finding(self, key: str) -> tidy.Finding | None:
        return next((f for f in self.tidy_findings if f.key == key), None)

    async def tidy_off(self, key: str, user_id: str | None) -> dict[str, Any]:
        """'Uitzetten' after the administrator's yes (B-243): an entity that is a duplicate or
        no longer provided (a bundle: all of it, one by one), or an automation that points to
        what is gone. Through the safety gate; nothing is ever deleted."""
        await self._tidy_load()
        finding = self._tidy_finding(key)
        if finding is None:
            return {"ok": False, "reason": "unknown"}
        if finding.kind in OFF_ENTITY:
            targets = list(finding.follows) if finding.kind.endswith("_group") else [finding.target]
            reason = finding.kind.removesuffix("_group")
            done = [t for t in targets if await self._tidy_entity(t, False, user_id, reason)]
        elif finding.kind == "missing_ref" and finding.detail.get("kind") == "automation":
            done = (
                [finding.target]
                if await self._tidy_automation(finding.target, False, user_id)
                else []
            )
        elif finding.kind == "unused_on":  # an automation that has not run for long (B-240)
            done = (
                [finding.target]
                if await self._tidy_automation(finding.target, False, user_id, "unused")
                else []
            )
        else:
            return {"ok": False, "reason": "not_allowed"}
        if done:
            await self._tidy_save()
            self.tidy_findings = [f for f in self.tidy_findings if f.key != key]
        return {
            "ok": bool(done),
            "done": len(done),
            "of": len(targets) if finding.kind in OFF_ENTITY else 1,
        }

    async def tidy_on(
        self, target: str, user_id: str | None, reason: str = "undo"
    ) -> dict[str, Any]:
        """'Terugzetten' (B-243): always possible, also during a pause."""
        await self._tidy_load()
        entry = self._tidy_off.get(target)
        if entry is None or entry.get("status") != "off":
            return {"ok": False, "reason": "not_off"}
        if entry.get("kind") == "entity":
            ok = await self._tidy_entity(target, True, user_id, reason)
        else:
            ok = await self._tidy_automation(target, True, user_id, reason)
        if ok:
            entry.update(status="back", back_at=self.clock.now().isoformat())
            await self._tidy_save()
        return {"ok": ok}

    async def tidy_all_on(self, user_id: str | None, reason: str = "undo") -> list[str]:
        """'Alles terugzetten', one by one; gives what could not go back on (E-112, E-274)."""
        await self._tidy_load()
        failed = []
        for target, entry in list(self._tidy_off.items()):
            if (
                entry.get("status") == "off"
                and not (await self.tidy_on(target, user_id, reason))["ok"]
            ):
                failed.append(target)
        return failed

    async def _tidy_entity(
        self, target: str, enable: bool, user_id: str | None, reason: str
    ) -> bool:
        assert self.gate is not None
        now = self.clock.now()
        decision = await self.gate.submit_tidy(
            target,
            enable,
            Layer.RESTORE if enable else Layer.PERSON_ANSWER,
            now,
            user_id,
            reason if not enable else "undo",
        )
        await self._record(decision)
        if not decision.executed:
            return False
        entity = self.model.entities.get(target)
        if entity is not None:
            self.model.entities[target] = replace(entity, disabled=not enable)
            self.model.forget_rooms()  # the same entity, now (not) disabled
        if not enable:
            self._tidy_off[target] = {
                "kind": "entity",
                "reason": reason,
                "at": now.isoformat(),
                "by": user_id,
                "status": "off",
            }
            self._tidy_gate_sync()
        return True

    async def _tidy_automation(
        self, target: str, enable: bool, user_id: str | None, reason: str = "missing_ref"
    ) -> bool:
        assert self.gate is not None
        now = self.clock.now()
        decision = await self.gate.submit(
            ActionRequest(
                Layer.RESTORE if enable else Layer.PERSON_ANSWER,
                target,
                TURN_ON if enable else TURN_OFF,
                reason="undo" if enable else "tidy",
                created_at=now,
                user_id=user_id,
            )
        )
        await self._record(decision)
        if not decision.executed:
            return False
        if not enable:
            self._tidy_off[target] = {
                "kind": "automation",
                "reason": reason,
                "at": now.isoformat(),
                "by": user_id,
                "status": "off",
            }
        return True

    async def tidy_choose(self, key: str, choice: str, user_id: str | None) -> bool:
        """'Laat zo' or 'Geen dubbele' (B-244): the finding goes and does not come back."""
        await self._tidy_load()
        kind = key.partition(":")[0]
        if kind not in tidy.CHOICES.get(choice, ()):
            return False
        entry: dict[str, Any] = {
            "choice": choice,
            "at": self.clock.now().isoformat(),
            "by": user_id,
        }
        if key == LOOSE_KEY:  # the loose backups on the card now; a new one brings it back
            shown = [b["id"] for b in (self.tidy_system or {}).get("loose") or []]
            if not shown:
                return False
            before = (self._tidy_choices.get(LOOSE_KEY) or {}).get("ids") or []
            entry["ids"] = sorted(set(before) | set(shown))
        self._tidy_choices[key] = entry
        await self._tidy_save()
        self.tidy_findings = [f for f in self.tidy_findings if f.key != key]
        if key in (BACKUP_KEY, KEEP_ALL_KEY, LOOSE_KEY):
            self._tidy_tiles(self.clock.now())
        return True

    async def tidy_undo_choice(self, key: str) -> bool:
        """'Terugdraaien': the choice goes, and a round at once brings the card back."""
        await self._tidy_load()
        if self._tidy_choices.pop(key, None) is None:
            return False
        await self._tidy_save()
        await self.tidy_round()
        if key in (BACKUP_KEY, KEEP_ALL_KEY, LOOSE_KEY):
            self._tidy_tiles(self.clock.now())
        return True

    async def tidy_mark(
        self,
        target: str,
        how: str,
        user_id: str | None,
        start: str | None = None,
        end: str | None = None,
        until: str | None = None,
    ) -> bool:
        """'Bewust uit' (B-239): always, in the dark, at fixed hours or until a date. After 90
        days HouseMind asks whether it still holds."""
        await self._tidy_load()
        if how not in ("always", "dark", "hours", "until"):
            return False
        if how == "hours" and (tidy._minutes(start) is None or tidy._minutes(end) is None):
            return False
        if how == "until":
            try:
                datetime.fromisoformat(str(until))
            except ValueError:
                return False
        now = self.clock.now()
        self._tidy_marks[target] = tidy.Mark(
            target,
            how,
            start if how == "hours" else None,
            end if how == "hours" else None,
            until if how == "until" else None,
            (now + tidy.REVIEW_AFTER).isoformat(),
            now.isoformat(),
            user_id,
        )
        await self._tidy_save()
        await self._tidy_requiet()
        return True

    async def tidy_read_in(self, key: str, value: Any) -> bool:
        """The choices and 'Bewust uit' from a settings file (B-244, E-296): only what is whole,
        and what this house already chose stays. What HouseMind switched off never travels."""
        await self._tidy_load()
        if not isinstance(value, dict):
            return False
        for k, raw in value.items():
            if key == "tidy_marks":
                try:
                    mark = tidy.Mark(**raw)
                except TypeError:
                    continue
                if mark.target == str(k) and mark.how in ("always", "dark", "hours", "until"):
                    self._tidy_marks.setdefault(mark.target, mark)
            elif isinstance(raw, dict) and str(k).partition(":")[0] in tidy.CHOICES.get(
                str(raw.get("choice")), ()
            ):
                self._tidy_choices.setdefault(str(k), dict(raw))
        await self._tidy_save()
        return True

    async def tidy_unmark(self, target: str) -> bool:
        """'Terugdraaien' of 'Bewust uit': a round at once brings the card back."""
        await self._tidy_load()
        if self._tidy_marks.pop(target, None) is None:
            return False
        await self._tidy_save()
        await self.tidy_round()
        return True

    async def tidy_review_ok(self, target: str) -> bool:
        """'Ja, laat zo' on 'Klopt dit nog?': the next question in 90 days."""
        await self._tidy_load()
        mark = self._tidy_marks.get(target)
        if mark is None:
            return False
        later = (self.clock.now() + tidy.REVIEW_AFTER).isoformat()
        self._tidy_marks[target] = replace(mark, review_at=later)
        await self._tidy_save()
        await self._tidy_requiet()
        return True

    async def _tidy_requiet(self) -> None:
        """Apply the marks to the last round at once, so the page follows the choice."""
        if self.world is None:
            return
        now = self.clock.now()
        self.tidy_findings = tidy.apply_marks(
            self.tidy_findings,
            self._tidy_marks,
            self.model,
            now,
            now.astimezone(self.world.tz),
            self._tidy_sun_down(),
        )

    def _tidy_configured(self, states: dict[str, Any]) -> list[tidy.Configured]:
        """The automations, scripts and scenes with what they point to."""
        found = [
            tidy.Configured(
                r.automation.entity_id,
                r.automation.name,
                "automation",
                r.automation.refs,
                r.automation.config_id,
            )
            for r in self.automations.readings.values()
            if r.automation.readable
        ]
        found += [
            tidy.Configured(s.entity_id, s.name, "script", s.refs, s.entity_id.partition(".")[2])
            for s in self.scripts
            if s.readable
        ]
        for entity_id, state in sorted(states.items()):
            members = state.attributes.get("entity_id") if entity_id.startswith("scene.") else None
            if not isinstance(members, list):
                continue
            refs = tuple(f"entity:{m}" for m in members if isinstance(m, str) and "." in m)
            config_id = state.attributes.get("id")
            found.append(
                tidy.Configured(
                    entity_id,
                    str(state.attributes.get("friendly_name") or entity_id),
                    "scene",
                    refs,
                    str(config_id) if config_id else None,
                )
            )
        # A group of the day's rhythm that names a lamp that is not there (E-470).
        book = self._hm.daylight
        for entry_id in book.missing:
            rhythm = book.rhythms[entry_id]
            found.append(
                tidy.Configured(
                    book.main_switch(rhythm) or entry_id,
                    rhythm.title,
                    "day_rhythm",
                    tuple(f"entity:{lamp}" for lamp in rhythm.lights),
                )
            )
        return found


_ASKED = frozenset({Status.OFFERED, Status.TURNED_OFF})  # E-42 already asks about these


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _pct(tile: dict[str, Any]) -> str:
    return f"{round(float(tile.get('share') or 0) * 100)} %"


def _short(disk: dict[str, Any], fits: int) -> bool:
    """Fewer backups fit than this (E-397): the reason the disk tile looks the way it does."""
    return disk.get("room") is not None and float(disk["free"]) < fits * float(disk["backup"])


def _size(size: float, languages: Languages) -> dict[str, Any]:
    """A size as the page shows it (GB from 1 GB on, else MB), in the reader's own way of
    writing a number – '2,3 GB', '2.3 GB' – for every language there is, also an own one
    (code QA 3, punt 8: the four were written in the code)."""
    gib = size / 2**30
    return {
        "lang": {
            code: f"{languages.number(code, gib, 1)} GB"
            if gib >= 1
            else f"{round(size / 2**20)} MB"
            for code in languages.languages
        }
    }
