"""Security and cameras (E-121, E-180 – E-190; design 16.2 – 16.8).

What a detection means (16.2):

- a person counts as someone in that room, outside as someone outside; outside in the dark the
  lamps there go on, as when coming in (E-183). Everyone away: an important message with the
  camera's image. Everyone asleep: nothing;
- a vehicle is never presence; everyone away: an important message with the image;
- an animal is nothing at all (E-90);
- a package: an information message (it comes with the day's bundle) and a line in the day
  summary (level 2);
- the doorbell: a person at the door, and its camera on every wall tablet in kiosk mode for 30
  seconds; a message on the phone only when everyone is away (B-66);
- smoke or CO heard: inside an emergency, outside only a message (E-80, E-178). A camera's own
  smoke sensor goes to the emergency reactions; a heard event gets the message and the lights;
- glass breaking inside: an alarm situation – a message that also comes at night (B-64);
- a crying baby only after a yes (care and wellbeing), barking, a car alarm, a horn or a siren
  only when chosen (level 4) – as information.

An image is only added when everyone is away or in an alarm situation: the phone fetches it
from Home Assistant itself (``/api/camera_proxy/…``); HouseMind sends only that path and never
keeps, looks at or sends an image (E-186, E-15). While nobody is home every camera is one
message, a line per place and kind ("9 keer een voertuig bij Tuin … 5 keer iemand bij
Oprit"); another detection updates it quietly, a new message only after half an hour without
one (E-373). Other camera messages: at most one per camera per ten minutes.

Switching it on by itself (E-540): an administrator's yes in House settings – or to the one
question that comes when the last five answers to "switch it on?" were yes – is a standing yes:
when everyone has left, or when the house goes to sleep, HouseMind switches the alarm on itself
with that yes and says so at once. Only when it is safe: away when no one is home and no sensor
saw anyone for ten minutes, night when no window or outside door is open; an alarm that wants a
code to switch on keeps the question. Switched off again within ten minutes twice in a week, it
asks again. Generic: what every alarm in Home Assistant reports – its modes and whether it
wants a code. Switching off stays a person's, always.

The alarm (16.6): when it goes off, all lights on, the window coverings open, a spoken warning
and an emergency message with an image (E-121). Switched on, gone off and switched off are
lines in the Logboek, the hundred lamps of going off one of them (E-533). A minute after it is
switched off, the lamps and coverings go back to how they were just before it went off – not
what someone changed in that minute, and not the lamps of a room where someone is now: those go
off as usual when it is empty (E-534). HouseMind only *asks* to switch it on – when
everyone has left and when the house goes to sleep – once each time; with a code it opens the
alarm in Home Assistant. It never switches it off (E-31). Its state counts in the world view
(world/house_mode.py). With more alarms, "the alarm" is the one in use – switched on or gone
off in the history Home Assistant still has – unless an administrator chose; when that is not
clear, an administrator is asked once, with a button per alarm (E-311).

A detection is kept 30 days as a kind, a room and a time – never an image, a face, a plate or a
name (E-189, E-18). An AI gets nothing of it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import alarm_reaction, house_settings
from housemind.brain.cameras import CameraIndex, CameraWords, Detection, load_camera_words
from housemind.brain.levels import white
from housemind.brain.notify import CAMERA_BUNDLE, CAMERA_BUNDLE_KEY, Kind, bundle_lines
from housemind.core.model import Entity, Source, State, StateChange
from housemind.knowledge.matcher import recognise
from housemind.safety.emergency import Emergency
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.safety.hard_limits import OPENER_ROLES, PROTECTED_COVER_CLASSES
from housemind.world.house_mode import HouseMode

if TYPE_CHECKING:
    from housemind.runtime import HouseMind
    from housemind.world.world import WorldState

_LOGGER = logging.getLogger(__name__)

# House settings (Huisinstellingen → Beveiliging): which alarm is "the alarm" when there are
# more (B-67), and the sounds that are only used after a choice.
SETTINGS = house_settings.SECURITY_KEYS  # from the one table (code QA 3, punt 1)
# The alarm on by itself (E-540).
MOMENTS = ("away", "night")
SELF_AFTER = 5  # the last this many answers yes: the one question
SELF_ASK_AGAIN = timedelta(days=30)  # after a no to that question
QUIET_FOR = timedelta(minutes=10)  # away: no sensor saw anyone this long
SELF_WAIT = timedelta(minutes=15)  # not quiet by then: the question after all
QUICK_OFF = timedelta(minutes=10)  # switched off this soon after HouseMind switched it on …
QUICK_TIMES = 2  # … this often …
QUICK_WEEK = timedelta(days=7)  # … within a week: it asks again
# What an alarm can do (Home Assistant's supported features of alarm_control_panel).
ARM_HOME, ARM_AWAY, ARM_NIGHT = 1, 2, 4
OPENABLE_CLASSES = frozenset({"curtain", "blind", "shade"})  # never "shutter" (B-170)
AWAY_MODES = (HouseMode.AWAY, HouseMode.VACATION)
# An alarm in one of these states is in use (E-311); disarmed and unavailable say nothing.
IN_USE = frozenset(
    {
        "armed_away",
        "armed_home",
        "armed_night",
        "armed_vacation",
        "armed_custom_bypass",
        "arming",
        "pending",
        "triggered",
    }
)
USE_DAYS = 10  # about what Home Assistant's recorder keeps
CHOICE_BUTTONS = 3  # more alarms than this: the message points to House settings
LIGHT_SKILL = "light_on_person_outside"
QUIET_AFTER = timedelta(hours=1)  # a camera message goes after an hour without a new one
# "Alarm op nacht zetten?" is a question of the moment: it does not keep Vesta orange all night.
ARM_KEEP = timedelta(minutes=30)
# After it is switched off, the house goes back to how it was (E-534) – a minute later, so
# whoever switched it off can first look around, and what they change stays.
BACK_AFTER = timedelta(minutes=1)


@dataclass(slots=True)
class Window:
    """The ten minutes of one camera's message."""

    start: datetime
    last: datetime
    count: int
    key: str
    info: bool = False  # information waits for the day's bundle: it is never taken back
    away: bool = False  # only for while everyone is away: it goes when someone is home
    # The cameras while away (E-373): per place and kind its line – count, time, last seen.
    parts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class Watch:
    words: CameraWords = field(default_factory=CameraWords)
    windows: dict[str, Window] = field(default_factory=dict)  # topic → its window
    doorbell: tuple[str | None, datetime] | None = None  # camera, when it rang
    lit: dict[str, datetime] = field(default_factory=dict)  # outside lamp → last person
    seen: dict[str, datetime] = field(default_factory=dict)  # camera → last person
    # What an AI in the house saw on a camera's image (E-424): camera → the sentence.
    described: dict[str, str] = field(default_factory=dict)
    last: dict[tuple[str, str], datetime] = field(default_factory=dict)  # (camera, kind) → when
    mode_before: HouseMode | None = None
    asked: dict[str, str] = field(default_factory=dict)  # "away"/"night" → that occasion
    asked_at: dict[str, datetime] = field(default_factory=dict)  # arm question → when
    alarm_entity: str | None = None  # the administrator's choice (B-67)
    in_use: set[str] = field(default_factory=set)  # alarms switched on or gone off (E-311)
    use_read_for: tuple[str, ...] | None = None  # the alarms the history was read for
    sounds: set[str] = field(default_factory=set)  # optional sounds that were chosen
    # The window coverings that open when the alarm goes off: only the ones an administrator
    # chose (QA 2, B-170) – a garage roller door can look like a shutter.
    open_covers: set[str] = field(default_factory=set)
    # The alarm going off (E-533, E-534): how each lamp and covering was just before, when it
    # went off, and when the house goes back after it was switched off.
    before: dict[str, State] = field(default_factory=dict)
    went_off: dict[str, datetime] = field(default_factory=dict)  # alarm → when
    back_at: datetime | None = None
    back_alarm: str | None = None
    # The alarm on by itself (E-540): per moment ("away", "night") the standing yes and who
    # gave it, the last answers, when the question was asked, and quick switches off.
    self_arm: dict[str, str] = field(default_factory=dict)  # moment → who said yes
    answers: dict[str, list[bool]] = field(default_factory=dict)
    offered: dict[str, str] = field(default_factory=dict)  # moment → when asked (ISO)
    quick_off: dict[str, list[str]] = field(default_factory=dict)  # moment → when (ISO)
    armed_by_me: tuple[str, datetime, str] | None = None  # alarm, when, moment
    self_wait: tuple[str, str, datetime] | None = None  # moment, how, since: until it is quiet
    # The alarm reaction (E-542 – E-546): the lamps that flash red at a break-in and the
    # coverings that go up at a fire, both an administrator's choice; what is going on now
    # (topic → alarm_reaction.Going) and the lamps HouseMind is flashing itself.
    signal: set[str] = field(default_factory=set)
    fire_covers: set[str] = field(default_factory=set)
    going: dict[str, Any] = field(default_factory=dict)
    blinking: set[str] = field(default_factory=set)


