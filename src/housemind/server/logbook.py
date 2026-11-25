"""The logbook (E-263, B-117): one timeline, the newest on top, of what HouseMind did or left
(with Why? and Undo), the notifications, the person's conversations with Vesta, the morning
briefing and the house report. It takes the place of the tabs Meldingen and Gesprek and of
"Wat ik bedacht" in full (redesign step 3). How much each line shows follows the level: one
sentence (2), saw · thought · did (3), and the trace (4). The app searches, filters and groups
the lines the way Brein does (E-323); the period – the last two days, today, yesterday, the
last 7 or 30 days, or one day – is asked here, as far back as the database keeps it (E-18).
An administrator also sees each question to an AI: what was sent and what came back (E-348).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from housemind.brain.filters import part_of
from housemind.ha.integration import FOREVER
from housemind.knowledge.catalog import Step
from housemind.runtime import DOMAINS, HouseMind
from housemind.safety.gate import ActionRequest, Check, Decision, Layer, Outcome
from housemind.server.views import (
    Viewer,
    _local,
    _thought,
    mind_groups,
    morning_card,
    notice_item,
)

KEEP = timedelta(days=7)  # notifications that were handled, and the house report
DONE = {1: 10, 2: 20, 3: 30, 4: 50}  # level → lines of what it did, asked or refused
WOULD = {2: 5, 3: 10, 4: 20}  # level → lines of what it only watched (from the brain's memory)
DAYS = timedelta(days=2)
TALKS = 30
KEPT = 30  # days the database keeps decisions and conversations (E-18): the furthest back
PERIODS = ("recent", "today", "yesterday", "week", "month", "day")
PER_DAY_ROWS = 300  # decisions read per day of the period, and lines at most
MOST_LINES = 600


def period_of(
    hm: HouseMind, now: datetime, choice: str | None, day: str | None
) -> tuple[str, datetime, datetime | None, int]:
    """The period asked (E-323) as [since, until) in the house's own days, and how many days
    it covers. "recent", the default, is the last two days."""
    choice = choice if choice in PERIODS else "recent"
    local = _local(hm, now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)

    def start(days_back: int) -> datetime:
        return (midnight - timedelta(days=days_back)).astimezone(now.tzinfo)

    oldest = start(KEPT - 1)
    if choice == "today":
        return choice, start(0), None, 1
    if choice == "yesterday":
        return choice, start(1), start(0), 1
    if choice == "week":
        return choice, start(6), None, 7
    if choice == "month":
        return choice, oldest, None, KEPT
    if choice == "day":
        try:
            asked = date.fromisoformat(str(day))
        except ValueError:
            return "recent", now - DAYS, None, 2
        back = (midnight.date() - asked).days
        if back < 0:
            return choice, start(0), None, 1  # a day still to come: today
        since = start(back)
        return choice, max(since, oldest), start(back - 1) if back > 0 else None, 1
    return "recent", now - DAYS, None, 2


def decision_from_row(row: Any) -> Decision:
    """A decision as the database keeps it (B-180), not one of the last hundred thoughts in
    memory, so the logbook is complete and survives a restart."""
    trace = json.loads(row["trace"] or "{}")
    step = trace.get("step")
    request = ActionRequest(
        Layer(row["source_layer"]),
        str(row["entity_id"]),
        str(row["action"]),
        json.loads(row["data"] or "{}"),
        reason=str(trace.get("why") or ""),
        skill=trace.get("skill"),
        area_id=trace.get("area"),
        facts=trace.get("facts") or {},
    )
    return Decision(
        request,
        Outcome(row["outcome"]),
        row["reason"],
        Step[str(step).upper()] if step else None,
        tuple(Check(str(c[0]), bool(c[1]), str(c[2] or "")) for c in trace.get("checks") or ()),
        datetime.fromisoformat(str(row["at"])),
    )


def alarm_kind(state: str) -> str:
    """armed, triggered, disarmed or back – how the line looks."""
    return "armed" if state.startswith("armed") else state


def alarm_text(hm: HouseMind, lang: str, row: dict[str, Any]) -> str:
    """One line of the alarm in the Logboek (E-533, E-534), in the person's language."""
    t = hm.languages.text
    entity = hm.model.entities.get(str(row["alarm"]))
    name = entity.name if entity is not None else str(row["alarm"])
    state = str(row["state"])
    lamps, covers = int(row["lamps"] or 0), int(row["covers"] or 0)
    if state.startswith("armed"):
        mode = t(lang, f"ui.logbook.alarm.mode.{state.removeprefix('armed_')}")
        return t(lang, "ui.logbook.alarm.armed", name=name, mode=mode)
    if state == "triggered":
        opened = t(lang, "ui.logbook.alarm.and_covers") if covers else ""
        did = (
            t(lang, "ui.logbook.alarm.did", count=lamps, covers=opened)
            if lamps
            else t(lang, "ui.logbook.alarm.did_none")
        )
        return t(lang, "ui.logbook.alarm.triggered", name=name, did=did)
    if state == "disarmed":
        seconds = row.get("seconds")
        if seconds is None:
            return t(lang, "ui.logbook.alarm.disarmed", name=name)
        after = (
            t(lang, "ui.logbook.alarm.seconds", count=int(seconds))
            if int(seconds) < 120
            else t(lang, "ui.logbook.alarm.minutes", count=round(int(seconds) / 60))
        )
        return t(lang, "ui.logbook.alarm.disarmed_after", name=name, after=after)
    # back: the house as it was (E-534)
    if lamps:
        also = t(lang, "ui.logbook.alarm.and_covers_back") if covers else ""
        return t(lang, "ui.logbook.alarm.back", count=lamps, covers=also)
    return t(lang, "ui.logbook.alarm.back_covers")


