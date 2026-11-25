"""Converting Home Assistant JSON into HouseMind types. Pure functions, easy to test."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from housemind.core.model import (
    Area,
    Category,
    Context,
    Device,
    Entity,
    Floor,
    HouseModel,
    Label,
    Source,
    State,
)
from housemind.ha.adapter import (
    HAAutomation,
    HAConfigEntry,
    HALightRhythm,
    HAScript,
    HAStep,
    HATrigger,
    HistoryChange,
)

# Steps inside an automation's actions that change no device (waiting, checking, variables).
_NO_EFFECT = frozenset(
    {"delay", "wait_template", "wait_for_trigger", "condition", "stop", "variables"}
)
_TARGET_KEYS = ("entity_id", "area_id", "device_id", "floor_id", "label_id")
_DIM = ("brightness_increase", "brightness_decrease")  # a lamp's device actions (E-448)


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_context(raw: dict[str, Any] | None) -> Context | None:
    if not raw or not raw.get("id"):
        return None
    return Context(id=str(raw["id"]), user_id=raw.get("user_id"), parent_id=raw.get("parent_id"))


def parse_state(raw: dict[str, Any] | None) -> State | None:
    if not raw:
        return None
    return State(
        entity_id=str(raw["entity_id"]),
        state=str(raw.get("state", "unknown")),
        attributes=dict(raw.get("attributes") or {}),
        last_changed=parse_time(raw.get("last_changed")),
        context=parse_context(raw.get("context")),
    )


def build_model(
    floors: list[dict[str, Any]],
    areas: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    states: dict[str, State],
    labels: list[dict[str, Any]] | None = None,
    categories: dict[str, list[dict[str, Any]]] | None = None,
) -> HouseModel:
    """Combine the registries with state attributes into one house model.

    Device class, features, unit and colour modes come from the state attributes, which HA
    always fills in for active entities; the registry adds ids, areas and categories.
    """
    model = HouseModel()
    for raw in labels or []:  # labels and categories: hints, never leading (E-345)
        if raw.get("label_id"):
            model.labels[str(raw["label_id"])] = Label(
                str(raw["label_id"]), str(raw.get("name") or raw["label_id"]), raw.get("icon")
            )
    for scope, found in (categories or {}).items():
        for raw in found or []:
            if raw.get("category_id"):
                model.categories[str(raw["category_id"])] = Category(
                    str(raw["category_id"]), scope, str(raw.get("name") or ""), raw.get("icon")
                )
    for f in floors:
        model.floors[f["floor_id"]] = Floor(
            f["floor_id"], f.get("name", f["floor_id"]), f.get("level")
        )
    for a in areas:
        model.areas[a["area_id"]] = Area(
            a["area_id"],
            a.get("name", a["area_id"]),
            a.get("floor_id"),
            tuple(str(x) for x in a.get("aliases") or ()),
            tuple(str(x) for x in a.get("labels") or ()),
            a.get("icon") or None,
        )
    for d in devices:
        name = d.get("name_by_user") or d.get("name") or d["id"]
        model.devices[d["id"]] = Device(
            d["id"],
            name,
            d.get("area_id"),
            d.get("integration"),
            d.get("manufacturer"),
            d.get("model"),
            tuple(
                f"{pair[0]}:{pair[1]}"
                for pair in d.get("identifiers") or ()
                if isinstance(pair, list | tuple) and len(pair) == 2
            ),
            tuple(str(x) for x in d.get("labels") or ()),
        )
    registry = {e["entity_id"]: e for e in entities}
    for entity_id in sorted(set(registry) | set(states)):
        reg = registry.get(entity_id, {})
        state = states.get(entity_id)
        attrs = state.attributes if state else {}
        capabilities: dict[str, Any] = dict(reg.get("capabilities") or {})
        if "supported_color_modes" in attrs:
            capabilities["supported_color_modes"] = list(attrs["supported_color_modes"])
        name = (
            reg.get("name") or reg.get("original_name") or attrs.get("friendly_name") or entity_id
        )
        model.entities[entity_id] = Entity(
            entity_id=entity_id,
            registry_id=str(reg.get("id") or entity_id),
            name=str(name),
            device_id=reg.get("device_id"),
            area_id=reg.get("area_id"),
            device_class=attrs.get("device_class")
            or reg.get("device_class")
            or reg.get("original_device_class"),
            supported_features=int(
                attrs.get("supported_features") or reg.get("supported_features") or 0
            ),
            unit=attrs.get("unit_of_measurement") or reg.get("unit_of_measurement"),
            entity_category=reg.get("entity_category"),
            disabled=reg.get("disabled_by") is not None,
            hidden=reg.get("hidden_by") is not None,
            platform=reg.get("platform"),
            capabilities=capabilities,
            unique_id=str(reg["unique_id"]) if reg.get("unique_id") is not None else None,
            translation_key=reg.get("translation_key"),
            labels=tuple(str(x) for x in reg.get("labels") or ()),
            categories={
                str(scope): str(category)
                for scope, category in (reg.get("categories") or {}).items()
                if category
            },
            config_entry_id=reg.get("config_entry_id"),
            original_name=reg.get("original_name"),
            named_by_person=bool(reg.get("name")),
            icon=reg.get("icon") or None,  # only what a person chose; never original_icon
        )
    return model


_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_REF_KEYS = {"entity_id": "entity", "entities": "entity", "device_id": "device", "area_id": "area"}


def refs_of(config: Any) -> tuple[str, ...]:
    """Every explicit reference in a configuration: ``entity_id``, ``device_id`` and
    ``area_id`` in triggers, conditions, actions, targets and a blueprint's input (B-236).
    A template is left out: what it points to is not certain. A scene it makes itself with
    ``scene.create`` is ``creates:scene.…``: it exists only after the first run."""
    found: list[str] = []

    def add(kind: str, value: Any) -> None:
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str):
                continue
            for part in (p.strip() for p in item.split(",")):
                if not part or "{" in part or part in ("all", "none"):
                    continue
                if kind == "entity" and not _ENTITY_ID.match(part):
                    continue
                found.append(f"{kind}:{part}")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # A scene the configuration makes itself (scene.create) exists once it ran.
            if node.get("action", node.get("service")) == "scene.create":
                scene_id = _mapping(node.get("data")).get("scene_id")
                if isinstance(scene_id, str) and scene_id and "{" not in scene_id:
                    found.append(f"creates:scene.{scene_id}")
            for key, value in node.items():
                kind = _REF_KEYS.get(str(key))
                if kind is not None and not isinstance(value, dict):
                    add(kind, value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    return tuple(dict.fromkeys(found))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def _ids(value: Any) -> tuple[str, ...]:
    """Entity, area or device ids: one string, a comma separated string or a list."""
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str):
            out += [part.strip() for part in item.split(",") if part.strip()]
    return tuple(out)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None  # an entity id as the limit: not a fixed number


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _single(value: Any) -> str | None:
    items = [v for v in _as_list(value) if isinstance(v, str)]
    return items[0] if len(items) == 1 else None


def _duration(value: Any) -> float | None:
    """Home Assistant's "for" in seconds: {hours, minutes, seconds}, "HH:MM[:SS]" or a number
    of seconds; a template says nothing (E-444)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        units = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1, "milliseconds": 0.001}
        total = 0.0
        for unit, factor in units.items():
            if unit in value:
                number = _number(value[unit])
                if number is None:
                    return None  # a template in one of them
                total += number * factor
        return total
    if isinstance(value, str):
        pieces = value.strip().split(":")
        try:
            numbers = [float(p) for p in pieces]
        except ValueError:
            return None
        if len(numbers) == 1:
            return numbers[0]
        if len(numbers) in (2, 3):
            hours, minutes, seconds = [*numbers, 0.0][:3]  # HH:MM or HH:MM:SS
            return hours * 3600 + minutes * 60 + seconds
    return None