# -- starting and the house changing --------------------------------------------------------


async def start(hm: HouseMind) -> None:
    """The knowledge, the settings and which entity reports what."""
    hm.security.words = load_camera_words([p / "security" for p in hm.paths.knowledge])
    chosen = await hm.setting("alarm_entity")
    hm.security.alarm_entity = chosen if isinstance(chosen, str) else None
    sounds = await hm.setting("camera_sounds")
    hm.security.sounds = {str(s) for s in sounds or []} & set(hm.security.words.optional_sounds)
    covers = await hm.setting("alarm_open_covers")
    hm.security.open_covers = {str(c) for c in covers or []}
    lamps = await hm.setting("alarm_signal_lamps")
    hm.security.signal = {str(c) for c in lamps or []}
    fire = await hm.setting("fire_open_covers")
    hm.security.fire_covers = {str(c) for c in fire or []}
    chosen_self = await hm.setting("alarm_self") or {}
    hm.security.self_arm = {str(k): str(v) for k, v in chosen_self.items() if k in MOMENTS and v}
    learned = await hm.db.get_setting("alarm_learn", {}) or {}
    hm.security.answers = {
        str(k): [bool(a) for a in v][-SELF_AFTER:]
        for k, v in (learned.get("answers") or {}).items()
        if k in MOMENTS
    }
    hm.security.offered = {str(k): str(v) for k, v in (learned.get("offered") or {}).items()}
    hm.security.quick_off = {
        str(k): [str(x) for x in v] for k, v in (learned.get("quick_off") or {}).items()
    }
    rebuild(hm)
    assert hm.world is not None
    hm.security.mode_before = hm.world.modes.mode  # the first change of mode counts
    await read_use(hm)


async def read_use(hm: HouseMind) -> None:
    """Which alarms are in use (E-311): switched on or gone off in the history Home Assistant
    still has (about ten days), or now. Again when an alarm comes or goes."""
    assert hm.world is not None
    w = hm.security
    found = alarms(hm)
    w.use_read_for = tuple(found)
    used: set[str] = set()
    if len(found) > 1:
        now = hm.clock.now()
        try:
            changes = await hm.ha.history(found, now - timedelta(days=USE_DAYS), now)
        except (OSError, TimeoutError):
            changes = []  # read again when the house changes; until then: what is seen live
        used = {c.entity_id for c in changes if c.state in IN_USE}
    used |= {e for e in found if (s := hm.world.states.get(e)) is not None and s.state in IN_USE}
    w.in_use = used & set(found)
    hm.world.alarm_entity = the_alarm(hm)


def rebuild(hm: HouseMind) -> None:
    """Again after the house changed in Home Assistant."""
    assert hm.world is not None and hm.emergencies is not None
    hm.cameras = CameraIndex(hm.model, hm.security.words, hm.world.states)
    hm.emergencies.heard = {
        entity_id: Emergency.SMOKE if kind == "smoke" else Emergency.CARBON_MONOXIDE
        for entity_id, kind in hm.cameras.smoke.items()
    }
    hm.world.alarm_entity = the_alarm(hm)


def alarms(hm: HouseMind) -> list[str]:
    return sorted(
        e.entity_id
        for e in hm.model.entities.values()
        if e.domain == "alarm_control_panel" and not e.disabled and not e.hidden
    )


def the_alarm(hm: HouseMind) -> str | None:
    """ "The alarm": the only one, or the one an administrator chose (B-67) – that choice
    always wins –, else the only one in use (E-311). None when that is not clear."""
    found = alarms(hm)
    if len(found) == 1:
        return found[0]
    chosen = hm.security.alarm_entity
    if chosen in found:
        return chosen
    return automatic(hm)


def automatic(hm: HouseMind) -> str | None:
    """With more alarms: the only one in use, if there is exactly one (E-311)."""
    used = [e for e in alarms(hm) if e in hm.security.in_use]
    return used[0] if len(used) == 1 else None


async def set_setting(hm: HouseMind, key: str, value: Any) -> bool:
    if key == "alarm_entity" and (value is None or value in alarms(hm)):
        await hm.db.set_setting(key, value)
        hm.security.alarm_entity = value
        assert hm.world is not None
        hm.world.alarm_entity = the_alarm(hm)
        await hm._resolve("alarm_choice")
        return True
    if key == "alarm_open_covers" and isinstance(value, list):
        chosen = sorted({str(c) for c in value} & set(coverings(hm)))
        await hm.db.set_setting(key, chosen)
        hm.security.open_covers = set(chosen)
        return True
    if key == "alarm_signal_lamps" and isinstance(value, list):
        chosen = sorted({str(c) for c in value} & set(alarm_reaction.colour_lamps(hm)))
        await hm.db.set_setting(key, chosen)
        hm.security.signal = set(chosen)
        return True
    if key == "fire_open_covers" and isinstance(value, list):
        chosen = sorted({str(c) for c in value} & set(alarm_reaction.fire_coverings(hm)))
        await hm.db.set_setting(key, chosen)
        hm.security.fire_covers = set(chosen)
        return True
    if key == "alarm_self" and isinstance(value, dict):
        # The administrator's standing yes (E-540): per moment on or off.
        for moment in MOMENTS:
            if moment in value:
                await _self_arm(hm, moment, bool(value[moment]), "settings")
        return True
    if key == "camera_sounds" and isinstance(value, list):
        chosen = sorted({str(s) for s in value} & set(hm.security.words.optional_sounds))
        await hm.db.set_setting(key, chosen)
        hm.security.sounds = set(chosen)
        return True
    return False


def settings_view(hm: HouseMind, lang: str) -> dict[str, Any]:
    """For Huisinstellingen: the alarms to choose from (with more than one), which are in use
    and which one HouseMind takes by itself (E-311), and the sounds."""
    names = {e: _name(hm, e) for e in alarms(hm)}
    suggested = set(alarm_reaction.suggested_lamps(hm))
    return {
        "alarms": [{"id": e, "name": n, "inUse": e in hm.security.in_use} for e, n in names.items()]
        if len(names) > 1
        else [],
        "alarm": hm.security.alarm_entity,
        "auto": automatic(hm),
        "cameras": bool(hm.cameras and hm.cameras.sources),
        "sounds": sorted(hm.security.words.optional_sounds),
        "chosen": sorted(hm.security.sounds),
        # What opens when the alarm goes off (B-170): nothing until an administrator chooses.
        "hasAlarm": bool(alarms(hm)),
        "coverings": [
            {"id": c, "name": _name(hm, c), "area": a.name if (a := hm.model.area_of(c)) else ""}
            for c in coverings(hm)
        ],
        "open": sorted(hm.security.open_covers & set(coverings(hm))),
        # On by itself (E-540): per moment, and whether this alarm can – no code, that mode.
        "selfArm": {m: m in hm.security.self_arm for m in MOMENTS},
        "selfCan": _self_can(hm),
        # The alarm reaction (E-543, E-546): signal lamps (with HouseMind's suggestion) and
        # what goes up at a fire – nothing until an administrator chooses.
        "signalLamps": [
            {
                "id": e,
                "name": _name(hm, e),
                "area": a.name if (a := hm.model.area_of(e)) else "",
                "suggested": e in suggested,
            }
            for e in alarm_reaction.colour_lamps(hm)
        ],
        "signal": sorted(hm.security.signal),
        "fireCovers": [
            {"id": c, "name": _name(hm, c), "area": a.name if (a := hm.model.area_of(c)) else ""}
            for c in alarm_reaction.fire_coverings(hm)
        ]
        if alarm_reaction.detects_fire(hm)
        else [],
        "fireOpen": sorted(hm.security.fire_covers),
    }


def _self_can(hm: HouseMind) -> dict[str, bool]:
    """Whether the alarm in use can go on by itself at each moment (E-540): it has that mode
    and does not want a code to switch on."""
    alarm = the_alarm(hm)
    if alarm is None or _needs_code(hm, alarm):
        return {m: False for m in MOMENTS}
    return {m: _arm_choice(hm, alarm, m) is not None for m in MOMENTS}


