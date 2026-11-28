"""Notifications (E-126 t/m E-128, E-20, E-122, E-289 t/m E-291, B-30, B-31, H8.1, H20).

Four kinds:
- emergency  always, to every phone with the HA app, also at night; only an explicit "no"
             to messages in danger stops it (E-20, E-291)
- important  directly, to whom it concerns (B-31), within each person's daily budget;
             safety never waits for a budget, a counter or quiet hours (E-289)
- info       bundled into one moment a day per person (19:00 unless chosen, B-30)
- learning   proposals and questions (B-16)

Everything that is not an emergency or about safety waits for the end of a person's quiet
hours (E-290). One topic is one notification: it is updated, not repeated, and disappears
when solved (E-122). Phone buttons are signed so nobody can fake a "Do it" (E-86, lesson M3).

Each person can put a kind of message off ("Later": 1, 4 or 8 hours, or until tomorrow 08:00)
or switch it off ("Niet meer tonen"), never an emergency; it then neither shows in their app
nor goes to their phone, and Logboek can switch it on again (E-372). The cameras while nobody
is home are one message, a line per place and kind (E-373).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any

from housemind.core.model import State
from housemind.ha.adapter import HAUser, HomeAssistant
from housemind.i18n.catalog import Languages
from housemind.memory.database import Database


class Kind(StrEnum):
    EMERGENCY = "emergency"
    IMPORTANT = "important"
    INFO = "info"
    LEARNING = "learning"


BUDGET = {1: 0, 2: 3, 3: 6, 4: 20}  # messages per day (E-127); questions have their own
# One budget for all questions together, per person per day (E-294): proposals, questions to
# get to know the house, about automations, "Was this good?". Level 4 sets it, default five.
QUESTIONS = {1: 0, 2: 1, 3: 3}
QUESTIONS_EXPERT = 5
SLIDER = {"less": -1, "normal": 0, "more": 2}  # level 2 slider (E-128)
BUNDLE_AT = time(19, 0)  # B-30
ESCALATE_AFTER = timedelta(minutes=15)  # B-31
# Information gets old: after a week it leaves the list, so weekly tips do not pile up. If it
# still holds, HouseMind says it again.
INFO_KEEP = timedelta(days=7)
IGNORED_BEFORE_BUNDLING = 5
# Ignoring is a few hours without any reaction (E-127): a message only counts as ignored
# three hours after it went out, if nobody answered it and it did not solve itself.
IGNORED_AFTER = timedelta(hours=3)
QUIET_DEFAULT = (time(23, 0), time(7, 0))  # quiet hours until HouseMind knows better (E-290)
# About safety: never skipped because it was ignored before or the day's budget is full.
SAFETY_TOPICS = frozenset(
    {
        "open_when_away",
        "guests_unconfirmed",
        "water_running",
        "house",
        "device_hot",
        "emergency",
        "emergency_lost",
        "selfcheck",
    }
)
# Questions about safety: closing the main water valve while water keeps running. They come
# like safety (at night, outside the question budget) and never become a silent try (E-289).
SAFETY_SKILLS = frozenset({"water_leak_flow"})
# About security (16.4, 16.6): someone on the driveway while everyone is away, "Alarm op
# afwezig zetten?". They do not wait for a budget or quiet hours either, but Carefree only
# gets an alarm situation (E-190).
SECURITY_TOPICS = frozenset({"camera", "alarm_arm"})
# The cameras together in one message while nobody is home (E-373): a line per place and kind.
CAMERA_BUNDLE = "camera:away"
CAMERA_BUNDLE_KEY = "notify.camera.bundle"
ALL_CAMERAS = "camera"  # "Later" or "Niet meer tonen" for every camera line while away
LATER = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "8h": timedelta(hours=8)}
MORNING = time(8, 0)  # "Morgen om 08:00"


def part_key(part: dict[str, Any]) -> str:
    """One line of the camera message, as a person can switch it off: a kind at a place."""
    return f"camera:{part.get('area') or ''}:{part.get('kind')}"


def kind_key(topic: str) -> str:
    """What "Later" and "Niet meer tonen" are about (E-372): every camera line together, or
    the message's own subject – this door, this camera's sound."""
    return ALL_CAMERAS if topic == CAMERA_BUNDLE else topic


def later_until(choice: Any, now: datetime, local: datetime) -> datetime:
    """Until when "Later" puts a message off: 1, 4 or 8 hours, or tomorrow at 08:00."""
    if choice == "morning":
        tomorrow = (local + timedelta(days=1)).replace(
            hour=MORNING.hour, minute=MORNING.minute, second=0, microsecond=0
        )
        return tomorrow.astimezone(now.tzinfo)
    return now + LATER.get(str(choice), LATER["1h"])


def notice_text(
    languages: Languages,
    lang: str,
    text_key: str,
    params: dict[str, Any],
    skip: frozenset[str] = frozenset(),
) -> str:
    """A message's words in a language; the camera message line by line, without the lines
    this person put off or switched off (E-372, E-373)."""
    if text_key != CAMERA_BUNDLE_KEY:
        text = languages.text(lang, text_key, **params)
        seen = params.get("seen")
        if isinstance(seen, str) and seen:  # what an AI in the house saw (E-424)
            text = f"{text} {languages.text(lang, 'notify.camera.seen', text=seen)}"
        return text
    lines = bundle_lines(languages, lang, params.get("parts") or [], skip)
    return languages.text(lang, CAMERA_BUNDLE_KEY, lines=lines)


def bundle_lines(
    languages: Languages,
    lang: str,
    parts: list[dict[str, Any]],
    skip: frozenset[str] = frozenset(),
) -> str:
    """The lines of the camera message: "9 keer een voertuig bij Tuin, het laatst om 08:48" –
    and what an AI in the house saw on that camera's image (E-424)."""

    def line(part: dict[str, Any]) -> str:
        text = languages.text(
            lang,
            f"notify.camera.line.{part.get('kind')}",
            count=int(part.get("count") or 1),
            where=str(part.get("where") or ""),
            at=str(part.get("at") or ""),
        )
        seen = part.get("seen")
        if isinstance(seen, str) and seen:
            text = f"{text} ({languages.text(lang, 'notify.camera.seen_line', text=seen)})"
        return text

    return ". ".join(line(part) for part in parts if part_key(part) not in skip)