def _pause_item(hm: HouseMind, lang: str, row: Any) -> dict[str, Any]:
    """A pause or a resume, and who (QA 3, gat 3): someone without a name is a wall tablet,
    Assist or an automation."""
    t = hm.languages.text
    who = str(row["display_name"] or t(lang, "pause.log.someone"))
    domain = str(row["part"]).partition(".")[2]
    part = next((k for k, ds in DOMAINS.items() if domain in ds), None)
    what = t(lang, f"ui.domain.{part}") if part else t(lang, "pause.log.house")
    if row["state"] == "on":
        text = t(lang, "pause.log.resumed", what=what, who=who)
    else:
        until = datetime.fromisoformat(str(row["state"]))
        if until.year >= FOREVER.year:
            text = t(lang, "pause.log.open", what=what, who=who)
        else:
            text = t(lang, "pause.log.until", what=what, who=who, at=_when(hm, until)["at"])
    return {
        "type": "part",
        "id": int(row["id"]),
        "part": "pause",
        "off": row["state"] != "on",
        "text": text,
        **_when(hm, datetime.fromisoformat(str(row["at"]))),
    }


@dataclass
class _Asked:
    """Questions and their answers in one line (QA 3, schermen 9): per asked decision whether
    the answer was yes, when, and the decision that came of it; and the decisions that are in
    such a line already."""

    answers: dict[int, tuple[bool, datetime, Decision | None]] = field(default_factory=dict)
    after: set[int] = field(default_factory=set)


SAME_MOMENT = timedelta(minutes=1)  # a question and its decision, an answer and what it did
YES = ("do", "always", "from_now")