async def _save_learn(hm: HouseMind) -> None:
    w = hm.security
    await hm.db.set_setting(
        "alarm_learn", {"answers": w.answers, "offered": w.offered, "quick_off": w.quick_off}
    )


async def _self_arm(hm: HouseMind, moment: str, on: bool, who: str) -> None:
    """The standing yes for one moment (E-540), kept."""
    w = hm.security
    if on:
        w.self_arm[moment] = who
    else:
        w.self_arm.pop(moment, None)
    w.quick_off.pop(moment, None)
    await hm.db.set_setting("alarm_self", dict(w.self_arm))
    await _save_learn(hm)


def _only_not_quiet(hm: HouseMind, snapshot: WorldState) -> bool:
    """Away and unsafe only because a sensor saw someone just now – the people who just left:
    worth waiting for (E-540). Someone without a phone, or not away: a question at once."""
    assert hm.world is not None
    return snapshot.mode in AWAY_MODES and not hm.world.modes.phoneless


async def _self_waiting(hm: HouseMind, snapshot: WorldState) -> None:
    """Everyone just left, the standing yes (E-540): on by itself once no sensor saw anyone
    for ten minutes; still not quiet after a quarter of an hour – someone may be there – the
    question after all. Someone home again, or the alarm on: nothing."""
    assert hm.world is not None
    w = hm.security
    waiting = w.self_wait
    if waiting is None:
        return
    moment, how, since = waiting
    alarm = the_alarm(hm)
    state = hm.world.states.get(alarm) if alarm else None
    if (
        alarm is None
        or state is None
        or state.state != "disarmed"
        or moment not in w.self_arm
        or not _still(f"alarm_arm:{how}", snapshot.mode, snapshot.local.hour)
    ):
        w.self_wait = None
        return
    if not _unsafe(hm, snapshot, moment):
        w.self_wait = None
        await _arm_myself(hm, alarm, how, moment, snapshot)
    elif snapshot.now - since >= SELF_WAIT:
        w.self_wait = None
        await _ask_arm(hm, snapshot, alarm, how, moment)


def _moment_of(how: str) -> str:
    return "away" if how == "away" else "night"


def _unsafe(hm: HouseMind, snapshot: WorldState, moment: str) -> bool:
    """Not now by itself (E-540): away while a sensor saw someone in the last ten minutes, or
    people without a phone live here; night while a window or outside door is open."""
    assert hm.world is not None
    if moment == "away":
        # Not the guests flag: leaving through the hall makes the house Gasten for a moment,
        # and the flag stays after; what the sensors see now is what counts.
        if snapshot.mode not in AWAY_MODES:
            return True
        if hm.world.modes.phoneless:
            return True  # someone without a phone may be home, asleep: always the question
        for area_id, occupancy in snapshot.occupancy.items():
            if not hm.world.indoors(area_id):
                continue
            last = occupancy.last_activity
            if occupancy.occupied or (last is not None and snapshot.now - last < QUIET_FOR):
                return True
        return False
    return snapshot.mode is not HouseMode.SLEEP or any(
        (s := hm.world.states.get(e)) is not None and s.state == "on" for e in hm.openings
    )


async def _learn_answer(hm: HouseMind, moment: str, yes: bool) -> None:
    """An answer to "switch it on?" – or switching it on by hand at that moment (E-540). The
    last five yes, and no standing yes yet: one question to the administrators."""
    w = hm.security
    w.answers[moment] = [*w.answers.get(moment, []), yes][-SELF_AFTER:]
    await _save_learn(hm)
    if moment in w.self_arm or not yes:
        return
    if len(w.answers[moment]) < SELF_AFTER or not all(w.answers[moment]):
        return
    if not _self_can(hm).get(moment):
        return  # a code, or no such mode: it stays a question
    now = hm.clock.now()
    asked = w.offered.get(moment)
    if asked is not None and now - datetime.fromisoformat(asked) < SELF_ASK_AGAIN:
        return
    w.offered[moment] = now.isoformat()
    await _save_learn(hm)
    assert hm.world is not None
    alarm = the_alarm(hm)
    await hm._raise(
        Kind.LEARNING,
        f"alarm_self_offer:{moment}",
        f"notify.alarm.self_offer_{moment}",
        {"name": _name(hm, alarm) if alarm else "", "count": SELF_AFTER},
        hm.world.snapshot(now),
        "admins",
        (("yes", "notify.answer.from_now_yes"), ("no", "notify.answer.not_ever")),
    )


async def _arm_myself(
    hm: HouseMind, alarm: str, how: str, moment: str, snapshot: WorldState
) -> bool:
    """Switch it on with the standing yes (E-540), and say so at once."""
    assert hm.gate is not None
    w = hm.security
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PERSON_ANSWER,  # the administrator's yes, given once for this moment
            alarm,
            f"alarm_control_panel.alarm_arm_{how}",
            {},
            "alarm_self",
            created_at=snapshot.now,
            user_id=w.self_arm.get(moment),
            facts={"alarm_self": moment},
        )
    )
    await hm._record(decision)
    if decision.outcome is not Outcome.EXECUTED:
        _LOGGER.warning("Switching the alarm on by itself did not work: %s", decision.reason)
        return False
    w.armed_by_me = (alarm, snapshot.now, moment)
    lang = hm._language()
    mode = hm.languages.text(lang, f"ui.logbook.alarm.mode.{how}")
    await hm._raise(
        Kind.IMPORTANT,
        f"alarm_self:{moment}",
        f"notify.alarm.self_{moment}",
        {"name": _name(hm, alarm), "mode": mode},
        snapshot,
        "away" if moment == "away" else "home",
    )
    return True


def coverings(hm: HouseMind, classes: frozenset[str] = OPENABLE_CLASSES) -> list[str]:
    """What may open at an alarm (B-170, the administrator's choice): only curtains, blinds
    and shades – never a shutter (a garage roller door often is one). At a fire (E-546) the
    caller adds roller shutters. Never anything that opens a garage door, gate or door
    (``openers``, a class or a role), nothing in a garage, and nothing in a group with one:
    one function for both, so what keeps a door shut holds for both."""
    assert hm.gate is not None
    roles, openers = hm.gate.state.roles, hm.gate.state.openers

    def door(e: str) -> bool:
        entity = hm.model.entities.get(e)
        return (
            e in openers
            or roles.get(e) in OPENER_ROLES
            or (entity is not None and entity.device_class in PROTECTED_COVER_CLASSES)
        )

    risky = {
        member
        for group, members in hm.model.groups.items()
        if any(door(e) for e in (group, *members))
        for member in (group, *members)
    }
    found = []
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.domain != "cover" or entity.hidden or entity.disabled or entity.entity_category:
            continue
        if entity.device_class not in classes or entity_id in hm.model.groups:
            continue
        area = hm.model.area_of(entity_id)
        if area is not None and hm.area_roles.get(area.id) == "garage":
            continue
        if door(entity_id) or entity_id in risky:
            continue
        kind = recognise(hm.catalog, entity).kind
        if kind is not None and kind.key == "cover.window_covering":
            found.append(entity_id)
    return found


def _name(hm: HouseMind, entity_id: str) -> str:
    entity = hm.model.entities.get(entity_id)
    return entity.name if entity else entity_id


# -- a change -------------------------------------------------------------------------------


async def on_change(hm: HouseMind, change: StateChange) -> None:
    """Every change passes here: a detection, or the alarm."""
    assert hm.world is not None
    now = hm.clock.now()
    detection = hm.cameras.detect(change, now) if hm.cameras is not None else None
    if detection is not None:
        await on_detection(hm, detection)
    if change.entity_id.startswith("alarm_control_panel."):
        await _alarm_changed(hm, change)
    if hm.security.before and change.source in (Source.PERSON, Source.PHYSICAL):
        # Someone changed a lamp or covering after the alarm: that stays (E-534).
        for unit in list(hm.security.before):
            if change.entity_id == unit or change.entity_id in hm.model.groups.get(unit, ()):
                del hm.security.before[unit]


