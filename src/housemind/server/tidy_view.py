"""What the group 'Home Assistant netjes' shows (B-245, deel A): the last round, in plain names,
each with the place in Home Assistant where it can be opened. Only for administrators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from housemind.brain import tidy_system
from housemind.brain.icons import DOMAIN_ICONS
from housemind.brain.tidy import MARKABLE, Finding, shown_name

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

LISTED = 30  # follow-ups named at most; the rest is a number
UNUSED = frozenset({"unused_on", "unused_off"})  # their own part of the page (B-240)


def tidy_view(hm: HouseMind) -> dict[str, Any]:
    """The last round (B-245): when it ran, how many things to look at, and what is broken;
    what HouseMind switched off (B-243) and what the administrator decided (B-239, B-244)."""
    names = {**{e: _name(hm, e) for e in hm.model.entities}, **_device_names(hm)}
    broken = [f for f in hm.tidy_findings if f.kind not in UNUSED]
    unused = [f for f in hm.tidy_findings if f.kind in UNUSED]
    system = _system(hm)
    update = _update(hm)
    return {
        "checkedAt": hm.tidy_checked.isoformat() if hm.tidy_checked else None,
        "startsAt": hm._tidy_count_from.isoformat() if hm._tidy_count_from else None,
        "count": len(hm.tidy_findings)
        + sum(1 for t in ("backup", "database", "disk") if system and _looks(system[t]))
        + (1 if system and system["keepAll"] else 0)
        + (1 if system and system["loose"] else 0)
        + (1 if update and update["lost"] else 0),
        "broken": [_finding(hm, f) for f in broken],
        "unused": [_finding(hm, f) for f in unused],
        "system": system,
        "update": update,
        "off": [
            {
                "target": target,
                "name": names.get(target, target),
                "kind": entry.get("kind"),
                "reason": entry.get("reason"),
                "at": entry.get("at"),
            }
            for target, entry in sorted(hm._tidy_off.items(), key=lambda kv: str(kv[1].get("at")))
            if entry.get("status") == "off"
        ],
        "decided": [
            {
                "key": key,
                "choice": choice.get("choice"),
                "name": names.get(key.partition(":")[2], key.partition(":")[2]),
                "at": choice.get("at"),
                **({"count": len(choice.get("ids") or [])} if "ids" in choice else {}),
            }
            for key, choice in sorted(hm._tidy_choices.items())
        ],
        "marks": [
            {
                "target": mark.target,
                "name": names.get(mark.target, _source_name(hm, mark.target)),
                "how": mark.how,
                "start": mark.start,
                "end": mark.end,
                "until": mark.until,
                "reviewAt": mark.review_at,
                "at": mark.at,
            }
            for mark in sorted(hm._tidy_marks.values(), key=lambda m: m.at)
        ],
    }


def _name(hm: HouseMind, entity_id: str) -> str:
    """Every name on the page as Home Assistant shows it."""
    return shown_name(entity_id, hm.model, hm.world.states if hm.world is not None else {})


def _looks(tile: dict[str, Any]) -> bool:
    """A tile that asks to be looked at (B-241)."""
    return str(tile.get("state")) in ("old", "none", "grows", "filling", "full")


def _system(hm: HouseMind) -> dict[str, Any] | None:
    """The three tiles (B-241): the backup, the database with what changes most, the disk."""
    tiles = hm.tidy_system
    if tiles is None:
        return None
    database = dict(tiles["database"])
    database["busiest"] = [
        {**_entity(hm, str(b["entity"])), "every": b["every"]}
        for b in database.get("busiest") or []
    ]
    return {
        "at": tiles["at"],
        "backup": tiles["backup"],
        "database": database,
        "disk": tiles["disk"],
        "keepAll": bool(tiles.get("keepAll")),  # Home Assistant keeps every backup (E-397)
        "loose": tiles.get("loose") or [],  # old loose backups, the oldest first (E-397)
    }


def _update(hm: HouseMind) -> dict[str, Any] | None:
    """The last update (B-242): of what, from which version, the looks so far, and what does
    not work since – one line per integration, with its entities below."""
    record = hm.tidy_update
    if record is None:
        return None
    lost_entries = set(record.get("lost_entries") or [])
    groups: dict[str, dict[str, Any]] = {}
    for entry_id in sorted(lost_entries):
        title, domain = hm.tidy_entry_names.get(entry_id, (entry_id, ""))
        groups[entry_id] = _group(entry_id, title, domain, down=True)
    for entity_id in record.get("lost") or []:
        entity = hm.model.entities.get(entity_id)
        source = (entity.config_entry_id if entity else None) or (
            f"platform:{(entity.platform or entity.domain) if entity else entity_id.split('.')[0]}"
        )
        if source not in groups:
            title, domain = hm.tidy_entry_names.get(source, (source.removeprefix("platform:"), ""))
            groups[source] = _group(source, title, domain or source.removeprefix("platform:"))
        groups[source]["count"] += 1
        if len(groups[source]["entities"]) < LISTED:
            groups[source]["entities"].append(_entity(hm, entity_id))
    for group in groups.values():  # one entity: its own dialog
        if not group["down"] and group["count"] == 1 and group["entities"]:
            group["open"] = _entity_page(group["entities"][0]["entityId"])
    following = tidy_system.next_check(record)
    return {
        "from": record.get("from") or {},
        "to": record.get("to") or {},
        "at": record.get("at"),
        "checks": record.get("checks") or [],
        "next": following.isoformat() if following else None,
        "lost": sorted(groups.values(), key=lambda g: (not g["down"], -g["count"], g["name"])),
        "back": [_entity(hm, e) for e in (record.get("back") or [])[:LISTED]],
        "backCount": len(record.get("back") or []),
        "known": bool(record.get("checks")) or bool(record.get("before")),
    }


def _group(source: str, title: str, domain: str, down: bool = False) -> dict[str, Any]:
    return {
        "source": source,
        "name": title,
        "down": down,
        "count": 0,
        "entities": [],
        "open": _integration(domain, source) if down else _entities_of(source, domain),
    }


def _device_names(hm: HouseMind) -> dict[str, str]:
    return {d: x.name for d, x in hm.model.devices.items()}


def _source_name(hm: HouseMind, target: str) -> str:
    """The name of an integration a mark is on: from a finding that has it, else its id."""
    for f in hm.tidy_findings:
        if f.detail.get("source") == target and f.detail.get("source_name"):
            return str(f.detail["source_name"])
    return target.removeprefix("platform:")


def _can(f: Finding) -> list[str]:
    """The buttons of a card besides 'Openen in Home Assistant' (B-243, B-239, B-244)."""
    if f.detail.get("review"):
        return ["review"]
    if f.kind in ("duplicate",):
        return ["off", "not_duplicate"]
    if f.kind == "not_provided":
        return ["off"]
    if f.kind == "not_provided_group":
        return ["off_all"]
    if f.kind == "missing_ref":
        return ["off", "leave"] if f.detail.get("kind") == "automation" else ["leave"]
    if f.kind in MARKABLE:
        return ["mark"]
    if f.kind == "unused_on":  # B-240: 'Uitzetten' or 'Laat aan'
        return ["off", "keep_on"]
    if f.kind == "unused_off":  # 'Verwijderen in Home Assistant' is the open button
        return ["keep"]
    return []


def _entity(hm: HouseMind, entity_id: str) -> dict[str, Any]:
    area = hm.model.area_of(entity_id)
    return {
        "entityId": entity_id,
        "name": _name(hm, entity_id),
        "room": area.name if area else None,
        "icon": _icon(hm, entity_id),  # E-512
    }


def _icon(hm: HouseMind, entity_id: str) -> str:
    """What a card is about, as an icon (E-512); what left Home Assistant by its domain."""
    entity = hm.model.entities.get(entity_id)
    if entity is None:
        return DOMAIN_ICONS.get(entity_id.split(".", 1)[0], "device")
    function = hm.world.functions.get(entity_id) if hm.world is not None else None
    device = hm.model.devices.get(entity.device_id) if entity.device_id else None
    return hm.icons.of(entity, function, device.name if device else None).icon


def _open(hm: HouseMind, finding: Finding) -> str:
    """The place in Home Assistant to open, already showing what the card is about (B-243):
    deleting is for the administrator there."""
    if finding.kind.startswith("energy_"):
        return "/config/energy"  # the energy settings: changed there, never by HouseMind
    if finding.kind == "integration_down":  # its page, with this entry picked out
        return _integration(str(finding.detail.get("domain") or ""), finding.target)
    if finding.kind in (
        "integration_silent",
        "not_provided_group",
        "unavailable_group",
        "meter_still_group",
    ):  # the entities, filtered on this integration
        return _entities_of(
            str(finding.detail.get("source") or ""), str(finding.detail.get("domain") or "")
        )
    if finding.kind == "device_down":
        return f"/config/devices/device/{finding.target}"
    if finding.kind in ("missing_ref", "unused_on", "unused_off"):
        kind, config_id = finding.detail.get("kind"), finding.detail.get("config_id")
        if config_id and kind in ("automation", "script", "scene"):
            return f"/config/{kind}/edit/{config_id}"
        return f"/config/{kind or 'automation'}/dashboard"
    # One entity: the list of entities with its settings open (or its history, for a meter).
    return _entity_page(finding.target, "history" if finding.kind == "meter_still" else "settings")


def _entity_page(entity_id: str, view: str = "settings") -> str:
    """Home Assistant opens an entity's dialog from the address (more-info-entity-id)."""
    return f"/config/entities?more-info-entity-id={quote(entity_id)}&more-info-view={view}"