@dataclass(slots=True)
class Person:
    user_id: str
    name: str
    level: int
    is_admin: bool
    home: bool
    language: str
    preferences: dict[str, Any] = field(default_factory=dict)
    consents: dict[str, Any] = field(default_factory=dict)
    notify_services: list[str] = field(default_factory=list)
    quiet: tuple[time, time] = QUIET_DEFAULT  # this person's quiet hours (E-290)


def in_quiet(person: Person, local: datetime) -> bool:
    """Is it this person's quiet hours now? The window may run past midnight."""
    return _within(person.quiet, local.time())


def _within(window: tuple[time, time], moment: time) -> bool:
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def bundle_time(person: Person) -> time:
    """When this person gets the day's bundle (B-30): their own time, else 19:00. A time
    inside their quiet hours moves to the end of them (E-126)."""
    raw = str(person.preferences.get("bundle_at") or "")
    try:
        hours, minutes = (int(x) for x in raw.split(":"))
        at = time(hours, minutes)
    except ValueError:
        at = BUNDLE_AT
    return person.quiet[1] if _within(person.quiet, at) else at


@dataclass(frozen=True, slots=True)
class Delivery:
    user_id: str
    channel: str  # "push" or "app"
    title: str
    message: str
    actions: tuple[tuple[str, str], ...] = ()


class Signer:
    def __init__(self, secret: str) -> None:
        self._key = secret.encode()

    def sign(self, payload: str) -> str:
        tag = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"HM|{payload}|{tag}"

    def verify(self, action: str) -> str | None:
        parts = action.split("|")
        if len(parts) < 3 or parts[0] != "HM":
            return None
        payload = "|".join(parts[1:-1])
        expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return payload if hmac.compare_digest(expected, parts[-1]) else None


def people_by_user(states: Mapping[str, State]) -> dict[str, State]:
    """Each Home Assistant user's person, by user id, in one look at the states (code QA 3,
    punt 7: five places each went through every state for one user)."""
    return {
        str(state.attributes["user_id"]): state
        for entity_id, state in states.items()
        if entity_id.startswith("person.") and state.attributes.get("user_id")
    }


def notify_services_for(
    user_id: str,
    states: Mapping[str, State],
    services: set[str],
    people: Mapping[str, State] | None = None,
) -> list[str]:
    """Companion phones of a person: person → device_trackers → notify.mobile_app_*."""
    person = (people if people is not None else people_by_user(states)).get(user_id)
    if person is None:
        return []
    found = []
    for tracker in person.attributes.get("device_trackers") or []:
        name = f"mobile_app_{str(tracker).split('.', 1)[-1]}"
        if name in services:
            found.append(f"notify.{name}")
    return found