async def on_detection(hm: HouseMind, detection: Detection) -> None:
    assert hm.world is not None
    w = hm.security
    kind, now = detection.kind, detection.at
    if kind == "animal":
        return  # never a person, never a message (E-90, E-182)
    inside = hm.world.indoors(detection.area_id)
    if kind == "motion" and not inside:
        return  # trees, cars and cats: outside a camera's own motion counts for nothing
    same = (detection.camera or detection.entity_id, kind)
    before = w.last.get(same)
    w.last[same] = now
    if before is not None and (now - before).total_seconds() < w.words.same_seconds:
        return  # the camera reported it twice: as an event and as a sensor
    if kind in ("person", "doorbell", "motion") and detection.area_id is not None:
        # Someone in that room – inside as with a motion sensor, outside as someone outside;
        # first, before it is written down (code QA 3, punt 18).
        hm.world.occupancy.seen(detection.area_id, now, person=kind != "motion")
    await hm.db.execute(
        "INSERT INTO detections(at, kind, area_id, camera) VALUES(?,?,?,?)",
        (now.isoformat(), kind, detection.area_id, detection.camera),
    )
    if kind == "motion":
        return
    snapshot = hm.world.snapshot(now)
    away = snapshot.residents_home == 0 and snapshot.mode in AWAY_MODES
    asleep = snapshot.mode is HouseMode.SLEEP
    if kind == "person" and detection.camera:
        w.seen[detection.camera] = now
    if kind == "person" and not inside and detection.area_id and snapshot.outside_dark:
        await _outside_light(hm, detection, snapshot)
    if kind == "doorbell":
        w.doorbell = (detection.camera, now)  # every wall tablet in kiosk mode (E-187)
    if kind in ("person", "vehicle", "doorbell"):
        if away:
            await _message(hm, detection, snapshot, Kind.IMPORTANT, image=True, concerns="away")
        return  # at home the doorbell rings itself; asleep nothing (E-182, B-66)
    if kind == "package":
        await _message(hm, detection, snapshot, Kind.INFO, image=False, concerns="home")
        return
    if kind in ("smoke", "co"):
        await _heard_alarm(hm, detection, snapshot, inside)
        return
    if kind == "glass":
        if inside:  # an alarm situation: also at night, with the image (16.3)
            await _message(hm, detection, snapshot, Kind.IMPORTANT, image=True, safety=True)
        return
    if kind in w.sounds and not asleep:
        if kind == "baby":  # with consent only (care and wellbeing): to who is home
            await _message(hm, detection, snapshot, Kind.IMPORTANT, image=False, concerns="home")
        else:
            await _message(hm, detection, snapshot, Kind.INFO, image=False, concerns="home")


def _where(hm: HouseMind, detection: Detection) -> str:
    area = hm.model.areas.get(detection.area_id or "")
    if area is not None:
        return area.name
    return _name(hm, detection.camera) if detection.camera else _name(hm, detection.entity_id)


async def _message(
    hm: HouseMind,
    detection: Detection,
    snapshot: WorldState,
    kind: Kind,
    *,
    image: bool,
    concerns: str = "home",
    safety: bool = False,
) -> None:
    """One message per camera per ten minutes; another detection updates it. While nobody
    is home all cameras together are one message (E-373)."""
    assert hm.router is not None
    if concerns == "away" and detection.kind in ("person", "vehicle", "doorbell"):
        await _camera_bundle(hm, detection, snapshot, kind, image=image)
        return
    w = hm.security
    now = detection.at
    group = "glass" if detection.kind == "glass" else "camera"
    topic = f"{group}:{detection.camera or detection.entity_id}"
    if detection.kind not in ("person", "vehicle", "doorbell", "glass"):
        topic += f":{detection.kind}"  # a package or a sound: a message of its own
    key = f"notify.camera.{detection.kind}"
    local = now.astimezone(snapshot.local.tzinfo)
    window = w.windows.get(topic)
    minutes = timedelta(minutes=w.words.window_minutes)
    info = kind is Kind.INFO
    if window is not None and topic in hm._raised and (info or now - window.start < minutes):
        window.count += 1
        window.last = now
        params = {"where": _where(hm, detection), "count": window.count, "at": f"{local:%H:%M}"}
        if detection.camera and detection.camera in w.described:
            params["seen"] = w.described[detection.camera]  # until a newer image is described
        await hm.router.refresh(topic, params, hm._people, now, snapshot.local)
        _describe_soon(hm, topic, detection)
        return
    if topic in hm._raised:
        await hm._resolve(topic)  # ten minutes later: a new message
    w.windows[topic] = Window(now, now, 1, key, info, concerns == "away")
    camera = detection.camera
    push: dict[str, str] = {}
    if camera and image:
        push["image"] = f"/api/camera_proxy/{camera}"
    if camera:
        push["url"] = f"entityId:{camera}"  # a tap opens the camera in Home Assistant
    params = {"where": _where(hm, detection), "count": 1, "at": f"{local:%H:%M}"}
    w.described.pop(camera or "", None)  # a new message: what was seen before is old
    await hm._raise(kind, topic, key, params, snapshot, concerns, (), safety, push)
    _describe_soon(hm, topic, detection)


async def _camera_bundle(
    hm: HouseMind, detection: Detection, snapshot: WorldState, kind: Kind, *, image: bool
) -> None:
    """While nobody is home every camera is one message (E-373), a line per place and kind –
    "9 keer een voertuig bij Tuin, het laatst om 08:48. 5 keer iemand bij Oprit, …" – with
    the image of the camera that saw something last. Another detection updates it quietly;
    after half an hour without one, a new message. Before, each camera had its own message
    every ten minutes: 29 in three hours in the real house."""
    assert hm.router is not None
    w = hm.security
    now = detection.at
    local = now.astimezone(snapshot.local.tzinfo)
    window = w.windows.get(CAMERA_BUNDLE)
    quiet = timedelta(minutes=w.words.bundle_minutes)
    fresh = window is None or CAMERA_BUNDLE not in hm._raised or now - window.last >= quiet
    if fresh:
        if CAMERA_BUNDLE in hm._raised:
            await hm._resolve(CAMERA_BUNDLE)  # half an hour quiet: a new message
        window = Window(now, now, 0, CAMERA_BUNDLE_KEY, away=True)
        w.windows[CAMERA_BUNDLE] = window
    assert window is not None
    line = (detection.area_id or "", detection.kind)
    part = window.parts.setdefault(
        line,
        {"area": line[0], "where": _where(hm, detection), "kind": detection.kind, "count": 0},
    )
    if detection.camera:
        part["camera"] = detection.camera  # whose image an AI in the house may describe
    part["count"] += 1
    part["at"] = f"{local:%H:%M}"
    part["last"] = now.isoformat()
    window.count += 1
    window.last = now
    parts = sorted(window.parts.values(), key=lambda p: str(p["last"]), reverse=True)
    params: dict[str, Any] = {
        "parts": [{k: v for k, v in p.items() if k != "last"} for p in parts],
        "at": f"{local:%H:%M}",
        # In the house's language, for where a message is said without a person (Vesta).
        "lines": bundle_lines(hm.languages, hm._language(), parts),
    }
    push: dict[str, str] = {}
    if detection.camera and image:
        push["image"] = f"/api/camera_proxy/{detection.camera}"
    if detection.camera:
        push["url"] = f"entityId:{detection.camera}"  # a tap opens the camera in HA
    if not fresh:
        await hm.router.refresh(CAMERA_BUNDLE, params, hm._people, now, snapshot.local, push)
        _describe_soon(hm, CAMERA_BUNDLE, detection)
        return
    await hm._raise(
        kind, CAMERA_BUNDLE, CAMERA_BUNDLE_KEY, params, snapshot, "away", (), False, push
    )
    _describe_soon(hm, CAMERA_BUNDLE, detection)


SEEN = frozenset({"person", "vehicle", "doorbell", "package"})  # what an image can show


def _describe_soon(hm: HouseMind, topic: str, detection: Detection) -> None:
    """With an AI in the house and an administrator's own yes (E-424): what the camera's image
    shows is added to its message, beside the core – never by an AI in the cloud."""
    camera = detection.camera
    if camera is None or detection.kind not in SEEN or not hm.ai.images_on():
        return
    hm.ai_jobs.submit(f"image:{camera}", lambda: _describe(hm, topic, camera))


async def _describe(hm: HouseMind, topic: str, camera: str) -> None:
    label = hm.ai.labels.to_label.get(camera, "camera")
    text = await hm.ai.describe_image(camera, label, hm.languages.resolve(hm._language()))
    if not text or hm.router is None or hm.world is None:
        return
    hm.security.described[camera] = text
    row = await hm.db.fetchone(
        "SELECT params FROM notifications WHERE topic = ? AND resolved_at IS NULL", (topic,)
    )
    if row is None:
        return
    params = json.loads(row["params"] or "{}")
    if topic == CAMERA_BUNDLE:
        window = hm.security.windows.get(CAMERA_BUNDLE)
        for part in [*params.get("parts", []), *(window.parts.values() if window else [])]:
            if part.get("camera") == camera:
                part["seen"] = text
        params["lines"] = bundle_lines(hm.languages, hm._language(), params.get("parts", []))
    else:
        params["seen"] = text
    now = hm.clock.now()
    await hm.router.refresh(topic, params, hm._people, now, hm.world.snapshot(now).local)