def parse_trigger(raw: Any) -> HATrigger | None:
    if not isinstance(raw, dict) or raw.get("enabled") is False:
        return None
    platform = raw.get("trigger") or raw.get("platform")
    if not isinstance(platform, str):
        return None
    if platform in ("sun", "zone"):
        to = _single(raw.get("event"))
    elif platform == "device":
        to = _single(raw.get("type"))
    elif platform == "event":
        to = _single(raw.get("event_type"))  # which event: a button, an action in the app (E-448)
    else:
        to = _single(raw.get("to"))
    attribute = raw.get("attribute")
    subtype = raw.get("subtype") if platform == "device" else None
    # A trigger like "occupancy.cleared" has its wait under options (E-461).
    waits = raw.get("for", _mapping(raw.get("options")).get("for"))
    return HATrigger(
        platform=platform,
        # A trigger like "switch.turned_on" names its entities under target (E-448).
        entity_ids=_ids(raw.get("entity_id")) + _ids(_mapping(raw.get("target")).get("entity_id")),
        to=to,
        subtype=str(subtype) if isinstance(subtype, str | int) else None,
        attribute=attribute if isinstance(attribute, str) else None,
        below=_number(raw.get("below")),
        above=_number(raw.get("above")),
        at=_single(raw.get("at")),
        for_s=_duration(waits),
    )


