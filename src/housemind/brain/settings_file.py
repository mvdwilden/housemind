"""Saving the house's settings as a file, and reading them back in (E-296, E-274, 20.18).

One readable file, ``housemind-instellingen-JJJJ-MM-DD.json``: the house settings (never a key
or a secret, also not nested), the house rules, people's own moments, the roles of rooms an
administrator chose, what is left alone or protected, limits and steps set by hand, and the
thresholds. No conversations, no logbook, no people, and no consents: a yes is given in this
house, so HouseMind asks again. Reading it back shows first what will change; devices and rooms
this house does not have are skipped. A file of a newer version or another format is refused,
with the reason. Every setting goes through the same checks as on the screen, and a file never
loosens safety: the protected list only grows, and no room loses a role that protects (QA 2,
M-2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from housemind import __version__
from housemind.brain import house_settings
from housemind.energy.model import EnergySettings
from housemind.knowledge.area_roles import ROLE_ORDER

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

FORMAT = "housemind-settings"
VERSION = 1
SECRET_WORDS = ("secret", "token", "password", "key")
# The house settings that travel with the file: what people chose, nothing learned or secret,
# and no consent (learning, forwarding questions: QA 2, M-2).
SETTING_KEYS = house_settings.FILE_KEYS  # the one table (code QA 3, punt 1)
# A role that protects: a garage keeps a roller door shut, a room inside gets the whole smoke
# reaction. A file never takes such a role away (QA 2, M-2).
PROTECTIVE_ROLES = frozenset({"garage"})
RULE_COLUMNS = (
    "scope_type",
    "scope_id",
    "effect",
    "action",
    "value",
    "time_from",
    "time_to",
    "conditions",
    "source_text",
)


def _clean(value: Any) -> Any:
    """Never a key or a secret, also not deeper inside a setting."""
    if isinstance(value, dict):
        return {
            k: _clean(v)
            for k, v in value.items()
            if not any(word in str(k).lower() for word in SECRET_WORDS)
        }
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def name(hm: HouseMind) -> str:
    return f"housemind-instellingen-{hm.clock.now().date().isoformat()}.json"


async def make(hm: HouseMind) -> dict[str, Any]:
    """The settings file of this house."""
    settings = {}
    for key in SETTING_KEYS:
        value = await hm.db.get_setting(key)
        if value is not None:
            settings[key] = _clean(value)
    rules = [
        {c: row[c] for c in RULE_COLUMNS}
        for row in await hm.db.fetchall("SELECT * FROM house_rules WHERE enabled = 1 ORDER BY id")
    ]
    scenes = [
        {"area_id": r["area_id"], "key": r["key"], "name": r["name"], "states": r["states"]}
        for r in await hm.db.fetchall("SELECT * FROM scenes WHERE source = 'person' ORDER BY id")
    ]
    roles = {
        str(r["area_id"]): str(r["role"])
        for r in await hm.db.fetchall("SELECT * FROM areas WHERE role_source = 'person'")
        if r["role"]
    }
    return {
        "format": FORMAT,
        "version": VERSION,
        "housemind": __version__,
        "made": hm.clock.now().isoformat(),
        "settings": settings,
        "house_rules": rules,
        "scenes": scenes,
        "area_roles": roles,
    }


def _known(hm: HouseMind, key: str) -> bool:
    """A device, a room or a part key this house has."""
    if key in hm.model.entities:
        return True
    if key.startswith("area:"):
        return key[5:] in hm.model.areas
    if "@" in key:
        return key.partition("@")[2] in hm.model.areas
    return "." not in key  # a part or a domain


def _fit(hm: HouseMind, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What of the file fits this house, and what is skipped (by name)."""
    skipped: list[str] = []
    settings: dict[str, Any] = {}
    for key, value in (data.get("settings") or {}).items():
        if key not in SETTING_KEYS:
            continue
        if key in ("excluded", "protected") and isinstance(value, list):
            skipped += [str(e) for e in value if str(e) not in hm.model.entities]
            value = [e for e in value if str(e) in hm.model.entities]
        elif key == "pet_areas" and isinstance(value, list):
            skipped += [str(a) for a in value if str(a) not in hm.model.areas]
            value = [a for a in value if str(a) in hm.model.areas]
        elif key in ("admin_max_step", "admin_step") and isinstance(value, dict):
            skipped += [str(k) for k in value if not _known(hm, str(k))]
            value = {k: v for k, v in value.items() if _known(hm, str(k))}
        elif key in ("tidy_choices", "tidy_marks") and isinstance(value, dict):
            # A choice is 'kind:target', 'Bewust uit' just the target: an entity, a device or
            # an integration (B-244).
            aim = {
                str(k): str(k).partition(":")[2] if key == "tidy_choices" else str(k) for k in value
            }
            here = {t for t in aim.values() if t in hm.model.devices or _known(hm, t)}
            skipped += [t for t in aim.values() if t not in here]
            value = {k: v for k, v in value.items() if aim[str(k)] in here}
        settings[key] = _clean(value)
    rules = []
    for rule in data.get("house_rules") or []:
        if not isinstance(rule, dict):
            continue
        scope, target = str(rule.get("scope_type")), str(rule.get("scope_id"))
        if (scope == "entity" and target not in hm.model.entities) or (
            scope == "area" and target not in hm.model.areas
        ):
            skipped.append(target)
            continue
        rules.append({c: rule.get(c) for c in RULE_COLUMNS})
    scenes = []
    for scene in data.get("scenes") or []:
        if isinstance(scene, dict) and str(scene.get("area_id")) in hm.model.areas:
            scenes.append(scene)
        elif isinstance(scene, dict):
            skipped.append(str(scene.get("area_id")))
    roles = {}
    for area_id, role in (data.get("area_roles") or {}).items():
        now = hm.area_roles.get(area_id)
        if (
            area_id in hm.model.areas
            and role in ROLE_ORDER
            and not (now in PROTECTIVE_ROLES and role != now)
            and not (role == "outside" and now != "outside")
        ):
            roles[area_id] = str(role)
        else:
            skipped.append(area_id)
    fitting = {"settings": settings, "house_rules": rules, "scenes": scenes, "area_roles": roles}
    return fitting, sorted(set(skipped))