async def _outside_light(hm: HouseMind, detection: Detection, snapshot: WorldState) -> None:
    """Someone really outside in the dark: the lamps there, as when coming in (E-183)."""
    assert hm.world is not None
    for entity in _lamps(hm, detection.area_id):
        state = hm.world.states.get(entity.entity_id)
        if entity.entity_id in hm.security.lit:
            hm.security.lit[entity.entity_id] = detection.at  # stays on a little longer
            continue
        if state is None or state.state != "off":
            continue
        decision = await hm.consider(
            ActionRequest(
                Layer.REFLEX,
                entity.entity_id,
                "light.turn_on",
                {},
                "camera_person",
                skill=LIGHT_SKILL,
                area_id=detection.area_id,
                created_at=detection.at,
                facts={"area": _where(hm, detection)},
            )
        )
        if decision is not None and decision.outcome is Outcome.EXECUTED:
            hm.security.lit[entity.entity_id] = detection.at


def _lamps(hm: HouseMind, area_id: str | None) -> list[Entity]:
    """The lamps there as the skills see them (E-458): a group that steers them instead of its
    lamps one by one, nothing left alone, no status light or setting."""
    if hm.skills is None or area_id is None or area_id not in hm.model.areas:
        return []
    return hm.skills._entities(area_id, "light")


async def _heard_alarm(
    hm: HouseMind, detection: Detection, snapshot: WorldState, inside: bool
) -> None:
    """A smoke or CO alarm heard by a camera (an event, no sensor of its own): inside an
    emergency with the lights on to see the way out, outside only a message (E-80, E-178)."""
    assert hm.emergencies is not None and hm.gate is not None and hm.world is not None
    emergency = Emergency.SMOKE if detection.kind == "smoke" else Emergency.CARBON_MONOXIDE
    topic = f"emergency:{detection.entity_id}"
    hm.security.windows[topic] = Window(detection.at, detection.at, 1, "heard")
    where = {"area": _where(hm, detection)}
    if topic in hm._raised:
        return
    if not inside:
        await hm._raise(Kind.EMERGENCY, topic, f"notify.outside_{emergency.value}", where, snapshot)
        return
    executed = []
    for request in hm.emergencies.requests_for(
        emergency, detection.entity_id, detection.area_id, hm.world.states
    ):
        decision = await hm.gate.submit(request)
        await hm._record(decision)
        executed.append(decision)
    done = hm._done_text(emergency, executed)
    await hm._raise(
        Kind.EMERGENCY,
        topic,
        f"notify.heard_{emergency.value}",
        {**where, "done": done},
        snapshot,
    )


# -- the alarm (E-121, E-188) ---------------------------------------------------------------


async def _alarm_changed(hm: HouseMind, change: StateChange) -> None:
    assert hm.world is not None
    new = change.new.state if change.new is not None else None
    old = change.old.state if change.old is not None else None
    topic = f"alarm:{change.entity_id}"
    await _log_change(hm, change.entity_id, old, new)
    if new == "triggered" and old != "triggered":
        await _alarm_goes_off(hm, change.entity_id)
    elif new != "triggered" and topic in hm._raised:
        await hm._resolve(topic)
    if new != "triggered":
        await alarm_reaction.over(hm, topic)  # E-542 – E-544
    if new is not None and new.startswith("armed"):
        for mode in ("away", "night", "home"):
            if f"alarm_arm:{mode}" in hm._raised:
                # Switched on by hand while asked: a yes too (E-540).
                await _learn_answer(hm, _moment_of(mode), True)
            await hm._resolve(f"alarm_arm:{mode}")
    if new in IN_USE and change.entity_id not in hm.security.in_use:
        hm.security.in_use.add(change.entity_id)  # in use from now on (E-311)
        hm.world.alarm_entity = the_alarm(hm)


async def _log(
    hm: HouseMind,
    alarm: str,
    state: str,
    previous: str | None,
    lamps: int = 0,
    covers: int = 0,
    seconds: int | None = None,
) -> None:
    """One line of the alarm for the Logboek (E-533)."""
    await hm.db.execute(
        "INSERT INTO alarm_log(at, alarm, state, previous, lamps, covers, seconds) "
        "VALUES(?,?,?,?,?,?,?)",
        (hm.clock.now().isoformat(), alarm, state, previous, lamps, covers, seconds),
    )


async def _log_change(hm: HouseMind, alarm: str, old: str | None, new: str | None) -> None:
    """Switched on (or to another mode), and switched off after it was on or went off; not
    the steps in between, nor a device that dropped away and came back."""
    if old is None or new is None or old in ("unavailable", "unknown"):
        return
    now = hm.clock.now()
    w = hm.security
    if new.startswith("armed") and new != old:
        await _log(hm, alarm, new, old)
    elif new == "disarmed" and old in IN_USE:
        await _off_after_mine(hm, alarm, now)  # E-540
        went = w.went_off.pop(alarm, None)
        seconds = round((now - went).total_seconds()) if went is not None else None
        await _log(hm, alarm, "disarmed", old, seconds=seconds)
        if w.before:
            w.back_at, w.back_alarm = now + BACK_AFTER, alarm  # the house back in a minute
        if went is not None and seconds is not None:
            await _was_off(hm, alarm, went, seconds)
    if new is not None and new.startswith("armed"):
        await hm._resolve(f"alarm_was:{alarm}")  # on again: the last time is over


async def _was_off(hm: HouseMind, alarm: str, went: datetime, seconds: int) -> None:
    """It went off and is switched off again (E-539): one important word for everyone, and
    Vesta stays orange until someone saw it – so whoever comes home later knows."""
    assert hm.world is not None
    lang = hm._language()
    t = hm.languages.text
    after = (
        t(lang, "ui.logbook.alarm.seconds", count=seconds)
        if seconds < 120
        else t(lang, "ui.logbook.alarm.minutes", count=round(seconds / 60))
    )
    at = went.astimezone(hm.world.tz).strftime("%H:%M")
    await hm._raise(
        Kind.IMPORTANT,
        f"alarm_was:{alarm}",
        "notify.alarm.was_off",
        {"name": _name(hm, alarm), "at": at, "after": after},
        hm.world.snapshot(hm.clock.now()),
    )


async def _off_after_mine(hm: HouseMind, alarm: str, now: datetime) -> None:
    """Switched off after HouseMind switched it on by itself (E-540): its word goes; within
    ten minutes it was not the moment – twice in a week and HouseMind asks again."""
    w = hm.security
    mine, w.armed_by_me = w.armed_by_me, None
    for moment in MOMENTS:
        await hm._resolve(f"alarm_self:{moment}")
    if mine is None or mine[0] != alarm or now - mine[1] > QUICK_OFF:
        return
    moment = mine[2]
    week = [x for x in w.quick_off.get(moment, []) if now - datetime.fromisoformat(x) < QUICK_WEEK]
    w.quick_off[moment] = [*week, now.isoformat()]
    if len(w.quick_off[moment]) < QUICK_TIMES:
        await _save_learn(hm)
        return
    await _self_arm(hm, moment, False, "")
    w.answers[moment] = []
    await _save_learn(hm)
    assert hm.world is not None
    await hm._raise(
        Kind.INFO,
        f"alarm_self_back:{moment}",
        f"notify.alarm.self_back_{moment}",
        {"name": _name(hm, alarm)},
        hm.world.snapshot(now),
        "admins",
    )


# -- Test het huis (E-541) ---------------------------------------------------------------


def test_reaction(
    hm: HouseMind, area_id: str, now: datetime
) -> tuple[str, list[ActionRequest], int] | None:
    """What HouseMind does in this room when the alarm goes off (E-541): its lamps full and
    white, the coverings an administrator chose open – and how many lamps elsewhere go on too.
    None without an alarm in use. The alarm itself is never touched by a test."""
    alarm = the_alarm(hm)
    if alarm is None:
        return None
    here: list[ActionRequest] = []
    elsewhere = 0
    for request in _alarm_requests(hm, f"alarm:{alarm}", now, alarm):
        area = hm.model.area_of(request.entity_id)
        if area is not None and area.id == area_id:
            here.append(request)
        elif request.action == "light.turn_on":
            elsewhere += _units(hm, request.entity_id)
    # Music and television of this room quiet (E-542).
    here += alarm_reaction.media_requests(hm, f"alarm:{alarm}", now, {"alarm": alarm}, area_id)
    return alarm, here, elsewhere


