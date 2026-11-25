"""Home Assistant tidy (E-351 – E-354, B-236 – B-238): what is broken in Home Assistant, found
carefully and told once per cause.

The rules only: no Home Assistant and no clock of their own. A round gets the house model, the
states, the integrations (config entries), the automations, scripts and scenes, what HouseMind
itself follows and the time, and gives back the findings. ``seen`` holds since when each one
was first seen (or since Home Assistant says it changed); a finding shows only after an hour
(B-238), so a restart leaves nothing behind. A reference to what is gone shows at once.
HouseMind never deletes anything here and never changes what someone made (E-03).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from housemind.core.model import Entity, HouseModel, State
from housemind.ha.adapter import HAConfigEntry

SHOWN_AFTER = timedelta(hours=1)  # B-238: even away is no fault
HEAVY_AFTER = timedelta(hours=24)
# What does not pass by itself shows at once: its own rule already waited (B-238, B-265 – B-267).
AT_ONCE = frozenset(
    {"missing_ref", "meter_still", "integration_silent", "energy_no_grid", "energy_issues"}
)
SILENT_AFTER = timedelta(hours=24)  # B-266: a whole integration without a value this long
SILENT_MIN = 3  # entities, so one odd sensor is no 'integration that measures nothing'
# How long a meter from the energy settings may stand still before it counts (B-265).
STILL_AFTER = {
    "grid": timedelta(hours=12),
    "battery": timedelta(days=2),
    "water": timedelta(days=3),
    "gas": timedelta(days=7),
    "device": timedelta(days=7),
}
SOLAR_EXPECTED_WH = 1000.0  # the sun was expected to give at least this while it stood still
SOLAR_SUN_HOURS = 6.0  # without a forecast: this many hours of sun
# The states of an integration that does not start (B-236).
DOWN_STATES = frozenset(
    {"setup_error", "setup_retry", "not_loaded", "migration_error", "failed_unload"}
)
NOT_WORKING = frozenset({"unavailable", "unknown"})
_NUMBERED = re.compile(r"^(?P<base>[a-z0-9_]+?)_(?P<n>[2-9]|[1-9][0-9]+)$")


@dataclass(frozen=True, slots=True)
class Configured:
    """An automation, script or scene with what it points to (its ``refs``)."""

    entity_id: str
    name: str
    kind: str  # "automation", "script", "scene" or "day_rhythm"
    refs: tuple[str, ...]  # "entity:light.x", "device:abc", "area:kitchen" (refs_of)
    config_id: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing to look at (B-236). Its follow-ups are part of it and never count apart."""

    key: str  # "<kind>:<target>", also the key in ``seen``
    kind: str  # missing_ref, not_provided, duplicate, integration_down, device_down, unavailable
    target: str  # an entity id, a device id, a config entry id or an automation's entity id
    name: str
    since: datetime
    heavy: bool = False
    follows: tuple[str, ...] = ()  # the entities that are down because of it (B-237)
    depends: tuple[str, ...] = ()  # automations and scripts that point to it or its follow-ups
    used: int = 0  # how many of them HouseMind itself follows
    missing: tuple[str, ...] = ()  # for missing_ref: what is not there ("entity:light.x")
    suggest: str | None = None  # a working entity it looks like (missing_ref, duplicate)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Round:
    """What one round found, and the updated ``seen``."""

    findings: list[Finding]
    seen: dict[str, str]


def shown_name(entity_id: str, model: HouseModel, states: dict[str, State]) -> str:
    """The name as Home Assistant shows it: its friendly name, often the device's name with the
    entity's ('WKR KEUKEN LEDSTRIP Vermogen', not 'Vermogen'). Without a state: the device's
    name before the entity's, the way Home Assistant makes it."""
    state = states.get(entity_id)
    if state is not None and state.attributes.get("friendly_name"):
        return str(state.attributes["friendly_name"])
    entity = model.entities.get(entity_id)
    if entity is None:
        return entity_id
    device = model.devices.get(entity.device_id or "")
    if device is not None and device.name and device.name.lower() not in entity.name.lower():
        return f"{device.name} {entity.name}"
    return entity.name