def parse_conditions(raw: Any) -> list[HATrigger]:
    """What must be true for an automation, in the terms of a trigger, also inside and/or/not
    – only to say in plain words what it does (E-204, B-346); a template stays a template."""
    found: list[HATrigger] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            found.append(HATrigger(platform="template"))  # a template in short form
            continue
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        kind = item.get("condition")
        if kind in ("and", "or", "not"):
            found += parse_conditions(item.get("conditions"))
            continue
        if not isinstance(kind, str):
            continue
        attribute = item.get("attribute")
        to = item.get("state") if kind == "state" else item.get("type")
        if kind == "sun":
            to = item.get("after") or item.get("before")
        found.append(
            HATrigger(
                platform=kind,
                entity_ids=_ids(item.get("entity_id")),
                to=_single(to),
                attribute=attribute if isinstance(attribute, str) else None,
                below=_number(item.get("below")),
                above=_number(item.get("above")),
                at=_single(item.get("after") or item.get("before")) if kind == "time" else None,
            )
        )
    return found


def _walk(
    actions: Any, steps: list[HAStep], other: list[int], starts: list[str] | None = None
) -> None:
    """Collect the device actions of an automation, also inside choose, if and repeat –
    and the scenes and scripts it starts (E-258)."""
    for raw in _as_list(actions):
        if isinstance(raw, list):
            _walk(raw, steps, other, starts)
            continue
        if not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        nested = False
        for option in _as_list(raw.get("choose")):
            if isinstance(option, dict):
                _walk(option.get("sequence"), steps, other, starts)
                nested = True
        for key in ("then", "else", "default", "sequence", "parallel"):
            if key in raw:
                _walk(raw[key], steps, other, starts)
                nested = True
        repeat = raw.get("repeat")
        if isinstance(repeat, dict):
            _walk(repeat.get("sequence"), steps, other, starts)
            nested = True
        if starts is not None and isinstance(raw.get("scene"), str):
            starts.append(raw["scene"])  # the short form: {"scene": "scene.evening"}
        action = raw.get("action") or raw.get("service")
        if starts is not None and isinstance(action, str):
            starts.extend(_started(action, raw))
        if isinstance(action, str) and "." in action:
            target = _mapping(raw.get("target"))
            data = _mapping(raw.get("data"))
            ids = {
                key: _ids(target.get(key)) + _ids(data.get(key)) + _ids(raw.get(key))
                for key in _TARGET_KEYS
            }
            if ids["floor_id"] or ids["label_id"]:
                other[0] += 1  # a target HouseMind cannot resolve to devices
            steps.append(
                HAStep(
                    action=action,
                    entity_ids=ids["entity_id"],
                    area_ids=ids["area_id"],
                    device_ids=ids["device_id"],
                    data={k: v for k, v in data.items() if k not in _TARGET_KEYS},
                )
            )
        elif raw.get("device_id") and isinstance(raw.get("domain"), str) and raw.get("type"):
            # A device action: {"type": "turn_on", "domain": "light", "device_id": …}; also
            # toggling, and a remote dimming a lamp (E-448).
            dims = raw["domain"] == "light" and raw["type"] in _DIM
            if raw["type"] in ("turn_on", "turn_off", "toggle") or dims:
                steps.append(
                    HAStep(
                        action=f"{raw['domain']}.{raw['type']}",
                        entity_ids=_ids(raw.get("entity_id")),
                        device_ids=_ids(raw.get("device_id")),
                    )
                )
            else:
                other[0] += 1
        elif not nested and not (_NO_EFFECT & set(raw)) and "if" not in raw:
            other[0] += 1  # scenes, events and anything else HouseMind does not do


def _branches(raw: dict[str, Any]) -> list[Any]:
    """The sequences inside one action: choose, if, repeat and the like."""
    found = [o.get("sequence") for o in _as_list(raw.get("choose")) if isinstance(o, dict)]
    found += [raw[key] for key in ("then", "else", "default", "sequence", "parallel") if key in raw]
    repeat = raw.get("repeat")
    if isinstance(repeat, dict):
        found.append(repeat.get("sequence"))
    return found