def test_arming(
    hm: HouseMind, moment: str, picture: WorldState, states: dict[str, State], lang: str
) -> tuple[str, dict[str, Any]] | None:
    """What HouseMind would do with the alarm in this situation (E-541): when everyone has
    left ("away") or the house goes to sleep ("night") – switch it on itself, ask, or nothing.
    The text key and its values; None when there is nothing to say. Only ever pretended."""
    assert hm.world is not None
    alarm = the_alarm(hm)
    if alarm is None:
        return ("test.arm.which", {}) if len(alarms(hm)) > 1 else None
    state = states.get(alarm)
    name = _name(hm, alarm)
    if state is None:
        return None
    if state.state.startswith("armed"):
        return "test.arm.is_on", {"name": name}
    if state.state != "disarmed":
        return None
    how = _arm_choice(hm, alarm, moment)
    if how is None:
        return "test.arm.no_mode", {"name": name}
    mode = hm.languages.text(lang, f"ui.logbook.alarm.mode.{how}")
    values = {"name": name, "mode": mode}
    if _needs_code(hm, alarm):
        return "test.arm.code", values
    if moment not in hm.security.self_arm:
        return "test.arm.ask", values
    if moment == "away":
        if hm.world.modes.phoneless:
            return "test.arm.phoneless", values
        return "test.arm.self_away", values  # once no sensor saw anyone for ten minutes
    if any((s := states.get(e)) is not None and s.state == "on" for e in hm.openings):
        return "test.arm.open", values
    return "test.arm.self_night", values


def test_message(hm: HouseMind) -> tuple[str, dict[str, Any], dict[str, str]] | None:
    """Doe het echt (E-541): the message the tester gets – what everyone gets when the alarm
    really goes off, said to be a test, with a camera's image as it would be."""
    alarm = the_alarm(hm)
    if alarm is None:
        return None
    camera = _alarm_camera(hm, alarm, hm.clock.now())
    push = {"image": f"/api/camera_proxy/{camera}", "url": f"entityId:{camera}"} if camera else {}
    return "notify.alarm.test", {"name": _name(hm, alarm)}, push


def _units(hm: HouseMind, entity_id: str) -> int:
    """The lamps themselves: a group counts its lamps (E-458)."""
    return len(hm.model.groups.get(entity_id, ())) or 1


async def _alarm_goes_off(hm: HouseMind, alarm: str) -> None:
    """E-121: all lights on, the window coverings open, a spoken warning everywhere, and an
    emergency message with an image to every phone."""
    assert hm.world is not None and hm.gate is not None
    now = hm.clock.now()
    snapshot = hm.world.snapshot(now)
    topic = f"alarm:{alarm}"
    executed = []
    w = hm.security
    w.back_at = None  # gone off again before the house went back: it waits for the next off
    w.went_off[alarm] = now
    for request in _alarm_requests(hm, topic, now, alarm):
        state = hm.world.states.get(request.entity_id)
        if state is not None and request.entity_id not in w.before:
            w.before[request.entity_id] = state  # how it was, for afterwards (E-534)
        decision = await hm.gate.submit(request)
        await hm._record(decision)
        executed.append(decision)
    done_kinds = [d.request.action for d in executed if d.outcome is Outcome.EXECUTED]
    lamps = sum(  # the lamps themselves: a group counts its lamps (E-458)
        _units(hm, d.request.entity_id)
        for d in executed
        if d.outcome is Outcome.EXECUTED and d.request.action == "light.turn_on"
    )
    await _log(hm, alarm, "triggered", None, lamps, done_kinds.count("cover.open_cover"))
    parts: list[dict[str, Any]] = []
    if lamps:  # counted, never "all" (QA 2, M-15)
        parts.append({"t": "notify.done.lights", "p": {"count": lamps}})
    if "cover.open_cover" in done_kinds:
        parts.append({"t": "notify.done.alarm_covers"})
    done = {"join": parts, "sep": ""}
    camera = _alarm_camera(hm, alarm, now)
    push = {"image": f"/api/camera_proxy/{camera}", "url": f"entityId:{camera}"} if camera else {}
    push.update(alarm_reaction.links(hm, "burglary", camera, alarm))  # E-545
    params = {"name": _name(hm, alarm), "done": done}
    await hm._raise(Kind.EMERGENCY, topic, "notify.alarm.triggered", params, snapshot, push=push)
    # Media quiet, the warning again, the tablet, the signal lamps flashing (E-542 – E-544).
    blink = [
        d.request.entity_id
        for d in executed
        if d.outcome is Outcome.EXECUTED
        and d.request.entity_id in w.signal
        and not alarm_reaction.flashes_itself(hm, d.request.entity_id)
    ]
    where = alarm_reaction.where_it_began(hm, now)
    await alarm_reaction.begin(
        hm, "burglary", topic, topic, snapshot, where, camera, alarm, blink, {"alarm": alarm}
    )
    lang = hm._language()
    text = hm.languages.text(lang, "notify.alarm.triggered", name=_name(hm, alarm), done=done)
    spoken = await hm._vesta_room(snapshot, "emergency", {"text": text}, topic, 100)
    for area_id in list(hm.speech.devices):
        await hm.say(snapshot, area_id, spoken, "emergency")
    await hm._vesta_phones("emergency", {"text": text}, topic, 100, set())


def _alarm_requests(
    hm: HouseMind, reason: str, now: datetime, alarm: str = ""
) -> list[ActionRequest]:
    facts = {"alarm": alarm or reason.removeprefix("alarm:")}  # its reason, kept (E-533)
    out = []
    openable = set(coverings(hm))
    lamps: set[str] = set()
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.hidden or entity.entity_category or entity_id in hm.model.groups:
            continue
        if entity.domain == "light":
            if entity_id in hm.security.signal and hm.model.unit(entity_id) == entity_id:
                continue  # a signal lamp on its own: red, below (E-543)
            # A lamp through the group that steers it, once; nothing left alone (E-458).
            unit = hm.model.unit(entity_id)
            if unit is None or unit in lamps or unit not in hm.model.entities:
                continue
            lamps.add(unit)
            entity_id, entity = unit, hm.model.entities[unit]
        kind = recognise(hm.catalog, entity).kind
        if kind is None:
            continue
        if entity.domain == "light":
            # Full and white, as far as the lamp can (E-495).
            turn_on = kind.allowed("light.turn_on")
            data = white(entity_id, hm.light_levels, turn_on.values) if turn_on else {}
            out.append(
                ActionRequest(
                    Layer.EMERGENCY,
                    entity_id,
                    "light.turn_on",
                    data,
                    reason,
                    created_at=now,
                    facts=facts,
                )
            )
        elif (
            kind.key == "cover.window_covering"
            and entity_id in hm.security.open_covers
            and entity_id in openable  # checked again at the moment it goes off
        ):
            # Only what an administrator chose (B-170); never a garage door, gate or door.
            out.append(
                ActionRequest(
                    Layer.EMERGENCY,
                    entity_id,
                    "cover.open_cover",
                    {},
                    reason,
                    created_at=now,
                    facts=facts,
                )
            )
    # The signal lamps an administrator chose: red, flashing – last, so a group's white does
    # not win over a lamp in it (E-543).
    for lamp in sorted(hm.security.signal):
        found = hm.model.entities.get(lamp)
        if found is not None and not found.hidden and not found.disabled:
            out.append(alarm_reaction.signal_request(hm, lamp, reason, now, facts))
    return out


def _light_back(hm: HouseMind, entity_id: str, was: State) -> dict[str, Any] | None:
    """How a lamp that was on goes back: its brightness and colour as far as the lamp can."""
    entity = hm.model.entities.get(entity_id)
    kind = recognise(hm.catalog, entity).kind if entity is not None else None
    allowed = kind.allowed("light.turn_on") if kind is not None else None
    if allowed is None:
        return None
    attrs = was.attributes
    data: dict[str, Any] = {}
    brightness = attrs.get("brightness")
    if isinstance(brightness, int | float) and brightness > 0:
        data["brightness_pct"] = max(1, min(100, round(brightness * 100 / 255)))
    mode = attrs.get("color_mode")
    kelvin = attrs.get("color_temp_kelvin")
    if mode == "color_temp" and isinstance(kelvin, int | float):
        data["color_temp_kelvin"] = int(kelvin)
    elif mode in ("hs", "xy", "rgb", "rgbw", "rgbww") and isinstance(attrs.get("hs_color"), list):
        data["hs_color"] = list(attrs["hs_color"])
    return {k: v for k, v in data.items() if k in allowed.values}