def _working(state: State | None) -> bool:
    return state is not None and state.state not in NOT_WORKING


def classify(
    entity: Entity,
    state: State | None,
    model: HouseModel,
    entries: dict[str, HAConfigEntry] | None,
    had_value: bool,
) -> str:
    """How an entity is, carefully and in a fixed order (B-236): the first rule that fits.
    'Unavailable' is never 'gone' by itself; what someone switched off is no fault."""
    if entity.disabled:
        return "disabled"
    registered = entity.platform is not None  # not in the registry: only its state counts
    if registered and entity.device_id and entity.device_id not in model.devices:
        return "orphaned"
    if registered and entries is not None and entity.config_entry_id:
        entry = entries.get(entity.config_entry_id)
        if entry is None:
            return "orphaned"
        if entry.disabled:
            return "disabled"
    if state is None:
        return "orphaned" if registered else "state_only"
    if state.state == "unavailable" and state.attributes.get("restored"):
        entry = entries.get(entity.config_entry_id or "") if entries is not None else None
        if entry is not None and entry.state == "loaded":
            return "not_provided"  # its integration runs, but no longer gives it
    if state.state == "unavailable":
        return "unavailable"
    if state.state == "unknown":
        return "unknown" if had_value else "never_valued"
    return "active"


def _similar(missing: str, model: HouseModel, states: dict[str, State]) -> str | None:
    """A working entity of the same domain that looks like a missing one: ``x_2`` or ``x``,
    or a name that is almost the same."""
    domain, _, object_id = missing.partition(".")
    numbered = _NUMBERED.match(object_id)
    near = [f"{domain}.{object_id}_2"]
    if numbered:
        near.append(f"{domain}.{numbered['base']}")
    for candidate in near:
        if candidate in model.entities and _working(states.get(candidate)):
            return candidate
    best, score = None, 0.0
    for entity_id in model.entities:
        if not entity_id.startswith(f"{domain}.") or not _working(states.get(entity_id)):
            continue
        ratio = SequenceMatcher(None, object_id, entity_id.partition(".")[2]).ratio()
        if ratio > score:
            best, score = entity_id, ratio
    return best if score >= 0.85 else None


def _exists(ref: str, model: HouseModel, states: dict[str, State]) -> bool:
    kind, _, value = ref.partition(":")
    if kind == "entity":
        return value in model.entities or value in states
    if kind == "device":
        return value in model.devices
    return value in model.areas