def _lamps_end(actions: Any) -> bool | None:
    """What a sequence leaves its lamps as (E-461): True when the last it does with lamps is
    put them out or bring a scene back, False when it leaves them on, None without lamps. With
    a choice every way through has to end so."""
    for raw in reversed(_as_list(actions)):
        if isinstance(raw, list):
            found = _lamps_end(raw)
        elif not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        else:
            found = _action_ends(raw)
        if found is not None:
            return found
    return None


def _action_ends(raw: dict[str, Any]) -> bool | None:
    inside = [end for end in map(_lamps_end, _branches(raw)) if end is not None]
    if inside:
        return all(inside)
    if isinstance(raw.get("scene"), str):
        return True
    action = raw.get("action") or raw.get("service")
    if isinstance(action, str):
        domain, _, service = action.partition(".")
    else:
        domain, service = str(raw.get("domain") or ""), str(raw.get("type") or "")
    if domain == "scene":
        return True if service == "turn_on" else None  # scene.create only remembers
    if domain in ("light", "switch") and service in ("turn_on", "turn_off", "toggle"):
        return service == "turn_off"
    return None


def _longest_wait(actions: Any) -> float:
    """The longest wait in a sequence, in seconds; a wait with a template counts as a minute."""
    longest = 0.0
    for raw in _as_list(actions):
        if isinstance(raw, list):
            longest = max(longest, _longest_wait(raw))
        elif isinstance(raw, dict) and raw.get("enabled") is not False:
            if "delay" in raw:
                seconds = _duration(raw["delay"])
                longest = max(longest, 60.0 if seconds is None else seconds)
            for inside in _branches(raw):
                longest = max(longest, _longest_wait(inside))
    return longest


def _started(action: str, raw: dict[str, Any]) -> list[str]:
    """The scenes and scripts an action starts: scene.turn_on, script.turn_on or a script
    called by its own name."""
    domain, _, service = action.partition(".")
    if domain not in ("scene", "script"):
        return []
    if service in ("turn_on", "toggle", "apply", "create"):
        target = _mapping(raw.get("target"))
        data = _mapping(raw.get("data"))
        found = _ids(target.get("entity_id")) + _ids(data.get("entity_id"))
        return [e for e in found + _ids(raw.get("entity_id")) if e.startswith(f"{domain}.")]
    return [action] if domain == "script" and service not in ("turn_off", "reload") else []


def parse_automation(state: State, config: dict[str, Any] | None) -> HAAutomation:
    """An automation from its state and its configuration (``automation/config``)."""
    attrs = state.attributes
    name = str(attrs.get("friendly_name") or state.entity_id)
    config_id = attrs.get("id") or (config or {}).get("id")
    if not config:
        return HAAutomation(
            state.entity_id,
            name,
            state.state == "on",
            str(config_id) if config_id else None,
            readable=False,
        )
    raw_triggers = _as_list(config.get("triggers", config.get("trigger")))
    triggers = tuple(t for t in (parse_trigger(raw) for raw in raw_triggers) if t is not None)
    steps: list[HAStep] = []
    other = [0]
    starts: list[str] = []
    actions = config.get("actions", config.get("action"))
    _walk(actions, steps, other, starts)
    return HAAutomation(
        entity_id=state.entity_id,
        name=name,
        enabled=state.state == "on",
        config_id=str(config_id) if config_id else None,
        triggers=triggers,
        steps=tuple(steps),
        other=other[0],
        starts=tuple(dict.fromkeys(starts)),
        refs=refs_of(config),
        conditions=tuple(parse_conditions(config.get("conditions", config.get("condition")))),
        ends=_lamps_end(actions) is True,
        wait_s=_longest_wait(actions),
    )


def parse_script(state: State, config: dict[str, Any] | None) -> HAScript:
    """A script from its state and its configuration (``script/config``), to show where it
    acts – never to run, change or delete it (E-03)."""
    name = str(state.attributes.get("friendly_name") or state.entity_id)
    if not config:
        return HAScript(state.entity_id, name, readable=False)
    steps: list[HAStep] = []
    starts: list[str] = []
    _walk(config.get("sequence"), steps, [0], starts)
    return HAScript(
        state.entity_id, name, tuple(steps), tuple(dict.fromkeys(starts)), refs=refs_of(config)
    )


def form_values(fields: Any) -> dict[str, Any]:
    """The values in a form of Home Assistant by field name, also those in a folded section
    (found in the real house: half a group's settings are under "advanced")."""
    values: dict[str, Any] = {}
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict):
            continue
        if isinstance(field.get("schema"), list):
            values.update(form_values(field["schema"]))
        elif "name" in field:
            shown = (field.get("description") or {}).get("suggested_value")
            values[str(field["name"])] = field.get("default", shown)
    return values