async def _put_back(hm: HouseMind, snapshot: WorldState) -> None:
    """The alarm was switched off a minute ago: the lamps and coverings as they were just
    before it went off (E-534). What someone changed since stays; a lamp in a room where
    someone is now stays on and goes off as usual once the room is empty."""
    assert hm.world is not None and hm.gate is not None
    w = hm.security
    alarm = w.back_alarm or ""
    now = snapshot.now
    facts = {"alarm": alarm, "back": True}
    reason = f"alarm_back:{alarm}"
    lamps = covers = 0
    for entity_id, was in sorted(w.before.items()):
        state = hm.world.states.get(entity_id)
        if state is None:
            continue
        request: ActionRequest | None = None
        if entity_id.startswith("light."):
            if was.state == "off" and state.state == "on":
                area = hm.model.area_of(entity_id)
                if area is not None and snapshot.occupied(area.id):
                    continue  # someone there: it goes off as usual when they leave
                request = ActionRequest(
                    Layer.EMERGENCY, entity_id, "light.turn_off", {}, reason, created_at=now
                )
            elif was.state == "on" and state.state == "on":
                data = _light_back(hm, entity_id, was)
                if data:
                    request = ActionRequest(
                        Layer.EMERGENCY, entity_id, "light.turn_on", data, reason, created_at=now
                    )
        elif entity_id.startswith("cover.") and was.state == "closed" and state.state != "closed":
            request = ActionRequest(
                Layer.EMERGENCY, entity_id, "cover.close_cover", {}, reason, created_at=now
            )
        if request is None:
            continue
        decision = await hm.gate.submit(replace(request, facts=facts))
        await hm._record(decision)
        if decision.outcome is Outcome.EXECUTED:
            if entity_id.startswith("light."):
                lamps += _units(hm, entity_id)
            else:
                covers += 1
    w.before.clear()
    w.back_at = w.back_alarm = None
    if lamps or covers:
        await _log(hm, alarm, "back", "disarmed", lamps, covers)


def _alarm_camera(hm: HouseMind, alarm: str, now: datetime) -> str | None:
    """The camera that last saw someone (ten minutes), else one near the alarm, else any."""
    recent = [
        (at, camera)
        for camera, at in hm.security.seen.items()
        if now - at <= timedelta(minutes=hm.security.words.window_minutes)
    ]
    if recent:
        return max(recent)[1]
    cameras = sorted(
        e.entity_id
        for e in hm.model.entities.values()
        if e.domain == "camera" and not e.disabled and not e.hidden
    )
    if not cameras:
        return None
    area = hm.model.area_of(alarm)
    near = [
        c for c in cameras if area is not None and (a := hm.model.area_of(c)) and a.id == area.id
    ]
    assert hm.world is not None
    inside = [c for c in cameras if hm.world.indoors(a.id if (a := hm.model.area_of(c)) else None)]
    return (near or inside or cameras)[0]


def _arm_choice(hm: HouseMind, alarm: str, moment: str) -> str | None:
    """Which way to switch it on: away when everyone left; night when going to sleep – the
    home setting when the alarm has no night setting."""
    assert hm.world is not None
    state = hm.world.states.get(alarm)
    features = state.attributes.get("supported_features") if state is not None else None
    entity = hm.model.entities.get(alarm)
    if not isinstance(features, int):
        features = entity.supported_features if entity and entity.supported_features else 0
    known = bool(features)
    if moment == "away":
        return "away" if not known or features & ARM_AWAY else None
    if not known or features & ARM_NIGHT:
        return "night"
    return "home" if features & ARM_HOME else None


def _needs_code(hm: HouseMind, alarm: str) -> bool:
    assert hm.world is not None
    state = hm.world.states.get(alarm)
    if state is None:
        return False
    return state.attributes.get("code_arm_required") is not False and bool(
        state.attributes.get("code_format")
    )


async def _suggest_arming(hm: HouseMind, snapshot: WorldState) -> None:
    """Once when everyone has left, once when the house goes to sleep (E-188)."""
    assert hm.world is not None
    w = hm.security
    mode, hour = snapshot.mode, snapshot.local.hour
    before, w.mode_before = w.mode_before, mode
    evening = hour >= 21 or hour < 3
    for topic in [t for t in hm._raised if t.startswith("alarm_arm:")]:
        old = snapshot.now - w.asked_at.get(topic, snapshot.now) >= ARM_KEEP
        if old or not _still(topic, mode, hour):
            await hm._resolve(topic)  # the moment has passed: the question does not come back
    if w.self_wait is not None:
        await _self_waiting(hm, snapshot)
    moment: str | None = None
    occasion = ""
    if before is not None and before is not mode:
        if mode is HouseMode.AWAY and before in (HouseMode.HOME, HouseMode.SLEEP, HouseMode.GUESTS):
            moment, occasion = "away", (hm.world.modes.since or snapshot.now).isoformat()
        elif mode is HouseMode.SLEEP and before is HouseMode.HOME:
            moment, occasion = "night", _evening(snapshot)
    if (
        moment is None
        and evening
        and mode is HouseMode.HOME
        and w.asked.get("night") != (_evening(snapshot))
    ):
        if await hm.db.get_setting("bedtime_checked") == _evening(snapshot):
            moment, occasion = "night", _evening(snapshot)  # the bedtime check (E-107)
    if moment is None or w.asked.get(moment) == occasion:
        return
    alarm = the_alarm(hm)
    if alarm is None and len(alarms(hm)) > 1:
        w.asked[moment] = occasion
        if "alarm_choice" not in hm._raised:
            await _ask_which(hm, snapshot)
        return
    state = hm.world.states.get(alarm) if alarm else None
    if alarm is None or state is None or state.state != "disarmed":
        return
    how = _arm_choice(hm, alarm, moment)
    if how is None:
        return
    w.asked[moment] = occasion
    if moment in w.self_arm and not _needs_code(hm, alarm):  # the standing yes (E-540)
        if not _unsafe(hm, snapshot, moment):
            if await _arm_myself(hm, alarm, how, moment, snapshot):
                return
        elif moment == "away" and _only_not_quiet(hm, snapshot):
            w.self_wait = (moment, how, snapshot.now)  # just left: until the house is quiet
            return
    await _ask_arm(hm, snapshot, alarm, how, moment)


async def _ask_arm(hm: HouseMind, snapshot: WorldState, alarm: str, how: str, moment: str) -> None:
    """ "Alarm op afwezig zetten?" (E-188): yes and no, or with a code a tap that opens it."""
    w = hm.security
    topic = f"alarm_arm:{how}"
    code = _needs_code(hm, alarm)
    key = f"notify.alarm.arm_{how}" + ("_code" if code else "")
    actions = () if code else (("yes", "notify.answer.plan_yes"), ("no", "notify.answer.plan_no"))
    concerns = "away" if moment == "away" else "home"
    push = {"url": f"entityId:{alarm}"}  # with a code: the alarm opens in Home Assistant
    params = {"name": _name(hm, alarm)}
    await hm._raise(Kind.IMPORTANT, topic, key, params, snapshot, concerns, actions, push=push)
    w.asked_at[topic] = snapshot.now


async def _ask_which(hm: HouseMind, snapshot: WorldState) -> None:
    """Which alarm is "the alarm"? Once, to an administrator, with a button per alarm – up to
    three; with more, the message points to House settings (B-67, E-311)."""
    found = alarms(hm)
    buttons = found if len(found) <= CHOICE_BUTTONS else []
    actions = tuple((e, "notify.alarm.choose_button") for e in buttons)
    push = {f"label:{e}": _name(hm, e) for e in buttons}  # each button says its alarm's name
    key = "notify.alarm.choose" if buttons else "notify.alarm.choose_settings"
    await hm._raise(Kind.IMPORTANT, "alarm_choice", key, {}, snapshot, "admins", actions, push=push)


def choice_buttons(hm: HouseMind) -> tuple[list[str], dict[str, str]]:
    """The answers to "which alarm?" in the app: the alarms and their names."""
    found = alarms(hm)
    buttons = found if len(found) <= CHOICE_BUTTONS else []
    return buttons, {e: _name(hm, e) for e in buttons}