def find(
    model: HouseModel,
    states: dict[str, State],
    entries: list[HAConfigEntry] | None,
    configured: list[Configured],
    followed: set[str],
    now: datetime,
    seen: dict[str, str],
    valued: set[str],
    extra: Iterable[Finding] = (),
    skip: frozenset[str] = frozenset(),
) -> Round:
    """One round (B-236 – B-238). ``valued``: the entities that had a value in the round
    before, so an ``unknown`` counts only after a real value. Rounds start only 10 minutes
    after Home Assistant started (B-238, in ``parts/tidy.py``). ``entries`` None: the
    integrations could not be read; what depends on them is left as it was in ``seen``.
    ``extra``: the meters and the energy settings (B-265, B-267), found apart; they are
    weighed, bundled and sorted with the rest. ``skip``: the administrator's choices (B-244),
    left out before bundling."""
    by_entry = {e.entry_id: e for e in entries} if entries is not None else None
    new_seen: dict[str, str] = {}
    if by_entry is None:
        keep = ("integration_down:", "not_provided:")
        new_seen.update({k: v for k, v in seen.items() if k.startswith(keep)})

    def since(key: str, entity_ids: Iterable[str] = ()) -> datetime:
        """Since when: as kept, or else since Home Assistant says it changed (its
        ``last_changed``), and at the latest from now (B-238)."""
        if key in seen:
            new_seen[key] = seen[key]
            return datetime.fromisoformat(seen[key])
        changed = [states[e].last_changed for e in entity_ids if e in states]
        first = min([now, *changed])  # never in the future
        new_seen[key] = first.isoformat()
        return first

    statuses: dict[str, str] = {}
    for entity_id, entity in model.entities.items():
        if entity.platform == "housemind" or entity.domain in ("automation", "script", "scene"):
            continue
        statuses[entity_id] = classify(
            entity, states.get(entity_id), model, by_entry, entity_id in valued
        )
    # Who points to what: automations, scripts and scenes (for "what depends on it").
    pointed: dict[str, list[str]] = {}
    for item in configured:
        for ref in item.refs:
            pointed.setdefault(ref, []).append(item.entity_id)

    def depends_on(entity_ids: set[str]) -> tuple[str, ...]:
        found = {a for e in entity_ids for a in pointed.get(f"entity:{e}", [])}
        return tuple(sorted(found))

    findings: list[Finding] = []
    covered: set[str] = set()  # entities that are a follow-up or have a finding of their own

    # 1. An integration that does not start: its entities are its follow-ups (B-237).
    for entry in entries or []:
        if entry.disabled or entry.state not in DOWN_STATES:
            continue
        follows = {
            e.entity_id
            for e in model.entities.values()
            if e.config_entry_id == entry.entry_id and not e.disabled
        }
        covered |= follows
        key = f"integration_down:{entry.entry_id}"
        findings.append(
            Finding(
                key,
                "integration_down",
                entry.entry_id,
                entry.title or entry.domain,
                since(key, follows),
                follows=tuple(sorted(follows)),
                depends=depends_on(follows),
                used=len(follows & followed),
                detail={
                    "domain": entry.domain,
                    "state": entry.state,
                    "source": entry.entry_id,
                    "source_name": entry.title or entry.domain,
                },
            )
        )

    # 1b. An integration that runs but measures nothing: all its entities have had no value for
    # a day or longer, also when HouseMind never saw one (B-266, E-370).
    for entry in entries or []:
        if entry.disabled or entry.state != "loaded":
            continue
        its_entities = [
            e.entity_id
            for e in model.entities.values()
            if e.config_entry_id == entry.entry_id
            and not e.disabled
            and e.platform != "housemind"
            and e.domain not in ("automation", "script", "scene")
        ]
        if len(its_entities) < SILENT_MIN or any(e in covered for e in its_entities):
            continue
        present = [st for st in (states.get(e) for e in its_entities) if st is not None]
        if not present or any(st.state not in NOT_WORKING for st in present):
            continue
        last = max(st.last_changed for st in present)
        if now - last < SILENT_AFTER:
            continue
        follows = set(its_entities)
        covered |= follows
        key = f"integration_silent:{entry.entry_id}"
        new_seen[key] = seen.get(key, last.isoformat())
        findings.append(
            Finding(
                key,
                "integration_silent",
                entry.entry_id,
                entry.title or entry.domain,
                datetime.fromisoformat(new_seen[key]),
                follows=tuple(sorted(follows)),
                depends=depends_on(follows),
                used=len(follows & followed),
                detail={
                    "domain": entry.domain,
                    "source": entry.entry_id,
                    "source_name": entry.title or entry.domain,
                },
            )
        )

    # 2. A whole device down: all its entities that are on have no value (B-237).
    members: dict[str, list[str]] = {}
    for entity_id, status in statuses.items():
        device_id = model.entities[entity_id].device_id
        if device_id and device_id in model.devices and status not in ("disabled", "state_only"):
            members.setdefault(device_id, []).append(entity_id)
    for device_id, entity_ids in sorted(members.items()):
        live = [e for e in entity_ids if statuses[e] != "never_valued"]
        if not live or any(e in covered for e in live):
            continue
        if not all(statuses[e] in ("unavailable", "unknown") for e in live) or len(live) < 2:
            continue
        follows = set(live)
        covered |= follows
        key = f"device_down:{device_id}"
        findings.append(
            Finding(
                key,
                "device_down",
                device_id,
                model.devices[device_id].name,
                since(key, follows),
                follows=tuple(sorted(follows)),
                depends=depends_on(follows),
                used=len(follows & followed),
                detail=_entity_detail(model.entities[live[0]], by_entry),
            )
        )

    # 3. A duplicate: x_2 does not work and x of the same integration does, or the other way.
    for entity_id, status in sorted(statuses.items()):
        if entity_id in covered or status not in ("unavailable", "unknown", "orphaned"):
            continue
        entity = model.entities[entity_id]
        domain, _, object_id = entity_id.partition(".")
        other: str | None = None
        numbered = _NUMBERED.match(object_id)
        if numbered:
            base = f"{domain}.{numbered['base']}"
            if _same_source(entity, base, model) and statuses.get(base) == "active":
                other = base
        else:
            siblings = [
                e
                for e in model.entities
                if (m := _NUMBERED.match(e.partition(".")[2]))
                and e.startswith(f"{domain}.")
                and m["base"] == object_id
                and statuses.get(e) == "active"
                and _same_source(entity, e, model)
                and (
                    model.entities[e].device_id == entity.device_id
                    or model.entities[e].name == entity.name
                )
            ]
            if len(siblings) == 1:
                other = siblings[0]
        if other is None:
            continue
        covered.add(entity_id)
        key = f"duplicate:{entity_id}"
        findings.append(
            Finding(
                key,
                "duplicate",
                entity_id,
                shown_name(entity_id, model, states),
                since(key, (entity_id,)),
                depends=depends_on({entity_id}),
                used=int(entity_id in followed),
                suggest=other,
                detail=_entity_detail(entity, by_entry),
            )
        )

    # 4. No longer given by its integration, and 5. long unavailable.
    for entity_id, status in sorted(statuses.items()):
        if entity_id in covered:
            continue
        entity = model.entities[entity_id]
        if status == "not_provided":
            kind = "not_provided"
        elif status in ("unavailable", "unknown", "orphaned"):
            if entity.entity_category in ("diagnostic", "config"):
                continue  # only together with its device (B-236)
            kind = "unavailable"
        else:
            continue
        key = f"{kind}:{entity_id}"
        findings.append(
            Finding(
                key,
                kind,
                entity_id,
                shown_name(entity_id, model, states),
                since(key, (entity_id,)),
                depends=depends_on({entity_id}),
                used=int(entity_id in followed),
                detail=_entity_detail(entity, by_entry),
            )
        )

    # 6. An automation, script or scene that points to what is not there. A scene made with
    # scene.create (by this or another configuration) exists only after it ran: not missing.
    created = {
        r.partition(":")[2] for item in configured for r in item.refs if r.startswith("creates:")
    }
    for item in configured:
        missing = tuple(
            r
            for r in item.refs
            if not r.startswith("creates:")
            and r.partition(":")[2] not in created
            and not _exists(r, model, states)
        )
        if not missing:
            continue
        key = f"missing_ref:{item.entity_id}"
        first = next((r for r in missing if r.startswith("entity:")), None)
        findings.append(
            Finding(
                key,
                "missing_ref",
                item.entity_id,
                item.name,
                since(key),
                missing=missing,
                suggest=_similar(first.partition(":")[2], model, states) if first else None,
                detail={"kind": item.kind, "config_id": item.config_id},
            )
        )

    for f in extra:
        if f.kind.startswith("energy_"):  # since when first seen (B-267)
            f = replace(f, since=since(f.key))
        findings.append(f)

    # What does not pass by itself shows at once: its own rule already waited (B-238).
    shown = [
        _weigh(f, now)
        for f in findings
        if f.key not in skip and (f.kind in AT_ONCE or now - f.since >= SHOWN_AFTER)
    ]
    shown = _bundle(shown)
    shown.sort(key=lambda f: (not f.heavy, f.since, f.name.lower()))
    return Round(shown, new_seen)


