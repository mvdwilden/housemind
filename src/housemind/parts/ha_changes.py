"""Home Assistant restarting, updating and changing (H7.4, H7.5): reading the house again –
rooms, devices, groups and roles –, the self-check after an update, coming back after the
connection was lost, and catching up at the start (QA 2, M-28, M-29)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import groups, history, security
from housemind.brain.automations import (
    Automations,
)
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.house_knowledge import HouseKnowledge
from housemind.brain.household import Household
from housemind.brain.notify import (
    Kind,
)
from housemind.brain.rhythm import alarm_sensors, daily_words
from housemind.brain.rules import RuleBook
from housemind.brain.scenes import (
    Scene,
)
from housemind.brain.skills import SkillEngine
from housemind.brain.speech import Speech, speakers, tts_engine
from housemind.brain.water import WaterWatch, flow_sensors, meters
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel, Source, StateChange
from housemind.energy.manager import EnergyManager
from housemind.energy.setup import energy_words
from housemind.ha.adapter import HAConfig, HomeAssistant, Installation
from housemind.i18n.catalog import Languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.compat import Compatibility
from housemind.knowledge.matcher import recognise
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import SafetyGate
from housemind.safety.hard_limits import find_openers
from housemind.world.climate import Temperatures, sort_temperatures
from housemind.world.openings import house_openings
from housemind.world.world import World, WorldState
from housemind.world.zones import open_spaces, sort_zones

if TYPE_CHECKING:
    from housemind.brain.daylight import DaylightBook
    from housemind.runtime import Status

_LOGGER = logging.getLogger(__name__)

# Domains where HA can make a group whose members HouseMind then handles one by one.
GROUPABLE = frozenset({"light", "switch", "cover", "fan", "lock", "valve", "media_player"})
RECOVER_S = 60  # coming back after HA was away may take this long; then the next tick tries again
UNTESTED_TRIAL = timedelta(hours=24)
INSTALL_AGAIN = timedelta(hours=24)  # how Home Assistant is installed: read once a day (E-390)


def _readable(entity_id: str) -> str:
    """A name from a code, when nothing better is known: "light.reading_lamp" → "Reading lamp"."""
    words = entity_id.split(".", 1)[-1].replace("_", " ").strip()
    return words[:1].upper() + words[1:]


class HAChangesPart(Part):
    """Home Assistant restarting, updating and changing (H7.4, H7.5): reading the house again –"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        daylight: DaylightBook
        _alarm_sensors: list[str]

        async def _emergencies_now(self) -> None: ...

        _history_seen: set[str]
        _measurements_changed: Any
        _model_dirty: datetime | None

        async def _notify(self) -> None: ...
        async def _on_change(self, change: StateChange) -> None: ...

        _own_scenes: list[Scene]

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

        _raised: dict[str, tuple[Kind, str, datetime]]

        async def _refresh_labels(self) -> None: ...
        async def _apply_label_roles(self) -> None: ...
        def _cooking_roles(self) -> None: ...

        _refresh_orb: Any

        async def _resolve(self, topic: str) -> None: ...
        def tidy_connected(self) -> None: ...

        _sync_automations: Any
        _z2m_members: dict[str, list[str]]
        area_roles: dict[str, str]
        automations: Automations
        catalog: Catalog
        clock: Clock
        compatibility: Compatibility
        config: HAConfig | None
        db: Database
        energy: EnergyManager | None
        gate: SafetyGate | None
        ha: HomeAssistant
        history_status: history.HistoryStatus
        household: Household | None
        installation: Installation | None
        _install_read: datetime | None
        knowledge: HouseKnowledge
        languages: Languages
        model: HouseModel
        openings: set[str]

        def place(self) -> tuple[float, float] | None: ...

        rules: RuleBook
        self_check: dict[str, Any]
        skills: SkillEngine | None
        speech: Speech

        def start_history(self) -> bool: ...

        status: Status
        temperatures: Temperatures
        vesta_voice: Any
        voice_for: Any
        water: WaterWatch
        world: World | None

    async def _load_area_roles(self) -> None:
        words = role_words(self.languages)
        stored = {r["area_id"]: r for r in await self.db.fetchall("SELECT * FROM areas")}
        for area in self.model.areas.values():
            row = stored.get(area.id)
            if row is not None and row["role_source"] == "person" and row["role"]:
                self.area_roles[area.id] = str(row["role"])
                continue
            self.area_roles[area.id] = area_role(area, words)
            await self.db.execute(
                "INSERT INTO areas(area_id, name, floor_id, role, role_source) "
                "VALUES(?,?,?,?, 'auto') ON CONFLICT(area_id) DO UPDATE SET "
                "name = excluded.name, floor_id = excluded.floor_id, role = CASE "
                "WHEN areas.role_source = 'person' THEN areas.role ELSE excluded.role END",
                (area.id, area.name, area.floor_id, self.area_roles[area.id]),
            )

    async def _on_connection(self, connected: bool) -> None:
        """Lost: do nothing new and show "let op". Back: look at the house again, then the
        self-check, because HA may have been updated (E-152, E-155)."""
        if not connected:
            self.status.ha_lost = self.clock.now()
            _LOGGER.warning("No connection to Home Assistant; doing nothing new until it is back")
            self._refresh_orb()
            await self._notify()
            return
        self.tidy_connected()  # what is broken counts from 10 minutes later (B-238)
        await self._recover()

    async def _recover(self) -> None:
        """Back: read the house again. Fails that, or takes it longer than RECOVER_S, then
        HouseMind stays careful and the next tick tries again (M-29)."""
        try:
            async with asyncio.timeout(RECOVER_S):
                self.config = await self.ha.ha_config()
                if self.world is not None:
                    self.world.location = self.place()
                await self._refresh_model()
        except Exception:
            _LOGGER.exception("Could not read the house again after the connection came back")
            return
        self.status.ha_lost = None
        await self._self_check()
        await self._emergencies_now()
        self._refresh_orb()
        await self._notify()

    async def _on_registry(self, _event: str) -> None:
        """A device was added, renamed or moved in HA: look again once it is quiet."""
        self._model_dirty = self.clock.now()

    async def _refresh_model(self) -> None:
        """Read the registries and states again, in place: what HouseMind knows about the
        house right now (modes, who is where, people winning) stays as it is."""
        assert self.world is not None and self.gate is not None
        fresh = await self.ha.load_model()
        for part in ("floors", "areas", "devices", "entities", "labels", "categories"):
            target = getattr(self.model, part)
            target.clear()
            target.update(getattr(fresh, part))
        await self._load_area_roles()
        self.world.load_states(await self.ha.get_states())
        if self.world.lux_cache is not None:
            self.world.lux_cache.clear()  # the house changed: find its light sensors again
        await self._read_z2m_groups()
        self._index_groups()
        self.daylight.index()  # which lamps its groups name, as the house is now (E-465)
        self.world.occupancy.index()
        self._alarm_sensors = alarm_sensors(self.model, daily_words(self.languages, "alarm"))
        self._sort_temperatures()
        self._sort_openings()
        self.world.prime(self.clock.now())
        self._opener_roles()
        security.rebuild(self._hm)
        new = set(history.entities_to_read(self._hm)) - self._history_seen
        if self.history_status.read_at and new:
            self.start_history()  # a new device: read again, all of it, so nothing counts twice
        self.speech.devices = speakers(self.model)
        self.speech.tts = tts_engine(self.model, self.world.states)
        self.speech.voice = await self.voice_for(None, await self.vesta_voice())
        if self.energy is not None:
            await self.energy.refresh(self.model, self.world.states, energy_words(self.languages))
            self._energy_roles()
        await self._apply_label_roles()  # the lamps confirmed from a label (E-345)
        configured = self.energy.setup.water if self.energy is not None else []
        self.water.meters = meters(self.model, self.world.states, configured)
        self.water.flows = flow_sensors(self.model, self.water.meters)
        if self.household is not None:
            self.household.index()
            self._cooking_roles()
        await self._sync_automations()
        await self._refresh_labels()
        self._measurements_changed = True
        if self.skills is not None:
            self.skills.forget()  # new devices, rooms or roles: find each room's devices again

    async def _read_z2m_groups(self) -> None:
        """Zigbee2MQTT groups whose members HA does not list (older HA or Zigbee2MQTT): their
        members from Zigbee2MQTT's own retained list of groups, through HA's MQTT (E-312)."""
        assert self.world is not None
        states = self.world.states

        def unlisted(e: Entity) -> bool:
            device = self.model.devices.get(e.device_id or "")
            attributes = states[e.entity_id].attributes if e.entity_id in states else {}
            return (
                groups.source_of(e, device, attributes) == "zigbee2mqtt"
                and groups.members_of(attributes) is None
            )

        missing = [e for e in self.model.entities.values() if e.domain in GROUPABLE and unlisted(e)]
        if not missing:
            self._z2m_members = {}
            return
        raw = await self.ha.mqtt_retained("zigbee2mqtt/bridge/groups")
        self._z2m_members = groups.z2m_members(raw, self.model)

    def _index_groups(self) -> None:
        """Groups made in HA or in the radio network itself (E-312). A group has priority over
        its lamps: a group in the radio network (Zigbee2MQTT, ZHA, Hue, deCONZ) first – the
        biggest one in one room, so a whole room goes in one radio message, much faster than
        lamp by lamp; then HA's own groups – the smallest one, as they bring no speed and only
        one call (lamps that mostly work together, like a dining table's). Its room is the
        lamps' room. A group over several rooms only gathers.

        Met rust laten (E-458, the administrator's choice): a group ticked holds itself and
        everything in it – no group with any of that in it steers. A lamp ticked by itself is
        never addressed on its own, but a group that steers takes it along."""
        assert self.world is not None and self.gate is not None
        m = self.model
        m.groups.clear()
        m.steered_by.clear()
        m.group_area.clear()
        m.group_source.clear()
        m.steering.clear()
        raw: dict[str, list[str]] = {}
        for entity_id, state in self.world.states.items():
            if entity_id.split(".", 1)[0] not in GROUPABLE or not groups.is_group(state.attributes):
                continue
            entity = m.entities.get(entity_id)
            device = m.devices.get(entity.device_id or "") if entity else None
            m.group_source[entity_id] = groups.source_of(entity, device, state.attributes)
            members = groups.members_of(state.attributes) or self._z2m_members.get(entity_id, [])
            raw[entity_id] = [x for x in members if x != entity_id]
        for entity_id in self._z2m_members:
            if entity_id not in raw and entity_id in m.entities:
                m.group_source[entity_id] = "zigbee2mqtt"
                raw[entity_id] = list(self._z2m_members[entity_id])

        def leaves(group: str, seen: frozenset[str] = frozenset()) -> set[str]:
            out: set[str] = set()
            for member in raw.get(group, []):
                if member in raw and member not in seen:
                    out |= leaves(member, seen | {group})
                elif member in m.entities:
                    out.add(member)
            return out

        for group in raw:
            m.groups[group] = tuple(sorted(leaves(group)))
        for group, lamps in m.groups.items():
            rooms = {a.id for lamp in lamps if (a := m.area_of(lamp)) is not None}
            if len(rooms) == 1:
                m.group_area[group] = rooms.pop()
        ticked = self.gate.state.excluded
        m.held = {g for g in raw if g in ticked}
        m.held |= {lamp for g in m.held for lamp in m.groups[g]}
        m.held |= {g for g in raw if m.groups[g] and set(m.groups[g]) <= m.held}
        m.left_alone = set(ticked) | m.held
        steering = [
            g
            for g in m.group_area
            if m.groups[g] and g not in m.held and not set(m.groups[g]) & m.held
        ]
        radio = sorted(
            (g for g in steering if m.group_source.get(g) in groups.RADIO),
            key=lambda g: (-len(m.groups[g]), g),
        )
        others = sorted(
            (g for g in steering if m.group_source.get(g) not in groups.RADIO),
            key=lambda g: (len(m.groups[g]), g),
        )
        for group in radio + others:
            lamps = m.groups[group]
            if not any(lamp in m.steered_by for lamp in lamps):
                for lamp in lamps:  # never overlapping: a lamp has one group that steers it
                    m.steered_by[lamp] = group
                m.steering.add(group)
        if self.skills is not None:
            self.skills.forget()  # which lamp a group steers decides what each room has (S1)

    def _sort_openings(self) -> None:
        """Doors and windows of the house, not of appliances or vehicles, and what presence may
        count, after an administrator excluded something – also the zones of presence sensors
        (E-215). Where the rules doubted, what an administrator said holds (B-359)."""
        assert self.gate is not None
        excluded = self.gate.state.excluded
        decided = self.knowledge.decided("opening")
        self.openings = house_openings(self.model, self._opening_words(), excluded, decided)
        zones = sort_zones(
            self.model,
            daily_words(self.languages, "zone"),
            daily_words(self.languages, "presence_words"),
            daily_words(self.languages, "bed"),
            self.gate.state.excluded,
        )
        if self.skills is not None:
            self.skills.openings = self.openings
            self.skills.open_space = open_spaces(zones, self.model)
        if self.world is not None:
            self.world.occupancy.excluded = set(self.gate.state.excluded)
            self.world.occupancy.zones = zones
            self.world.occupancy.index()
            # Who still sleeps in a bedroom (E-483): bed words from every language file.
            self.world.sleep_watch.bed_words = tuple(daily_words(self.languages, "bed"))
            self.world.index_sleep()

    def _opener_roles(self) -> None:
        """A garage door, gate or door opener is protected whatever HA calls it and whatever
        else it is: a cover, a relay or a button (E-31; QA 3, gat 1). The gate keeps them in a
        set of their own that no label, AI finding or learned role can overwrite; a cover also
        gets the role, as before, so the rest of the house knows it."""
        assert self.gate is not None
        garage_rooms = {a for a, role in self.area_roles.items() if role == "garage"}
        openers = find_openers(
            self.model,
            garage_rooms,
            daily_words(self.languages, "garage_door"),
            daily_words(self.languages, "opener"),
            self.gate.state.roles,
        )
        self.gate.state.openers = openers
        for entity_id in openers:
            if entity_id.startswith("cover."):
                self.gate.state.roles.setdefault(entity_id, "garage_opener")

    def _opening_words(self) -> list[str]:
        keys = ("fridge", "appliance", "cooking")
        return [w for key in keys for w in daily_words(self.languages, key)]

    def _sort_temperatures(self) -> None:
        """Which sensor measures each room; where the rules doubted, what an administrator
        chose holds (B-359)."""
        words = daily_words(self.languages, "temperature_not_room")
        chosen = self.knowledge.chosen_temperatures()
        self.temperatures = sort_temperatures(self.model, self.area_roles, words, chosen)
        if self.skills is not None:
            self.skills.room_climates = set(self.temperatures.rooms_climate)
            self.skills.room_air = self.temperatures.of_air

    def _followed(self) -> set[str]:
        """Devices HouseMind was told about by name: in house rules, own moments, automations
        it took over, and what steers energy."""
        followed = {r.scope_id for r in self.rules.rules if r.scope_type == "entity"}
        for scene in self._own_scenes:
            followed |= set(scene.steps)
        followed |= {
            e
            for e, status in self.automations.status.items()
            if status is AutomationStatus.TURNED_OFF
        }
        assert self.gate is not None
        followed |= set(self.gate.state.roles)
        return followed

    async def _self_check(self) -> None:
        """E-152: does everything HouseMind uses still exist? An action HA no longer offers
        is only watched from now on; a missing device gets a word. Both go away by
        themselves once it works again."""
        assert self.gate is not None and self.world is not None
        try:
            available = await self.ha.available_actions()
        except (ConnectionError, TimeoutError):
            return
        broken: dict[str, set[str]] = {}
        for entity in self.model.entities.values():
            if entity.entity_id not in self.world.states:
                continue  # switched off in HA: not used
            kind = recognise(self.catalog, entity, self.gate.state.roles.get(entity.entity_id)).kind
            for allowed in kind.actions if kind is not None else ():
                domain, _, service = allowed.action.partition(".")
                if domain == entity.domain and service not in available.get(domain, set()):
                    broken.setdefault(allowed.action, set()).add(entity.entity_id)
        self.gate.state.broken_actions = set(broken)
        followed = self._followed()
        # Remember the names of what HouseMind follows, so a device that disappeared can still
        # be named by its name and not by a code.
        names = dict(await self.db.get_setting("followed_names", {}) or {})
        names.update({e: self.model.entities[e].name for e in followed if e in self.model.entities})
        await self.db.set_setting("followed_names", {e: names[e] for e in followed if e in names})
        missing = sorted(e for e in followed if e not in self.model.entities)
        snapshot = self.world.snapshot(self.clock.now())
        if broken:
            hit = sorted({self.model.entities[e].name for found in broken.values() for e in found})
            await self._raise(
                Kind.IMPORTANT,
                "selfcheck:actions",
                "notify.selfcheck_actions",
                {"devices": ", ".join(hit[:5]), "number": len(hit)},
                snapshot,
                concerns="admins",
            )
        elif "selfcheck:actions" in self._raised:
            await self._resolve("selfcheck:actions")
        if missing:
            await self._raise(
                Kind.INFO,
                "selfcheck:missing",
                "notify.selfcheck_missing",
                {
                    "devices": ", ".join(names.get(e) or _readable(e) for e in missing[:5]),
                    "count": len(missing),
                },
                snapshot,
                concerns="admins",
            )
        elif "selfcheck:missing" in self._raised:
            await self._resolve("selfcheck:missing")
        self.self_check = {
            "at": self.clock.now().isoformat(),
            "ha_version": self.config.version if self.config else None,
            "broken_actions": sorted(broken),
            "missing": missing,
        }
        await self._check_version(snapshot)

    async def _check_version(self, snapshot: WorldState) -> None:
        """E-142, E-155: a Home Assistant newer than tested – careful devices only after a
        yes, until a day went by without failures (or a knowledge update lists the version);
        older than supported – a clear word for the administrators."""
        assert self.gate is not None
        version = self.config.version if self.config else "unknown"
        status = self.compatibility.status(version)
        untested = False
        if status == "newer":
            seen = await self.db.get_setting("ha_untested") or {}
            if seen.get("version") != version:
                seen = {"version": version, "since": self.clock.now().isoformat()}
                await self.db.set_setting("ha_untested", seen)
            since = datetime.fromisoformat(str(seen["since"]))
            failed = await self.db.fetchone(
                "SELECT count(*) AS n FROM decisions WHERE outcome = 'failed' AND at >= ?",
                (since.isoformat(),),
            )
            clean = not self.gate.state.broken_actions and not (failed and failed["n"])
            untested = not (clean and self.clock.now() - since >= UNTESTED_TRIAL)
        self.gate.state.untested_ha = untested
        if untested:
            await self._raise(
                Kind.INFO,
                "ha:untested",
                "notify.ha_untested",
                {"version": version},
                snapshot,
                concerns="admins",
            )
        elif "ha:untested" in self._raised:
            await self._resolve("ha:untested")
        if status == "older":
            await self._raise(
                Kind.IMPORTANT,
                "ha:too_old",
                "notify.ha_too_old",
                {"version": version, "oldest": self.compatibility.oldest_text()},
                snapshot,
                concerns="admins",
            )
        elif "ha:too_old" in self._raised:
            await self._resolve("ha:too_old")
        await self._check_install(snapshot)

    async def _check_install(self, snapshot: WorldState) -> None:
        """E-390, B-294: Supervised, or a Home Assistant the Supervisor calls not healthy or not
        supported – one calm word for the administrators that HouseMind simply goes on.
        Supervised goes first, then not healthy. Read at the start and once a day after."""
        now = self.clock.now()
        if self._install_read is None or now - self._install_read >= INSTALL_AGAIN:
            self._install_read = now
            self.installation = await self.ha.installation()
        found = self.installation
        key = None
        if found is not None:
            if found.kind == "supervised":
                key = "notify.install_supervised"
            elif not found.healthy:
                key = "notify.install_unhealthy"
            elif not found.supported:
                key = "notify.install_unsupported"
        if key is not None:
            await self._raise(Kind.INFO, "ha:install", key, {}, snapshot, concerns="admins")
        elif "ha:install" in self._raised and found is not None:
            await self._resolve("ha:install")  # only on a clear answer: never on a guess

    def _energy_roles(self) -> None:
        """Tell the gate which device does what in the energy setup (catalogue roles)."""
        assert self.energy is not None and self.gate is not None
        s = self.energy.setup
        roles = self.gate.state.roles
        if s.export_limit:
            roles[s.export_limit] = "export_limit"
        for appliance in s.appliances:
            roles[appliance.start] = "appliance_start"
        # Writing sparingly per integration (E-340, K3).
        integrations = self.energy.integrations
        self.gate.state.cloud = set(integrations.cloud)
        self.gate.state.flash = set(integrations.flash)
        self.gate.state.netcode = tuple(integrations.netcode)
        # Another system steers these: HouseMind keeps off (E-68).
        self.gate.state.other_system |= s.manager_entities

    async def _caught_up(self) -> None:
        """Listening now, read the house once more (M-28): what changed while HouseMind was
        starting – a leak that began, a door that opened – counts as a change now, not only
        at its next one. A change the listener already brought in is newer and stays."""
        assert self.world is not None
        for entity_id, state in (await self.ha.get_states()).items():
            old = self.world.states.get(entity_id)
            if old is not None and (
                old.state == state.state or old.last_changed > state.last_changed
            ):
                continue
            await self._on_change(StateChange(entity_id, old, state, Source.UNKNOWN))