def _still(topic: str, mode: HouseMode, hour: int) -> bool:
    """Is it still the moment of this question? Away: everyone is still away. Night: the house
    sleeps, or it is still evening."""
    if topic == "alarm_arm:away":
        return mode in AWAY_MODES
    return mode is HouseMode.SLEEP or not 5 <= hour < 21


def _evening(snapshot: WorldState) -> str:
    local = snapshot.local
    return (local.date() - timedelta(days=1) if local.hour < 12 else local.date()).isoformat()


async def answer(hm: HouseMind, topic: str, code: str, user_id: str | None) -> bool:
    """An answer to "Alarm op afwezig zetten?": yes switches it on, after this person's yes
    (it passes the safety gate like every action); no only closes the question. An answer to
    "which alarm?" is that alarm (E-311)."""
    if topic == "alarm_choice":
        return code in alarms(hm) and await hm.set_house_setting("alarm_entity", code)
    if topic.startswith("alarm_self_offer:") and code in ("yes", "no"):
        moment = topic.split(":", 1)[1]
        await hm._resolve(topic)
        if code == "yes" and moment in MOMENTS:
            await _self_arm(hm, moment, True, user_id or "answer")  # from now on (E-540)
        await hm._notify()
        return True
    if not topic.startswith("alarm_arm:") or code not in ("yes", "no"):
        return False
    how = topic.split(":", 1)[1]
    await hm._resolve(topic)
    await _learn_answer(hm, _moment_of(how), code == "yes")  # E-540
    alarm = the_alarm(hm)
    if code == "no" or alarm is None or how not in ("away", "night", "home"):
        return True
    assert hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    if not _still(topic, snapshot.mode, snapshot.local.hour):
        # A late yes on the phone, while someone is home again: not on (it could go off).
        _LOGGER.info("A late yes to switching the alarm on: the moment has passed")
        return True
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PERSON_ANSWER,
            alarm,
            f"alarm_control_panel.alarm_arm_{how}",
            {},
            "alarm_arm",
            created_at=hm.clock.now(),
            user_id=user_id,
        )
    )
    await hm._record(decision)
    if decision.outcome is not Outcome.EXECUTED:
        _LOGGER.warning("Switching the alarm on did not work: %s", decision.reason)
    await hm._notify()
    return True


# -- every tick ----------------------------------------------------------------------------


async def tick(hm: HouseMind, snapshot: WorldState) -> None:
    assert hm.world is not None
    w = hm.security
    now = snapshot.now
    if w.back_at is not None and now >= w.back_at:
        await _put_back(hm, snapshot)  # a minute after the alarm was switched off (E-534)
    if w.use_read_for != tuple(alarms(hm)):
        await read_use(hm)  # an alarm came or went: which are in use? (E-311)
    if "alarm_choice" in hm._raised and the_alarm(hm) is not None:
        await hm._resolve("alarm_choice")  # it became clear by itself
    for topic, window in list(w.windows.items()):
        quiet = now - window.last
        if topic.startswith("emergency:"):
            if quiet >= timedelta(minutes=w.words.window_minutes):
                del w.windows[topic]  # a heard smoke or CO alarm that stopped
                await hm._resolve(topic)
        elif window.info:
            if topic not in hm._raised:
                del w.windows[topic]  # read, put away, or a week old
        elif quiet >= QUIET_AFTER or (window.away and snapshot.residents_home > 0):
            del w.windows[topic]
            if not topic.startswith("glass:"):
                await hm._resolve(topic)  # someone is home again, or it has been quiet
    hold = timedelta(minutes=w.words.light_minutes)
    for lamp, last in list(w.lit.items()):
        if now - last < hold:
            continue
        del w.lit[lamp]
        state = hm.world.states.get(lamp)
        if state is not None and state.state == "on":
            area = hm.model.area_of(lamp)
            await hm.consider(
                ActionRequest(
                    Layer.REFLEX,
                    lamp,
                    "light.turn_off",
                    {},
                    "camera_person_gone",
                    skill=LIGHT_SKILL,
                    area_id=area.id if area else None,
                    created_at=now,
                )
            )
    if w.doorbell is not None and now - w.doorbell[1] > timedelta(seconds=w.words.doorbell_seconds):
        w.doorbell = None
        await hm._notify()  # the window on the tablets closes
    await _suggest_arming(hm, snapshot)
    await alarm_reaction.tick(hm, snapshot)  # the warning again, the reminder (E-542, E-545)


# -- for the screens -----------------------------------------------------------------------


def doorbell_view(hm: HouseMind) -> dict[str, Any] | None:
    """The doorbell on a wall tablet (E-187): the live image comes straight from Home
    Assistant with the camera's own short-lived key – never through HouseMind."""
    w = hm.security
    if w.doorbell is None or hm.world is None:
        return None
    camera, at = w.doorbell
    left = w.words.doorbell_seconds - (hm.clock.now() - at).total_seconds()
    if left <= 0:
        return None
    state = hm.world.states.get(camera) if camera else None
    token = state.attributes.get("access_token") if state is not None else None
    url = f"/api/camera_proxy_stream/{camera}?token={token}" if camera and token else None
    area = hm.model.area_of(camera) if camera else None
    return {
        "at": at.isoformat(),
        "seconds": w.words.doorbell_seconds,
        "left": round(left),
        "where": area.name if area else (_name(hm, camera) if camera else ""),
        "url": url,
    }


async def day_lines(hm: HouseMind, lang: str, since: datetime, tz: Any) -> list[str]:
    """Level 2 (E-190): "Pakket bezorgd om 14:02" in the day summary."""
    rows = await hm.db.fetchall(
        "SELECT at, kind FROM detections WHERE at >= ? AND kind IN ('package', 'doorbell') "
        "ORDER BY at",
        (since.isoformat(),),
    )
    lines: list[str] = []
    last: dict[str, datetime] = {}
    for row in rows:
        at = datetime.fromisoformat(str(row["at"]))
        kind = str(row["kind"])
        if kind in last and at - last[kind] < timedelta(minutes=hm.security.words.window_minutes):
            continue  # one ring is one line, also when it rang three times
        last[kind] = at
        local = at.astimezone(tz)
        lines.append(hm.languages.text(lang, f"ui.day.camera.{kind}", at=f"{local:%H:%M}"))
    return lines[-4:]


def followed(hm: HouseMind, entity_id: str, lang: str) -> str | None:
    """Level 4 (E-190): per camera which detections HouseMind follows and from what; per
    detecting entity what it counts as."""
    if hm.cameras is None:
        return None
    t = hm.languages.text
    if entity_id.startswith("camera."):
        pairs = hm.cameras.follows(entity_id)
        if not pairs:
            return t(lang, "ui.devices.camera.none")
        kinds = list(dict.fromkeys(t(lang, f"ui.camera.kind.{k}") for k, _ in pairs))
        return t(lang, "ui.devices.camera.follows", kinds=", ".join(kinds))
    source = hm.cameras.sources.get(entity_id)
    if source is None:
        return None
    reported: list[str | None] = (
        [source.kind] if source.kind else [*source.kinds] or [source.default]
    )
    names = ", ".join(t(lang, f"ui.camera.kind.{k}") for k in reported if k)
    camera = _name(hm, source.camera) if source.camera else t(lang, "ui.devices.camera.no_camera")
    return t(lang, f"ui.devices.camera.{source.form}", kinds=names, camera=camera)


async def counts(hm: HouseMind, days: int = 30) -> dict[str, dict[str, int]]:
    """For the diagnostics (16.9): per kind how many entities HouseMind follows and how many
    detections it saw – only numbers, never which camera."""
    since = (hm.clock.now() - timedelta(days=days)).isoformat()
    rows = await hm.db.fetchall(
        "SELECT kind, count(*) AS n FROM detections WHERE at >= ? GROUP BY kind", (since,)
    )
    followed: dict[str, int] = {}
    for source in hm.cameras.sources.values() if hm.cameras else []:
        reported: list[str | None] = (
            [source.kind] if source.kind else [*source.kinds] or [source.default]
        )
        for kind in reported:
            if kind:
                followed[kind] = followed.get(kind, 0) + 1
    if hm.cameras is not None and hm.cameras.smoke:
        followed["heard_alarm_sensors"] = len(hm.cameras.smoke)
    return {
        "followed": dict(sorted(followed.items())),
        "seen": {str(r["kind"]): int(r["n"]) for r in rows},
    }