async def _asked_and_answered(
    hm: HouseMind, since: datetime, end: str, decisions: list[Decision]
) -> _Asked:
    found = _Asked()
    rows = await hm.db.fetchall(
        "SELECT at, entity_id, action, answered_at, answer FROM proposals "
        "WHERE status = 'answered' AND answered_at IS NOT NULL AND at >= ? AND at < ?",
        (since.isoformat(), end),
    )
    for row in rows:
        entity, action = str(row["entity_id"]), str(row["action"])
        asked_at = datetime.fromisoformat(str(row["at"]))
        answered_at = datetime.fromisoformat(str(row["answered_at"]))
        question = next(
            (
                d
                for d in decisions
                if d.outcome is Outcome.PROPOSED
                and d.request.entity_id == entity
                and d.request.action == action
                and abs(d.at - asked_at) <= SAME_MOMENT
            ),
            None,
        )
        if question is None:
            continue
        then = next(
            (
                d
                for d in decisions
                if d.request.layer is Layer.PERSON_ANSWER
                and d.request.entity_id == entity
                and abs(d.at - answered_at) <= SAME_MOMENT
            ),
            None,
        )
        found.answers[id(question)] = (str(row["answer"]) in YES, answered_at, then)
        if then is not None:
            found.after.add(id(then))
    return found


def _when(hm: HouseMind, at: datetime) -> dict[str, str]:
    local = _local(hm, at)
    return {"when": at.isoformat(), "day": local.date().isoformat(), "at": local.strftime("%H:%M")}


def _answer(
    hm: HouseMind, lang: str, user_id: str, row: dict[str, Any], decision: Decision
) -> dict[str, Any]:
    """A 👍/👎 under the line it was given on, and what Vesta did with it (E-298): who gave it
    (only "you" or "someone", never a name), and whether it counted as a yes or a correction
    – with how far that is from a step back."""
    text = hm.languages.text
    good = bool(row["good"])
    at = _local(hm, datetime.fromisoformat(str(row["at"]))).strftime("%H:%M")
    who = text(lang, "ui.thumbs.who_you" if row["user_id"] == user_id else "ui.thumbs.who_someone")
    said = text(lang, "ui.thumbs.log_good" if good else "ui.thumbs.log_bad", who=who, at=at)
    r = decision.request
    learned = row.get("learned")
    if not learned or not r.skill or not r.area_id or hm.learning is None:
        key, n = "ui.thumbs.learned_none", 0
    elif learned == "yes":
        key, n = "ui.thumbs.learned_yes", 0
    else:
        n = len(hm.learning.recent_setbacks(r.skill, r.area_id, hm.clock.now()))
        if hm._skill_step(r.skill, r.area_id) is Step.OBSERVE:
            key = "ui.thumbs.learned_observe"
        else:
            key = "ui.thumbs.learned_back" if n >= 2 else "ui.thumbs.learned_correction"
    area = hm.model.areas.get(r.area_id or "")
    learned_text = text(
        lang,
        key,
        skill=text(lang, f"skills.{r.skill}") if r.skill else "",
        area=area.name if area else "",
        n=max(n, 1),
    )
    return {"good": good, "text": f"{said} {learned_text}"}