def _bundle(findings: list[Finding]) -> list[Finding]:
    """Two or more entities of one integration that are no longer provided, or unavailable,
    are one card (B-258): 'Victron: 76 entiteiten niet meer geleverd'. Their times stay per
    entity in ``seen``; one entity alone keeps its own card."""
    groups: dict[tuple[str, str], list[Finding]] = {}
    out: list[Finding] = []
    for f in findings:
        source = f.detail.get("source")
        if f.kind in ("not_provided", "unavailable", "meter_still") and source:
            groups.setdefault((f.kind, str(source)), []).append(f)
        else:
            out.append(f)
    for (kind, source), items in sorted(groups.items()):
        if len(items) < 2:
            out.extend(items)
            continue
        first = items[0]
        out.append(
            Finding(
                f"{kind}_group:{source}",
                f"{kind}_group",
                source,
                str(first.detail.get("source_name") or source),
                min(f.since for f in items),
                heavy=any(f.heavy for f in items),
                follows=tuple(sorted(f.target for f in items)),
                depends=tuple(sorted({a for f in items for a in f.depends})),
                used=sum(f.used for f in items),
                detail={k: first.detail.get(k) for k in ("source", "source_name", "domain")},
            )
        )
    return out


def _same_source(entity: Entity, other_id: str, model: HouseModel) -> bool:
    other = model.entities.get(other_id)
    return other is not None and other.platform == entity.platform


