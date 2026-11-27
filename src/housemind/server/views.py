"""What each screen shows, per person and per level (H9.2, E-17, E-35).

The server decides what a person gets to see: level 1 gets the Orb and one calm line, level 2
adds the day and the rooms, level 3 proposals, the floor plan and steps, level 4 everything the
brain thinks. Texts are resolved here in the person's language, so the UI only shows them.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from housemind import __version__
from housemind.ai.privacy import names_in_rooms
from housemind.ai.providers import is_local
from housemind.ai.service import AI_TASK_PER_DAY
from housemind.brain import alarm_reaction, groups, icons, news, security, setting_up
from housemind.brain import parts as part_list
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.briefing import PARTS
from housemind.brain.care import CareSettings
from housemind.brain.filters import PARTS as HOUSE_PARTS
from housemind.brain.filters import causes_of, part_of, parts_of, rooms_of
from housemind.brain.functions import FUNCTIONS, OTHER_DEVICE, SWITCH_CHOICES, UNKNOWN
from housemind.brain.house_knowledge import CONFIRMED, REJECTED, SUGGESTED, Finding
from housemind.brain.lampday import KEEP as LAMP_DAY_KEEP
from housemind.brain.lampday import what_changed
from housemind.brain.levels import COLOURS, WAIT_MAX
from housemind.brain.names import sentence_case
from housemind.brain.notify import (
    ALL_CAMERAS,
    CAMERA_BUNDLE,
    INFO_KEEP,
    kind_key,
    notice_text,
    part_key,
)
from housemind.brain.readiness import ORDER as READY_ORDER
from housemind.brain.readiness import (
    automation_state,
    does_view,
    history_line,
    readiness,
    readiness_others,
)
from housemind.brain.rules import rule_text
from housemind.brain.skills import light_off_hold
from housemind.core.model import Entity
from housemind.energy.battery import empty as battery_empty
from housemind.energy.tips import tips
from housemind.knowledge import updates
from housemind.knowledge.area_roles import ROLE_ORDER
from housemind.knowledge.catalog import Step
from housemind.knowledge.labels import automation_hint, category_names, entity_hint, label_names
from housemind.knowledge.matcher import recognise
from housemind.knowledge.roles import EntityRole, entity_role
from housemind.memory.database import KEEP_DAYS, KEEP_LONGER
from housemind.parts.proof import PROOF_KEYS, READY
from housemind.runtime import DOMAINS, LEVELS, RESIDENT_TOP_LEVEL, HouseMind
from housemind.safety.gate import Decision, Layer, Outcome
from housemind.world.calendar import DayType, day_type

PREF_CHOICES: dict[str, tuple[str, ...]] = {
    "amount": ("less", "normal", "more"),
    "text_size": ("normal", "large"),
    "theme": ("auto", "dark", "light"),
    "address": ("je", "u"),  # how Vesta speaks to this person (E-228)
    "timing": ("normal", "longer", "long"),  # time to talk and to read (E-299)
    # The tab open in Brein last, per person and on every device (E-398, B-316).
    # Wat ik weet (E-524) for administrators on Betrokken and Expert.
    "brain_tab": (
        "readiness",
        "rules",
        "rhythm",
        "knowledge",
        "devices",
    ),
}
NOT_WINDOW_COVERS = {"garage", "gate", "door", "awning", "damper", "window"}
ROOM_DOMAINS = ("light", "cover", "climate", "fan", "switch", "media_player")


def device_icon(hm: HouseMind, entity: Any, state: Any = None) -> dict[str, Any]:
    """An entity's icon (E-509, B-435) and what it shows of the state now (a lamp's
    brightness, a closed curtain …). Where it came from is not sent: no screen shows it."""
    function = hm.world.functions.get(entity.entity_id) if hm.world is not None else None
    device = hm.model.devices.get(entity.device_id) if entity.device_id else None
    choice = hm.icons.of(entity, function, device.name if device is not None else None)
    return icons.with_state(choice.icon, entity, state)


# The parts of the energy plan as icons (E-512).
ENERGY_PART_ICONS = {
    "boiler": "boiler",
    "heat_pump": "heat_pump",
    "appliance": "plug",
    "export": "grid",
}


def icon_look(hm: HouseMind, entity_id: str | None) -> dict[str, Any]:
    """The icon of what a decision, a question or a notice is about (E-511), as it is now;
    nothing for what is no entity (the house, a person's own words)."""
    entity = hm.model.entities.get(entity_id or "")
    if entity is None:
        return {}
    state = hm.world.states.get(entity.entity_id) if hm.world is not None else None
    return device_icon(hm, entity, state)


ENTITY_IN = re.compile(r"\b([a-z_]+\.[a-z0-9_]+)\b")


def _notice_icon(hm: HouseMind, topic: str, params: dict[str, Any]) -> dict[str, Any]:
    """The device a notice is about, from its subject or its words' values (E-511)."""
    for text in (topic, *(str(v) for v in params.values() if isinstance(v, str))):
        for found in ENTITY_IN.findall(text):
            if found in hm.model.entities:
                return icon_look(hm, found)
    return {}


@dataclass(slots=True)
class Viewer:
    user_id: str
    name: str | None
    level: int
    is_admin: bool
    preferences: dict[str, Any] = field(default_factory=dict)
    consents: dict[str, Any] = field(default_factory=dict)
    ha_language: str | None = None
    browser_language: str | None = None
    tablet: str | None = None  # a wall tablet in this area (E-134)

    def language(self, hm: HouseMind) -> str:
        """Own choice → language of the HA profile (E-25) → HA's system language → browser →
        English. A browser in another language never wins over Home Assistant."""
        chosen = self.preferences.get("language")
        for candidate in (
            chosen if chosen and chosen != "auto" else None,
            self.ha_language,
            hm.config.language if hm.config else None,
            self.browser_language,
        ):
            if candidate and candidate.split("-", 1)[0].lower() in hm.languages.languages:
                return hm.languages.resolve(candidate)
        return "en"


async def load_viewer(hm: HouseMind, user_id: str, name: str | None, trusted_local: bool) -> Viewer:
    row = await hm.db.fetchone("SELECT * FROM people WHERE user_id = ?", (user_id,))
    if row is None:
        # A new resident begins where the administrator chose, Carefree unless set (E-287).
        level = await hm.setting("new_person_level")
        await hm.db.execute(
            # OR IGNORE: two screens of a new person can arrive at once.
            "INSERT OR IGNORE INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
            (user_id, name, level if level in (1, 2, 3, 4) else 1, hm.clock.now().isoformat()),
        )
        row = await hm.db.fetchone("SELECT * FROM people WHERE user_id = ?", (user_id,))
        await hm._refresh_people()
    assert row is not None
    users = {u.id: u for u in await hm.ha.users()}
    user = users.get(user_id)
    # Outside Home Assistant (local demo) the person at the keyboard is the administrator.
    is_admin = bool(user.is_admin) if user is not None else trusted_local
    level = int(row["level"])
    if not is_admin:  # no higher than an administrator allows residents (QA 3, gat 3)
        level = min(level, await resident_top_level(hm))
    return Viewer(
        user_id=user_id,
        name=name or (user.name if user else None),
        level=level,
        is_admin=is_admin,
        preferences=json.loads(row["preferences"] or "{}"),
        consents=json.loads(row["consents"] or "{}"),
    )


async def resident_top_level(hm: HouseMind) -> int:
    """The highest level for someone who is not an administrator (QA 3, gat 3): Involved,
    unless an administrator chose otherwise."""
    top = await hm.setting("resident_top_level")
    return int(top) if top in (1, 2, 3, 4) else RESIDENT_TOP_LEVEL


def _fmt_number(hm: HouseMind, value: float, lang: str, digits: int = 1) -> str:
    return hm.languages.number(lang, value, digits)


def _local(hm: HouseMind, value: datetime) -> datetime:
    return value.astimezone(ZoneInfo(hm.config.time_zone if hm.config else "UTC"))


# -- the snapshot ------------------------------------------------------------------------


async def snapshot(hm: HouseMind, viewer: Viewer) -> dict[str, Any]:
    lang = viewer.language(hm)
    level = viewer.level
    top = 4 if viewer.is_admin else await resident_top_level(hm)  # QA 3, gat 3
    again = bool(await hm.db.get_setting("intro_again"))
    data: dict[str, Any] = {
        # Vesta for this viewer (E-284): what concerns them, for administrators also what is
        # for administrators; "let op" without HA only from level 2 (H7.4).
        # A shared screen shows nothing that is for one person only.
        "orb": hm.orb_state(
            admin=viewer.is_admin,
            level=level,
            user_id=None if viewer.tablet else viewer.user_id,
        ),
        # The alarm is on (E-539): asleep, her word says "secured" too.
        "secured": hm.alarm_armed(),
        "line": await status_line(hm, lang, level),
        # Why she has her colour, in that line instead (E-389): the same viewer as her colour.
        "why": await hm.vesta_why(
            lang, level, viewer.is_admin, None if viewer.tablet else viewer.user_id
        ),
        "mind": mind(hm, lang, level),  # what the brain thought, per level (E-218)
        "level": level,
        "language": lang,
        # One list, its gaps already filled; no English beside it (code QA 3, punt 12).
        "texts": hm.languages.complete(lang),
        "fallback": {},
        "levels": [{"level": n, "key": key} for n, key in LEVELS if n <= top],
        "user": {"name": viewer.name, "isAdmin": viewer.is_admin},
        "paused": pause_info(hm),
        # A testkwartier that runs (E-477): the bar at the top, for an administrator on Expert.
        "tests": hm.test_running(lang) if viewer.is_admin and level >= 4 else [],
        # Where Vesta's voice goes now, for the privacy statement (20.19).
        "speech": (
            {"cloud": hm.speech_out["cloud"], "local": hm.speech_out["local"]}
            if hm.speech_out
            else None
        ),
        # After a failed update (E-301): what is wrong, and only that screen.
        "safeMode": hm.safe_mode,
        # The house mode, for the one-tap buttons in Vesta's window (E-286).
        "houseMode": hm.world.modes.mode.value if hm.world is not None else "home",
        # Reading the history (E-257), and whether it stands still (E-396).
        "history": {**hm.history_status.view(), "stalled": setting_up.stalled(hm)},
        "features": {
            "energy": bool(hm.energy and hm.energy.setup.has_any),
            "ai": hm.ai.status(),
        },
        "version": __version__,
        "haOldest": hm.compatibility.oldest_text(),  # Over HouseMind (E-413)
        "news": whats_new(hm, viewer, lang),
        "languageNames": language_names(hm, lang),
        "prefs": preferences(viewer),
        "youGet": you_get(hm, viewer, lang),  # what this person gets (E-407)
        "keepTerms": keep_terms(lang, hm),  # how long HouseMind keeps what (E-409)
        # The manual in the app (E-238): whether there is one in this language, and which part
        # tells more about each Help question (Lees meer).
        "manual": {
            "available": lang in hm.library.manuals,
            "links": {
                p.key.removeprefix("help:").replace(":", "."): p.more
                for p in hm.library.pieces.get(lang, [])
                if p.source == "help" and p.more
            },
        },
        "vesta": {
            "name": hm.vesta_name(lang),
            # Only where the language has a polite form (je/u, du/Sie, tu/vous).
            "formal": hm.vesta_phrases.has_formal(lang),
        },
        # The colour schemes that passed the check (E-266, E-267); each device chooses one.
        "themes": [s.view(lang) for s in hm.schemes.ordered()],
        "areas": [
            {"id": a.id, "name": a.name}
            for a in sorted(hm.model.areas.values(), key=lambda a: a.name)
        ],
        "tablet": tablet_view(hm, viewer.tablet, lang) if viewer.tablet else None,
        "onboarding": {
            "person": not viewer.preferences.get("onboarded"),
            "house": viewer.is_admin
            and (await hm.db.get_setting("consent_learning") is None or again),
            "again": viewer.is_admin and again,  # with the answers of now (E-272)
        },
        # Not set up yet: only the start screen and the steps (E-277).
        "setup": {
            "state": await setting_up.state(hm),
            "language": hm._language(),
            "readWait": await setting_up.read_wait(hm),  # seconds Klaar still waits (E-396)
            "labels": hm.labels_used(),  # labels used as a hint, one line (E-345)
        },
    }
    active, total = await hm.active_areas()
    data["activeAreas"], data["totalAreas"] = active, total
    if level == 1:
        # Carefree: one calm screen; only an emergency card can come with it (E-283).
        data["notifications"] = [
            n
            for n in await notifications(hm, lang, viewer.is_admin, viewer.user_id)
            if n["kind"] == "emergency"
        ]
        # The day ring around Vesta (E-435): a dot for each time it did something today.
        day = await day_flow(hm, lang)
        data["dayRing"] = [dot["at"] for dot in day["core"] if dot["outcome"] == "executed"]
    if level >= 2:
        data["energy"] = await energy(hm, lang, level, viewer.is_admin)
        data["day"] = await day_summary(hm, lang)
        data["dayNote"] = day_note(hm, lang)
        data["rooms"] = rooms(hm, lang, level)
        data["notifications"] = await notifications(
            hm, lang, viewer.is_admin, None if viewer.tablet else viewer.user_id
        )
        # "Klaar voor autonomie?" (E-256): one sentence per part on Huis, all of it in Brein.
        data["readiness"] = await readiness(hm, lang, level, viewer.is_admin)
        if level >= 3:
            # The automations no part lists, so Brein has the one list (E-523).
            data["readinessOther"] = await readiness_others(
                hm, lang, level, viewer.is_admin, data["readiness"]
            )
            if viewer.is_admin:
                data["knowledgeWaiting"] = knowledge_waiting(hm)  # on its tab (E-524)
        if level <= 3:
            data["dayRooms"] = day_rooms(await day_flow(hm, lang))
        # The rhythm of the house in one sentence on Huis, under "Wat ik bedacht" (E-368, K1).
        if level == 2 and (line := rhythm_line(hm, lang)):
            data["rhythmLine"] = line
    if level >= 3:
        asked = await hm.open_proposals()
        # The house in motion (E-439): what it did last; the newest open question has its own
        # dotted path in the network, from its room and device in ``proposals``.
        data["livePath"] = live_path(hm, lang, {p.entity_id for p in asked})
        data["proposals"] = await proposals(hm, lang, level, asked)
        data["floors"] = floors(hm)
        data["rules"] = rules(hm, lang)
        data["rhythm"] = rhythm(hm, lang, level)
        # What HouseMind learned per room, in its sentences or the AI's story (E-415).
        data["rhythm"]["habits"] = hm.habits_view(data.get("readiness") or [], lang)
        for habit in data["rhythm"]["habits"]:  # the room's icon (E-511)
            area = hm.model.areas.get(habit.get("area") or "")
            if area is not None:
                habit["icon"] = icons.area_icon(area, hm.area_roles.get(area.id))
    if level >= 4:
        data["reaction"] = reaction(hm)  # one line in Over HouseMind (QA 3, schermen 3)
    return data


async def status_line(hm: HouseMind, lang: str, level: int = 2) -> str:
    """One calm line (B-24): pause, inside temperature, who is home."""
    t = hm.languages.text
    parts: list[str] = []
    if hm.status.ha_lost is not None and level >= 2:
        parts.append(t(lang, "status.ha_lost"))
    if not hm.activated:
        parts.append(t(lang, "status.inactive"))  # until the introduction is done (E-275)
    paused = hm.paused_until()
    if paused is not None:
        if paused.year >= 9999:
            parts.append(t(lang, "pause.paused_open"))
        else:
            # "tot morgen 06:00" when it is not today: "tot 06:00" read as this morning (E-83).
            end = _local(hm, paused)
            key = (
                "pause.paused_line"
                if end.date() == _local(hm, hm.clock.now()).date()
                else "pause.paused_line_tomorrow"
            )
            parts.append(t(lang, key, time=end.strftime("%H:%M")))
    temperature = await hm.inside_temperature()
    if temperature is not None:
        unit = hm.config.temperature_unit if hm.config else "°C"
        parts.append(
            t(
                lang,
                "ui.inside_temperature",
                temperature=f"{_fmt_number(hm, temperature, lang)} {unit}",
            )
        )
    home = hm.world.residents_home() if hm.world is not None else 0
    parts.append(
        t(lang, "status.people_home", count=home) if home else t(lang, "status.nobody_home")
    )
    return " · ".join(parts)


def you_get(hm: HouseMind, viewer: Viewer, lang: str) -> list[str]:
    """What this person gets, from their own level and choices (E-407, B-333): emergencies,
    notifications and questions a day, the day's overview and their quiet hours."""
    t = hm.languages.text
    person = next((p for p in hm._people if p.user_id == viewer.user_id), None)
    if person is None or hm.router is None:
        return []
    p = viewer.preferences
    if viewer.level >= 2:
        lines = [t(lang, "ui.you_get.emergency_always")]
    elif viewer.consents.get("emergency_push") is True:
        lines = [t(lang, "ui.you_get.emergency_phone")]
    else:
        lines = [t(lang, "ui.you_get.emergency_app")]
    notices = hm.router._budget(person)
    lines.append(
        t(lang, "ui.you_get.notices", count=notices)
        if notices
        else t(lang, "ui.you_get.no_notices")
    )
    if questions := hm.router.question_budget(person):
        lines.append(t(lang, "ui.you_get.questions", count=questions))
    if viewer.level >= 2:
        lines.append(t(lang, "ui.you_get.overview", at=str(p.get("bundle_at") or "19:00")))
    if viewer.level >= 4 and p.get("quiet_from") and p.get("quiet_until"):
        lines.append(t(lang, "ui.you_get.quiet", start=p["quiet_from"], end=p["quiet_until"]))
    return lines


def keep_terms(lang: str, hm: HouseMind) -> list[str]:
    """How long HouseMind keeps what, short to long (E-409, B-335): the terms it cleans up
    by itself (E-18)."""
    t = hm.languages.text
    longer = {table: days for table, _, days in KEEP_LONGER}
    return [
        t(lang, "ui.my_data.keep.week", days=INFO_KEEP.days),
        t(lang, "ui.my_data.keep.month", days=KEEP_DAYS),
        t(lang, "ui.my_data.keep.proposals", days=longer["proposals"]),
        t(lang, "ui.my_data.keep.years", years=round(longer["reports"] / 365)),
        t(lang, "ui.my_data.keep.always"),
        t(lang, "ui.my_data.keep.voice"),
    ]


def pause_info(hm: HouseMind) -> dict[str, Any]:
    paused = hm.paused_until()
    hour, morning = hm._pause_until(60), hm._pause_until(-1)  # when they end (E-405)
    return {
        "hour": _local(hm, hour).strftime("%H:%M") if hour is not None else None,
        "morning": _local(hm, morning).strftime("%H:%M") if morning is not None else None,
        "active": paused is not None,
        "open": paused is not None and paused.year >= 9999,
        "until": _local(hm, paused).strftime("%H:%M") if paused is not None else None,
        "domains": {
            d: (None if u.year >= 9999 else _local(hm, u).strftime("%H:%M"))
            for d, u in hm.paused_domains().items()
        },
    }


def preferences(viewer: Viewer) -> dict[str, Any]:
    p = viewer.preferences
    raw_briefing = p.get("briefing")
    briefing: dict[str, Any] = raw_briefing if isinstance(raw_briefing, dict) else {}
    raw_kinds = p.get("kinds")
    kinds: dict[str, Any] = raw_kinds if isinstance(raw_kinds, dict) else {}
    return {
        "amount": p.get("amount", "normal"),
        "kinds": {k: kinds.get(k, True) for k in ("important", "info", "learning")},
        "maxPerDay": p.get("max_per_day", 20),
        "questionsPerDay": int(p.get("questions_per_day", 5)),  # E-294, level 4
        "language": p.get("language", "auto"),
        "textSize": p.get("text_size", "normal"),
        "timing": p.get("timing", "normal"),  # time to talk and to read (E-299)
        "theme": p.get("theme", "auto"),
        "brainTab": p.get("brain_tab", "readiness"),
        # Messages in danger (E-20): yes, no, or not chosen yet (None: only emergencies).
        "emergencyPush": _yes_no(viewer.consents.get("emergency_push")),
        "bundleAt": str(p.get("bundle_at") or "19:00"),  # the day's bundle (B-30, E-126)
        "quietFrom": str(p.get("quiet_from") or ""),  # own quiet hours, level 4 (E-290)
        "quietUntil": str(p.get("quiet_until") or ""),
        "briefing": {k: bool(briefing.get(k, True)) for k in PARTS},
        "bedtimeCheck": bool(p.get("bedtime_check", True)),
        "reportDay": int(p.get("report_day", 6)),
        "spokenPerDay": int(p.get("spoken_per_day", 6)),
        "address": p.get("address", "je"),
        "tour": [int(x) for x in p.get("tour", []) if isinstance(x, int)],  # levels toured (E-249)
    }


def _yes_no(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


async def day_summary(hm: HouseMind, lang: str) -> str:
    """Level 2: what HouseMind did today, in one or two sentences."""
    now = hm.clock.now()
    local = _local(hm, now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(now.tzinfo)
    rows = await hm.db.fetchall(
        "SELECT entity_id, source_layer FROM decisions WHERE outcome = 'executed' AND at >= ?",
        (start.isoformat(),),
    )
    counts = {"light": 0, "cover": 0, "climate": 0}
    for row in rows:
        if row["source_layer"] in ("emergency", "person_answer"):
            continue
        domain = str(row["entity_id"]).split(".", 1)[0]
        if domain in counts:
            counts[domain] += 1
    t = hm.languages.text
    parts = [t(lang, f"ui.day.{d}", count=n) for d, n in counts.items() if n]
    # What the cameras saw (E-190): "Pakket bezorgd om 14:02."
    seen = " ".join(await security.day_lines(hm, lang, start, local.tzinfo))
    if not parts:
        return f"{t(lang, 'ui.day.quiet')} {seen}".strip()
    return f"{t(lang, 'ui.day.did', what=_join(parts, t(lang, 'ui.and')))} {seen}".strip()


def house_part(hm: HouseMind, now: datetime) -> str | None:
    """The part of the day as the house lives it – it woke, it sleeps, its learned rhythm
    (E-455) – or None when only the clock could say it."""
    part = hm.world.part_now(now) if hm.world is not None else None
    return part.kind if part is not None and part.how != "clock" else None


async def morning_card(hm: HouseMind, viewer: Viewer, lang: str) -> str | None:
    """The morning briefing on the home screen, in the house's morning (E-107): from waking
    up until its day begins, as the house lives it (code QA 3, punt 6); 5:00 – 11:00 only
    when it has no rhythm yet."""
    if hm.world is None:
        return None
    now = hm.clock.now()
    snap = hm.world.look(now)  # a screen only looks: the house mode stays (design 70.1)
    part = house_part(hm, now)
    morning = part == "morning" if part is not None else 5 <= snap.local.hour < 11
    if snap.mode.value != "home" or not morning:
        return None
    chosen = viewer.preferences.get("briefing")
    parts = {k for k in PARTS if not isinstance(chosen, dict) or chosen.get(k, True)}
    if viewer.tablet:
        parts = hm._home_prefs()[0]  # a shared screen: what everyone at home wants
    if not parts:
        return None
    return await hm.briefing(lang, parts, snap)


def tablet_view(hm: HouseMind, area_id: str, lang: str) -> dict[str, Any] | None:
    """A wall tablet (E-134): its room first, awake when someone is there, dark at night."""
    if hm.world is None or area_id not in hm.model.areas:
        return None
    t = hm.languages.text
    snap = hm.world.look(hm.clock.now())  # a screen only looks (design 70.1)
    # Dark in the house's night (code QA 3, punt 6); 23:00 – 6:00 only without a rhythm yet.
    part = house_part(hm, snap.now)
    clock_night = snap.local.hour >= 23 or snap.local.hour < 6
    night = snap.mode.value == "sleep" or (part == "night" if part is not None else clock_night)
    weather = None
    found = sorted(e for e in hm.world.states if e.startswith("weather."))
    if found:
        state = hm.world.states[found[0]]
        temp = state.attributes.get("temperature")
        if isinstance(temp, int | float) and state.state not in ("unknown", "unavailable"):
            weather = t(
                lang,
                "tablet.weather",
                temp=_fmt_number(hm, float(temp), lang, 0),
                condition=t(lang, f"weather.{state.state}"),
            )
    room = next((r for r in rooms(hm, lang, 2) if r["id"] == area_id), None)
    return {
        "area": area_id,
        "name": hm.model.areas[area_id].name,
        "awake": snap.occupied(area_id),
        "night": night,
        "weather": weather,
        "summary": room["summary"] if room else "",
        "scenes": room["scenes"] if room else [],
        "doorbell": security.doorbell_view(hm),  # 30 seconds, kiosk mode only (E-187)
        "alarm": alarm_reaction.screen(hm, lang),  # red while it goes on (E-544)
    }


def day_note(hm: HouseMind, lang: str) -> dict[str, Any] | None:
    """Today is a day that differs (E-89): which kind, and how HouseMind knows."""
    note = hm.days.get(_local(hm, hm.clock.now()).date())
    if note is None:
        return None
    kind, source = note
    t = hm.languages.text
    return {
        "kind": kind,
        "source": source,
        "text": t(lang, f"days.line.{source}", kind=t(lang, f"days.kind.{kind}")),
    }


def _join(parts: list[str], and_word: str) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {and_word} " + parts[-1]


def _members(hm: HouseMind, entity_id: str) -> dict[str, Any]:
    """A group as Apparaten en entiteiten knows it (E-312): its members, and whether it lives in
    the radio network. The network shows it as one dot for them (E-515)."""
    members = hm.model.groups.get(entity_id)
    if not members:
        return {}
    return {"members": list(members), "radio": hm.model.group_source.get(entity_id) in groups.RADIO}


def rooms(hm: HouseMind, lang: str, level: int) -> list[dict[str, Any]]:
    assert hm.world is not None
    t = hm.languages.text
    snap = hm.world.look(hm.clock.now())  # a screen only looks (design 70.1)
    by_area: dict[str, list[Any]] = {}
    for entity in hm.model.entities.values():
        found = hm.model.area_of(entity.entity_id)
        if found is not None:
            by_area.setdefault(found.id, []).append(entity)
    out = []
    for area in sorted(hm.model.areas.values(), key=lambda a: a.name):
        occupancy = snap.occupancy.get(area.id)
        occupied = bool(occupancy and occupancy.occupied)
        devices = []
        lights_on: list[float] = []
        # Only what measures the room (E-209): not a socket's or a chip's own warmth.
        temperature = hm.temperatures.of_room(area.id, hm.world.states)
        covers_closed = covers_total = 0
        leds = hm.world.status_led_ids
        functions = hm.world.functions
        for entity in by_area.get(area.id, []):
            state = hm.world.states.get(entity.entity_id)
            if state is None:
                continue
            if entity.domain == "light" and state.state == "on" and entity.entity_id not in leds:
                brightness = state.attributes.get("brightness")
                lights_on.append(
                    float(brightness) * 100 / 255 if isinstance(brightness, int | float) else 100.0
                )
            if entity.domain == "cover" and entity.device_class not in NOT_WINDOW_COVERS:
                covers_total += 1
                covers_closed += state.state == "closed"
            if entity.domain in ROOM_DOMAINS and not entity.hidden:
                state_text = t(lang, f"ui.state.{state.state}")
                function = functions.get(entity.entity_id, UNKNOWN)
                devices.append(
                    {
                        "id": entity.entity_id,
                        "name": entity.name,
                        "domain": entity.domain,
                        "state": state.state if state_text.startswith("ui.state.") else state_text,
                        "on": state.state in ("on", "open", "heat", "playing"),
                        # A device's own light or one of its settings (E-426): no lamp, and
                        # nothing HouseMind switches.
                        "kind": "status_led"
                        if entity.entity_id in leds or function == "status_light"
                        else "setting"
                        if entity_role(entity) is EntityRole.SETTING or function == "setting"
                        else "",
                        # What it is, in a word (E-427, E-432), and whether only AI said so.
                        "function": t(lang, f"ui.function.{function}"),
                        "fromAi": hm.world.function_sources.get(entity.entity_id) == "ai",
                        "device": entity.device_id or "",
                        **device_icon(hm, entity, state),  # E-510
                        **_members(hm, entity.entity_id),  # E-515
                    }
                )
        zones = list(occupancy.zones) if occupancy else []
        # Someone in a part of the room says where (E-215): "Someone there (Bed)".
        seen = [z.name for z in zones if z.on and z.part]
        if occupied and seen:
            parts = [t(lang, "ui.room.occupied_in", zones=", ".join(seen))]
        else:
            parts = [t(lang, "ui.room.occupied" if occupied else "ui.room.empty")]
        if lights_on:
            parts.append(t(lang, "ui.room.light", pct=round(max(lights_on))))
        if covers_total and covers_closed == covers_total:
            parts.append(t(lang, "ui.room.covers_closed"))
        if temperature is not None:
            parts.append(f"{_fmt_number(hm, temperature, lang)} °C")
        out.append(
            {
                "id": area.id,
                "name": area.name,
                "floor": area.floor_id,
                "role": hm.area_roles.get(area.id, "other"),
                "icon": icons.area_icon(area, hm.area_roles.get(area.id)),  # E-510
                "active": _active(devices),
                "occupied": occupied,
                "dark": bool(snap.dark.get(area.id)),
                "lightsOn": len(lights_on),
                "temperature": temperature,
                "summary": " · ".join(parts),
                "devices": devices,
                "groups": _by_device(hm, devices),
                "zones": [_zone(hm, lang, area.id, z) for z in zones],
                "scenes": [
                    {"id": sc.id, "name": hm.scene_name(sc, lang), "source": sc.source}
                    for sc in hm.scenes(area.id)
                ],
            }
        )
    return out


def _active(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What is on in a room, for its card (E-510): the lamps first, the brightest first, then
    what plays or runs – each as its icon and what that shows."""
    on = [d for d in devices if d["on"] and not d["kind"] and d["domain"] != "cover"]
    on.sort(key=lambda d: (d["icon"] != "lamp", -(d.get("pct") or 0), d["name"]))
    keys = ("icon", "pct", "closed", "level")
    return [{k: d[k] for k in keys if k in d} | {"name": d["name"]} for d in on]


def _by_device(hm: HouseMind, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A room's entities per device (E-426): the device's name and type, its entities, its
    settings last. What has no device comes last, without a name."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in devices:
        groups.setdefault(item["device"], []).append(item)
    out: list[dict[str, Any]] = []
    for device_id, items in groups.items():
        device = hm.model.devices.get(device_id)
        name = device.name if device is not None else ""
        model = (device.model or "") if device is not None else ""
        out.append(
            {
                "id": device_id,
                "name": name,
                # The type only when the name does not say it already.
                "model": model if model and model.lower() not in name.lower() else "",
                # Ids into the room's ``devices``, not the rows again (code QA 3, punt 16).
                "items": [i["id"] for i in sorted(items, key=lambda i: i["kind"] == "setting")],
            }
        )
    return sorted(out, key=lambda g: (not g["name"], g["name"].lower()))


def _zone(hm: HouseMind, lang: str, area_id: str, zone: Any) -> dict[str, Any]:
    """One zone of a presence sensor in the room view: who is there, and what follows it."""
    t = hm.languages.text
    notes = []
    if zone.sensor_area != area_id and zone.sensor_area in hm.model.areas:
        notes.append(t(lang, "ui.room.zone_from", room=hm.model.areas[zone.sensor_area].name))
    if zone.bed:
        notes.append(t(lang, "ui.room.zone_bed"))
    elif zone.lamps:
        m = hm.model
        names = [  # what HouseMind switches: a group that steers, or a lamp on its own
            m.entities[e].name for e in zone.lamps if e in m.entities and m.unit(e) == e
        ]
        notes.append(t(lang, "ui.room.zone_lamps", lamps=_join(names, t(lang, "ui.and"))))
    return {
        "id": zone.entity_id,
        "name": zone.name,
        "on": zone.on,
        "state": t(lang, "ui.room.zone_someone" if zone.on else "ui.room.zone_nobody"),
        "note": " · ".join(notes),
    }


def floors(hm: HouseMind) -> list[dict[str, Any]]:
    out = []
    for floor in sorted(hm.model.floors.values(), key=lambda f: -(f.level or 0)):
        out.append(
            {
                "id": floor.id,
                "name": floor.name,
                "rooms": sorted(a.id for a in hm.model.areas.values() if a.floor_id == floor.id),
            }
        )
    loose = sorted(a.id for a in hm.model.areas.values() if a.floor_id not in hm.model.floors)
    if loose:
        out.append({"id": "_other", "name": "", "rooms": loose})
    return out


async def notifications(
    hm: HouseMind, lang: str, admin: bool = False, user_id: str | None = None
) -> list[dict[str, Any]]:
    items = []
    for row in await hm.notifications():
        item = notice_item(hm, lang, dict(row), admin, user_id)
        if item is not None and not item.get("hidden"):  # put off or switched off (E-372)
            items.append(item)
    return items


def muted_list(hm: HouseMind, lang: str) -> list[dict[str, Any]]:
    """What an administrator switched off for the house with "Niet meer tonen" (E-412)."""
    if hm.router is None:
        return []
    out = []
    for key, label in hm.router.muted().items():
        text = hm.languages.text(lang, str(label.get("text_key")), **(label.get("params") or {}))
        out.append({"key": key, "text": text})
    return sorted(out, key=lambda m: m["text"].casefold())


# Where a finding stands, in the words of the page (E-524): Te bevestigen, Bevestigd, Klopt niet.
KNOWLEDGE_STATE = {SUGGESTED: "open", CONFIRMED: "yes", REJECTED: "no"}


def knowledge_waiting(hm: HouseMind) -> int:
    """How many lines wait in Wat ik weet (E-524): the number on its tab in Brein, the same
    lines as Te bevestigen there."""
    return sum(
        1
        for (kind, subject, field), f in hm.knowledge.findings.items()
        if f.status == SUGGESTED
        and field != "summary"
        and (subject in hm.model.areas if kind == "area" else subject in hm.model.entities)
        and hm.still_open(f)
    )


def house_knowledge(hm: HouseMind, lang: str) -> dict[str, Any]:
    """Wat ik weet in Brein (E-524, B-448), for administrators on Betrokken and Expert: every
    finding with where it stands – Te bevestigen (also what was derived from behaviour, E-203),
    Bevestigd and Klopt niet –, its room and its device, so that the page can group per room
    and fold per device; and the scenes and scripts, only to read. A suggestion the house
    outgrew waits silently (``still_open``); an answer always shows, so it can be taken back
    (E-525)."""
    rows = [
        r
        for (_, _, field), f in sorted(hm.knowledge.findings.items())
        if field != "summary"
        and (f.status != SUGGESTED or hm.still_open(f))
        and (r := _knowledge_row(hm, lang, f)) is not None
    ]
    t = hm.languages.text
    return {
        "ai": hm.ai.status() == "on",
        "busy": hm.reading_now(),
        "recognised": hm.recognised_devices(),
        "rows": rows,
        "functions": [
            {"id": f, "name": t(lang, f"ui.function.{f}")} for f in sorted(FUNCTIONS - {UNKNOWN})
        ],
        "described": len(hm.knowledge.of("automation", "summary")),
        "roles": [{"id": r, "name": t(lang, f"ui.knowledge.room.{r}")} for r in ROLE_ORDER],
        "switchRoles": [
            {"id": r, "name": t(lang, f"ui.knowledge.switch.{r}")} for r in SWITCH_CHOICES
        ],
        "scenes": scenes_scripts(hm, lang) if hm.gate and hm.world else [],
    }


def _knowledge_row(hm: HouseMind, lang: str, finding: Finding) -> dict[str, Any] | None:
    """One line of the page: what it is about, what HouseMind thinks, where that came from,
    how sure and why, and what Klopt means for it."""
    t = hm.languages.text
    value, field = finding.value, finding.field
    area = hm.model.areas.get(finding.subject) if finding.kind == "area" else None
    entity = hm.model.entities.get(finding.subject) if finding.kind == "entity" else None
    if area is None and entity is None:
        return None
    name = area.name if area is not None else entity.name if entity is not None else ""
    room = area if area is not None else hm.model.area_of(finding.subject)
    device = (
        hm.model.devices.get(entity.device_id)
        if entity is not None and entity.device_id is not None
        else None
    )
    role = str(value.get("role") or "")
    options: list[dict[str, str]] = []
    unknown = field == "role" and area is not None and role == "other"
    if unknown:  # a room HouseMind does not know (E-486): choose, or Geen van deze
        open_ = finding.status == SUGGESTED
        what = t(lang, "ui.knowledge.room_unknown" if open_ else "ui.knowledge.none_of_these")
        note = t(lang, "ui.knowledge.room_unknown_note") if open_ else ""
    elif field == "role" and area is not None:
        what, note = t(lang, f"ui.knowledge.room.{role}"), t(lang, "ui.knowledge.room_note")
    elif field == "role":
        what = t(lang, f"ui.knowledge.switch.{role}")
        if what == f"ui.knowledge.switch.{role}":  # a role the catalogue added later
            what = role.replace("_", " ")
        note = (
            t(lang, "ui.knowledge.switch_note_other")
            if role == OTHER_DEVICE
            else t(lang, "ui.knowledge.switch_note", what=what)
        )
    elif field == "temperature":
        sensor = hm.model.entities.get(str(value.get("sensor")))
        if sensor is None:
            return None
        what, note = t(lang, "ui.knowledge.what.temperature", sensor=sensor.name), ""
        options = [
            {"id": e, "name": hm.model.entities[e].name}
            for e in value.get("options", [])
            if e in hm.model.entities
        ]
    elif field == "measures":
        what, note = t(lang, "ui.knowledge.what.measures"), t(lang, "ui.knowledge.measures_note")
    elif field == "function":  # what it is (E-427, design 51)
        what = t(lang, f"ui.function.{value.get('function')}")
        note = t(lang, "ui.knowledge.function_note")
    else:  # opening, status_led, one_door (E-504): yes or no
        yes = "yes" if value.get("yes") else "no"
        what, note = t(lang, f"ui.knowledge.what.{field}_{yes}"), ""
    if field in ("temperature", "opening", "status_led", "one_door"):
        note = t(lang, "ui.knowledge.doubt_note")
    out: dict[str, Any] = {
        "id": finding.key,
        "kind": finding.kind,
        "field": field,
        "label": t(lang, f"ui.knowledge.kind.{finding.kind if field == 'role' else field}"),
        "name": name,
        "what": what,
        "role": role,
        "source": finding.source,
        "fromAI": finding.from_ai,
        "note": note,
        "state": KNOWLEDGE_STATE.get(finding.status, "open"),
        "since": finding.since,
        "room": room.name if room is not None else "",
        "roomId": room.id if room is not None else "",
    }
    if device is not None:
        out["device"], out["deviceId"] = device.name, device.id
        if device.model:
            out["deviceModel"] = device.model
    # What HouseMind thought itself, under a person's own answer (E-525).
    was = finding.value.get("was") if finding.source == "person" else None
    if isinstance(was, dict) and isinstance(was.get("value"), dict):
        source = str(was.get("source") or "rules")
        guess = _knowledge_row(hm, lang, replace(finding, value=was["value"], source=source))
        if guess is not None:
            out["was"] = guess["what"]
    if unknown and finding.status == SUGGESTED:
        out["unknown"] = True  # no Klopt: only the list, with Geen van deze
    why = _knowledge_why(hm, lang, finding)
    if why:
        out["why"] = why
    if finding.certainty is not None:
        out["certainty"] = round(finding.certainty * 100)
    if options:
        out["options"] = options
    return out


def _knowledge_why(hm: HouseMind, lang: str, finding: Finding) -> str | None:
    """Why HouseMind thinks so: AI's own sentence, or – derived from behaviour or where the
    rules doubt – in HouseMind's own words, in the reader's language."""
    t = hm.languages.text
    value = finding.value
    if finding.source == "behaviour":
        facts = dict(value.get("facts") or {})
        lamp = hm.model.entities.get(str(facts.get("lamp")))
        if lamp is not None:
            facts["lamp"] = lamp.name
        for key in ("hours", "kw"):
            if isinstance(facts.get(key), float):
                facts[key] = hm.languages.number(lang, facts[key])
        return t(lang, f"ui.knowledge.why.{value.get('rule')}", **facts)
    if finding.source == "rules" and finding.field == "temperature":
        sensor = hm.model.entities.get(str(value.get("sensor")))
        return t(lang, "ui.knowledge.why.temperature", sensor=sensor.name if sensor else "")
    if finding.source == "rules":
        yes = "yes" if value.get("yes") else "no"
        return t(lang, f"ui.knowledge.why.{finding.field}_{yes}")
    why = value.get("why")
    return why if isinstance(why, str) and why else None


def _does(hm: HouseMind, entity_id: str) -> dict[str, Any] | None:
    """What an automation or script does in plain words, from AI (B-346): only to read."""
    return does_view(hm, entity_id)


def _proof_labels(hm: HouseMind, lang: str) -> dict[str, str]:
    t = hm.languages.text
    return {
        "yes": t(lang, "notify.answer.from_now_yes"),
        "later": t(lang, "notify.answer.later"),
        "no": t(lang, "notify.answer.not_ever"),
    }


def notice_item(
    hm: HouseMind,
    lang: str,
    row: dict[str, Any],
    admin: bool,
    user_id: str | None = None,
    full: bool = False,
) -> dict[str, Any] | None:
    """One notification as the app shows it, with its answers while it is open (also in the
    logbook, E-263); None when it is not for this person: a message for administrators only
    for them, a message for one person only for that person (20.5). An emergency is for all.
    Whether this person put it off or the house switched it off ("hidden", E-372, E-412); for
    an administrator also what "Niet meer tonen" offers ("mute") and what can be shown again
    ("unmute"). ``full``: every line of the camera message, also the ones not wanted
    (Logboek)."""
    topic = str(row["topic"])
    concerns = str(row.get("concerns") or "home")
    if row["kind"] != "emergency":
        if concerns == "admins" and not admin:
            return None
        if concerns.startswith("user:") and concerns[5:] != user_id:
            return None
    params = json.loads(row["params"] or "{}")
    actions: list[str] = []
    labels: dict[str, str] = {}  # a button's own words: an alarm's name (E-311)
    proposal = None
    if topic.startswith("proposal:"):
        proposal = int(topic.split(":", 1)[1])
        actions = ["do", "not_now", "from_now", "never"]
    elif topic.startswith(READY):
        # One yes (E-460): whoever it concerns, but it says for good, so not on a wall tablet.
        actions = ["yes", "later", "no"] if user_id is not None else []
        labels = _proof_labels(hm, lang)
    elif topic.startswith(("automation:", "restore:")):
        if not admin:
            return None  # only an administrator decides about automations (E-42, E-255)
        actions = ["yes", "no"]
        if str(row["text_key"]) in PROOF_KEYS:  # from proof (E-460): with "Not yet"
            actions = ["yes", "later", "no"]
            labels = _proof_labels(hm, lang)
        elif topic.startswith("automation:"):  # 'Ja, zet uit' – the others a plain ja or nee
            t = hm.languages.text
            labels = {
                "yes": t(lang, "notify.answer.turn_off"),
                "no": t(lang, "notify.answer.keep_on"),
            }
    elif topic.startswith("house:cooking:") and str(row["text_key"]).endswith("_off"):
        # "Zal ik hem uitzetten?" (E-61, B-327): Uitzetten or Laat aan, for anyone at home.
        t = hm.languages.text
        actions = ["yes", "no"]
        labels = {"yes": t(lang, "notify.answer.turn_off"), "no": t(lang, "notify.answer.keep_on")}
    elif topic.startswith("rule_suggest:"):
        if not admin:
            return None  # a house rule is an administrator's (E-417)
        actions = ["yes", "no"]
    elif topic.startswith("unusual:"):
        # Something unusual (E-416): 'Zal ik hem uitzetten?', for anyone at home.
        t = hm.languages.text
        actions = ["yes", "no"]
        labels = {"yes": t(lang, "notify.answer.turn_off"), "no": t(lang, "notify.answer.keep_on")}
    elif topic.startswith(("label_lamp:", "label_system:")):
        if not admin:
            return None  # a lamp or 'met rust laten' is for an administrator (E-345)
        actions = ["yes", "no"]
    elif topic.startswith("knowledge:"):
        if not admin:
            return None  # what HouseMind knows of the house: an administrator's (B-360)
        t = hm.languages.text
        actions = ["yes", "no"]
        labels = {"yes": t(lang, "ui.knowledge.yes"), "no": t(lang, "ui.knowledge.no")}
    elif topic == "vacation_suggestion":
        actions = ["yes", "no"]  # "Zijn jullie op vakantie?" (E-286)
    elif topic.startswith("alarm_arm:"):
        # "Alarm op afwezig zetten?" (E-188); with a code it opens in HA, so no yes here.
        actions = [] if str(row["text_key"]).endswith("_code") else ["plan_yes", "plan_no"]
    elif topic.startswith("alarm_self_offer:"):
        if not admin:
            return None  # the standing yes to switch the alarm on: an administrator's (E-540)
        t = hm.languages.text
        actions = ["yes", "no"]
        labels = {
            "yes": t(lang, "notify.answer.from_now_yes"),
            "no": t(lang, "notify.answer.not_ever"),
        }
    elif topic == "alarm_choice":
        if not admin:
            return None  # only an administrator chooses which alarm it is (B-67, E-311)
        actions, labels = security.choice_buttons(hm)
    elif topic.startswith("thumbs:"):
        actions = ["good", "bad"]  # "Was dit goed?" (E-298)
    elif topic in ("calendar_consent", "battery_remote", "battery_calibrate"):
        if not admin:
            return None
        # One yes for the calendar (20.19), for a battery's remote control (E-338) or for
        # calibrating its controls (E-339).
        actions = ["yes", "no"]
    elif topic == "care_consent":
        actions = ["yes", "no"]  # the person care is about says it themselves (QA 2, M-8)
    elif topic.startswith("room_kind:"):
        if not admin:
            return None  # what kind of room it is: an administrator's (E-486)
        t = hm.languages.text
        actions = ["choose", "no"]  # Kiezen opens the list, Nu niet closes it
        labels = {"choose": t(lang, "notify.answer.choose"), "no": t(lang, "notify.answer.not_now")}
    elif topic.startswith("tidy:"):
        if not admin:
            return None  # Home Assistant netjes is for an administrator (E-361)
        # 'Bekijken' opens the page (B-245); no Later for information, as elsewhere (QA 3).
        later = [] if row["kind"] == "info" else ["snooze"]
        actions = ["view", *later, "dismiss"]
    elif row["kind"] == "info":
        actions = ["dismiss"]  # putting off a message for information helps nobody (QA 3)
    elif row["kind"] != "emergency":
        actions = ["snooze", "dismiss"]
    open_ = row.get("resolved_at") is None
    text_key = str(row["text_key"])
    extra: dict[str, Any] = {}
    skip: frozenset[str] = frozenset()
    if hm.router is not None and row["kind"] != "emergency":
        now = hm.clock.now()
        if not full:
            skip = hm.router.skipped(user_id, now)
        if hidden := hm.router.hidden(user_id, topic, params, now):
            extra["hidden"] = hidden
            until = hm.router.later_of(user_id, kind_key(topic), now)
            if hidden == "later" and until is not None:
                extra["laterUntil"] = _local(hm, until).strftime("%H:%M")
        if admin and user_id:  # the house's choice, made by an administrator (E-412)
            extra.update(_mute_choices(hm, lang, topic, text_key, params, open_, actions))
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "text": notice_text(hm.languages, lang, text_key, params, skip),
        "at": _local(hm, datetime.fromisoformat(row["at"])).strftime("%H:%M"),
        "actions": actions if open_ else [],
        "proposal": proposal,
        **({"labels": labels} if labels else {}),
        **extra,
        **_notice_icon(hm, topic, params),
    }


def _mute_choices(
    hm: HouseMind,
    lang: str,
    topic: str,
    text_key: str,
    params: dict[str, Any],
    open_: bool,
    actions: list[str],
) -> dict[str, Any]:
    """ "Niet meer tonen" (E-372, E-412): for a plain message its own subject; for the cameras
    each line – "een voertuig bij Tuin" – or all of them. And what of it can be shown again."""
    assert hm.router is not None
    t = hm.languages.text
    muted = hm.router.muted()
    if topic == CAMERA_BUNDLE:
        choices = [
            {
                "key": part_key(part),
                "text": t(lang, f"notify.camera.mute.{part.get('kind')}", where=part.get("where")),
                "label": {
                    "text_key": f"notify.camera.mute.{part.get('kind')}",
                    "params": {"where": part.get("where")},
                },
            }
            for part in params.get("parts") or []
        ]
        every: dict[str, Any] = {"text_key": "notify.camera.mute.all", "params": {}}
        choices.append({"key": ALL_CAMERAS, "text": t(lang, every["text_key"]), "label": every})
    else:
        own = {"text_key": text_key, "params": params}
        choices = [
            {"key": topic, "text": notice_text(hm.languages, lang, text_key, params), "label": own}
        ]
    out: dict[str, Any] = {}
    shown_again = [{"key": c["key"], "text": c["text"]} for c in choices if c["key"] in muted]
    if shown_again:
        out["unmute"] = shown_again
    offered = [c for c in choices if c["key"] not in muted]
    every_off = topic == CAMERA_BUNDLE and ALL_CAMERAS in muted
    plain = actions in (["snooze", "dismiss"], ["dismiss"]) or topic.startswith("tidy:")
    if open_ and plain and offered and not every_off:
        out["mute"] = offered
    return out


async def proposals(
    hm: HouseMind, lang: str, level: int, asked: list[Any] | None = None
) -> list[dict[str, Any]]:
    assert hm.explainer is not None
    out = []
    # One card per skill and room (E-499): the other lamps of the question go with its answer
    # (runtime.answer, same_question), so they are no cards of their own.
    together: dict[tuple[str, str], list[Any]] = {}
    shown: list[Any] = []
    for proposal in asked if asked is not None else await hm.open_proposals():
        key = (proposal.skill or "", proposal.area_id or "")
        if all(key) and key in together:
            together[key].append(proposal)
            continue
        together[key] = [proposal]
        shown.append(proposal)
    for proposal in shown:
        decision = _proposal_decision(hm, proposal)
        lines = hm.explainer.explain(decision, level, lang, hm.clock.now())
        domain = proposal.entity_id.split(".", 1)[0]
        area = hm.model.areas.get(proposal.area_id or "") or hm.model.area_of(proposal.entity_id)
        stand = hm.explainer.stand_question(lang, decision)
        group = together[(proposal.skill or "", proposal.area_id or "")]
        extra: dict[str, Any] = {}
        if stand is not None and area is not None:
            extra["lamps"] = _proposal_lamps(hm, lang, group, area.id)
        out.append(
            {
                **extra,
                "id": proposal.id,
                "question": stand[0] if stand else hm.explainer.question(lang, decision),
                "reason": stand[1]
                if stand
                else " ".join(lines[2:] if len(lines) > 2 else lines[1:]),
                "domain": hm.languages.text(lang, f"ui.domain.{domain}"),
                "area": area.name if area else "",
                "canRule": bool(proposal.skill and proposal.area_id),
                # Its path in the network while it waits (E-439): room, device and time.
                "areaId": area.id if area else None,
                "entity": proposal.entity_id,
                "at": _local(hm, proposal.at).strftime("%H:%M"),
                **icon_look(hm, proposal.entity_id),  # E-511
            }
        )
    return out


def _proposal_lamps(
    hm: HouseMind, lang: str, group: list[Any], area_id: str
) -> list[dict[str, Any]]:
    """What a stand does to each lamp of the room (E-499): its own percentage and colour, off,
    or as it is – also the lamps that are on and that the question leaves alone."""
    t = hm.languages.text
    levels = hm.light_levels
    out: list[dict[str, Any]] = []
    named: set[str] = set()
    for proposal in group:
        named.add(proposal.entity_id)
        entity = hm.model.entities.get(proposal.entity_id)
        item: dict[str, Any] = {"name": entity.name if entity else proposal.entity_id}
        data = proposal.data or {}
        if proposal.action.endswith("turn_off"):
            item["text"] = t(lang, "ui.proposal.goes_off")
            item["off"] = True
        else:
            if "brightness_pct" in data:
                item["pct"] = data["brightness_pct"]
            if "color_temp_kelvin" in data:
                item["kelvin"] = data["color_temp_kelvin"]
            if "color_name" in data:
                item["colour"] = data["color_name"]
                item["text"] = t(lang, f"stands.colour.{data['color_name']}")
            elif "color_temp_kelvin" not in data and levels.can(proposal.entity_id) == "dim":
                item["text"] = t(lang, "ui.proposal.only_brightness")
        out.append(item)
    assert hm.world is not None
    for lamp in hm.stand_lamps(area_id):
        state = hm.world.states.get(lamp)
        if lamp in named or state is None or state.state != "on":
            continue
        entity = hm.model.entities.get(lamp)
        out.append(
            {
                "name": entity.name if entity else lamp,
                "text": t(lang, "ui.proposal.stays"),
                "stays": True,
            }
        )
    return out


def _proposal_decision(hm: HouseMind, proposal: Any) -> Decision:
    request = proposal.request(Layer.LEARNING, proposal.at, None)
    return Decision(request, Outcome.PROPOSED, "proposed", Step.PROPOSE, (), proposal.at)


RHYTHM_DAYS = (DayType.WORKDAY, DayType.FREE, DayType.HOLIDAY)
OTHER_DAYS = 28  # working from home: the last four weeks (E-367)


def rhythm(hm: HouseMind, lang: str, level: int = 3) -> dict[str, Any]:
    """The rhythm of the house (E-366 – E-368): per type of day a bar of the day and the moments
    in words, out of the ordinary, and – when the house never slept – why not. Expert also sees
    how many days each time held."""
    t = hm.languages.text
    days = []
    for day in RHYTHM_DAYS:
        moments = _rhythm_moments(hm, lang, level, day)
        if moments:
            # The bar also while getting up and going to bed are still being learned, with the
            # unsure stretches striped over it (E-507, B-433).
            stretches = hm.rhythm.day(day, learning=True)
            bar = [{"kind": k, "from": a, "to": b} for k, a, b in stretches]
            unsure = [{"from": a, "to": b} for a, b in hm.rhythm.unsure(day)] if bar else []
            label = t(lang, f"rhythm.day.{day.value}")
            days.append(
                {
                    "key": day.value,
                    "label": label,
                    "bar": bar,
                    "unsure": unsure,
                    "learning": bool(bar) and not hm.rhythm.day(day),
                    "moments": moments,
                }
            )
    slept = any(hm.rhythm.bed.values()) or any(hm.rhythm.wake.values())
    note = None if slept else t(lang, "rhythm.learning.no_sleep")
    return {
        "now": _rhythm_now(hm, lang),
        **_rhythm_today(hm),
        "days": days,
        "other": _rhythm_other(hm, lang),
        "note": note,
    }


def _rhythm_today(hm: HouseMind) -> dict[str, Any]:
    """Where today is on the bar (E-507): its type of day and the minute now, in the house's
    own time (so also in the time machine) – nothing on a day that is different (E-89)."""
    local = _local(hm, hm.clock.now())
    if local.date().isoformat() in hm.days.notes:
        return {}
    country = hm.config.country if hm.config else None
    today = day_type(local.date(), country).value
    return {"today": today, "minute": local.hour * 60 + local.minute}


def _rhythm_now(hm: HouseMind, lang: str) -> str | None:
    """The part of the day now, from the house's rhythm, since when and how HouseMind knows it
    (E-455): "Nu: ochtend, sinds 06:42. Toen werd het huis wakker." """
    found = hm.world.part_now(hm.clock.now()) if hm.world is not None else None
    if found is None:
        return None
    t = hm.languages.text
    part = t(lang, f"rhythm.now.part.{found.kind}")
    since = _local(hm, found.since).strftime("%H:%M")
    line = t(lang, "rhythm.now.line", part=part, time=since)
    return f"{line} {t(lang, f'rhythm.now.how.{found.how}')}"


def _rhythm_moments(hm: HouseMind, lang: str, level: int, day: DayType) -> list[dict[str, Any]]:
    t = hm.languages.text
    status = {k: hm.rhythm.status(k, day) for k in ("wake", "leave", "back", "bed")}
    if not status["wake"].count and not status["bed"].count:
        return []

    def clock(at: Any) -> str:
        return str(at.strftime("%H:%M"))

    def moment(key: str, value: str | None = None, of: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"key": key, "label": t(lang, f"rhythm.moment.{key}")}
        found = status[of] if of else None
        if value is not None:
            out["value"] = value
            if level >= 4 and found is not None and found.count:
                unit = "nights" if of in ("wake", "bed") else "days"
                detail = t(lang, f"rhythm.detail.{unit}", close=found.close, count=found.count)
                out["detail"] = detail
        elif found is not None:
            if found.low is not None and found.high is not None:
                spread: dict[str, Any] = {"from": clock(found.low), "to": clock(found.high)}
                out["learning"] = t(lang, "rhythm.learning.spread", **spread)
            else:
                out["learning"] = t(lang, "rhythm.learning.nights", count=found.count)
        return out

    def span(a: Any, b: Any) -> str:
        ends: dict[str, Any] = {"from": clock(a), "to": clock(b)}
        return t(lang, "rhythm.from_to", **ends)

    wake, bed = status["wake"].usual, status["bed"].usual
    stretches = {k: (a, b) for k, a, b in hm.rhythm.day(day) if k != "sleep"}
    out = [moment("wake", t(lang, "rhythm.around", time=clock(wake)) if wake else None, "wake")]
    leave, back = status["leave"].usual, status["back"].usual
    if "away" in stretches and leave and back and wake and bed:
        if "morning" in stretches:
            out.append(moment("morning", span(wake, leave)))
        out.append(moment("away", span(leave, back), "leave"))
        out.append(moment("evening", span(back, bed), "back"))
    elif wake and bed:
        out.append(moment("home", span(wake, bed)))
    out.append(moment("bed", t(lang, "rhythm.around", time=clock(bed)) if bed else None, "bed"))
    return out


def _rhythm_other(hm: HouseMind, lang: str) -> list[dict[str, Any]]:
    """Out of the ordinary (E-367): only what there is."""
    t = hm.languages.text
    out: list[dict[str, Any]] = []
    today = _local(hm, hm.clock.now()).date()
    at_home = [
        day
        for day, (kind, _) in hm.days.notes.items()
        if kind in ("home_work", "home_day")
        and 0 <= (today - datetime.fromisoformat(day).date()).days < OTHER_DAYS
    ]
    if at_home:
        value = t(lang, "rhythm.other.home_work_count", count=len(at_home))
        out.append({"key": "home_work", "label": t(lang, "rhythm.other.home_work"), "value": value})
    for key in ("vacation", "guests"):
        period = hm.rhythm.periods.get(key)
        if period:
            out.append(
                {
                    "key": key,
                    "label": t(lang, f"rhythm.other.{key}"),
                    "from": period[0],
                    "to": period[1] or None,
                }
            )
    if hm._alarm_sensors:
        value = t(lang, "rhythm.other.alarm_count", count=len(hm._alarm_sensors))
        out.append({"key": "alarm", "label": t(lang, "rhythm.other.alarm"), "value": value})
    return out


def rhythm_line(hm: HouseMind, lang: str) -> str | None:
    """Informed: one sentence about the rhythm (E-368)."""
    for day in (DayType.WORKDAY, DayType.FREE):
        wake, bed = hm.rhythm.usual("wake", day), hm.rhythm.usual("bed", day)
        if wake is not None and bed is not None:
            times: dict[str, Any] = {"wake": wake.strftime("%H:%M"), "bed": bed.strftime("%H:%M")}
            return str(hm.languages.text(lang, f"rhythm.sentence.{day.value}", **times))
    return None


def rules(hm: HouseMind, lang: str) -> list[dict[str, Any]]:
    return [
        {
            "id": rule.id,
            "text": rule_text(rule, lang, hm.languages, hm.model),
            **_rule_icon(hm, rule),
        }
        for rule in hm.rules.rules
    ]


def _rule_icon(hm: HouseMind, rule: Any) -> dict[str, Any]:
    """What a house rule is about (E-511): a device, a room, a kind of device or the house."""
    if rule.scope_type == "entity":
        return icon_look(hm, rule.scope_id) or {"icon": "device"}
    if rule.scope_type == "area" and rule.scope_id in hm.model.areas:
        area = hm.model.areas[rule.scope_id]
        return {"icon": icons.area_icon(area, hm.area_roles.get(area.id))}
    if rule.scope_type == "domain":
        return {"icon": icons.DOMAIN_ICONS.get(rule.scope_id, "device")}
    return {"icon": "home"}


GATE_STEPS = ("1_valid", "2_allowed", "3_now", "4_step", "5_sensible", "6_done")


def gate_checks(hm: HouseMind, lang: str, decision: Decision) -> list[dict[str, str]]:
    """The gate's six checks of one decision, in words (QA 3, schermen 8): passed, where it
    stopped, or not reached – for a Logboek line on Expert, instead of the decision trail."""
    t = hm.languages.text
    done = {check.name: check for check in decision.checks}
    return [
        {
            "name": t(lang, f"ui.gate_step.{name}"),
            "state": "none" if (c := done.get(name)) is None else "ok" if c.ok else "stop",
        }
        for name in GATE_STEPS
    ]


_DAY_FLOW: dict[str, Any] = {}  # the day's flow, worked out at most once a minute


FLOWS_FOR = timedelta(seconds=20)  # how long the line flows after HouseMind did something


def live_path(
    hm: HouseMind, lang: str, open_entities: set[str] | None = None
) -> dict[str, Any] | None:
    """The house in motion from Betrokken (E-439): the newest thing HouseMind did or asked
    today, as a path from its room through the brain to the device, with one line. The line
    flows in the first twenty seconds after it did something – ``flowFor``, the milliseconds
    still to go – long enough to see it when you look a moment later; the line under the network
    stays as what it did last. A question still open is not this line: it waits as its own
    dotted path.
    ``id``: the decision for 'Waarom?' beside that line, when the brain still has it."""
    assert hm.explainer is not None
    now = hm.clock.now()
    today = _local(hm, now).date()
    ids = {id(recent.decision): recent.id for recent in hm.recent.values()}
    for decision in reversed(hm.status.thoughts):
        if _local(hm, decision.at).date() != today:
            break
        if decision.outcome not in (Outcome.EXECUTED, Outcome.PROPOSED):
            continue
        r = decision.request
        if decision.outcome is Outcome.PROPOSED and r.entity_id in (open_entities or set()):
            continue  # it waits for an answer, as its own dotted line
        area = hm.model.area_of(r.entity_id)
        if area is None:
            continue
        left = FLOWS_FOR - (now - decision.at) if decision.outcome is Outcome.EXECUTED else None
        return {
            "area": area.id,
            "entity": r.entity_id,
            "outcome": decision.outcome.value,
            "at": _local(hm, decision.at).strftime("%H:%M"),
            "text": hm.explainer.thought(lang, decision, now),
            "id": ids.get(id(decision)),
            "flowFor": max(0, int(left.total_seconds() * 1000)) if left is not None else 0,
        }
    return None


def day_rooms(day: dict[str, Any], most: int = 4) -> list[dict[str, Any]]:
    """Today per room for Geïnformeerd and Betrokken (E-436): the rooms where HouseMind did,
    asked or left the most, each with a mark per decision at its time of day."""
    by_room: dict[str, list[dict[str, Any]]] = {}
    for dot in day["core"]:
        if dot["area"]:
            by_room.setdefault(dot["area"], []).append({"at": dot["at"], "outcome": dot["outcome"]})
    busiest = sorted(by_room.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:most]
    return [{"room": name, "marks": marks} for name, marks in busiest]


async def day_flow(hm: HouseMind, lang: str) -> dict[str, Any]:
    """Today, each decision as a dot at its time of day with its room – for the day ring on
    Ontzorgd and Vandaag per room (E-435, E-436). Times are parts of the local day (0 =
    midnight). Worked out at most once a minute."""
    now = hm.clock.now()
    key = f"{id(hm)}|{lang}"
    kept = _DAY_FLOW.get(key)
    if kept is not None and now - kept["at"] < timedelta(minutes=1):
        return dict(kept["data"])
    local = _local(hm, now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)

    def part(at: str) -> float:
        when = _local(hm, datetime.fromisoformat(at))
        return round(max(0.0, min(1.0, (when - midnight).total_seconds() / 86400)), 4)

    rows = await hm.db.fetchall(
        "SELECT at, outcome, entity_id FROM decisions WHERE at >= ? ORDER BY at",
        (midnight.astimezone(UTC).isoformat(),),
    )

    def room(entity_id: str) -> str:
        area = hm.model.area_of(entity_id)
        return area.name if area is not None else ""

    data = {
        "core": [
            {
                "at": part(str(r["at"])),
                "outcome": str(r["outcome"]),
                "area": room(str(r["entity_id"])),
            }
            for r in rows[-300:]
        ],
        "now": part(now.isoformat()),
    }
    _DAY_FLOW[key] = {"at": now, "data": data}
    return dict(data)


# What the brain thought (E-218), per level: how many, from how long ago, and which kinds.
MIND = {1: (1, 1), 2: (3, 3), 3: (6, 3), 4: (12, 3)}  # level → (items, hours)
# Gate reasons that are no news: the same device changed a moment ago, the situation changed,
# an unknown device. Only level 4 sees them.
MIND_NOISE = frozenset({"rate_limit", "stale_context", "unknown_entity", "unknown_device_kind"})


def mind(hm: HouseMind, lang: str, level: int) -> list[dict[str, Any]]:
    """What the brain thought, as a train of thought – saw → thought → did (E-218).
    Level 1: the last thing it did, one sentence. Level 2: the last few things it
    did or asked. Level 3: also what it considered but left, and why, with what it saw.
    Level 4: more, with the details. Only what matters: the same thought again is no news,
    and one thought about several lamps in a room is one line."""
    count, hours = MIND.get(level, MIND[4])
    now = hm.clock.now()
    groups = mind_groups(hm, level, count, now - timedelta(hours=hours))
    return [_thought(hm, lang, level, d, devices, now) for d, devices in groups]


def mind_groups(
    hm: HouseMind,
    level: int,
    count: int,
    since: datetime,
    decisions: list[Decision] | None = None,
) -> list[tuple[Decision, list[str]]]:
    """The thoughts since ``since``, newest first, as they show for this level: the same
    thought once, several lamps of one thought together, at most ``count``. ``decisions``:
    others than the brain's thoughts in memory, newest first (the logbook's own, B-180)."""
    assert hm.explainer is not None
    groups: dict[tuple[str, ...], tuple[Decision, list[str]]] = {}
    left = 0
    for decision in reversed(hm.status.thoughts) if decisions is None else decisions:
        if decision.at < since:
            break
        r, outcome = decision.request, decision.outcome
        done = outcome in (Outcome.EXECUTED, Outcome.PROPOSED)
        # Level 2 also sees what HouseMind would have done on Observe, in the would-form
        # (20.14); what the gate refused stays for levels 3 and 4.
        would = outcome is Outcome.OBSERVED
        if (level == 1 and outcome is not Outcome.EXECUTED) or (level == 2 and not (done or would)):
            continue
        code = (decision.reason or "").split(":", 1)[0]
        if level < 4 and code in MIND_NOISE:
            continue
        key = (r.skill or r.entity_id, r.area_id or "", r.action, outcome.value, code)
        if key in groups:
            devices = groups[key][1]
            if r.entity_id not in devices:
                devices.append(r.entity_id)  # the same thought about another lamp
            continue
        if len(groups) >= count:
            continue  # full: only gather more lamps for the thoughts already shown
        if not done:
            if level in (2, 3) and left >= count // 2:
                continue  # what it left or would do never crowds out what it did or asked
            left += 1
        groups[key] = (decision, [r.entity_id])
    return list(groups.values())


def _thought(
    hm: HouseMind, lang: str, level: int, decision: Decision, devices: list[str], now: datetime
) -> dict[str, Any]:
    """One line of what the brain thought; several lamps named together."""
    assert hm.explainer is not None
    r, outcome = decision.request, decision.outcome
    names = [hm.model.entities[e].name if e in hm.model.entities else e for e in devices]
    first = names[0]
    together = _join(names[::-1], hm.languages.text(lang, "ui.and"))

    def named(text: str) -> str:
        return text.replace(first, together, 1) if len(names) > 1 else text

    area = hm.model.areas.get(r.area_id or "")
    item: dict[str, Any] = {
        "at": _local(hm, decision.at).strftime("%H:%M"),
        "area": area.name if area else "",
        "outcome": outcome.value,
        "outcomeName": hm.languages.text(lang, f"ui.outcome.{outcome.value}"),
        "text": named(hm.explainer.thought(lang, decision, now)),
        **icon_look(hm, devices[0] if devices else r.entity_id),  # E-511
    }
    if level >= 2:
        seen = hm.explainer.observations(lang, decision)
        reason = hm.explainer.reason_phrase(lang, decision)
        did = named(
            hm.explainer.action_phrase(lang, decision)
            if outcome is Outcome.EXECUTED
            else hm.languages.text(
                lang,
                "mind.did_asked" if outcome is Outcome.PROPOSED else "mind.did_left",
                action=hm.explainer.wanted(lang, decision),
            )
        )
        # Cause and effect, for the flow on Huis (E-436): the first thing it saw, or else why;
        # what it asked or left without "gevraagd:" – the flow's own word says that.
        item["cause"] = seen[0] if seen else reason
        item["effect"] = (
            did
            if outcome is Outcome.EXECUTED
            else named(sentence_case(hm.explainer.wanted(lang, decision)))
        )
        if level >= 3:
            item["saw"] = seen
            item["thought"] = reason
            item["did"] = did
    if level >= 4:
        item["details"] = hm.explainer.explain(decision, 4, lang, now)[1:]
        item["checks"] = gate_checks(hm, lang, decision)
    return item


def reaction(hm: HouseMind) -> dict[str, Any]:
    """From trigger to done (L8): the middle of the last 50, and the slowest of them – one line
    in Over HouseMind on Expert (QA 3, schermen 3); processor and memory are HA's to show."""
    found = hm.status.reactions
    return {
        "ms": statistics.median(found) if found else None,
        "maxMs": max(found, default=None),
    }


# -- data on request ----------------------------------------------------------------------


STEP_KEYS = {Step.OBSERVE: "observe", Step.PROPOSE: "propose", Step.AUTONOMOUS: "autonomous"}


def devices(hm: HouseMind, lang: str) -> list[dict[str, Any]]:
    """Devices and entities with what HouseMind recognised (level 3: devices, 4: entities)."""
    assert hm.gate is not None and hm.world is not None
    t = hm.languages.text
    out = []
    for entity in sorted(hm.model.entities.values(), key=lambda e: (e.area_id or "~", e.name)):
        if entity.hidden or entity.disabled or entity.entity_category:
            continue
        if entity.domain == "automation":
            continue  # not a device: existing automations have their own place (E-42)
        match = recognise(hm.catalog, entity, hm.gate.state.roles.get(entity.entity_id))
        kind = match.kind
        area = hm.model.area_of(entity.entity_id)
        state = hm.world.states.get(entity.entity_id)
        device = hm.model.devices.get(entity.device_id) if entity.device_id else None
        controls = bool(kind and kind.actions)
        part = part_of(entity.entity_id, hm.gate.state.roles)
        # Where HouseMind itself says "other", a label from Home Assistant may say it (E-345).
        hint = entity_hint(hm.model, hm.label_hints, entity) if part == "other" else None
        if hint is not None and hint[0] in HOUSE_PARTS:
            part = hint[0]
        else:
            hint = None
        if entity.entity_id in hm.gate.state.excluded or entity.entity_id in hm.model.held:
            step = "left_alone"  # also what is in a group left alone (E-458)
        elif not controls or kind is None:
            step = "measure"
        else:  # how far HouseMind goes with its part, as on the card (E-256)
            step = STEP_KEYS[min(kind.max_step, hm.domain_step(part))]
        domain = t(lang, f"ui.domain.{entity.domain}")
        group = _group_view(hm, lang, entity.entity_id)
        steered = hm.model.steered_by.get(entity.entity_id)
        where = area.name if area else ""
        if group is not None and entity.entity_id not in hm.model.group_area and group["members"]:
            where = t(lang, "ui.devices.group.multi_room")  # a group over several rooms
        row: dict[str, Any] = {}
        if group is not None:
            row["group"] = group
        if steered and steered != entity.entity_id:
            steering = hm.model.entities.get(steered)
            row["partOf"] = {"id": steered, "name": steering.name if steering else steered}
        if entity.domain == "light" and controls:
            row["levels"] = _levels_view(hm, lang, entity.entity_id)  # E-490 – E-496
        out.append(
            {
                **row,
                "id": entity.entity_id,
                "name": entity.name,
                "area": where,
                "areaId": area.id if area else "",
                "device": device.name if device else "",
                "deviceId": entity.device_id or "",
                "typeKey": entity.domain,
                "part": part,
                "step": step,
                "domain": entity.domain if domain == f"ui.domain.{entity.domain}" else domain,
                "kind": kind.key if kind else None,
                "safety": kind.safety_class.value if kind else None,
                "maxStep": t(lang, f"steps.{kind.max_step.name.lower()}") if kind else None,
                "controls": controls,
                "excluded": entity.entity_id in hm.gate.state.excluded,
                "state": state.state if state else "",
                "detects": security.followed(hm, entity.entity_id, lang),  # level 4 (E-190)
                "labels": label_names(hm.model, entity),
                "labelHint": hint[1] if hint else None,
                "vesta": hm.names.device(entity.entity_id, lang),  # how she says it (E-411)
                **device_icon(hm, entity, state),  # E-510
            }
        )
    return out


def _levels_view(hm: HouseMind, lang: str, entity_id: str) -> dict[str, Any]:
    """A lamp's standen for Brein › Apparaten (B-413): what it can, its four standen and their
    colours, where they come from, the lamps of the same type, and its day's rhythm."""
    levels = hm.light_levels
    t = hm.languages.text
    standen, source, origin = levels.of(entity_id)
    can = levels.can(entity_id)
    low, high = levels.kelvin(entity_id)

    def name(e: str | None) -> str:
        found = hm.model.entities.get(e) if e else None
        return found.name if found else (e or "")

    steered = hm.model.steered_by.get(entity_id)
    book = hm.daylight
    rhythm: dict[str, Any] | None = None
    if can != "onoff":
        group = book.group_of(entity_id) if book.rhythms else None
        if book.rhythms:  # the integration in Home Assistant decides (E-465, E-496)
            rhythm = {
                "by": "ha",
                "name": group.title if group is not None else None,
                "ours": group is not None and book.ours(group),  # HouseMind does it (E-579)
            }
        elif book.known:  # HouseMind's own rhythm (E-471): its own tick box
            rhythm = {"by": "own", "on": standen.rhythm}
    return {
        "can": can,
        "pct": list(standen.pct),
        "colour": [dict(c) for c in standen.colour],
        "dim": standen.dim,
        "source": source,
        "sourceName": name(origin) if source in ("group", "type") else "",
        "kelvin": [low, high],
        "colours": [{"key": c, "name": t(lang, f"stands.colour.{c}")} for c in COLOURS]
        if can == "colour"
        else [],
        "edit": not (steered and steered != entity_id),
        "rhythm": rhythm,
        "today": _lamp_today(hm, lang, entity_id),
        "offWait": _off_wait_view(hm, lang, entity_id),
    }


def _off_wait_view(hm: HouseMind, lang: str, entity_id: str) -> dict[str, Any] | None:
    """Uit na … zonder beweging (E-575): this lamp's own wait, or the one its room learned or
    starts with; None where lamps never go off by themselves after people left (outside)."""
    skills = hm.skills
    area = hm.model.area_of(entity_id)
    if skills is None or area is None:
        return None
    params = skills.params.get("light_off_leave", {})
    role = hm.area_roles.get(area.id, "other")
    if role in params.get("never_in", ()):
        return None
    t = hm.languages.text
    occupancy = hm.world.occupancy if hm.world is not None else None
    sensed = occupancy.sensed(area.id) if occupancy is not None else True
    signals = occupancy.areas.get(area.id) if occupancy is not None else None
    presence = bool(signals is not None and signals.has_presence_sensor) or bool(
        occupancy is not None and occupancy.zones.in_area(area.id)
    )
    room, learned = light_off_hold(params, skills.prefs, area.id, role, False, presence)
    room = round(room * 2) / 2  # to the half minute, as Brein says it
    own = hm.light_levels.off_wait(entity_id)

    def minutes(value: float) -> str:
        return t(lang, "ui.stands.wait.minutes", n=hm.languages.number(lang, value))

    if not sensed:
        line = t(lang, "ui.stands.wait.line_none")
    elif own == 0:
        line = t(lang, "ui.stands.wait.line_now")
    elif own is not None:
        line = t(lang, "ui.stands.wait.line_own", minutes=minutes(own))
    else:
        key = "line_learned" if learned else "line_start"
        line = t(lang, f"ui.stands.wait.{key}", minutes=minutes(room))
    return {
        "minutes": own,  # None: what the room learned counts
        "room": room,
        "roomText": minutes(room),
        "learned": learned,
        "line": line,
        "sensed": sensed,
        "motionOnly": sensed and not presence,
        "max": WAIT_MAX,
    }


def _lamp_today(hm: HouseMind, lang: str, entity_id: str) -> dict[str, Any]:
    """Vandaag met deze lamp (E-500): per change the time, what it did and who went first,
    and since when HouseMind saw it (its last start, when that was today)."""
    t = hm.languages.text
    now = hm.clock.now()
    events = hm.lamp_day.today(entity_id, now)
    rows: list[dict[str, Any]] = []
    before = None
    for event in events:
        words = [t(lang, f"ui.stands.day.what.{w}") for w in what_changed(before, event)]
        what = t(lang, "ui.stands.day.join").join(words)
        rule = t(lang, f"skills.{event.skill}") if event.skill else ""
        rule = rule[:1].upper() + rule[1:]
        if event.who == "rule" and event.stand:
            stand = t(lang, f"stands.stand.{event.stand}")
            who = t(lang, "ui.stands.day.who.rule_stand", rule=rule, stand=stand)
        else:
            who = t(lang, f"ui.stands.day.who.{event.who}", rule=rule)
        rows.append(
            {
                "time": _local(hm, event.at).strftime("%H:%M"),
                "what": what[:1].upper() + what[1:],
                "who": who,
                "on": event.on,
                "pct": round(event.pct) if event.pct is not None else None,
                "kelvin": round(event.kelvin) if event.kelvin is not None else None,
            }
        )
        before = event
    started = hm.started_at
    since = _local(hm, started).strftime("%H:%M") if now - started < LAMP_DAY_KEEP else None
    return {"events": rows, "since": since}  # from early to late, as the day went


def _group_view(hm: HouseMind, lang: str, entity_id: str) -> dict[str, Any] | None:
    """A group in Apparaten en entiteiten (E-312): what kind, its members, and whether it
    switches them in one go (it steers) or only gathers."""
    source = hm.model.group_source.get(entity_id)
    if source is None:
        return None
    m = hm.model
    members = list(m.groups.get(entity_id, ()))
    steers = entity_id in m.steering
    out: dict[str, Any] = {
        "source": source,
        "sourceName": hm.languages.text(lang, f"ui.devices.group.{source}"),
        "radio": source in groups.RADIO,
        "members": members,
        "steers": steers,
    }
    # Why it only gathers (E-458): left alone, over several rooms, or another group has them.
    other = sorted({m.steered_by[e] for e in members if e in m.steered_by} - {entity_id})
    if steers or not members:
        pass
    elif entity_id in m.held or any(e in m.held for e in members):
        out["why"] = hm.languages.text(lang, "ui.devices.group.why_left")
    elif entity_id not in m.group_area:
        out["why"] = hm.languages.text(lang, "ui.devices.group.why_rooms")
    elif other:
        names = [m.entities[g].name if g in m.entities else g for g in other]
        both = _join(names, hm.languages.text(lang, "ui.and"))
        out["why"] = hm.languages.text(lang, "ui.devices.group.why_other", names=both)
    return out


def whats_new(hm: HouseMind, viewer: Viewer, lang: str) -> dict[str, Any] | None:
    """What is new in this version for this person, once, until read (E-150). Someone who
    starts with this version gets none: their introduction counts as having read it."""
    prefs = viewer.preferences
    if viewer.tablet or not prefs.get("onboarded") or prefs.get("seen_version") == __version__:
        return None
    return news.view(hm.news, lang, viewer.level, __version__)


def language_names(hm: HouseMind, lang: str) -> dict[str, str]:
    """Each language in its own name; one that no person has checked yet says so, in the
    viewer's language (B-38, QA 2 M-39)."""
    names = {}
    for code in sorted(hm.languages.languages):
        name = hm.languages.text(code, "format.language_name")
        if hm.languages.text(code, "format.reviewed") == "no":
            name = hm.languages.text(lang, "format.not_reviewed", name=name)
        names[code] = name
    return names


async def vesta_settings(hm: HouseMind, lang: str) -> dict[str, Any]:
    """Her name and voice (E-220, E-230): the voices HA has, the recommended one first –
    the voice of HouseMind's own Assist pipeline."""
    recommended = await hm.ha.assist_voice()
    chosen = await hm.setting("vesta_voice")
    language = (recommended.language if recommended else None) or lang
    engines = []
    for engine in (await hm.ha.tts_engines(language))[:8]:
        state = hm.world.states.get(engine) if hm.world else None
        name = state.attributes.get("friendly_name") if state else None
        voices = await hm.ha.tts_voices(engine, language)
        engines.append(
            {
                "id": engine,
                "name": str(name or engine),
                "voices": [{"id": v, "name": n} for v, n in voices[:60]],
            }
        )
    return {
        "name": hm.vesta_name(lang),
        "defaultName": hm.vesta_phrases.name(lang),
        "language": language,
        # The language of the house, to change later (E-320): each in its own words.
        "houseLanguage": hm._language(),
        "languages": language_names(hm, lang),
        "chosen": chosen if isinstance(chosen, dict) else None,
        "recommended": (
            {
                "engine": recommended.engine,
                "language": recommended.language,
                "voice": recommended.voice,
            }
            if recommended
            else None
        ),
        "engines": engines,
    }


async def care_view(hm: HouseMind) -> dict[str, Any] | None:
    """Care as the administrator sees it (QA 2, M-8): whether the person has an account – then
    they say yes themselves – and whether they did."""
    raw = await hm.setting("care")
    settings = CareSettings.from_json(raw)
    if settings is None:
        return raw if isinstance(raw, dict) else None
    return {
        "person": settings.person,
        "contact": settings.contact,
        "consent": settings.consent,
        "ownAccount": hm.care_user(settings.person) is not None,
        "agreed": await hm.care_agreed(settings),
    }


async def departed(hm: HouseMind) -> list[dict[str, str]]:
    """People HouseMind still remembers who are no longer a user in Home Assistant (QA 2,
    M-9): an administrator can let HouseMind forget them, as 'Vergeet mij' would."""
    users = {u.id for u in await hm.ha.users()}
    if not users:
        return []  # HA's user list could not be read: nobody counts as gone
    rows = await hm.db.fetchall("SELECT user_id, display_name FROM people ORDER BY display_name")
    return [
        {"id": str(r["user_id"]), "name": str(r["display_name"] or r["user_id"])}
        for r in rows
        if r["user_id"] not in users and r["user_id"] != "local-user"
    ]


def off_since(hm: HouseMind, part: str) -> str | None:
    """Since when a part is off – not for one nobody ever turned on (E-400)."""
    off = hm.parts_off.get(part) or {}
    return None if off.get("by") == "default" else off.get("since")


def parts_view(hm: HouseMind) -> list[dict[str, Any]]:
    """The list of parts (E-399): the parts this house has, in the order of Brein, each with
    Uit, Observeren, Voorstellen or Autonoom."""
    have = {str(p.get("domain")) for p in hm.skill_params.values() if p.get("domain")}
    order = [*READY_ORDER, *(p for p in part_list.ALL if p not in READY_ORDER)]
    return [
        {
            "part": part,
            "state": hm.parts.get(part, part_list.default(part)),
            "experimental": part in part_list.EXPERIMENTAL,
            "offSince": off_since(hm, part),
        }
        for part in order
        if part in part_list.ALL and (part in have or part == "care")
    ]


async def house_settings(hm: HouseMind, lang: str) -> dict[str, Any]:
    assert hm.gate is not None
    config = hm.ai.config
    state, spent, budget = await hm.ai.budget_state()
    given_back = bool(await hm.db.get_setting("given_back", False))
    return {
        "currency": hm.config.currency if hm.config else "EUR",
        "givenBack": given_back,
        "consentLearning": bool(await hm.setting("consent_learning")),
        "historyLearning": await hm.setting("history_learning") is not False,
        "history": hm.history_status.view(),
        "maxStep": {
            d: next(
                (
                    hm.gate.state.admin_max_step[x].name.lower()
                    for x in ha
                    if x in hm.gate.state.admin_max_step
                ),
                "autonomous",
            )
            for d, ha in DOMAINS.items()
        },
        "petAreas": sorted(hm.pet_areas),
        "newPersonLevel": int(await hm.setting("new_person_level") or 1),  # E-287
        "residentTopLevel": await resident_top_level(hm),  # QA 3, gat 3
        "phonelessResidents": bool(await hm.setting("phoneless_residents")),
        "departed": await departed(hm),  # people no longer in HA: to forget (QA 2, M-9)
        # Their first names, only to leave them out at an AI; and names found in room names
        # (QA 2, B-174).
        "privateNames": [str(n) for n in await hm.setting("private_names") or []],
        "namesInRooms": names_in_rooms(hm.model, hm.languages),
        "livedIn": await hm.setting("lived_in") is not False,  # E-285
        "vestaWindow": await hm.setting("vesta_window") is not False,  # E-450
        "security": security.settings_view(hm, lang),  # which alarm, sounds (16.6, 16.2)
        "calendar": await hm.setting("calendar") is True,  # 20.19
        "calendarAsked": await hm.setting("calendar") is not None,  # setting up asks it
        "hasCalendar": any(
            e.startswith("calendar.") for e in (hm.world.states if hm.world else {})
        ),
        "experimental": sorted(hm.experimental),
        "parts": parts_view(hm),  # one list, Uit to Autonoom (E-399)
        "knowledgeUpdates": bool(await hm.setting("knowledge_updates")),
        "knowledgeVersion": updates.installed_version(hm.knowledge_target),
        "updatesPublished": updates.INDEX_URL is not None,
        "care": await care_view(hm),
        "persons": [
            {"id": e, "name": str(st.attributes.get("friendly_name") or e)}
            for e, st in sorted((hm.world.states if hm.world else {}).items())
            if e.startswith("person.")
        ],
        "contacts": [
            {"id": str(r["user_id"]), "name": str(r["display_name"] or r["user_id"])}
            for r in await hm.db.fetchall("SELECT user_id, display_name FROM people")
        ],
        "areas": [
            {"id": a.id, "name": a.name}
            for a in sorted(hm.model.areas.values(), key=lambda a: a.name)
        ],
        "automations": automations(hm, lang, given_back),
        "scenesScripts": scenes_scripts(hm, lang),
        "snapshots": hm.snapshot_days(),  # 20.13, on Expert
        "vesta": await vesta_settings(hm, lang),
        # Every scheme, also the ones that did not pass, with the reason (E-267).
        "themes": {
            "ok": [
                {"id": s.id, "name": s.name(lang), "own": s.own, "file": s.file, "level": s.level}
                for s in hm.schemes.ordered()
            ],
            "problems": [p.view() for p in hm.schemes.problems],
            "example": _scheme_example(hm),
        },
        "ai": {
            "enabled": config.enabled,
            "provider": config.provider,
            "baseUrl": config.base_url or "",
            "modelRoutine": config.model_routine or "",
            "modelStrong": config.model_strong or "",
            "monthlyBudget": config.monthly_budget,
            "hasKey": hm.ai.keys.has(f"ai:{config.provider}"),
            "status": hm.ai.status(),
            "consent": bool(config.consent_at),
            "local": hm.ai.local,
            # An own AI address outside the home network: the questions leave the house (M-7).
            "remote": config.provider in ("ollama", "openai_compatible")
            and bool(config.base_url)
            and not is_local(config.base_url or ""),
            "spent": round(spent, 2),
            "budgetState": state,
            "budget": budget,
            "taskModels": dict(config.task_models),  # Expert: a model per task (E-49)
            # The AI tasks of Home Assistant, and where each works (E-422).
            "aiTasks": hm.ai_tasks(),
            "aiTaskLimit": AI_TASK_PER_DAY,
            # Camera images described by the AI in the house (E-424): only when it can be.
            "images": config.images and hm.ai.images_possible(),
            "spentByTask": {k: round(v, 2) for k, v in (await hm.ai.spent_by_task()).items()},
        },
    }


def _scheme_example(hm: HouseMind) -> str:
    """The shipped Diep file, with its explanation, to copy for an own scheme (E-267)."""
    for folder in hm.paths.knowledge:
        path = folder / "themes" / "diep.yaml"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def automations(hm: HouseMind, lang: str, given_back: bool = False) -> list[dict[str, Any]]:
    """The existing automations for administrators (E-41, E-42): what HouseMind learns from
    each, whether it could do it itself, and what it switched off – to switch back on."""
    t = hm.languages.text
    out = []
    for reading in sorted(hm.automations.readings.values(), key=lambda r: r.automation.name):
        status = hm.automations.status.get(reading.entity_id, AutomationStatus.SEEN)
        state = automation_state(hm, reading, given_back)
        ready = reading.automation.enabled and reading.whole and hm.ready_to_take_over(reading)
        automation = reading.automation
        rooms = rooms_of(hm.model, automation.steps, automation.starts)
        parts = parts_of(hm.model, hm.gate.state.roles, automation.steps) if hm.gate else []
        entity = hm.model.entities.get(reading.entity_id)
        parts, hint = _hinted(hm, entity, parts)
        teaches = []
        for skill, area_id in reading.learned:
            area = hm.model.areas.get(area_id)
            teaches.append(f"{t(lang, f'skills.{skill}')} – {area.name if area else area_id}")
        out.append(
            {
                "id": reading.entity_id,
                "name": reading.automation.name,
                "state": state,
                "text": t(lang, f"ui.automations.state.{state}"),
                "teaches": teaches,
                "canTurnOff": ready and status is not AutomationStatus.TURNED_OFF,
                "canTurnOn": status is AutomationStatus.TURNED_OFF,
                "history": history_line(hm, lang, reading.entity_id),  # E-257
                # What the buttons above the list filter on (E-258).
                "rooms": [hm.model.areas[a].name for a in rooms],
                "parts": parts,
                "causes": causes_of(hm.model, automation),
                "scene": bool(automation.starts),
                "labels": label_names(hm.model, entity) if entity else [],
                "categories": category_names(hm.model, entity) if entity else [],
                "labelHint": hint,
                "does": _does(hm, reading.entity_id),  # Wat hij doet, van AI (B-346)
            }
        )
    return out


def _hinted(hm: HouseMind, entity: Entity | None, parts: list[str]) -> tuple[list[str], str | None]:
    """An automation, script or scene HouseMind cannot place takes the part its category or
    labels in Home Assistant point to (E-345); otherwise its own parts stand."""
    if entity is None or (parts and parts != ["other"]):
        return parts, None
    hint = automation_hint(hm.model, hm.label_hints, entity)
    if hint is None or hint[0] not in HOUSE_PARTS:
        return parts, None
    return [hint[0]], hint[1]


def scenes_scripts(hm: HouseMind, lang: str) -> list[dict[str, Any]]:
    """Home Assistant's scenes and scripts, only to look at (E-03, E-258): where they act,
    what they are about, and which automations start them."""
    assert hm.gate is not None and hm.world is not None
    t = hm.languages.text
    roles = hm.gate.state.roles
    started: dict[str, list[str]] = {}
    for reading in sorted(hm.automations.readings.values(), key=lambda r: r.automation.name):
        for entity_id in reading.automation.starts:
            started.setdefault(entity_id, []).append(reading.automation.name)
    scripts = {script.entity_id: script for script in hm.scripts}
    out = []
    for entity in hm.model.entities.values():
        if entity.domain not in ("scene", "script") or entity.hidden or entity.disabled:
            continue
        own = hm.model.area_of(entity.entity_id)
        readable = True
        if entity.domain == "scene":
            state = hm.world.states.get(entity.entity_id)
            members = state.attributes.get("entity_id", []) if state else []
            members = [m for m in members if isinstance(m, str)]
            rooms = rooms_of(hm.model, (), members)
            parts = [p for p in HOUSE_PARTS if p in {part_of(m, roles) for m in members}]
        else:
            script = scripts.get(entity.entity_id)
            readable = script is not None and script.readable
            steps = script.steps if script else ()
            rooms = rooms_of(hm.model, steps, script.starts if script else ())
            parts = parts_of(hm.model, roles, steps)
        if own is not None and own.id not in rooms:
            rooms = sorted([*rooms, own.id])
        parts, hint = _hinted(hm, entity, parts)
        names = started.get(entity.entity_id, [])
        if not readable:
            text = t(lang, "ui.ha_scenes.unreadable")
        elif names:
            text = t(lang, "ui.ha_scenes.started_by", names=", ".join(names))
        else:
            text = t(lang, "ui.ha_scenes.not_started")
        out.append(
            {
                "id": entity.entity_id,
                "name": entity.name,
                "kind": entity.domain,
                "rooms": [hm.model.areas[a].name for a in rooms],
                "parts": parts,
                "text": text,
                "labels": label_names(hm.model, entity),
                "categories": category_names(hm.model, entity),
                "labelHint": hint,
                "does": _does(hm, entity.entity_id),  # Wat hij doet, van AI (B-346)
            }
        )
    out.sort(key=lambda row: str(row["name"]).lower())
    return out


async def ai_preview(hm: HouseMind, lang: str, user_id: str | None) -> str:
    """Exactly what goes to the AI, before anyone agrees (lesson P1/P2): a question about the
    house – with the open notices, house rules and choices of who looks (E-384) – and one of
    Vesta's sentences – the task that goes most often (QA 2, M-7) – in the shape the AI gets
    them."""
    example = hm.languages.text(lang, "ui.ai.example_question")
    said = hm.languages.text(lang, "ui.ai.example_vesta")
    house = {**hm.house_facts(example), **await hm.ai_memory(lang, user_id)}
    sent = {
        "ask": {"question": hm.ai.labels.pseudonymise(example), "house": house},
        "vesta": {
            "moment": "tap",
            "messages": [
                {
                    "kind": "did",
                    "important": False,
                    "facts": {"text": hm.ai.labels.pseudonymise(said)},
                }
            ],
        },
    }
    return json.dumps(sent, ensure_ascii=False, indent=2)  # readable; the AI gets the same


# -- energy (E-73): nothing for Carefree, a sentence for Informed, the plan for Involved, the
# whole calculation per quarter for Expert ---------------------------------------------------------


def _money(hm: HouseMind, lang: str, amount: float) -> str:
    from housemind.energy.manager import CURRENCY_SIGNS

    currency = hm.energy.country.currency if hm.energy else "EUR"
    sign = CURRENCY_SIGNS.get(currency, currency)
    text = hm.languages.number(lang, abs(amount), 2)
    return f"{sign} {text}"


def _price(hm: HouseMind, lang: str, amount: float) -> str:
    """A price per kWh as the integration gives it: with its sign, and three decimals where two
    say too little. A negative price (−€ 0,006) is no free one (€ 0,00), and € 0,175 is no
    € 0,18."""
    from housemind.energy.manager import CURRENCY_SIGNS

    currency = hm.energy.country.currency if hm.energy else "EUR"
    sign = CURRENCY_SIGNS.get(currency, currency)
    places = 2 if f"{abs(amount):.3f}".endswith("0") else 3
    text = hm.languages.number(lang, abs(amount), places)
    minus = "−" if round(amount, 3) < 0 else ""
    return f"{minus}{sign} {text}"


def _block_price(hm: HouseMind, lang: str, plan: Any, start: datetime, end: datetime) -> str | None:
    """The average price per kWh in a block of the plan (E-304), as the screen shows it."""
    prices = [s.buy for s in plan.slots if start <= s.start < end and s.buy is not None]
    if not prices:
        return None
    price = _price(hm, lang, sum(prices) / len(prices))
    return hm.languages.text(lang, "ui.energy.per_kwh", price=price)


QUARTER = timedelta(minutes=15)
ROWS = ("boiler", "heat_pump", "export", "appliance")  # the timeline (B-224, B-253)


def _why(hm: HouseMind, lang: str, step: Any) -> str:
    """'omdat …' for a block of the plan, with its times in the house's time."""
    if not hm.explainer:
        return str(step.why)
    params = {
        k: _local(hm, v).strftime("%H:%M") if isinstance(v, datetime) else v
        for k, v in step.params.items()
    }
    reason = hm.explainer.energy_reason(lang, {"why": step.why, "params": params})
    return hm.languages.text(lang, "explain.because", reason=reason)


def _clock(hm: HouseMind, lang: str, plan: Any, soc_now: float | None) -> dict[str, Any] | None:
    """The day clock (B-223, B-253): the coming 24 hours per quarter – price, sun, what
    HouseMind does – with the now and how full the battery is in the middle, and a sentence for
    who cannot see the colours."""
    t = hm.languages.text
    slots = plan.slots[:96]
    if not slots:
        return None
    first = _local(hm, slots[0].start)

    def clock(i: int) -> str:
        return _local(hm, slots[0].start + i * QUARTER).strftime("%H:%M")

    def doing_at(moment: datetime) -> Any:
        return next((x for x in plan.steps if x.start <= moment < x.end), None)

    steps = [x for x in plan.steps if x.end > slots[0].start and x.start < slots[-1].start]
    now_step = doing_at(slots[0].start)
    doing = None
    if now_step is not None:
        end = _local(hm, now_step.end).strftime("%H:%M")
        doing = t(lang, f"ui.energy.clock.doing.{now_step.action}", time=end)
    elif slots[0].buy is not None:
        doing = t(lang, "ui.energy.per_kwh", price=_price(hm, lang, slots[0].buy))
    known = [(i, x.buy) for i, x in enumerate(slots) if x.buy is not None]
    summary = []
    if known:
        low, high = min(known, key=lambda k: k[1]), max(known, key=lambda k: k[1])
        summary.append(
            t(
                lang,
                "ui.energy.clock.prices",
                low=_price(hm, lang, low[1]),
                low_at=clock(low[0]),
                high=_price(hm, lang, high[1]),
                high_at=clock(high[0]),
            )
        )
    if steps:
        parts = [
            t(
                lang,
                "ui.energy.clock.run",
                what=t(lang, f"ui.energy.block.{x.action}"),
                start=_local(hm, max(x.start, slots[0].start)).strftime("%H:%M"),
                end=_local(hm, x.end).strftime("%H:%M"),
            )
            for x in steps
        ]
        line = ", ".join(parts)
        summary.append(line[:1].upper() + line[1:] + ".")
    points = []
    for i, x in enumerate(slots):
        step = doing_at(x.start)
        points.append(
            {
                "at": clock(i),
                "price": round(x.buy, 4) if x.buy is not None else None,
                "solar": round(x.solar_w),
                "doing": step.action if step is not None else None,
            }
        )
    return {
        "start": first.hour * 60 + first.minute,
        "now": t(lang, "ui.energy.clock.now", time=first.strftime("%H:%M")),
        "soc": round(soc_now) if soc_now is not None else None,
        "doing": doing,
        "summary": " ".join(summary),
        "points": points,
    }


def _blocks(hm: HouseMind, lang: str, plan: Any, now: datetime, step: int) -> list[dict[str, Any]]:
    """The timeline under the plan (B-224): per device what HouseMind does, as stretches of the
    chart (from, to), with the reason and the price – in place of the list."""
    t = hm.languages.text
    n = min(144, len(plan.slots))
    if not n:
        return []
    first: datetime = plan.slots[0].start
    points = math.ceil(n / step)

    def at(moment: datetime) -> int:
        return max(0, min(points, round((moment - first) / QUARTER / step)))

    def block(
        row: str, action: str, start: datetime, end: datetime, why: str, saving: float = 0.0
    ) -> dict[str, Any]:
        return {
            "expected": _expected(hm, lang, saving),
            "row": row,
            "action": action,
            "from": at(start),
            "to": max(at(end), at(start) + 1),
            "label": t(lang, f"ui.energy.block.{action}"),
            "start": _local(hm, start).strftime("%H:%M"),
            "end": _local(hm, end).strftime("%H:%M"),
            "why": why,
            "price": _block_price(hm, lang, plan, start, end),
        }

    out = []
    for x in plan.steps:
        if x.end <= now or x.start >= first + n * QUARTER:
            continue
        row = x.device if x.device in ROWS else "appliance"
        out.append(block(row, x.action, max(x.start, first), x.end, _why(hm, lang, x), x.saving))
    return sorted(out, key=lambda b: (ROWS.index(b["row"]), b["from"]))


def _price_basis(hm: HouseMind, lang: str, states: dict[str, Any]) -> str:
    """One line on what is in the prices shown (E-304): only what the integration states, or
    what the administrator chose – never a guess."""
    assert hm.energy is not None
    t = hm.languages.text
    e = hm.energy
    if not e.setup.price:
        return t(lang, "ui.energy.basis.fixed") if e.setup.fixed_price is not None else ""
    if e.settings.price_mode in ("market", "all_in"):
        return t(lang, f"ui.energy.basis.set_{e.settings.price_mode}")
    basis = e.price_basis(states)
    line = t(lang, f"ui.energy.basis.{basis.kind}")
    if basis.vat is not None and basis.kind in ("no_extra", "extra", "unknown"):
        line += " " + t(lang, "ui.energy.basis.vat_in" if basis.vat else "ui.energy.basis.vat_out")
    return line


def _step_text(hm: HouseMind, lang: str, step: Any) -> str:
    t = hm.languages.text
    key = f"energy.step.{step.device}_{step.action}"
    appliances = hm.energy.setup.appliances if hm.energy else []
    appliance = next((a for a in appliances if a.start == step.target), None)
    device = ""
    if appliance is not None:
        device = appliance.name
    elif step.target and step.target in hm.model.entities:
        device = hm.model.entities[step.target].name
    return t(lang, key, device=device)


def _device_name(hm: HouseMind, entity_id: str) -> str:
    return hm.model.device_name(entity_id)


async def energy(
    hm: HouseMind, lang: str, level: int, admin: bool = False
) -> dict[str, Any] | None:
    """The Energy tab (E-364): now and today, prices and the forecast, tips without steering,
    costs and base load. The home battery and the car are only shown (E-363)."""
    if hm.energy is None or not hm.energy.setup.has_any or level < 2 or hm.world is None:
        return None
    from housemind.energy.manager import CURRENCY_SIGNS
    from housemind.energy.meter import live as live_power
    from housemind.energy.meter import power as power_of

    e = hm.energy
    t = hm.languages.text
    now = hm.clock.now()
    states = hm.world.states
    s = e.setup
    plan = e.plan or e.replan(states, now)
    savings = await e.savings(now)
    today = await e.today(now)

    # One sentence (level 2) – also the first line for levels 3 and 4.
    lines: list[str] = []
    if savings["measuring"]:
        lines.append(t(lang, "energy.ui.measuring", count=savings["daysLeft"]))
        lines.append(_after_measuring(hm, lang))  # what comes after, in this house (E-388)
    elif plan is not None:
        upcoming = [x for x in plan.steps if x.end > now]
        if upcoming:
            step = upcoming[0]
            lines.append(
                t(
                    lang,
                    "energy.ui.next",
                    what=_step_text(hm, lang, step),
                    start=_local(hm, step.start).strftime("%H:%M"),
                    end=_local(hm, step.end).strftime("%H:%M"),
                )
            )
        else:
            lines.append(t(lang, "energy.ui.nothing"))
    # Only what was measured: a house whose meters give nothing hears no "€ 0,00".
    measured = bool(today["import_kwh"] or today["export_kwh"])
    if measured:
        lines.append(t(lang, "energy.ui.cost_today", cost=_money(hm, lang, today["cost"])))
    if savings["compared"]:
        key = "energy.ui.saved" if savings["total"] >= 0 else "energy.ui.saved_less"
        lines.append(
            t(lang, key, total=_money(hm, lang, savings["total"]), count=savings["compared"])
        )
    for part in s.managed:  # E-68: another system steers this; HouseMind watches
        lines.append(t(lang, f"energy.ui.managed_{part}"))
    if level >= 3 and e.own is not None:
        # Somebody else changed what HouseMind had set: it lets go and says so (E-328).
        for entity_id, at in e.own.others.items():
            if now - datetime.fromisoformat(at) < timedelta(hours=24):
                lines.append(t(lang, "energy.ui.other_set", device=_device_name(hm, entity_id)))

    # Now and today (B-252): the sun, the grid, the house, each battery and the car.
    grid = live_power(states, s.readings("grid"))
    solar = live_power(states, s.readings("solar"))
    batteries = []
    battery_total: float | None = None
    for battery in s.batteries:
        if battery_empty(battery, states):
            continue  # storage sensors without a battery (a50f11d)
        pct = power_of(states, battery.soc)
        watts = live_power(states, battery.readings())
        if watts is not None:
            battery_total = (battery_total or 0.0) + watts
        batteries.append(
            {
                "name": battery.name,
                "pct": round(pct) if pct is not None else None,
                "watts": round(watts) if watts is not None else None,
            }
        )
    if not s.batteries and s.battery_soc:
        pct, watts = power_of(states, s.battery_soc), live_power(states, s.readings("battery"))
        battery_total = watts
        batteries.append(
            {
                "name": t(lang, "ui.energy.now.battery"),
                "pct": round(pct) if pct is not None else None,
                "watts": round(watts) if watts is not None else None,
            }
        )
    house = None
    if grid is not None:
        house = max(0.0, grid + (solar or 0.0) - (battery_total or 0.0))
    car = None
    if s.car_plugged or s.charger_power or s.car_soc:
        plug = states.get(s.car_plugged or "")
        car_w = power_of(states, s.charger_power)
        car_pct = power_of(states, s.car_soc)
        car = {
            "plugged": plug.state == "on" if plug is not None else None,
            "watts": round(car_w) if car_w is not None else None,
            "pct": round(car_pct) if car_pct is not None else None,
        }
    from housemind.energy.manager import BASELINE

    out: dict[str, Any] = {
        "sentence": " ".join(lines),
        # The first weeks (E-408, B-334): "Dag 5 van 14" while it only measures.
        "measuringDay": {
            "day": BASELINE.days - savings["daysLeft"] + 1,
            "of": BASELINE.days,
        }
        if savings["measuring"]
        else None,
        # Lees meer: the part of the manual that explains it (E-388).
        "more": "energie-de-eerste-14-dagen-alleen-meten"
        if savings["measuring"]
        else "energie-wat-housemind-plant",
        "has": s.summary(),
        "live": {
            "solar": round(solar) if solar is not None else None,
            "grid": round(grid) if grid is not None else None,
            "house": round(house) if house is not None else None,
            "batteries": batteries,
            "car": car,
        },
        "today": {
            key: round(today[key], 1)
            for key in (
                "import_kwh",
                "export_kwh",
                "solar_kwh",
                "battery_in_kwh",
                "battery_out_kwh",
                "car_kwh",
            )
            if today["n"] and any(today[k] for k in ("import_kwh", "export_kwh", "solar_kwh"))
        },
    }
    soc_now = next((b["pct"] for b in batteries if b["pct"] is not None), None)
    if plan is not None:  # the day clock, from level 2 (E-346, B-223)
        out["clock"] = _clock(hm, lang, plan, soc_now)
        out["currency"] = CURRENCY_SIGNS.get(e.country.currency, e.country.currency)
    if level < 3:
        return out

    # The plan for the coming 24–48 hours (level 3): Betrokken per half hour, Expert per
    # quarter, with the margin of the sun and the price for giving back (E-346, B-222, B-225).
    # Before it, what was measured today: the battery and the car only there (B-253).
    if plan is not None:
        every = 1 if level >= 4 else 2
        band = e.solar_margin.band() if level >= 4 and e.solar_margin is not None else None
        chart = _measured_points(hm, await e.measured(now), every)
        past = len(chart)
        for j in range(0, min(144, len(plan.slots)), every):
            slot = plan.slots[j]
            point: dict[str, Any] = {
                "at": _local(hm, slot.start).strftime("%H:%M"),
                "price": round(slot.buy, 4) if slot.buy is not None else None,
                "solar": round(slot.solar_w),
                "house": round(slot.house_w),
                "battery": 0,
                "car": 0,
                "boiler": round(plan.boiler_w[j]),
                "grid": round(plan.grid_w[j]),
                "past": False,
            }
            if level >= 4:
                point["sell"] = round(slot.sell, 4) if slot.sell is not None else None
                if band is not None:
                    point["solarLow"] = round(slot.solar_w * band[0])
                    point["solarHigh"] = round(slot.solar_w * band[1])
            chart.append(point)
        out["chart"] = chart
        out["blocks"] = [
            {**b, "from": b["from"] + past, "to": b["to"] + past}
            for b in _blocks(hm, lang, plan, now, every)
        ]
        out["managed"] = list(s.managed)  # another system steers this (E-68)
        day = _local(hm, now).date()

        def when(moment: datetime) -> str:
            local = _local(hm, moment)
            days = (local.date() - day).days
            word = t(lang, f"ui.energy.day.{min(days, 2)}") if days > 0 else ""
            return f"{word} {local.strftime('%H:%M')}".strip()

        out["steps"] = [
            {
                "device": x.device,
                "what": _step_text(hm, lang, x),
                "start": when(x.start),
                "end": _local(hm, x.end).strftime("%H:%M"),
                "why": _why(hm, lang, x),
                "price": _block_price(hm, lang, plan, x.start, x.end),  # E-304
                "expected": _expected(hm, lang, x.saving),  # E-379
                **({"calc": _calc(hm, lang, x)} if level >= 4 and "now_price" in x.params else {}),
            }
            for x in plan.steps
            if x.end > now
        ][:20]
        out["priceBasis"] = _price_basis(hm, lang, states)
        known = [x for x in plan.slots[:144] if x.buy is not None]
        if known:  # the prices in the plan, in numbers (E-304)
            low = min(known, key=lambda x: x.buy or 0.0)
            high = max(known, key=lambda x: x.buy or 0.0)
            out["priceRange"] = t(
                lang,
                "ui.energy.price_range",
                low=_price(hm, lang, low.buy or 0.0),
                low_at=when(low.start),
                high=_price(hm, lang, high.buy or 0.0),
                high_at=when(high.start),
            )
        # Tips without steering (B-254): never a notification.
        # Not about the home battery or the car (QA 3, schermen 22): HouseMind does not steer
        # them, and HA's own energy dashboard shows them.
        found = tips(plan.slots, now, False, None, grid)
        out["tips"] = [_tip_text(hm, lang, tip, when) for tip in found]

    # What each part brought, on what was measured (E-379, B-277).
    if e.ledger is not None:
        resting = e.ledger.rest
        present = {
            "boiler": bool(s.boiler),
            "heat_pump": bool(s.heat_pump),
            "appliance": bool(s.appliances),
            "export": bool(s.export_limit),
        }
        parts = []
        for part, there in present.items():
            if not there:
                continue
            amount, count = e.ledger.saved(part, now)
            if part in resting:
                text = t(lang, "ui.energy.parts.resting")
            elif not count:
                text = t(lang, "ui.energy.parts.none")
            else:
                key = "ui.energy.parts.saved" if amount >= 0 else "ui.energy.parts.cost"
                text = t(lang, key, saving=_money(hm, lang, abs(amount)))
            parts.append(
                {
                    "name": t(lang, f"ui.energy.parts.{part}"),
                    "icon": ENERGY_PART_ICONS.get(part, "energy"),  # E-512
                    "text": text,
                    "resting": part in resting,
                    "amount": round(amount, 2),
                }
            )
        out["parts"] = parts

    # Costs and base load (B-255), when the meters measured something.
    month = await e.month_cost(now)
    if measured or month:
        out["costs"] = {
            "today": _money(hm, lang, today["cost"]),
            "month": _money(hm, lang, month),
            "exportKwh": round(today["export_kwh"], 1),
        }
    out["savings"] = {
        **savings,
        "days": [{**d, "label": d["day"][5:]} for d in savings["days"]],
        "totalText": _money(hm, lang, savings["total"]),
    }
    insights = await e.insights(now, states)
    out["baseLoad"] = (
        t(
            lang,
            "energy.ui.base_load",
            watt=insights["baseW"],
            kwh=insights["baseYearKwh"],
            cost=_money(hm, lang, float(insights["baseYearCost"] or 0)),
        )
        if insights["baseW"] is not None
        else t(lang, "energy.ui.base_load_unknown")
    )
    out["usersToday"] = [
        {"name": _device_name(hm, entity_id), "kwh": round(kwh, 1), **icon_look(hm, entity_id)}
        for entity_id, kwh in e.users_today(states)
    ]
    if level < 4:
        return out

    out["users"] = [
        {"name": _device_name(hm, u["entity"]), "watts": u["watts"], **icon_look(hm, u["entity"])}
        for u in insights["users"]
    ]
    return out


def _measured_points(hm: HouseMind, quarters: list[dict[str, Any]], every: int) -> list[Any]:
    """Today before now, as it was measured (B-253): per half hour or per quarter."""
    out: list[dict[str, Any]] = []
    for k in range(0, len(quarters), every):
        group = quarters[k : k + every]

        def mean(key: str, rows: list[dict[str, Any]] = group) -> float:
            return sum(float(r[key] or 0.0) for r in rows) / len(rows)

        prices = [r["price"] for r in group if r["price"] is not None]
        out.append(
            {
                "at": _local(hm, group[0]["start"]).strftime("%H:%M"),
                "price": round(sum(prices) / len(prices), 4) if prices else None,
                "solar": round(mean("solar")),
                "house": round(mean("house")),
                "battery": round(mean("battery")),
                "car": round(mean("car")),
                "boiler": 0,
                "grid": round(mean("grid")),
                "past": True,
            }
        )
    return out


def _after_measuring(hm: HouseMind, lang: str) -> str:
    """What HouseMind will do in this house once it has measured: only what it steers, only
    when it pays – or, honestly, that it steers nothing here (E-388)."""
    assert hm.energy is not None
    t = hm.languages.text
    s = hm.energy.setup
    present = (
        ("boiler", bool(s.boiler)),
        ("heat_pump", bool(s.heat_pump)),
        ("appliance", bool(s.appliances)),
        ("export", bool(s.export_limit)),
    )
    things = [t(lang, f"energy.ui.after.{part}") for part, there in present if there]
    if not things:
        return t(lang, "energy.ui.after.nothing")
    joined = things[0]
    if len(things) > 1:
        joined = ", ".join(things[:-1]) + " " + t(lang, "energy.ui.after.and") + " " + things[-1]
    return t(lang, "energy.ui.after.start", things=joined)


def _expected(hm: HouseMind, lang: str, saving: float) -> str | None:
    """'levert naar verwachting € 0,08 op' for a block of the plan (E-379)."""
    if saving < 0.005:
        return None
    return hm.languages.text(lang, "ui.energy.block_expected", saving=_money(hm, lang, saving))


def _calc(hm: HouseMind, lang: str, step: Any) -> str:
    """Expert: the price now and later, what is left of the warmth, and what it brings."""
    p = step.params
    return hm.languages.text(
        lang,
        "ui.energy.calc",
        now=_price(hm, lang, float(p["now_price"])),
        later=_price(hm, lang, float(p["later_price"])),
        kept=p.get("kept_pct", 100),
        saving=_money(hm, lang, float(p.get("gain", step.saving))),
    )


def _tip_text(hm: HouseMind, lang: str, tip: Any, when: Any) -> str:
    """One tip in plain words (B-254)."""
    params: dict[str, Any] = {}
    if tip.start is not None and tip.end is not None:
        params = {"start": when(tip.start), "end": _local(hm, tip.end).strftime("%H:%M")}
    if "price" in tip.values:
        params["price"] = _price(hm, lang, float(tip.values["price"]))
    return hm.languages.text(lang, f"energy.tip.{tip.key}", **params)


def energy_settings(hm: HouseMind) -> dict[str, Any] | None:
    if hm.energy is None or not hm.energy.setup.has_any:
        return None
    s = hm.energy.settings
    return {
        "goal": s.goal,
        "peakLimitKw": s.peak_limit_kw,
        "priceMode": s.price_mode,
        "has": hm.energy.setup.summary(),
        "country": hm.energy.country.country,
    }
