"""Vesta sets a step for an administrator, and asks about the automation that steers it (E-317).

"Zet licht bij binnenkomen in de keuken op Autonoom": she says what she understood and asks yes
or no. After a yes she sets it as the administrator's own step (20.10), and – when an automation
in Home Assistant steers it – asks whether to switch that off (or, after a step back, on again),
the same question as in the row of 'Klaar voor autonomie?'. Who may: only an administrator she
knows (the app, or Assist on their own phone); the callers check that, as for house rules.

A wish or a question travels as a plain dict (``payload``) so the app and Assist can keep it
while they wait for the answer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from housemind.brain.readiness import Readiness, steer_view, what
from housemind.brain.talk import normalise, part_named

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

STEPS = ("observe", "propose", "autonomous")


def _words(hm: HouseMind, lang: str, key: str) -> list[str]:
    raw = hm.languages.text(lang, key)
    return [] if raw == key else [w.strip().lower() for w in raw.split(",") if w.strip()]


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def find(hm: HouseMind, text: str, lang: str) -> dict[str, Any] | None:
    """A wish to set a step, or None when the sentence is no such wish. A wish that names
    nothing she knows comes back with ``found`` False, so she can say where to choose it."""
    t = normalise(text)
    if not any(_has(t, w) for w in _words(hm, lang, "talk.words.set_step")):
        return None
    step = next(
        (s for s in STEPS if _has(t, normalise(hm.languages.text(lang, f"steps.{s}")))), None
    )
    if step is None:
        return None
    assert hm.learning is not None
    pairs = set(hm.learning.steps) | set(hm.learning.first_seen) | hm.rules_possible()
    pairs = {pair for pair in pairs if hm.rule_there(*pair)}  # the rules Brein shows (E-485)
    area = next(
        (
            a.id
            for a in sorted(hm.model.areas.values(), key=lambda a: -len(a.name))
            if a.name and _has(t, normalise(a.name))
        ),
        None,
    )
    skill = next(
        (
            s
            for s in sorted(
                hm.skill_params, key=lambda s: -len(hm.languages.text(lang, f"skills.{s}"))
            )
            if _has(t, normalise(hm.languages.text(lang, f"skills.{s}")))
        ),
        None,
    )
    part = part_named(text, lang, hm.languages)
    step_name = hm.languages.text(lang, f"steps.{step}")
    if skill is not None and area is not None and (skill, area) in pairs:
        keys, chosen = [f"{skill}@{area}"], [(skill, area)]
        said = what(hm, lang, skill, area)
    elif part is not None and area is not None:
        chosen = sorted(
            p for p in pairs if p[1] == area and hm.skill_params.get(p[0], {}).get("domain") == part
        )
        keys = [f"{s}@{a}" for s, a in chosen]
        where = hm.names.where(area, lang) if hm.names else ""
        said = f"{hm.languages.text(lang, f'ui.domain.{part}')} {where}".strip()
    elif part is not None:
        keys = [part]
        chosen = sorted(p for p in pairs if hm.skill_params.get(p[0], {}).get("domain") == part)
        said = hm.languages.text(lang, f"ui.domain.{part}")
    elif area is not None and skill is None:
        keys = [f"area:{area}"]
        chosen = sorted(p for p in pairs if p[1] == area)
        said = hm.model.areas[area].name
    else:
        return {"found": False}
    if not keys or not chosen:
        return {"found": False}
    return {
        "kind": "step",
        "found": True,
        "keys": keys,
        "pairs": [list(p) for p in chosen],
        "step": step,
        "what": said,
        "stepName": step_name,
        "detailed": not (len(keys) == 1 and keys[0] == part),
    }


def allowed(hm: HouseMind, wish: dict[str, Any], lang: str) -> bool:
    """Every key may go to that step: the catalogue and the administrator's limit (20.10)."""
    view = Readiness(hm, lang, 4, True)
    for key in wish["keys"]:
        if "@" in key:
            skill, _, area = key.partition("@")
            domain = str(hm.skill_params.get(skill, {}).get("domain"))
            own = view._own(key, domain, {skill}, area)
        elif key.startswith("area:"):
            continue  # a whole room: each part keeps its own limit in the gate
        else:
            skills = {s for s, p in hm.skill_params.items() if p.get("domain") == key}
            own = view._own(key, key, skills, None)
        choice = next(c for c in own["choices"] if c["step"] == wish["step"])
        if not choice["allowed"]:
            return False
    return True


def next_question(
    hm: HouseMind, lang: str, pairs: list[list[str]], asked: list[str]
) -> dict[str, Any] | None:
    """The first automation question for these rows that was not asked yet (23.2, 23.3)."""
    for skill, area in pairs:
        view = steer_view(hm, lang, skill, area, admin=True)
        question = view.get("question")
        if question is None or question["id"] in asked:
            continue
        return {
            "kind": "automation",
            "id": question["id"],
            "turn": question["kind"],
            "text": question["text"],
            "pairs": pairs,
            "asked": [*asked, question["id"]],
        }
    return None


def _on(hm: HouseMind) -> set[str]:
    """The automations that are on: one HouseMind gave back shows up here after a step."""
    return {
        e
        for e, r in hm.automations.readings.items()
        if r.automation.enabled and e not in hm.automations.turned_off()
    }


def _name(hm: HouseMind, entity_id: str) -> str:
    reading = hm.automations.readings.get(entity_id)
    return reading.automation.name if reading is not None else entity_id


async def answer(
    hm: HouseMind, payload: dict[str, Any], yes: bool, user_id: str | None, lang: str
) -> tuple[str, dict[str, Any] | None]:
    """Her reply to a yes or no, and the next question if there is one."""
    t = hm.languages.text
    if payload.get("kind") == "step":
        if not yes:
            return t(lang, "talk.step.cancelled"), None
        if not allowed(hm, payload, lang):
            return t(
                lang, "talk.step.not_allowed", what=payload["what"], step=payload["stepName"]
            ), None
        off_before, on_before = set(hm.automations.turned_off()), _on(hm)
        for key in payload["keys"]:
            await hm.set_admin_step(key, payload["step"], user_id)
        done = t(lang, "talk.step.done", what=payload["what"], step=payload["stepName"])
        # The automations followed the step (E-42, E-255): she says what went off or on.
        for entity_id in sorted(set(hm.automations.turned_off()) - off_before):
            done += " " + t(lang, "ui.ready.steer.done_off", name=_name(hm, entity_id))
        for entity_id in sorted(_on(hm) - on_before):
            done += " " + t(lang, "ui.ready.steer.done_on", name=_name(hm, entity_id))
        follow = next_question(hm, lang, payload["pairs"], [])
        if follow is None:
            return done, None
        return f"{done} {follow['text']}", follow
    # The automation: off with its other rows along, or on again – or the no to either.
    entity_id = str(payload["id"])
    reading = hm.automations.readings.get(entity_id)
    name = reading.automation.name if reading is not None else entity_id
    if payload["turn"] == "off":
        result = (
            await hm.turn_off_automation(entity_id, user_id, along=True)
            if yes
            else await hm.leave_automation(entity_id, user_id)
        )
        key = "done_off" if yes else "left_on"
    else:
        result = (
            await hm.restore_automation(entity_id, user_id, reason="step_down")
            if yes
            else await hm.keep_automation_off(entity_id, user_id)
        )
        key = "done_on" if yes else "kept_off"
    said = t(lang, f"ui.ready.steer.{key if result['ok'] else 'not_done'}", name=name)
    follow = next_question(hm, lang, payload["pairs"], payload["asked"])
    if follow is None:
        return said, None
    return f"{said} {follow['text']}", follow