def _entity_detail(entity: Entity, entries: dict[str, HAConfigEntry] | None) -> dict[str, Any]:
    """Where an entity comes from: its integration, or else its platform (YAML, a template)
    – for bundling (B-258), the filter and the page in Home Assistant."""
    entry = (entries or {}).get(entity.config_entry_id or "")
    if entry is not None:
        source, name, domain = entry.entry_id, entry.title or entry.domain, entry.domain
    else:
        platform = entity.platform or entity.domain
        source, name, domain = f"platform:{platform}", platform, platform
    return {
        "device_id": entity.device_id,
        "platform": entity.platform,
        "source": source,
        "source_name": name,
        "domain": domain,
    }


def _weigh(finding: Finding, now: datetime) -> Finding:
    """Heavy after a day, or at once when automations or HouseMind depend on it (B-238)."""
    heavy = now - finding.since >= HEAVY_AFTER or bool(finding.depends) or finding.used > 0
    if finding.kind == "missing_ref":
        heavy = now - finding.since >= HEAVY_AFTER
    return finding if heavy == finding.heavy else replace(finding, heavy=heavy)


def valued_next(before: set[str], model: HouseModel, states: dict[str, State]) -> set[str]:
    """The next round's ``valued``: what has a real value now, and what had one and has
    been ``unknown`` since."""
    return {
        e
        for e in model.entities
        if _working(states.get(e))
        or (e in before and (state := states.get(e)) is not None and state.state == "unknown")
    }


# -- the meters of the energy settings (B-265, E-369) ---------------------------------------


@dataclass(frozen=True, slots=True)
class Meter:
    """A meter from Home Assistant's energy settings and what it measures."""

    entity_id: str
    role: str  # solar, grid, battery, gas, water or device


def meters_of(prefs: dict[str, Any]) -> list[Meter]:
    """The meters in the energy settings (``energy/get_prefs``) that are entities. A statistic
    from elsewhere (``tibber:…``) has no state to look at and is left out."""
    found: list[Meter] = []

    def add(stat: Any, role: str) -> None:
        if isinstance(stat, str) and "." in stat and ":" not in stat:
            found.append(Meter(stat, role))

    for source in prefs.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        kind = source.get("type")
        if kind == "grid":
            for flow in source.get("flow_from") or []:
                add(_mapping(flow).get("stat_energy_from"), "grid")
            for flow in source.get("flow_to") or []:
                add(_mapping(flow).get("stat_energy_to"), "grid")
            add(source.get("stat_energy_from"), "grid")
            add(source.get("stat_energy_to"), "grid")
        elif kind == "battery":
            add(source.get("stat_energy_from"), "battery")
            add(source.get("stat_energy_to"), "battery")
        elif kind in ("solar", "gas", "water"):
            add(source.get("stat_energy_from"), str(kind))
    for key in ("device_consumption", "device_consumption_water"):
        for device in prefs.get(key) or []:
            add(_mapping(device).get("stat_consumption"), "device")
    return list({m.entity_id: m for m in found}.values())


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def meters_memory(
    meters: list[Meter],
    states: dict[str, State],
    memory: dict[str, tuple[float, str]],
    now: datetime,
) -> dict[str, tuple[float, str]]:
    """Each meter's last value and since when it is that value. No value (unavailable, unknown)
    changes nothing: a meter that comes back with the same value still stands still."""
    out: dict[str, tuple[float, str]] = {}
    for meter in meters:
        value = _number(states.get(meter.entity_id))
        kept = memory.get(meter.entity_id)
        if value is None:
            if kept is not None:
                out[meter.entity_id] = kept
        elif kept is None or kept[0] != value:
            out[meter.entity_id] = (value, now.isoformat())
        else:
            out[meter.entity_id] = kept
    return out