def _refused(data: Any) -> str | None:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return "format"
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        return "newer"
    return None


async def preview(hm: HouseMind, data: Any) -> dict[str, Any]:
    """'Dit verandert': per group what reading the file would replace, and what is skipped."""
    reason = _refused(data)
    if reason is not None:
        return {"ok": False, "reason": reason}
    fitting, skipped = _fit(hm, data)
    changed = [
        key
        for key, value in fitting["settings"].items()
        if _clean(await hm.db.get_setting(key)) != value
    ]
    return {
        "ok": True,
        "made": data.get("made"),
        "changes": {
            "settings": changed,
            "house_rules": len(fitting["house_rules"]),
            "scenes": len(fitting["scenes"]),
            "area_roles": len(fitting["area_roles"]),
        },
        "skipped": skipped,
    }


async def apply(hm: HouseMind, data: Any) -> dict[str, Any]:
    """Replace those parts with the file's; the rest stays (after the administrator's yes)."""
    shown = await preview(hm, data)
    if not shown["ok"]:
        return shown
    fitting, _ = _fit(hm, data)
    now = hm.clock.now().isoformat()
    for key, value in fitting["settings"].items():
        await _set(hm, key, value)
    await hm.db.execute("UPDATE house_rules SET enabled = 0 WHERE enabled = 1")
    for rule in fitting["house_rules"]:
        await hm.db.execute(
            f"INSERT INTO house_rules(created, created_by, {', '.join(RULE_COLUMNS)}) "
            f"VALUES(?, NULL, {', '.join('?' for _ in RULE_COLUMNS)})",
            (now, *[_text(rule.get(c)) for c in RULE_COLUMNS]),
        )
    await hm.db.execute("DELETE FROM scenes WHERE source = 'person'")
    for scene in fitting["scenes"]:
        await hm.db.execute(
            "INSERT OR REPLACE INTO scenes(area_id, key, name, source, states, created_at) "
            "VALUES(?,?,?, 'person', ?, ?)",
            (
                scene["area_id"],
                str(scene.get("key")),
                scene.get("name"),
                _text(scene.get("states")) or "{}",
                now,
            ),
        )
    for area_id, role in fitting["area_roles"].items():
        await hm.db.execute(
            "UPDATE areas SET role = ?, role_source = 'person' WHERE area_id = ?", (role, area_id)
        )
    await hm.reload_settings()
    return shown


async def _set(hm: HouseMind, key: str, value: Any) -> bool:
    """One setting from the file, through the same checks as on the screen (QA 2, M-2); a bad
    value is left out and the house keeps its own."""
    if key == "protected":
        if not isinstance(value, list):
            return False
        current = {str(e) for e in await hm.setting("protected") or []}
        added = {str(e) for e in value if str(e) in hm.model.entities}
        await hm.db.set_setting(key, sorted(current | added))  # the list only grows
        return True
    if key == "admin_step":
        ok = isinstance(value, dict)
        for k, v in (value if isinstance(value, dict) else {}).items():
            step = v.get("step") if isinstance(v, dict) else v
            ok = (
                await hm.set_admin_step(str(k), step if isinstance(step, str) else None, None)
                and ok
            )
        return ok
    if key == "thresholds":
        ok = isinstance(value, dict)
        for part, values in (value if isinstance(value, dict) else {}).items():
            ok = (
                await hm.set_thresholds(str(part), values if isinstance(values, dict) else None)
                and ok
            )
        return ok
    if key == "energy":
        if hm.energy is None:
            return False
        await hm.energy.save_settings(EnergySettings.within(value, hm.energy.settings))
        return True
    if key in ("tidy_choices", "tidy_marks"):
        return await hm.tidy_read_in(key, value)
    return await hm.set_house_setting(key, value)


def _text(value: Any) -> Any:
    """Columns that hold JSON as text get text again."""
    if isinstance(value, dict | list):
        return json.dumps(value)
    return value