async def logbook(
    hm: HouseMind,
    viewer: Viewer,
    user_id: str,
    lang: str,
    choice: str | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    """The lines of the period, the newest first, and the house's own today (for Vandaag and
    Gisteren). Without a period: the last two days, the open notifications and this week's
    handled ones, the latest conversations and the house report."""
    level = viewer.level
    now = hm.clock.now()
    choice, since, until, days = period_of(hm, now, choice, day)
    recent = choice == "recent"
    end = (until or now + timedelta(seconds=1)).isoformat()
    items: list[dict[str, Any]] = []

    # What HouseMind did, asked or refused – from the database (B-180): every decision, also
    # emergencies, taking over automations, Do it, Undo and moments, kept over a restart.
    # What it only watched comes from its memory, apart and fewer (level 2: "what I would do").
    rows = await hm.db.fetchall(
        "SELECT * FROM decisions WHERE at >= ? AND at < ? ORDER BY id DESC LIMIT ?",
        (since.isoformat(), end, 500 if recent else PER_DAY_ROWS * days),
    )
    stored = []
    row_of: dict[int, int] = {}
    for row in rows:
        try:
            decision = decision_from_row(row)
        except (KeyError, ValueError, TypeError):
            continue  # an old row this version cannot read: left out, never a broken line
        if decision.request.facts.get("alarm") and not decision.request.facts.get("test"):
            continue  # the alarm's lamps are one line of the alarm (E-533), not a hundred
        row_of[id(decision)] = int(row["id"])
        stored.append(decision)
    most = DONE.get(level, 50)
    if not recent:
        most = min(MOST_LINES, max(most, most * days // 2))
    done = mind_groups(hm, level, most, since, decisions=stored)
    if level >= 2 and until is None:  # what it only watched is in its memory: only up to now
        watched = [d for d in reversed(hm.status.thoughts) if d.outcome is Outcome.OBSERVED]
        done += mind_groups(hm, level, WOULD.get(level, 20), since, decisions=watched)
    remembered = hm.recent
    roles = hm.gate.state.roles if hm.gate is not None else {}
    answered_q = await _asked_and_answered(hm, since, end, [d for d, _ in done])
    for decision, devices in done:
        if id(decision) in answered_q.after:
            continue  # done after a yes: one line with the question (QA 3, schermen 9)
        item = _thought(hm, lang, max(level, 2), decision, devices, now)
        row_id = row_of.get(id(decision))
        record = remembered.get(row_id) if row_id is not None else None
        item.update(_when(hm, decision.at))
        item["type"] = "done"
        item["id"] = row_id
        # For Filter: the part, as in Apparaten en entiteiten (E-323).
        item["part"] = part_of(decision.request.entity_id, roles)
        item["undo"] = bool(
            record
            and record.decision.executed
            and not record.undone
            and record.previous is not None
            and decision.request.layer not in (Layer.EMERGENCY, Layer.PERSON_ANSWER)
        )
        answered = answered_q.answers.get(id(decision))
        if answered is not None:
            # The question, the answer and what came of it in one line: "jij zei ja om 09:14".
            yes, at, then = answered
            when_ = _when(hm, at)["at"]
            item["answered"] = hm.languages.text(
                lang, "ui.logbook.answered_yes" if yes else "ui.logbook.answered_no", at=when_
            )
            if then is not None:
                item["outcome"] = then.outcome.value
                item["outcomeName"] = hm.languages.text(lang, f"ui.outcome.{then.outcome.value}")
        if row_id is not None and level >= 2:
            item["answers"] = [
                _answer(hm, lang, user_id, row, decision) for row in await hm.thumbs_for(row_id)
            ]
        items.append(item)

    # Notifications: the open ones with their answers, and what was handled this week – or,
    # for a period, every one of that period.
    week = (now - KEEP).isoformat()
    if recent:
        rows = await hm.db.fetchall(
            "SELECT * FROM notifications WHERE resolved_at IS NULL OR at >= ? ORDER BY id DESC "
            "LIMIT 100",
            (week,),
        )
    else:
        rows = await hm.db.fetchall(
            "SELECT * FROM notifications WHERE at >= ? AND at < ? ORDER BY id DESC LIMIT 300",
            (since.isoformat(), end),
        )
    for row in rows:
        if row["resolved_at"] is not None and str(row["topic"]).startswith("proposal:"):
            continue  # an answered question is in the line of its decision (QA 3, schermen 9)
        # Every line, also what this person switched off: shown as such, to switch on again.
        notice = notice_item(hm, lang, dict(row), viewer.is_admin, user_id, full=True)
        if notice is None:
            continue
        notice.update(_when(hm, datetime.fromisoformat(row["at"])))
        notice["type"] = "notice"
        notice["open"] = row["resolved_at"] is None
        items.append(notice)

    # The person's own conversations with Vesta (never someone else's).
    talks = await hm.db.fetchall(
        "SELECT id, at, question, answer FROM conversations WHERE user_id = ? AND at >= ? "
        "AND at < ? ORDER BY id DESC LIMIT ?",
        (user_id, "" if recent else since.isoformat(), end, TALKS if recent else 200),
    )
    for talk in talks:
        items.append(
            {
                "type": "talk",
                "id": int(talk["id"]),
                "question": str(talk["question"]),
                "answer": str(talk["answer"]),
                **_when(hm, datetime.fromisoformat(talk["at"])),
            }
        )

    # What went to an AI and what came back (E-348): only for an administrator, as the AI
    # settings are (privacy: "AI is only for administrators"). What was sent is what the AI
    # got – neutral names, never people's names (E-15); a question forwarded to another
    # assistant is there too.
    if viewer.is_admin and level >= 4:  # on Expert, as the AI screen promises (QA 3)
        asked = await hm.db.fetchall(
            "SELECT * FROM ai_log WHERE at >= ? AND at < ? ORDER BY id DESC LIMIT 500",
            (since.isoformat(), end),
        )
        for row in asked:
            items.append(
                {
                    "type": "ai",
                    "id": int(row["id"]),
                    "purpose": str(row["purpose"]),
                    "model": str(row["model"]),
                    "instruction": str(row["instruction"] or ""),  # why, and what answer
                    "sent": str(row["sent"]),
                    "received": str(row["received"]),
                    "ok": bool(row["ok"]),
                    "cost": round(float(row["cost"]), 5),
                    **_when(hm, datetime.fromisoformat(str(row["at"]))),
                }
            )

    # The alarm (E-533): switched on, gone off with what HouseMind did, switched off, and the
    # house put back (E-534) – a few lines, for everyone who has the Logboek.
    alarm_rows = await hm.db.fetchall(
        "SELECT * FROM alarm_log WHERE at >= ? AND at < ? ORDER BY id DESC LIMIT 200",
        (since.isoformat(), end),
    )
    for row in alarm_rows:
        items.append(
            {
                "type": "alarm",
                "id": int(row["id"]),
                "state": alarm_kind(str(row["state"])),
                "text": alarm_text(hm, lang, dict(row)),
                **_when(hm, datetime.fromisoformat(str(row["at"]))),
            }
        )

    # Parts turned off and on again (E-400, B-322): who did it, and when – for everyone.
    changes = await hm.db.fetchall(
        "SELECT c.id, c.at, c.part, c.state, p.display_name FROM part_changes c "
        "LEFT JOIN people p ON p.user_id = c.user_id WHERE c.at >= ? AND c.at < ? "
        "ORDER BY c.id DESC LIMIT 200",
        (since.isoformat(), end),
    )
    t = hm.languages.text
    for row in changes:
        if str(row["part"]).startswith("pause"):
            items.append(_pause_item(hm, lang, row))
            continue
        who = str(row["display_name"] or t(lang, "ui.parts.someone"))
        part = t(lang, f"ui.domain.{row['part']}")
        key = "ui.parts.log_off" if row["state"] == "off" else "ui.parts.log_on"
        items.append(
            {
                "type": "part",
                "id": int(row["id"]),
                "part": str(row["part"]),
                "off": row["state"] == "off",
                "text": t(lang, key, part=part, who=who),
                **_when(hm, datetime.fromisoformat(str(row["at"]))),
            }
        )

    # The morning briefing (while it is morning) and the latest house report.
    briefing = await morning_card(hm, viewer, lang) if until is None else None
    if briefing:  # shown this morning, so it is the newest thing until something happens
        items.append({"type": "briefing", "text": briefing, **_when(hm, now)})
    report = await hm.db.fetchone(
        "SELECT * FROM reports WHERE at < ? ORDER BY week DESC LIMIT 1", (end,)
    )
    first = week if recent else since.isoformat()
    if report and level >= 2 and str(report["at"]) >= first:
        lines, by_ai = hm.report_words(json.loads(report["data"]), level, lang)
        at = datetime.fromisoformat(str(report["at"]))
        items.append(
            {
                "type": "report",
                "week": str(report["week"]),
                "lines": lines,
                "ai": by_ai,  # in the AI's words; the numbers are the core's (E-418)
                **_when(hm, at),
            }
        )

    items.sort(key=lambda i: datetime.fromisoformat(i["when"]), reverse=True)
    today = _local(hm, now).date()
    return {
        "today": today.isoformat(),
        "items": items,
        "period": choice,
        "oldest": (today - timedelta(days=KEPT - 1)).isoformat(),  # for Kies een dag
    }