def meters_still(
    meters: list[Meter],
    model: HouseModel,
    states: dict[str, State],
    memory: dict[str, tuple[float, str]],
    now: datetime,
    entries: list[HAConfigEntry] | None,
    forecast: dict[datetime, float] | None = None,
    sun_hours: Callable[[datetime, datetime], float] | None = None,
    away: bool = False,
) -> list[Finding]:
    """The meters that stand still although they should move (B-265). The sun: while it was
    up and at least 1 kWh was expected; the grid: in and out both for 12 hours; the home
    battery: charge and discharge both for 2 days; water 3 days (not while away); gas and
    devices 7 days. HouseMind uses these meters, so each one is heavy."""
    by_entry = {e.entry_id: e for e in entries} if entries is not None else None
    still: dict[str, list[tuple[Meter, datetime, float | None]]] = {}
    for meter in meters:
        kept = memory.get(meter.entity_id)
        if kept is None or _number(states.get(meter.entity_id)) is None:
            continue
        since = datetime.fromisoformat(kept[1])
        expected: float | None = None
        if meter.role == "solar":
            if forecast:
                expected = sum(wh for at, wh in forecast.items() if since <= at <= now)
                if expected < SOLAR_EXPECTED_WH:
                    continue
            elif sun_hours is None or sun_hours(since, now) < SOLAR_SUN_HOURS:
                continue
        elif (meter.role == "water" and away) or now - since < STILL_AFTER.get(
            meter.role, STILL_AFTER["device"]
        ):
            continue  # water does not count while the house is away
        still.setdefault(meter.role, []).append((meter, since, expected))
    findings: list[Finding] = []
    for role, items in still.items():
        if role in ("grid", "battery"):  # both ways must stand still
            wanted = [m for m in meters if m.role == role]
            if len(items) < len(wanted):
                continue
        for meter, since, expected in items:
            entity = model.entities.get(meter.entity_id)
            state = states[meter.entity_id]
            detail = (
                _entity_detail(entity, by_entry)
                if entity is not None
                else {"source": f"platform:{meter.role}", "source_name": meter.role}
            )
            findings.append(
                Finding(
                    f"meter_still:{meter.entity_id}",
                    "meter_still",
                    meter.entity_id,
                    shown_name(meter.entity_id, model, states),
                    since,
                    used=1,
                    detail={
                        **detail,
                        "role": role,
                        "value": state.state,
                        "unit": state.attributes.get("unit_of_measurement"),
                        "expected_wh": round(expected) if expected is not None else None,
                    },
                )
            )
    return findings


def still_since(changes: list[tuple[datetime, str]], value: float) -> datetime | None:
    """From the history of a meter (oldest first): since when it has had this value, with
    times without a value in between left out. None: the history does not say."""
    first: datetime | None = None
    for at, raw in changes:
        try:
            number = float(raw)
        except ValueError:
            continue
        first = (first or at) if number == value else None
    return first


# -- the energy settings (B-267, E-371) -----------------------------------------------------