def _entities_of(source: str, domain: str) -> str:
    """The entities of one integration: by its entry, or else by its kind (YAML, a template)."""
    if source and not source.startswith("platform:"):
        return f"/config/entities?config_entry={quote(source)}"
    return f"/config/entities?domain={quote(domain or source.removeprefix('platform:'))}"


def _integration(domain: str, entry_id: str) -> str:
    """An integration's page, with one entry picked out."""
    return f"/config/integrations/integration/{quote(domain)}#config_entry={quote(entry_id)}"


def _finding(hm: HouseMind, f: Finding) -> dict[str, Any]:
    depends = [_name(hm, a) for a in f.depends]
    out: dict[str, Any] = {
        "key": f.key,
        "kind": f.kind,
        "name": f.name,
        "target": f.target,
        "since": f.since.isoformat(),
        "heavy": f.heavy,
        "followsCount": len(f.follows),
        "follows": [_entity(hm, e) for e in f.follows[:LISTED]],
        "depends": depends[:LISTED],
        "dependsCount": len(depends),
        "used": f.used,
        "open": _open(hm, f),
        "can": _can(f),
        "markTarget": _mark_target(f),
        "rooms": sorted(
            {area.name for e in (f.target, *f.follows) if (area := hm.model.area_of(e))}
        ),
    }
    if f.detail.get("source"):  # the integration, for the filter (B-258)
        out["source"] = {"id": f.detail["source"], "name": f.detail.get("source_name")}
    if f.kind in ("unavailable", "not_provided", "duplicate", "meter_still"):
        out["entity"] = _entity(hm, f.target)
    if f.kind == "meter_still":  # B-265: what it measures, its value, what was expected
        out["meter"] = {k: f.detail.get(k) for k in ("role", "value", "unit", "expected_wh")}
    if f.kind == "energy_no_grid":  # B-267: what looks like a grid meter
        out["candidates"] = [_entity(hm, e) for e in f.detail.get("candidates") or []]
    if f.kind == "energy_issues":
        out["issues"] = [
            {"type": i.get("type"), **_entity(hm, i.get("entity") or "")}
            for i in f.detail.get("issues") or []
        ]
    if f.kind == "integration_down":
        out["state"] = f.detail.get("state")
    if f.missing:
        out["missing"] = [m.partition(":")[2] for m in f.missing]
    if f.suggest:
        out["suggest"] = _entity(hm, f.suggest)
    if f.detail.get("suggest_mark"):  # 'Meestal uit tussen … – bewust?' (B-239)
        out["suggestMark"] = f.detail["suggest_mark"]
    if f.detail.get("review"):  # 'Klopt dit nog?' after 90 days
        out["review"] = f.detail["review"]
    if f.kind in UNUSED:  # B-240: how long, never run, a season's automation
        out["days"] = f.detail.get("days")
        out["never"] = bool(f.detail.get("never"))
        out["season"] = bool(f.detail.get("season"))
    return out


def _mark_target(f: Finding) -> str | None:
    """What 'Bewust uit' goes on: the entity, the device or the integration of the card."""
    if f.kind not in MARKABLE:
        return None
    if f.kind in ("unavailable_group", "integration_down", "integration_silent"):
        return str(f.detail.get("source") or f.target)
    return f.target
