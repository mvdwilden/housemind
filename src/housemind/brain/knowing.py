"""What Vesta knows herself (E-243 – E-245, B-90 – B-92): questions about the house, the
settings and the house rules, answered from what HouseMind already knows – without an AI.

A small, fixed set of question forms, with words per language (``talk.know.*`` and
``talk.settings.*``): is something on, open or locked; which are still on; how warm it is; is
anyone there; who is home; what she did; how much power; where a setting is; what waits for
you; which house rules there are. The answers are her own words (``know_…`` in her banks), with
plain names (E-248). Anything else returns None: then the Help, the manual or an AI may know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import yaml

from housemind.brain.readiness import readiness
from housemind.brain.talk import normalise
from housemind.core.model import Entity
from housemind.energy.meter import live

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

LEVELS = ((1, "carefree"), (2, "informed"), (3, "involved"), (4, "expert"))  # as in the runtime

# Devices whose state Vesta can say, and how she reads it.
_ON_OFF = {"light", "switch", "fan", "input_boolean", "humidifier", "siren", "water_heater"}
_OPENINGS = {"door", "window", "garage_door", "opening"}
_STATES = ("unlocked", "locked", "closed", "open", "off", "on")  # longest words are tried first


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    sheet: str  # "preferences", "settings" or "brain" (a tab of Brein, E-523)
    section: str
    title: str
    who: str  # "everyone" or "admins"
    now: str = ""


def load_settings(directories: list[Path]) -> dict[str, Setting]:
    """Where each setting is (knowledge/settings.yaml); a later folder replaces an entry."""
    out: dict[str, Setting] = {}
    for directory in directories:
        path = directory / "settings.yaml"
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, spec in (raw.get("settings") or {}).items():
            out[str(key)] = Setting(
                str(key),
                str(spec["sheet"]),
                str(spec["section"]),
                str(spec["title"]),
                str(spec.get("who", "everyone")),
                str(spec.get("now", "")),
            )
    return out


def state_of(entity: Entity, value: str) -> str | None:
    """A device's state as Vesta says it: on/off, open/closed, locked/unlocked – "unknown"
    when it does not answer, None when it is not something with such a state."""
    if value in ("unavailable", "unknown", ""):
        return "unknown"
    domain = entity.domain
    if domain in _ON_OFF:
        return {"on": "on", "off": "off"}.get(value)
    if domain == "climate":
        return "off" if value == "off" else "on"
    if domain == "media_player":
        return "off" if value in ("off", "standby") else "on"
    if domain in ("cover", "valve"):
        return {"open": "open", "opening": "open", "closed": "closed", "closing": "closed"}.get(
            value
        )
    if domain == "binary_sensor" and entity.device_class in _OPENINGS:
        return {"on": "open", "off": "closed"}.get(value)
    if domain == "lock":
        return {"locked": "locked", "unlocked": "unlocked", "open": "unlocked"}.get(value)
    return None


class Knowing:
    def __init__(self, hm: HouseMind) -> None:
        self.hm = hm

    # -- words -----------------------------------------------------------------------------

    def _words(self, lang: str, key: str) -> list[str]:
        raw = self.hm.languages.text(lang, key)
        if raw == key:
            return []
        found = [w.strip().lower() for w in raw.split(",") if w.strip()]
        return sorted(found, key=len, reverse=True)

    def _has(self, text: str, words: list[str]) -> str | None:
        for word in words:
            if re.search(rf"(?<![\w']){re.escape(word)}(?![\w'])", text):
                return word
        return None

    def _asked_state(self, text: str, lang: str) -> str | None:
        found = [
            (len(w), key)
            for key in _STATES
            if (w := self._has(text, self._words(lang, f"talk.know.state.{key}")))
        ]
        return max(found)[1] if found else None

    def _say(self, lang: str, topic: str, user_id: str | None, **facts: Any) -> str:
        return self.hm.vesta_fact(lang, topic, user_id, **{k: str(v) for k, v in facts.items()})

    # -- the answer ------------------------------------------------------------------------

    async def answer(
        self,
        text: str,
        lang: str,
        level: int,
        user_id: str | None,
        admin: bool,
        channel: str = "app",
    ) -> dict[str, Any] | None:
        t = normalise(text)
        if not t or self.hm.world is None:
            return None
        if self._has(t, self._words(lang, "talk.know.setting")):
            found = self._setting(t, lang, user_id, admin, channel, level)
            if found is not None:
                return found
        if self._has(t, self._words(lang, "talk.know.tidy")):
            return self._tidy(t, lang, user_id, admin, channel)
        if self._has(t, self._words(lang, "talk.know.ready")):
            return await self._ready(t, lang, user_id, admin, channel)
        if self._has(t, self._words(lang, "talk.know.rules")):
            return {"text": self._rules(lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.todo")):
            return {"text": await self._todo(lang, level, user_id, admin)}
        if self._has(t, self._words(lang, "talk.know.me")):
            return {"text": self._me(lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.home")):
            return {"text": self._home(lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.did")):
            return {"text": self._did(lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.energy")):
            return {"text": self._energy(lang, user_id)}
        area = self._area(t, lang)
        if self._has(t, self._words(lang, "talk.know.humid")):
            return {"text": self._humidity(area, lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.warm")):
            return {"text": await self._warm(area, lang, user_id)}
        if self._has(t, self._words(lang, "talk.know.someone")):
            if area is None:
                return {"text": self._home(lang, user_id)}
            return {"text": self._someone(area, lang, user_id)}
        if t.split()[0] in self._words(lang, "talk.words.command"):
            return None  # "Doe de lamp uit" asks for something, not how it is (E-349)
        asked = self._asked_state(t, lang)
        if asked is None:
            return None
        device = self._device(t, lang)
        if device is not None and not self._has(t, self._words(lang, "talk.know.which")):
            return {"text": self._state(device, asked, lang, user_id)}
        kind = self._kind(t, lang)
        if kind is not None:
            return {"text": self._which(kind, asked, area, lang, user_id)}
        if device is not None:
            return {"text": self._state(device, asked, lang, user_id)}
        return None

    # -- devices ---------------------------------------------------------------------------

    def _candidates(self) -> list[Entity]:
        assert self.hm.world is not None
        states = self.hm.world.states
        return [
            e
            for e in self.hm.model.entities.values()
            if not (e.hidden or e.disabled or e.entity_category)
            and e.entity_id in states
            and state_of(e, states[e.entity_id].state) is not None
        ]

    def _device(self, t: str, lang: str) -> str | None:
        """The device the question names: by its name in HA, or by its plain name."""
        candidates = self._candidates()
        for entity in sorted(candidates, key=lambda e: -len(e.name or "")):
            if entity.name and len(entity.name) >= 3 and normalise(entity.name) in t:
                return entity.entity_id
        best: tuple[int, str] | None = None
        tokens = set(re.findall(r"[\w'’-]+", t))
        for entity in candidates:
            thing = self.hm.names.thing(entity.entity_id, lang)
            if thing is None or thing.noun.lower() not in tokens:
                continue
            words = set(re.findall(r"[\w'’-]+", f"{thing.place} {thing.side}".lower()))
            score = 2 + len(words & tokens)
            if best is None or score > best[0]:
                best = (score, entity.entity_id)
        return best[1] if best else None

    def _kind(self, t: str, lang: str) -> str | None:
        """The kind of device a question asks about ("welke lampen" → lamp)."""
        lexicon = self.hm.names._lexicon(lang)
        if lexicon is None:
            return None
        for word in re.findall(r"[\w'’-]+", t):
            found = lexicon.noun(word)
            if found is not None:
                return found[3]
        return None

    def _state(self, entity_id: str, asked: str, lang: str, user_id: str | None) -> str:
        assert self.hm.world is not None
        entity = self.hm.model.entities[entity_id]
        thing = self.hm.names.thing(entity_id, lang)
        name = self.hm.names.device(entity_id, lang)
        lexicon = self.hm.names._lexicon(lang)
        state = state_of(entity, self.hm.world.states[entity_id].state)
        if state in (None, "unknown"):
            return self._say(lang, "know_state_unknown", user_id, thing=name)
        gender = thing.gender if thing else "m"
        said = lexicon.state(str(state), gender) if lexicon else str(state)
        if asked == state:
            return self._say(lang, "know_state_yes", user_id, thing=name, state=said)
        if _opposite(asked) == state:
            return self._say(lang, "know_state_no", user_id, thing=name, state=said)
        return self._say(lang, "know_state_is", user_id, thing=name, state=said)

    def _which(
        self, kind: str, asked: str, area: str | None, lang: str, user_id: str | None
    ) -> str:
        """ "Welke lampen zijn nog aan?": all of that kind in that state, per kind and room."""
        assert self.hm.world is not None
        states = self.hm.world.states
        lexicon = self.hm.names._lexicon(lang)
        found = []
        for entity in self._candidates():
            thing = self.hm.names.thing(entity.entity_id, lang)
            if thing is None or thing.head != kind:
                continue
            if area is not None and self.hm.names.area_of(entity.entity_id) != area:
                continue
            if state_of(entity, states[entity.entity_id].state) == asked:
                found.append((entity.entity_id, thing))
        if not found:
            said = lexicon.state(asked) if lexicon else asked
            return self._say(lang, "know_none", user_id, state=said)
        if len(found) == 1:
            entity_id, thing = found[0]
            said = lexicon.state(asked, thing.gender) if lexicon else asked
            name = self.hm.names.device(entity_id, lang)
            return self._say(lang, "know_state_is", user_id, thing=name, state=said)
        group = self.hm.names.group([e for e, _ in found], lang)
        gender = "f" if all(t.gender == "f" for _, t in found) else "m"
        said = lexicon.state(asked, gender, plural=True) if lexicon else asked
        return self._say(lang, "know_many", user_id, things=group, state=said)

    # -- rooms -----------------------------------------------------------------------------

    def _area(self, t: str, lang: str) -> str | None:
        """The room the question names: its name in HA (or an alias), or its plain words."""
        best: tuple[int, str] | None = None
        tokens = set(re.findall(r"[\w'’-]+", t))
        for area in self.hm.model.areas.values():
            for name in (area.name, *area.aliases):
                if len(name) >= 2 and normalise(name) in t:
                    score = 100 + len(name)
                    if best is None or score > best[0]:
                        best = (score, area.id)
            words = set(re.findall(r"[\w'’-]+", self.hm.names.room(area.id, lang).lower()))
            words -= {"van", "of", "von", "de"}
            if words and words <= tokens:
                score = 10 + len(words)
                if best is None or score > best[0]:
                    best = (score, area.id)
        return best[1] if best else None

    def _where(self, area: str, lang: str) -> str:
        return self.hm.names.where(area, lang) or self.hm.names.room(area, lang)

    def _degrees(self, value: float, lang: str) -> str:
        unit = self.hm.config.temperature_unit if self.hm.config else "°C"
        return f"{self.hm.languages.number(lang, value, 1)} {unit}"

    async def _warm(self, area: str | None, lang: str, user_id: str | None) -> str:
        assert self.hm.world is not None
        if area is None:
            return await self._say_house_temperature(lang, user_id)
        value = self.hm.temperatures.of_room(area, self.hm.world.states)
        where = self._where(area, lang)
        if value is None:
            return self._say(lang, "know_climate_unknown", user_id, where=where)
        return self._say(
            lang, "know_climate", user_id, where=where, temperature=self._degrees(value, lang)
        )

    async def _say_house_temperature(self, lang: str, user_id: str | None) -> str:
        value = await self.hm.inside_temperature()
        if value is None:
            return self._say(lang, "know_climate_unknown", user_id, where="")
        return self._say(
            lang, "know_climate_house", user_id, temperature=self._degrees(value, lang)
        )

    def _humidity(self, area: str | None, lang: str, user_id: str | None) -> str:
        assert self.hm.world is not None
        states = self.hm.world.states
        values = []
        for entity in self.hm.model.entities.values():
            if entity.domain != "sensor" or entity.device_class != "humidity":
                continue
            where = self.hm.names.area_of(entity.entity_id)
            if area is not None and where != area:
                continue
            try:
                values.append(float(states[entity.entity_id].state))
            except (KeyError, ValueError):
                continue
        place = self._where(area, lang) if area else ""
        if not values:
            return self._say(lang, "know_climate_unknown", user_id, where=place)
        return self._say(
            lang, "know_humidity", user_id, where=place, humidity=round(sum(values) / len(values))
        )

    def _someone(self, area: str, lang: str, user_id: str | None) -> str:
        assert self.hm.world is not None
        snapshot = self.hm.world.snapshot(self.hm.clock.now())
        topic = "know_someone_yes" if snapshot.occupied(area) else "know_someone_no"
        return self._say(lang, topic, user_id, where=self._where(area, lang))

    def _me(self, lang: str, user_id: str | None) -> str:
        """ "Where am I?" (20.27): the room, when a room sensor on this person's phone tells it;
        otherwise where she sees someone, honestly without knowing who is who. Only about the
        one who asks (B-18)."""
        hm = self.hm
        assert hm.world is not None
        person = hm._person(user_id)
        if person is not None:
            for area in hm.model.areas:
                if user_id in (hm._people_in(area) or set()):
                    return self._say(lang, "know_me_here", user_id, where=self._where(area, lang))
        snapshot = hm.world.snapshot(hm.clock.now())
        rooms = sorted(
            a for a, o in snapshot.occupancy.items() if o.occupied and hm.world.indoors(a)
        )
        if not rooms:
            return self._say(lang, "know_me_none", user_id)
        if len(rooms) == 1:
            return self._say(lang, "know_me_maybe", user_id, where=self._where(rooms[0], lang))
        names = [self._where(a, lang) for a in rooms[:4]]  # "in de keuken"
        joined = ", ".join(names[:-1]) + f" {hm.languages.text(lang, 'ui.and')} " + names[-1]
        return self._say(lang, "know_me_unsure", user_id, rooms=joined)

    def _home(self, lang: str, user_id: str | None) -> str:
        """Who is home: only how many – she speaks aloud, so never names (18.3)."""
        assert self.hm.world is not None
        count = self.hm.world.snapshot(self.hm.clock.now()).residents_home
        if not count:
            return self._say(lang, "know_home_none", user_id)
        if count == 1:
            return self._say(lang, "know_home_one", user_id)
        return self._say(lang, "know_home_count", user_id, count=count)

    # -- what she did, energy --------------------------------------------------------------

    def _did(self, lang: str, user_id: str | None) -> str:
        explainer = self.hm.vesta_explainer
        lexicon = self.hm.names._lexicon(lang)
        zone = ZoneInfo(self.hm.config.time_zone if self.hm.config else "UTC")
        today = self.hm.clock.now().astimezone(zone).date()
        done = [
            r.decision
            for r in self.hm.recent.values()
            if r.decision.executed
            and not r.undone
            and r.decision.at.astimezone(zone).date() == today
        ][-3:]
        if not done or explainer is None:
            return self._say(lang, "know_did_none", user_id)
        parts = [explainer.action_phrase(lang, d) for d in done]
        joiner = lexicon.joiner if lexicon else "and"
        text = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} {joiner} {parts[-1]}"
        return self._say(lang, "know_did", user_id, text=text)

    def _power(self, watts: float, lang: str) -> str:
        if abs(watts) >= 1000:
            return f"{self.hm.languages.number(lang, watts / 1000, 1)} kW"
        return f"{round(watts)} W"

    def _energy(self, lang: str, user_id: str | None) -> str:
        energy = self.hm.energy
        assert self.hm.world is not None
        if energy is None or not energy.setup.has_any:
            return self._say(lang, "know_energy_none", user_id)
        states = self.hm.world.states
        grid = live(states, energy.setup.readings("grid"))
        solar = live(states, energy.setup.readings("solar"))
        said = []
        if grid is not None or solar is not None:
            use = max(0.0, (grid or 0.0) + (solar or 0.0))
            said.append(self._say(lang, "know_energy_use", user_id, power=self._power(use, lang)))
        if solar is not None and solar > 0:
            said.append(self._say(lang, "know_energy_sun", user_id, power=self._power(solar, lang)))
        return " ".join(said) or self._say(lang, "know_energy_none", user_id)

    # -- settings --------------------------------------------------------------------------

    def _setting(
        self, t: str, lang: str, user_id: str | None, admin: bool, channel: str, level: int
    ) -> dict[str, Any] | None:
        """Where a setting is (E-244): she says where, what it is now, and offers to open it.
        She changes nothing herself; the house settings only open for an administrator."""
        best: tuple[int, Setting] | None = None
        for setting in self.hm.settings_catalog.values():
            word = self._has(t, self._words(lang, f"talk.settings.{setting.key}"))
            if word and (best is None or len(word) > best[0]):
                best = (len(word), setting)
        if best is None:
            return None
        setting = best[1]
        text = self.hm.languages.text
        sheet = text(
            lang,
            {"preferences": "ui.my_preferences", "brain": "tabs.brain"}.get(
                setting.sheet, "ui.house_settings"
            ),
        )
        name = self.hm.vesta_name(lang)
        section = name if setting.title == "@vesta" else text(lang, setting.title, name=name)
        now = self._now(setting, lang, user_id, level)
        lead = self._say(lang, "know_setting_now", user_id, value=now) + " " if now else ""
        if setting.who == "admins" and not admin:
            said = self._say(lang, "know_setting_admin", user_id, sheet=sheet, section=section)
            return {"text": lead + said}
        if channel == "assist":
            said = self._say(lang, "know_setting_spoken", user_id, sheet=sheet, section=section)
            return {"text": lead + said}
        said = self._say(lang, "know_setting_where", user_id, sheet=sheet, section=section)
        # A tab of Brein opens as Klaar voor autonomie? does: in Brein, at that tab.
        opens = "readiness" if setting.sheet == "brain" else setting.sheet
        return {"text": lead + said, "open": {"sheet": opens, "section": setting.section}}

    async def _ready(
        self, t: str, lang: str, user_id: str | None, admin: bool, channel: str
    ) -> dict[str, Any]:
        """How far HouseMind is (E-256): the sentence of the part asked about, or of the parts
        that have something to say. In the app she offers to show the card."""
        parts = await readiness(self.hm, lang, 1, admin)
        named: dict[str, Any] | None = None
        longest = 0
        for part in parts:
            word = self._has(t, self._words(lang, f"talk.know.part.{part['domain']}"))
            if word and len(word) > longest:
                longest, named = len(word), part
        spoken = channel == "assist"
        if named is not None:
            topic = "know_ready_spoken" if spoken else "know_ready"
            said = self._say(lang, topic, user_id, part=named["name"], text=named["text"])
            section = f"ready-{named['domain']}"
        else:
            busy = [p for p in parts if any(p["counts"].values())][:2] or parts[:1]
            joined = " ".join(f"{p['name']}: {p['text']}" for p in busy)
            topic = "know_ready_all_spoken" if spoken else "know_ready_all"
            said = self._say(lang, topic, user_id, text=joined)
            section = "readiness"
        if spoken:
            return {"text": said}
        return {"text": said, "open": {"sheet": "readiness", "section": section}}

    def _tidy(
        self, t: str, lang: str, user_id: str | None, admin: bool, channel: str
    ) -> dict[str, Any]:
        """'Is er iets stuk in Home Assistant?' (E-361, B-246), from the last round, also
        without an AI. A resident who is no administrator only hears whether something
        important is there; only an administrator is offered the page."""
        hm = self.hm
        if hm.tidy_checked is None:
            return {"text": self._say(lang, "know_tidy_not_yet", user_id)}
        backup = (hm.tidy_system or {}).get("backup") or {}
        if self._has(t, self._words(lang, "talk.know.tidy_backup")):
            days = backup.get("days")
            if backup.get("state") == "none":
                return {"text": self._say(lang, "know_tidy_backup_none", user_id)}
            if days is None:
                return {"text": self._say(lang, "know_tidy_backup_unknown", user_id)}
            if int(days) == 0:
                return {"text": self._say(lang, "know_tidy_backup_today", user_id)}
            return {"text": self._say(lang, "know_tidy_backup", user_id, count=int(days))}
        broken = [f for f in hm.tidy_findings if not f.kind.startswith("unused")]
        if not admin:
            disk = (hm.tidy_system or {}).get("disk") or {}
            important = any(f.heavy and (f.depends or f.used) for f in broken)
            important = important or backup.get("state") in ("old", "none")
            important = important or disk.get("state") == "full"
            topic = "know_tidy_member_some" if important else "know_tidy_member_none"
            return {"text": self._say(lang, topic, user_id)}
        if not broken:
            return {"text": self._say(lang, "know_tidy_none", user_id)}
        first = broken[0]  # the heaviest comes first (B-238)
        what = hm.languages.text(
            lang, f"notify.tidy.found.{first.kind}", name=first.name, count=len(first.follows)
        )
        if len(broken) == 1:
            said = self._say(lang, "know_tidy_one", user_id, text=what)
        else:
            said = self._say(lang, "know_tidy_some", user_id, count=len(broken), text=what)
        if channel == "assist":
            return {"text": said}
        said = f"{said} {self._say(lang, 'know_tidy_open', user_id)}"
        return {"text": said, "open": {"sheet": "settings", "section": "tidy"}}

    def _now(self, setting: Setting, lang: str, user_id: str | None, level: int) -> str:
        who = next((p for p in self.hm._people if p.user_id == user_id), None)
        text = self.hm.languages.text
        if setting.now == "level":
            key = dict(LEVELS).get(level)  # the level this screen shows
            return text(lang, f"levels.{key}") if key else ""
        if setting.now == "address" and who is not None:
            form = who.preferences.get("address") or "je"
            return text(lang, f"ui.prefs.address_{form}")
        if setting.now == "vesta":
            return self.hm.vesta_name(lang)
        if setting.now == "language":
            return text(lang, "format.language_name")
        return ""

    # -- what waits for you, house rules ---------------------------------------------------

    async def todo(self, lang: str, level: int, user_id: str | None, admin: bool) -> str:
        """What waits for you – also the choice Moet ik nog iets doen? after a tap (E-349)."""
        return await self._todo(lang, level, user_id, admin)

    async def _todo(self, lang: str, level: int, user_id: str | None, admin: bool) -> str:
        asks = await self.hm.vesta_asks(user_id, max(level, 2), admin, every=True)
        if not asks:
            return self._say(lang, "know_todo_none", user_id)
        lines = [await self.hm.vesta_ask_text(user_id, lang, ask) for ask in asks[:3]]
        if len(asks) == 1:
            head = self._say(lang, "know_todo_one", user_id)
        else:
            head = self._say(lang, "know_todo_some", user_id, count=len(asks))
        tail = (
            [self._say(lang, "know_todo_more", user_id, count=len(asks) - 3)]
            if len(asks) > 3
            else []
        )
        return " ".join([head, *lines, *tail])

    def _rules(self, lang: str, user_id: str | None) -> str:
        from housemind.server import views  # the same words as on the Huisregels screen

        rules = views.rules(self.hm, lang)
        if not rules:
            return self._say(lang, "know_rules_none", user_id)
        if len(rules) == 1:
            head = self._say(lang, "know_rules_one", user_id)
        else:
            head = self._say(lang, "know_rules_some", user_id, count=len(rules))
        lines = [str(r["text"]).rstrip(".") + "." for r in rules[:3]]
        tail = (
            [self._say(lang, "know_rules_more", user_id, count=len(rules) - 3)]
            if len(rules) > 3
            else []
        )
        return " ".join([head, *lines, *tail])


def _opposite(state: str) -> str:
    return {
        "on": "off",
        "off": "on",
        "open": "closed",
        "closed": "open",
        "locked": "unlocked",
        "unlocked": "locked",
    }.get(state, "")