def energy_settings(
    prefs: dict[str, Any],
    validation: dict[str, Any],
    model: HouseModel,
    states: dict[str, State],
    grid_words: list[str],
    not_grid_words: list[str],
    now: datetime,
) -> list[Finding]:
    """No grid meter in the energy settings (with up to two that look like one), and what Home
    Assistant itself found wrong there (``energy/validate``). HouseMind never changes them."""
    findings: list[Finding] = []
    sources = [s for s in prefs.get("energy_sources") or [] if isinstance(s, dict)]
    if not any(s.get("type") == "grid" for s in sources):
        used = {m.entity_id for m in meters_of(prefs)}
        findings.append(
            Finding(
                "energy_no_grid:energy",
                "energy_no_grid",
                "energy",
                "",
                now,
                used=1,
                detail={
                    "candidates": _grid_candidates(model, states, used, grid_words, not_grid_words)
                },
            )
        )
    issues: list[dict[str, str]] = []
    for part in ("energy_sources", "device_consumption", "device_consumption_water"):
        for found in validation.get(part) or []:
            for issue in found if isinstance(found, list) else []:
                kind = str(_mapping(issue).get("type") or "")
                for affected in _mapping(issue).get("affected_entities") or [[""]]:
                    entity_id = (
                        str(affected[0]) if isinstance(affected, list | tuple) and affected else ""
                    )
                    issues.append({"type": kind, "entity": entity_id})
    if issues:
        findings.append(
            Finding(
                "energy_issues:energy",
                "energy_issues",
                "energy",
                "",
                now,
                used=1,
                detail={"issues": issues},
            )
        )
    return findings


def _grid_candidates(
    model: HouseModel,
    states: dict[str, State],
    used: set[str],
    words: list[str],
    not_words: list[str],
) -> list[str]:
    """Up to two sensors that look like a grid meter: energy, counting up, with a value, and a
    name that says taking from the grid – never a cost, a month or a day."""
    scored: list[tuple[int, str]] = []
    for entity_id, state in states.items():
        if not entity_id.startswith("sensor.") or entity_id in used:
            continue
        attrs = state.attributes
        if attrs.get("device_class") != "energy":
            continue
        if attrs.get("state_class") not in ("total", "total_increasing") or _number(state) is None:
            continue
        entity = model.entities.get(entity_id)
        text = f"{entity_id} {entity.name if entity else attrs.get('friendly_name', '')}".lower()
        text = text.replace("_", " ")
        if any(w in text for w in not_words):
            continue
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, entity_id))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [entity_id for _, entity_id in scored[:2]]


# -- on purpose, and the administrator's choices (B-239, B-244, E-355, E-360) ----------------

MARK_GRACE = timedelta(minutes=30)  # after a window ends, still quiet for this long
REVIEW_AFTER = timedelta(days=90)  # then: 'Klopt dit nog?'
MARKABLE = frozenset(
    {"unavailable", "unavailable_group", "device_down", "integration_down", "integration_silent"}
)
# What a choice may be about: 'Laat zo' for a reference or for Home Assistant keeping all its
# backups (E-397), 'Geen dubbele' for a duplicate, 'Laat aan' and 'Laat staan' for an
# automation that has not run for long (B-240), 'Ik maak back-ups buiten Home Assistant'
# (B-241), and 'Bewust houden' for old loose backups (E-397).
CHOICES = {
    "leave": ("missing_ref", "backup_keep_all"),
    "not_duplicate": ("duplicate",),
    "keep_on": ("unused_on",),
    "keep": ("unused_off",),
    "own_backups": ("backup",),
    "keep_backups": ("backup_loose",),
}


@dataclass(frozen=True, slots=True)
class Mark:
    """'Bewust uit' (B-239): an entity, a device or an integration that is away on purpose –
    always, in the dark, at fixed hours or until a date."""

    target: str  # an entity id, a device id, a config entry id or "platform:…"
    how: str  # "always", "dark", "hours" or "until"
    start: str | None = None  # "22:00" (hours)
    end: str | None = None  # "07:00" (hours)
    until: str | None = None  # "2026-12-31" (until)
    review_at: str = ""  # since when HouseMind asks 'Klopt dit nog?'
    at: str = ""
    by: str | None = None


def mark_targets(finding: Finding, model: HouseModel) -> set[str]:
    """What a mark can be on to cover this finding: itself, and what it belongs to."""
    found = {finding.target}
    if finding.detail.get("source"):
        found.add(str(finding.detail["source"]))
    entity = model.entities.get(finding.target)
    if entity is not None:
        found |= {x for x in (entity.device_id, entity.config_entry_id) if x}
    if finding.detail.get("device_id"):
        found.add(str(finding.detail["device_id"]))
    return found