def parse_light_rhythm(entry_id: str, title: str, options: dict[str, Any]) -> HALightRhythm:
    """One group's settings (E-465): the fields of its options, in that integration's words."""
    plain = HALightRhythm(entry_id, title)

    def number(key: str, default: float) -> float:
        found = _number(options.get(key))
        return default if found is None else found

    def clock(key: str) -> str | None:
        value = options.get(key)
        return None if value in (None, "", "None", "none") else str(value)

    return HALightRhythm(
        entry_id,
        title,
        _ids(options.get("lights")),
        number("min_brightness", plain.min_brightness),
        number("max_brightness", plain.max_brightness),
        number("min_color_temp", plain.min_kelvin),
        number("max_color_temp", plain.max_kelvin),
        number("sleep_brightness", plain.sleep_brightness),
        number("sleep_color_temp", plain.sleep_kelvin),
        number("interval", plain.interval_s),
        number("transition", plain.transition_s),
        number("initial_transition", plain.initial_transition_s),
        options.get("only_once") is True,
        clock("sunrise_time"),
        clock("min_sunrise_time"),
        clock("max_sunrise_time"),
        number("sunrise_offset", plain.sunrise_offset_s),
        clock("sunset_time"),
        clock("min_sunset_time"),
        clock("max_sunset_time"),
        number("sunset_offset", plain.sunset_offset_s),
        str(options.get("brightness_mode") or plain.brightness_mode),
        number("brightness_mode_time_dark", plain.time_dark_s),
        number("brightness_mode_time_light", plain.time_light_s),
    )


def parse_config_entry(raw: dict[str, Any]) -> HAConfigEntry | None:
    """An integration from ``config_entries/get`` (B-236); one HA ignores is left out."""
    if not raw.get("entry_id") or raw.get("source") == "ignore":
        return None
    return HAConfigEntry(
        str(raw["entry_id"]),
        str(raw.get("domain") or ""),
        str(raw.get("title") or raw.get("domain") or ""),
        str(raw.get("state") or "loaded"),
        raw.get("disabled_by") is not None,
    )


def _when(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC)
    if isinstance(value, str):
        try:
            return parse_time(value)
        except ValueError:
            return None
    return None


def _cause_of(entry: dict[str, Any], own_user_id: str | None) -> tuple[Source, str | None]:
    """Who did a change, from its logbook entry – as live (E-115): HouseMind's own user,
    a person, an automation or script, else a switch or the device itself."""
    user = entry.get("context_user_id")
    if user and user == own_user_id:
        return Source.HOUSEMIND, None
    if user:
        return Source.PERSON, None
    kind = entry.get("context_event_type")
    domain = entry.get("context_domain")
    if kind in ("automation_triggered", "script_started") or domain in ("automation", "script"):
        cause = entry.get("context_entity_id")
        return Source.AUTOMATION, cause if isinstance(cause, str) else None
    return Source.PHYSICAL, None


def parse_history(raw: Any, logbook: Any, own_user_id: str | None) -> list[HistoryChange]:
    """Changes from ``history/history_during_period`` (the compressed form: ``s``, ``a``,
    ``lu``, ``lc``) with who did each from ``logbook/get_events``, oldest first (E-257)."""
    book: dict[tuple[str, str], list[tuple[datetime, Source, str | None]]] = {}
    for entry in logbook if isinstance(logbook, list) else []:
        if not isinstance(entry, dict):
            continue
        entity_id, state, when = (
            entry.get("entity_id"),
            entry.get("state"),
            _when(entry.get("when")),
        )
        if not isinstance(entity_id, str) or not isinstance(state, str) or when is None:
            continue
        source, cause = _cause_of(entry, own_user_id)
        book.setdefault((entity_id, state), []).append((when, source, cause))
    out: list[HistoryChange] = []
    for entity_id, rows in raw.items() if isinstance(raw, dict) else []:
        attributes: dict[str, Any] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("s"), str):
                continue
            at = _when(row.get("lc", row.get("lu")))
            if at is None:
                continue
            if isinstance(row.get("a"), dict):
                attributes = dict(row["a"])
            source, cause = Source.UNKNOWN, None
            for when, who, why in book.get((entity_id, row["s"]), []):
                if abs((when - at).total_seconds()) <= 2:
                    source, cause = who, why
                    break
            out.append(HistoryChange(entity_id, row["s"], dict(attributes), at, source, cause))
    out.sort(key=lambda change: change.at)
    return out