class NotificationRouter:
    def __init__(
        self, db: Database, ha: HomeAssistant, languages: Languages, signer: Signer
    ) -> None:
        self.db = db
        self.ha = ha
        self.languages = languages
        self.signer = signer
        self.deliveries: list[Delivery] = []  # last deliveries, for tests and the UI
        self._sent_today: dict[tuple[str, str], int] = {}
        self._ignored: dict[tuple[str, str], int] = {}
        self.unregistered: list[Person] = []  # users with a phone and no profile yet
        self._decayed_on: str | None = None
        # Sent, not yet answered: (person, notification) → (kind of topic, when).
        self._pending: dict[tuple[str, int], tuple[str, datetime]] = {}
        self._bundled: dict[str, str] = {}  # person → the day of their last bundle
        self._actions: dict[int, tuple[tuple[str, str], ...]] = {}  # buttons, to send again
        # A camera's image path and where a tap goes (16.4): only a path in Home Assistant;
        # "label:<code>" is a button's own words, such as an alarm's name (E-311).
        self._push: dict[int, dict[str, str]] = {}
        # Who reacted to a message (a button, the app): notification → people; "*" is someone
        # whose phone did not say who. Whoever did not, gets an alarm once more (E-545).
        self._seen: dict[int, set[str]] = {}
        self.snoozed: dict[str, datetime] = {}  # topic → until when it was put off (E-122)
        self._safety: set[int] = set()  # questions about safety (SAFETY_SKILLS)
        self._asked: set[str] = set()  # "person|topic|day": a question counted once (E-294)
        # Who is in a room, when HouseMind can tell (a room sensor per phone, E-287); the
        # runtime sets it. None: it cannot tell, so everyone at home.
        self.where: Callable[[str], set[str] | None] | None = None
        # Her name in a language, for the title of a push message: "Vesta – nood" (20.21).
        self.name_of: Callable[[str], str] = lambda lang: "Vesta"
        # A person's phones (E-585): to take an answered question off every phone it reached.
        self.phones_of: Callable[[str], list[str]] = lambda user_id: []
        # Per person what they put off (E-372): "later" key → until when.
        self._quiet: dict[str, dict[str, dict[str, Any]]] = {}
        # What an administrator switched off for the whole house (E-412): key → how it is
        # named (a text key and its params) and since when.
        self._muted: dict[str, dict[str, Any]] = {}

    # -- the counters survive a restart ---------------------------------------------------

    async def load(self) -> None:
        raw = await self.db.get_setting("notify_state", {})
        if not isinstance(raw, dict):
            return
        self._sent_today = _pairs(raw.get("sent"))
        self._ignored = _pairs(raw.get("ignored"))
        self._pending = {}
        for k, (topic_type, at) in (raw.get("pending") or {}).items():
            user_id, _, notification_id = k.rpartition("|")
            try:
                self._pending[(user_id, int(notification_id))] = (
                    str(topic_type),
                    datetime.fromisoformat(at),
                )
            except ValueError:
                continue
        self._bundled = {str(k): str(v) for k, v in (raw.get("bundled") or {}).items()}
        self._decayed_on = raw.get("decayed_on")
        self._asked = {str(k) for k in raw.get("asked") or []}
        for row in await self.db.fetchall(
            "SELECT topic, snoozed_until FROM notifications "
            "WHERE resolved_at IS NULL AND snoozed_until IS NOT NULL"
        ):
            self.snoozed[str(row["topic"])] = datetime.fromisoformat(str(row["snoozed_until"]))
        quiet = await self.db.get_setting("notify_quiet", {})
        quiet = quiet if isinstance(quiet, dict) else {}
        self._quiet = {
            str(user): {"later": {str(k): str(v) for k, v in (q.get("later") or {}).items()}}
            for user, q in quiet.items()
            if isinstance(q, dict)
        }
        muted = await self.db.get_setting("notify_muted", None)
        if isinstance(muted, dict):
            self._muted = {str(k): dict(v) for k, v in muted.items() if isinstance(v, dict)}
        else:
            # Before E-412 each person had their own list: they become the house's, once.
            self._muted = {}
            for q in quiet.values():
                for key, label in ((q or {}).get("muted") or {}).items():
                    known = self._muted.get(str(key))
                    if known is None or str(label.get("since", "")) < str(known.get("since", "")):
                        self._muted[str(key)] = dict(label)
            await self.db.set_setting("notify_muted", self._muted)
            await self.db.set_setting("notify_quiet", self._quiet)

    async def forget(self, user_id: str, local: datetime) -> None:
        """'Vergeet mij' (E-300): what the router counted for this person goes."""
        self._sent_today = {k: v for k, v in self._sent_today.items() if k[0] != user_id}
        self._ignored = {k: v for k, v in self._ignored.items() if k[0] != user_id}
        self._pending = {k: v for k, v in self._pending.items() if k[0] != user_id}
        self._bundled.pop(user_id, None)
        self._asked = {k for k in self._asked if not k.startswith(f"{user_id}|")}
        if self._quiet.pop(user_id, None) is not None:
            await self.db.set_setting("notify_quiet", self._quiet)
        await self._save(local)

    # -- what a person put off (E-372), what the house switched off (E-412) -----------------

    def _off(self, user_id: str | None, key: str, now: datetime) -> str | None:
        if key in self._muted:
            return "muted"
        until = ((self._quiet.get(user_id or "") or {}).get("later") or {}).get(key)
        if until and datetime.fromisoformat(until) > now:
            return "later"
        return None

    def hidden(
        self, user_id: str | None, topic: str, params: dict[str, Any], now: datetime
    ) -> str | None:
        """ "muted" when the house switched this message off, "later" when this person put it
        off; the camera message only when none of its lines is wanted."""
        state = self._off(user_id, kind_key(topic), now)
        if state is not None or topic != CAMERA_BUNDLE:
            return state
        parts = params.get("parts") or []
        states = [self._off(user_id, part_key(p), now) for p in parts]
        if parts and all(states):
            return "muted" if "muted" in states else "later"
        return None

    def skipped(self, user_id: str | None, now: datetime) -> frozenset[str]:
        """The camera lines not wanted now: switched off for the house, or put off by this
        person."""
        keys = set(self._muted)
        later = (self._quiet.get(user_id or "") or {}).get("later") or {}
        keys |= {k for k, v in later.items() if datetime.fromisoformat(v) > now}
        return frozenset(keys)

    def muted(self) -> dict[str, dict[str, Any]]:
        """What an administrator switched off for the house: key → how it is named."""
        return dict(self._muted)

    def later_of(self, user_id: str | None, key: str, now: datetime) -> datetime | None:
        until = ((self._quiet.get(user_id or "") or {}).get("later") or {}).get(key)
        moment = datetime.fromisoformat(until) if until else None
        return moment if moment is not None and moment > now else None

    async def mute(self, key: str, label: dict[str, Any], now: datetime) -> None:
        """Niet meer tonen (E-412): for everyone in the house; only an administrator asks it."""
        self._muted[key] = {**label, "since": now.isoformat()}
        await self.db.set_setting("notify_muted", self._muted)

    async def unmute(self, key: str, now: datetime) -> bool:
        found = self._muted.pop(key, None) is not None
        if key == ALL_CAMERAS:  # every camera line again, also one switched off on its own
            for line in [k for k in self._muted if k.startswith("camera:")]:
                found = self._muted.pop(line, None) is not None or found
        if found:
            await self.db.set_setting("notify_muted", self._muted)
        return found

    async def later(self, user_id: str, key: str, until: datetime, now: datetime) -> None:
        quiet = self._quiet.setdefault(user_id, {"later": {}})
        quiet.setdefault("later", {})[key] = until.isoformat()
        await self._save_quiet(now)

    async def _save_quiet(self, now: datetime) -> None:
        for quiet in self._quiet.values():  # what was put off and is over, goes
            later = quiet.get("later") or {}
            for key in [k for k, v in later.items() if datetime.fromisoformat(v) <= now]:
                del later[key]
        self._quiet = {u: q for u, q in self._quiet.items() if q.get("later")}
        await self.db.set_setting("notify_quiet", self._quiet)

    async def _save(self, local: datetime) -> None:
        today = local.date().isoformat()
        await self.db.set_setting(
            "notify_state",
            {
                "sent": {
                    f"{u}|{d}": n for (u, d), n in self._sent_today.items() if d.endswith(today)
                },
                "ignored": {f"{u}|{t}": n for (u, t), n in self._ignored.items()},
                "pending": {
                    f"{u}|{i}": [t, at.isoformat()] for (u, i), (t, at) in self._pending.items()
                },
                "bundled": self._bundled,
                "decayed_on": self._decayed_on,
                "asked": sorted(k for k in self._asked if k.endswith(today)),
            },
        )

    def _settle(self, now: datetime) -> None:
        """What nobody answered within three hours now counts as ignored (E-127)."""
        for key, (topic_type, at) in list(self._pending.items()):
            if now - at >= IGNORED_AFTER:
                del self._pending[key]
                counter = (key[0], topic_type)
                self._ignored[counter] = self._ignored.get(counter, 0) + 1

    # -- people ---------------------------------------------------------------------------

    async def people(self, states: dict[str, State], default_language: str) -> list[Person]:
        users = {u.id: u for u in await self.ha.users() if u.is_active and not u.system_generated}
        services = (await self.ha.available_actions()).get("notify", set())
        rows = {r["user_id"]: r for r in await self.db.fetchall("SELECT * FROM people")}
        persons = people_by_user(states)
        people = []
        for user_id, row in rows.items():
            user: HAUser | None = users.get(user_id)
            person = persons.get(user_id)
            home = person is not None and person.state == "home"
            prefs = json.loads(row["preferences"] or "{}")
            people.append(
                Person(
                    user_id=user_id,
                    name=row["display_name"] or (user.name if user else ""),
                    level=int(row["level"]),
                    is_admin=bool(user and user.is_admin),
                    home=home,
                    # Own choice, else what Home Assistant shows this person (kept by the app,
                    # 20.21), else the house's language.
                    language=str(
                        (
                            prefs.get("language")
                            if prefs.get("language") not in (None, "auto")
                            else None
                        )
                        or prefs.get("ha_language")
                        or default_language
                    ),
                    preferences=prefs,
                    consents=json.loads(row["consents"] or "{}"),
                    notify_services=notify_services_for(user_id, states, services, persons),
                )
            )
        # Someone with a phone who has not done the introduction yet still gets a message in
        # an emergency, also right after a fresh install. They are kept apart: only
        # emergencies reach them, and they count for nothing else.
        self.unregistered = []
        for user_id, user in users.items():
            if user_id in rows:
                continue
            phones = notify_services_for(user_id, states, services, persons)
            if not phones:
                continue
            self.unregistered.append(
                Person(
                    user_id=user_id,
                    name=user.name,
                    level=1,
                    is_admin=bool(user.is_admin),
                    home=(person := persons.get(user_id)) is not None and person.state == "home",
                    language=default_language,
                    preferences={},
                    consents={"emergency_push": True},
                    notify_services=phones,
                )
            )
        # Every phone with the HA app gets an emergency, also one that belongs to nobody
        # HouseMind knows (E-291). A phone of someone who said "no" stays with that person.
        known = {s for p in people + self.unregistered for s in p.notify_services}
        for service in sorted(services):
            name = f"notify.{service}"
            if service.startswith("mobile_app_") and name not in known:
                self.unregistered.append(
                    Person(
                        user_id=f"phone:{service}",
                        name="",
                        level=1,
                        is_admin=False,
                        home=False,
                        language=default_language,
                        consents={"emergency_push": True},
                        notify_services=[name],
                    )
                )
        return people

    # -- raising and resolving ------------------------------------------------------------

    async def raise_(
        self,
        kind: Kind,
        topic: str,
        text_key: str,
        params: dict[str, Any],
        people: list[Person],
        now: datetime,
        local: datetime,
        sleeping: bool,
        concerns: str = "home",
        actions: tuple[tuple[str, str], ...] = (),
        safety: bool = False,
        push: dict[str, str] | None = None,
    ) -> int | None:
        existing = await self.db.fetchone(
            "SELECT id FROM notifications WHERE topic = ? AND resolved_at IS NULL", (topic,)
        )
        if existing is not None:
            await self.db.execute(
                "UPDATE notifications SET params = ? WHERE id = ?",
                (json.dumps(params), existing["id"]),
            )
            return None
        await self.db.execute(
            "INSERT INTO notifications(at, kind, topic, text_key, params, severity, concerns) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                now.isoformat(),
                kind.value,
                topic,
                text_key,
                json.dumps(params),
                _severity(kind),
                concerns,
            ),
        )
        row = await self.db.fetchone("SELECT max(id) AS id FROM notifications")
        notification_id = int(row["id"]) if row else 0
        if actions:
            self._actions[notification_id] = actions
        if push:
            self._push[notification_id] = dict(push)
        if safety:
            self._safety.add(notification_id)
        if kind is Kind.EMERGENCY:
            people = people + self.unregistered  # also who has not done the introduction
        if kind is not Kind.INFO:
            await self._route(
                notification_id,
                kind,
                topic,
                text_key,
                params,
                people,
                now,
                local,
                sleeping,
                concerns,
                actions,
            )
        return notification_id

    async def resolve(self, topic: str, now: datetime) -> bool:
        """Answered or solved: closed for everyone, and taken off every phone it reached, so
        that nobody sees it or answers it a second time (E-585)."""
        rows = await self.db.fetchall(
            "SELECT id, delivered FROM notifications WHERE topic = ? AND resolved_at IS NULL",
            (topic,),
        )
        changed = await self.db.execute(
            "UPDATE notifications SET resolved_at = ? WHERE topic = ? AND resolved_at IS NULL",
            (now.isoformat(), topic),
        )
        # Solved by itself (the door was closed): nobody ignored it.
        solved = {int(r["id"]) for r in rows}
        for key in [k for k in self._pending if k[1] in solved]:
            del self._pending[key]
        for notification_id in solved:
            self._actions.pop(notification_id, None)
            self._push.pop(notification_id, None)
        self.snoozed.pop(topic, None)
        reached = {
            user_id
            for r in rows
            for user_id, how in json.loads(r["delivered"] or "{}").items()
            if how == "push"
        }
        for user_id in sorted(reached):
            for service in self.phones_of(user_id):
                with contextlib.suppress(Exception):  # a phone that is away: it does not matter
                    await self.ha.notify(service, "", "clear_notification", {"tag": topic})
        return changed > 0

    async def refresh(
        self,
        topic: str,
        params: dict[str, Any],
        people: list[Person],
        now: datetime,
        local: datetime,
        push: dict[str, str] | None = None,
    ) -> bool:
        """An open message gets new facts ("3 keer iemand op de oprit", 16.4): the same
        message on the phones it reached, replaced in place and quietly – not one more. Not
        for whoever put it off or switched it off (E-372). ``push``: a newer camera image."""
        row = await self.db.fetchone(
            "SELECT * FROM notifications WHERE topic = ? AND resolved_at IS NULL", (topic,)
        )
        if row is None:
            return False
        notification_id = int(row["id"])
        await self.db.execute(
            "UPDATE notifications SET params = ? WHERE id = ?",
            (json.dumps(params), notification_id),
        )
        delivered = json.loads(row["delivered"] or "{}")
        kind = Kind(str(row["kind"]))
        if push:
            self._push[notification_id] = {**self._push.get(notification_id, {}), **push}
        for person in people:
            if delivered.get(person.user_id) != "push":
                continue
            if kind is not Kind.EMERGENCY and self.hidden(person.user_id, topic, params, now):
                continue
            lang = self.languages.resolve(person.language)
            title = self.languages.text(lang, f"notify.title.{kind.value}", name=self.name_of(lang))
            skip = self.skipped(person.user_id, now)
            message = notice_text(self.languages, lang, str(row["text_key"]), params, skip)
            for service in person.notify_services:
                data = self._data(
                    notification_id, kind, topic, (), self._links(notification_id, lang)
                )
                if kind is not Kind.EMERGENCY:
                    # Updated in place: no new sound or buzz (Android, iPhone).
                    data["alert_once"] = True
                    data["push"] = {**data.get("push", {}), "interruption-level": "passive"}
                await self.ha.notify(service, title, message, data)
            self.deliveries.append(Delivery(person.user_id, "push", title, message))
        del self.deliveries[:-200]
        return True

    def _links(self, notification_id: int, lang: str) -> tuple[tuple[str, str], ...]:
        """Buttons that open something (E-545): ``link:<n>`` = "text key|where it goes" – the
        camera, the alarm in Home Assistant, the emergency number – in the reader's words."""
        own = self._push.get(notification_id, {})
        out = []
        for key in sorted(k for k in own if k.startswith("link:")):
            text_key, _, uri = own[key].partition("|")
            if uri:
                call = uri.removeprefix("tel:") if uri.startswith("tel:") else ""
                out.append((uri, self.languages.text(lang, text_key, number=call)))
        return tuple(out)

    async def remind(self, topic: str, people: list[Person], now: datetime, local: datetime) -> int:
        """An emergency still going after two minutes (E-545): the same message once more, with
        its sound, on the phones of whoever has not reacted to it. Returns how many."""
        row = await self.db.fetchone(
            "SELECT * FROM notifications WHERE topic = ? AND resolved_at IS NULL", (topic,)
        )
        if row is None:
            return 0
        notification_id = int(row["id"])
        seen = self._seen.get(notification_id, set())
        if "*" in seen:
            return 0
        delivered = json.loads(row["delivered"] or "{}")
        kind = Kind(str(row["kind"]))
        params = json.loads(row["params"] or "{}")
        sent = 0
        for person in people:
            if delivered.get(person.user_id) != "push" or person.user_id in seen:
                continue
            lang = self.languages.resolve(person.language)
            title = self.languages.text(lang, f"notify.title.{kind.value}", name=self.name_of(lang))
            skip = self.skipped(person.user_id, now)
            message = notice_text(self.languages, lang, str(row["text_key"]), params, skip)
            for service in person.notify_services:
                data = self._data(
                    notification_id, kind, topic, (), self._links(notification_id, lang)
                )
                await self.ha.notify(service, title, message, data)
            self.deliveries.append(Delivery(person.user_id, "push", title, message))
            sent += 1
        del self.deliveries[:-200]
        return sent

    def _data(
        self,
        notification_id: int,
        kind: Kind,
        topic: str,
        signed: tuple[tuple[str, str], ...],
        links: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, Any]:
        """What a push message carries besides its text: the tag that makes one topic one
        message, its buttons, a camera's image path and where a tap goes (16.4)."""
        data: dict[str, Any] = {"tag": topic, "group": "housemind"}
        if signed or links:
            data["actions"] = [{"action": a, "title": t} for a, t in signed] + [
                {"action": "URI", "title": t, "uri": u} for u, t in links
            ]
        if kind is Kind.EMERGENCY:
            data["push"] = {"interruption-level": "critical"}
            data["priority"] = "high"
        elif kind is Kind.IMPORTANT or topic.startswith("care:"):
            # Not readable on a locked phone (QA 2, L5): "nobody home, the door is open" or a
            # care message is for the person, not for whoever sees the screen. An emergency
            # stays readable for everyone.
            data["visibility"] = "private"
        push = self._push.get(notification_id, {})
        if push.get("image"):
            data["image"] = push["image"]  # the phone fetches it from HA itself (E-186)
        if push.get("url"):
            data["url"] = push["url"]  # iPhone
            data["clickAction"] = push["url"]  # Android
        return data

    async def snooze(self, notification_id: int, until: datetime) -> None:
        await self.db.execute(
            "UPDATE notifications SET snoozed_until = ? WHERE id = ?",
            (until.isoformat(), notification_id),
        )
        row = await self.db.fetchone(
            "SELECT topic FROM notifications WHERE id = ?", (notification_id,)
        )
        if row is not None:
            self.snoozed[str(row["topic"])] = until  # put off: Vesta is not orange for it

    def _decay(self, local: datetime) -> None:
        """What someone ignored counts less every day, so a kind of message comes back."""
        today = local.date().isoformat()
        if self._decayed_on == today:
            return
        self._decayed_on = today
        self._ignored = {k: v // 2 for k, v in self._ignored.items() if v // 2 > 0}

    async def acknowledged_topic(self, topic: str, user_id: str | None) -> None:
        """Someone answered (a phone button, a proposal card, Vesta): not ignored."""
        topic_type = topic.split(":", 1)[0]
        for key in [k for k in self._ignored if k[1] == topic_type]:
            if user_id is None or key[0] == user_id:
                self._ignored.pop(key, None)
        for sent, (kind, _) in list(self._pending.items()):
            if kind == topic_type and (user_id is None or sent[0] == user_id):
                del self._pending[sent]
        row = await self.db.fetchone(
            "SELECT id FROM notifications WHERE topic = ? AND resolved_at IS NULL", (topic,)
        )
        if row is not None:
            self._seen.setdefault(int(row["id"]), set()).add(user_id or "*")

    async def acknowledged(self, notification_id: int, user_id: str) -> None:
        """A person reacted: reset the ignore counter for this kind of topic."""
        row = await self.db.fetchone(
            "SELECT topic FROM notifications WHERE id = ?", (notification_id,)
        )
        if row:
            self._ignored.pop((user_id, str(row["topic"]).split(":", 1)[0]), None)
        self._pending.pop((user_id, notification_id), None)
        self._seen.setdefault(notification_id, set()).add(user_id)

    async def open_list(self, now: datetime) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM notifications WHERE resolved_at IS NULL "
            "AND (snoozed_until IS NULL OR snoozed_until < ?) ORDER BY severity DESC, at DESC",
            (now.isoformat(),),
        )
        return [dict(r) for r in rows]

    async def expire_info(self, now: datetime) -> list[str]:
        """Close information older than a week; returns the topics that were closed."""
        before = (now - INFO_KEEP).isoformat()
        rows = await self.db.fetchall(
            "SELECT topic FROM notifications WHERE kind = 'info' AND resolved_at IS NULL "
            "AND at < ?",
            (before,),
        )
        if rows:
            await self.db.execute(
                "UPDATE notifications SET resolved_at = ? WHERE kind = 'info' "
                "AND resolved_at IS NULL AND at < ?",
                (now.isoformat(), before),
            )
        return [str(r["topic"]) for r in rows]

    # -- routing --------------------------------------------------------------------------

    def _budget(self, person: Person) -> int:
        if person.level == 2:
            return max(
                0, BUDGET[2] + SLIDER.get(str(person.preferences.get("amount", "normal")), 0)
            )
        if person.level == 4 and isinstance(person.preferences.get("max_per_day"), int):
            return int(person.preferences["max_per_day"])
        return BUDGET.get(person.level, 3)

    def question_budget(self, person: Person) -> int:
        """How many questions this person gets a day (E-294): 0 / 1 / 3 / own (default 5)."""
        if person.level >= 4:
            chosen = person.preferences.get("questions_per_day")
            return int(chosen) if isinstance(chosen, int) else QUESTIONS_EXPERT
        return QUESTIONS.get(person.level, 0)

    def may_ask(self, person: Person, topic: str, local: datetime, count: bool = True) -> bool:
        """May this question go to this person now? It counts once a day per question,
        wherever it is shown first: Vesta's window, the phone, a card (E-252, E-294)."""
        today = local.date().isoformat()
        key = f"{person.user_id}|{topic}|{today}"
        if key in self._asked:
            return True
        used = self._sent_today.get((person.user_id, "q:" + today), 0)
        if used >= self.question_budget(person):
            return False
        if count:
            self._asked.add(key)
            self._sent_today[(person.user_id, "q:" + today)] = used + 1
        return True

    def _wants(self, person: Person, kind: Kind, safety: bool = False) -> bool:
        said = person.consents.get("emergency_push")
        if kind is Kind.EMERGENCY:
            # Only an explicit "no" to messages in danger stops it (E-20, E-291).
            return person.level >= 2 or said is not False
        if safety:
            # Safety always comes (E-289); on Carefree only after a "yes" to danger messages.
            return person.level >= 2 or said is True
        if person.level == 1:
            return False  # Carefree: not disturbed (E-17)
        enabled = person.preferences.get("kinds")
        return not (
            person.level >= 3 and isinstance(enabled, dict) and enabled.get(kind.value) is False
        )

    async def _route(
        self,
        notification_id: int,
        kind: Kind,
        topic: str,
        text_key: str,
        params: dict[str, Any],
        people: list[Person],
        now: datetime,
        local: datetime,
        sleeping: bool,
        concerns: str,
        actions: tuple[tuple[str, str], ...],
        only: set[str] | None = None,
    ) -> None:
        if kind is Kind.EMERGENCY:
            recipients = people
        elif concerns == "admins":
            recipients = [p for p in people if p.is_admin]
        elif concerns == "away":
            recipients = [p for p in people if not p.home]
        elif concerns.startswith("user:"):
            # One chosen person (a care contact, E-21): they asked for exactly this.
            recipients = [p for p in people if p.user_id == concerns[5:]]
        elif concerns.startswith("area:"):
            # A question about a room goes to who is there, if HouseMind can tell (E-287).
            there = self.where(concerns[5:]) if self.where is not None else None
            recipients = (
                [p for p in people if p.user_id in there]
                if there
                else [p for p in people if p.home] or people
            )
        else:
            recipients = [p for p in people if p.home] or people
        if only is not None:
            recipients = [p for p in recipients if p.user_id in only]
        topic_type = topic.split(":", 1)[0]
        row = await self.db.fetchone(
            "SELECT delivered FROM notifications WHERE id = ?", (notification_id,)
        )
        delivered: dict[str, str] = json.loads(row["delivered"] or "{}") if row else {}
        delivered.pop("held", None)
        designated = concerns.startswith("user:")
        safety = topic_type in SAFETY_TOPICS or notification_id in self._safety
        urgent = kind is Kind.EMERGENCY or safety or topic_type in SECURITY_TOPICS
        self._decay(local)
        self._settle(now)
        for person in recipients:
            if not designated and not self._wants(person, kind, safety):
                continue
            if kind is not Kind.EMERGENCY and self.hidden(person.user_id, topic, params, now):
                delivered[person.user_id] = "muted"  # put off or switched off (E-372)
                continue
            if not urgent and not designated and (sleeping or in_quiet(person, local)):
                # Quiet hours (E-290): it comes when they end, if it still matters. A question
                # never goes to a phone at night.
                delivered[person.user_id] = "quiet"
                continue
            if kind is Kind.LEARNING and not designated and not safety:
                # A question has its own budget (E-294); what does not fit waits in the
                # Logbook, where it can be answered.
                if not self.may_ask(person, topic, local):
                    delivered[person.user_id] = "wait"
                    continue
            elif not urgent and not designated:
                key = (person.user_id, local.date().isoformat())
                if self._sent_today.get(key, 0) >= self._budget(person):
                    delivered.setdefault(person.user_id, "held")  # in the bundle instead
                    continue
                if self._ignored.get((person.user_id, topic_type), 0) >= IGNORED_BEFORE_BUNDLING:
                    delivered.setdefault(person.user_id, "held")
                    continue  # learned: this person ignores these, they go to the daily bundle
                self._sent_today[key] = self._sent_today.get(key, 0) + 1
                self._pending[(person.user_id, notification_id)] = (topic_type, now)
            lang = self.languages.resolve(person.language)
            title = self.languages.text(lang, f"notify.title.{kind.value}", name=self.name_of(lang))
            skip = self.skipped(person.user_id, now)
            message = notice_text(self.languages, lang, text_key, params, skip)
            own = self._push.get(notification_id, {})
            signed = tuple(
                (
                    self.signer.sign(f"{notification_id}|{code}"),
                    own.get(f"label:{code}") or self.languages.text(lang, label),
                )
                for code, label in actions
            )
            links = self._links(notification_id, lang)
            for service in person.notify_services:
                data = self._data(notification_id, kind, topic, signed, links)
                await self.ha.notify(service, title, message, data)
            channel = "push" if person.notify_services else "app"
            self.deliveries.append(Delivery(person.user_id, channel, title, message, signed))
            delivered[person.user_id] = channel
        people_done = {k: v for k, v in delivered.items() if k not in ("escalated", "bundle")}
        if people_done and all(v == "held" for v in people_done.values()):
            delivered["held"] = "yes"
        del self.deliveries[:-200]
        await self.db.execute(
            "UPDATE notifications SET delivered = ? WHERE id = ?",
            (json.dumps(delivered), notification_id),
        )
        await self._save(local)

    async def escalate(
        self, people: list[Person], now: datetime, local: datetime, sleeping: bool
    ) -> int:
        """Important notifications nobody reacted to in 15 minutes also go to admins (B-31).
        Not while the house sleeps: then they wait for the morning."""
        if sleeping:
            return 0
        rows = await self.db.fetchall(
            "SELECT * FROM notifications WHERE kind = 'important' AND resolved_at IS NULL "
            "AND at < ? AND delivered NOT LIKE '%\"escalated\"%' AND concerns NOT LIKE 'user:%'",
            ((now - ESCALATE_AFTER).isoformat(),),
        )
        count = 0
        for row in rows:
            delivered_to = json.loads(row["delivered"] or "{}")
            admins = [
                p for p in people if p.is_admin and delivered_to.get(p.user_id, "held") == "held"
            ]
            if admins:
                await self._route(
                    int(row["id"]),
                    Kind.IMPORTANT,
                    str(row["topic"]),
                    str(row["text_key"]),
                    json.loads(row["params"]),
                    admins,
                    now,
                    local,
                    sleeping,
                    "admins",
                    (),
                )
                count += 1
            fresh = await self.db.fetchone(
                "SELECT delivered FROM notifications WHERE id = ?", (row["id"],)
            )
            delivered = json.loads(fresh["delivered"] or "{}") if fresh else {}
            delivered["escalated"] = "yes"
            await self.db.execute(
                "UPDATE notifications SET delivered = ? WHERE id = ?",
                (json.dumps(delivered), row["id"]),
            )
        return count

    async def deliver_held(
        self, people: list[Person], now: datetime, local: datetime, sleeping: bool = False
    ) -> int:
        """What waited for someone's quiet hours to end (E-290): it goes out now – to whom
        it waited for, if it still matters. Not while the house sleeps."""
        if sleeping:
            return 0
        rows = await self.db.fetchall(
            "SELECT * FROM notifications WHERE kind IN ('important', 'learning') "
            "AND resolved_at IS NULL AND delivered LIKE '%\"quiet\"%'"
        )
        count = 0
        for row in rows:
            waited = {
                user_id
                for user_id, status in json.loads(row["delivered"] or "{}").items()
                if status == "quiet"
            }
            ready = {p.user_id for p in people if p.user_id in waited and not in_quiet(p, local)}
            if not ready:
                continue
            await self._route(
                int(row["id"]),
                Kind(str(row["kind"])),
                str(row["topic"]),
                str(row["text_key"]),
                json.loads(row["params"]),
                people,
                now,
                local,
                False,
                str(row["concerns"] or "home"),
                self._actions.get(int(row["id"]), ()),
                only=ready,
            )
            count += 1
        return count

    async def direct(
        self, person: Person, title_key: str, message: str, tag: str, sleeping: bool
    ) -> bool:
        """A message for one person only (the weekly report, E-91) – info, so never at
        level Carefree, never while the house sleeps, and only if they want info."""
        if person.level < 2 or sleeping or not self._wants(person, Kind.INFO):
            return False
        lang = self.languages.resolve(person.language)
        title = self.languages.text(lang, title_key, name=self.name_of(lang))
        for service in person.notify_services:
            await self.ha.notify(service, title, message, {"tag": tag, "group": "housemind"})
        delivery = Delivery(
            person.user_id, "push" if person.notify_services else "app", title, message
        )
        self.deliveries.append(delivery)
        return True

    async def bundle(self, people: list[Person], local: datetime) -> list[Delivery]:
        """The day's bundle (B-30, E-126): at most once a day per person, at their own time
        (19:00 unless chosen), never in their quiet hours, only if they want information,
        and only with what is for them: open information, and what was held back for them."""
        today = local.date().isoformat()
        due = [
            p
            for p in people
            if p.level >= 2
            and self._wants(p, Kind.INFO)  # level 1, or someone who switched information off
            and self._bundled.get(p.user_id) != today
            and local.time() >= bundle_time(p)
            and not in_quiet(p, local)
        ]
        if not due:
            return []
        rows = await self.db.fetchall(
            "SELECT * FROM notifications WHERE resolved_at IS NULL AND kind != 'emergency'"
        )
        out: list[Delivery] = []
        changed: dict[int, dict[str, str]] = {}
        for person in due:
            mine = []
            for r in rows:
                concerns = str(r["concerns"] or "home")
                if concerns == "admins" and not person.is_admin:
                    continue
                if concerns.startswith("user:") and concerns[5:] != person.user_id:
                    continue
                if self.hidden(person.user_id, str(r["topic"]), json.loads(r["params"]), local):
                    continue  # put off or switched off (E-372)
                status = changed.setdefault(int(r["id"]), json.loads(r["delivered"] or "{}"))
                waiting = status.get(person.user_id) == "held"
                info = (
                    str(r["kind"]) == "info"
                    and person.user_id not in status
                    and status.get("bundle") != "yes"  # bundled for everyone before (H20)
                )
                if waiting or info:
                    mine.append(r)
                    status[person.user_id] = "bundle"
            self._bundled[person.user_id] = today
            if not mine:
                continue
            lang = self.languages.resolve(person.language)
            skip = self.skipped(person.user_id, local)
            lines = [
                notice_text(self.languages, lang, str(r["text_key"]), json.loads(r["params"]), skip)
                for r in mine
            ]
            title = self.languages.text(lang, "notify.title.info", name=self.name_of(lang))
            message = "\n".join(f"• {line}" for line in lines)
            for service in person.notify_services:
                await self.ha.notify(
                    service, title, message, {"tag": "housemind-daily", "group": "housemind"}
                )
            delivery = Delivery(
                person.user_id, "push" if person.notify_services else "app", title, message
            )
            out.append(delivery)
            self.deliveries.append(delivery)
        for notification_id, status in changed.items():
            await self.db.execute(
                "UPDATE notifications SET delivered = ? WHERE id = ?",
                (json.dumps(status), notification_id),
            )
        await self._save(local)
        return out


def _pairs(raw: Any) -> dict[tuple[str, str], int]:
    """Counters saved as "person|what": number."""
    out: dict[tuple[str, str], int] = {}
    for key, value in (raw or {}).items():
        first, sep, second = str(key).partition("|")
        if sep and isinstance(value, int):
            out[(first, second)] = value
    return out


def _severity(kind: Kind) -> int:
    return {Kind.EMERGENCY: 3, Kind.IMPORTANT: 2, Kind.LEARNING: 1, Kind.INFO: 0}[kind]