def _minutes(text: str | None) -> int | None:
    if not text or ":" not in text:
        return None
    hours, _, minutes = text.partition(":")
    try:
        value = int(hours) * 60 + int(minutes)
    except ValueError:
        return None
    return value if 0 <= value < 24 * 60 else None


def _in_hours(minute: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end  # across midnight


def quiet_now(
    mark: Mark, local: datetime, sun_down: Callable[[datetime], bool] | None = None
) -> bool:
    """Whether this mark keeps its findings quiet right now (``local``: the house's time), a
    window plus half an hour after it ended (B-239)."""
    if mark.how == "always":
        return True
    if mark.how == "until":
        try:
            return local.date() <= datetime.fromisoformat(str(mark.until)).date()
        except ValueError:
            return False
    if mark.how == "hours":
        start, end = _minutes(mark.start), _minutes(mark.end)
        if start is None or end is None:
            return False
        minute = local.hour * 60 + local.minute
        before = local - MARK_GRACE
        return _in_hours(minute, start, end) or _in_hours(
            before.hour * 60 + before.minute, start, end
        )
    if mark.how == "dark" and sun_down is not None:
        return sun_down(local) or sun_down(local - MARK_GRACE)
    return False


def apply_marks(
    findings: list[Finding],
    marks: dict[str, Mark],
    model: HouseModel,
    now: datetime,
    local: datetime,
    sun_down: Callable[[datetime], bool] | None = None,
) -> list[Finding]:
    """Leave out what is away on purpose now (B-239). A mark that is 90 days old brings its
    finding back, as 'Klopt dit nog?'."""
    out: list[Finding] = []
    for finding in findings:
        if finding.kind in MARKABLE:
            mark = next(
                (marks[t] for t in sorted(mark_targets(finding, model)) if t in marks), None
            )
            if mark is not None:
                due = bool(mark.review_at) and now >= datetime.fromisoformat(mark.review_at)
                if due:
                    finding = replace(finding, detail={**finding.detail, "review": mark.target})
                elif quiet_now(mark, local, sun_down):
                    continue
        out.append(finding)
    return out


def suggest_window(
    changes: list[tuple[datetime, str]],
    tz: Any,
    now: datetime,
    sun_down: Callable[[datetime], bool] | None = None,
) -> dict[str, str] | None:
    """Away at about the same time in at least 5 of the last 7 days (within an hour): those
    hours, rounded to half an hour – or 'in the dark' when every time was the dark (B-239).
    Only a suggestion: HouseMind never chooses it itself."""
    intervals: list[tuple[datetime, datetime]] = []
    away_since: datetime | None = None
    for at, state in sorted(changes):
        if state in NOT_WORKING:
            away_since = away_since or at
        elif away_since is not None:
            intervals.append((away_since, at))
            away_since = None
    week = now - timedelta(days=7)
    found = [
        (a.astimezone(tz), b.astimezone(tz))
        for a, b in intervals
        if a >= week and timedelta(hours=1) <= b - a <= timedelta(hours=18)
    ]
    if len(found) < 5:
        return None

    def from_noon(moment: datetime) -> int:  # nights become one stretch of minutes
        return (moment.hour * 60 + moment.minute - 720) % 1440

    starts = sorted(from_noon(a) for a, _ in found)
    ends = sorted(from_noon(b) for _, b in found)
    start, end = starts[len(starts) // 2], ends[len(ends) // 2]
    near = [
        (a, b)
        for a, b in found
        if abs(from_noon(a) - start) <= 60 and abs(from_noon(b) - end) <= 60
    ]
    if len({a.date() for a, _ in near}) < 5:
        return None
    if sun_down is not None and all(
        sun_down(a + timedelta(minutes=15))
        and not sun_down(b + timedelta(minutes=15))
        and not sun_down(a - timedelta(minutes=90))
        for a, b in near
    ):
        return {"how": "dark"}

    def clock(minutes: int) -> str:
        minutes = (minutes + 720) % 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return {"how": "hours", "start": clock(start // 30 * 30), "end": clock(-(-end // 30) * 30)}
