"""The App's web server: the UI through Home Assistant Ingress and one live websocket (E-86).

Only Home Assistant's Ingress proxy may connect when running as an App; the logged-in HA
user arrives in the ``X-Remote-User-*`` headers set by Ingress.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from aiohttp import WSCloseCode, WSMsgType, web

from housemind import __version__
from housemind.ai.service import TASKS as AI_TASKS
from housemind.ai.service import AIConfig
from housemind.brain import house_settings, setting_up, settings_file, start_over, stepwish
from housemind.brain.briefing import PARTS
from housemind.brain.conversation import Asked, Conversation, plain
from housemind.brain.conversation import choice as choice_of
from housemind.brain.days import KINDS
from housemind.brain.later import later
from housemind.brain.notify import ALL_CAMERAS, kind_key, later_until
from housemind.brain.proposals import Answer
from housemind.brain.readiness import readiness
from housemind.brain.rules import HouseRule, read_back
from housemind.brain.security import answer as answer_alarm
from housemind.brain.talk import normalise
from housemind.brain.voice import Moment
from housemind.demo.play import Busy, Locked, Play
from housemind.runtime import DOMAINS, HouseMind
from housemind.safety.gate import Decision, Layer
from housemind.server import bridge, manual, views
from housemind.server.logbook import logbook
from housemind.server.tidy_view import tidy_view

_LOGGER = logging.getLogger(__name__)


class _QuietBridge(logging.Filter):
    """The integration asks for the state every 20 seconds: a success is not worth a log
    line, as it would bury everything else; a failure still is."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not ("/integration/state" in message and '" 200 ' in message)


logging.getLogger("aiohttp.access").addFilter(_QuietBridge())

INGRESS_PROXY_IP = "172.30.32.2"
LOCAL_USER = "local-user"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; img-src 'self' data:; media-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'self'; form-action 'none'"
)

APP_KEY = web.AppKey("housemind", HouseMind)
INGRESS_ONLY_KEY = web.AppKey("ingress_only", bool)
DEMO_KEY = web.AppKey("demo", bool)
PLAY_KEY = web.AppKey("play", Play)  # 'Speel het huis': only a demo house has it (E-321)
FONTS_KEY = web.AppKey("manual_fonts", str)  # the app's fonts, for the manual page (E-324)
SESSIONS_KEY = web.AppKey("sessions", set["Session"])
MAX_AUDIO = 16000 * 2 * 8  # 8 seconds of 16 kHz 16-bit sound
MIN_AUDIO = 16000 * 2 // 4  # less than a quarter second: nothing was said
HEALTH_STALE = timedelta(minutes=10)  # no round of thinking for this long: stuck
MAX_SESSIONS = 50  # screens open at once: a house has a handful (QA 3, gat 10)
MAX_PER_SECOND = 20  # messages a second from one screen
PUSH_GAP_S = 0.2  # at most five pictures a second to one screen (code QA 3, punt 11)
_TEXTS_JSON: dict[int, tuple[object, str]] = {}  # a language's texts, as JSON once


def _as_json(key: str, value: Any) -> str:
    """A part of the picture as JSON; a language's texts only once (code QA 3, punt 11): the
    same dictionary until a language file changes."""
    if key != "texts":
        return json.dumps(value)
    kept = _TEXTS_JSON.get(id(value))
    if kept is None or kept[0] is not value:
        kept = (value, json.dumps(value))
        if len(_TEXTS_JSON) > 8:
            _TEXTS_JSON.clear()  # languages changed: the old ones go
        _TEXTS_JSON[id(value)] = kept
    return kept[1]


@web.middleware
async def security(request: web.Request, handler: Any) -> web.StreamResponse:
    response: web.StreamResponse
    from_ha = request.path.startswith(bridge.PREFIX) and bridge.from_home_assistant(request)
    if request.app[INGRESS_ONLY_KEY] and request.remote != INGRESS_PROXY_IP and not from_ha:
        response = web.Response(
            status=403, text="HouseMind is only reachable through Home Assistant."
        )
    elif _foreign_origin(request) or _not_local(request):
        response = web.Response(status=403, text="origin")
    else:
        response = await handler(request)
    # The live connection only to this App's own host (QA 3, gat 10): 'self', and for older
    # browsers, which do not count ws and wss as 'self', the host by name. The manual adds its
    # own scripts.
    response.headers.setdefault("Content-Security-Policy", _with_own_socket(CSP, request))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(self)"
    return response


def _with_own_socket(policy: str, request: web.Request) -> str:
    host = request.headers.get("X-Forwarded-Host") or request.host
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host or ""):
        return policy
    return policy.replace("connect-src 'self'", f"connect-src 'self' wss://{host} ws://{host}", 1)


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _not_local(request: web.Request) -> bool:
    """Outside Home Assistant the App listens on this computer only, and the person at the
    keyboard is its administrator; a page elsewhere that points its own name at this computer
    (DNS rebinding) must not be let in (QA 3, gat 10)."""
    if request.app[INGRESS_ONLY_KEY]:
        return False
    name = (
        request.host.rsplit(":", 1)[0]
        if not request.host.startswith("[")
        else (request.host.split("]", 1)[0] + "]")
    )
    return name.lower() not in _LOOPBACK


def _foreign_origin(request: web.Request) -> bool:
    """A change or the live connection from another website (QA 2, L1): a page elsewhere
    must not talk to HouseMind with the person's Home Assistant login. A browser always says
    where it comes from; that must be this App's own host – as the browser saw it, or as Home
    Assistant passes it on through Ingress. No Origin: not a browser on another site."""
    if request.method in ("GET", "HEAD", "OPTIONS") and request.path != "/api/ws":
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return False
    host = origin.split("://", 1)[-1].rstrip("/")
    return host not in {request.host, request.headers.get("X-Forwarded-Host", "")}


def user_of(request: web.Request) -> tuple[str, str | None]:
    user_id = request.headers.get("X-Remote-User-Id")
    name = request.headers.get("X-Remote-User-Display-Name") or request.headers.get(
        "X-Remote-User-Name"
    )
    return (user_id or LOCAL_USER), name


async def say(hm: HouseMind, language: str, admin: bool = True, level: int = 4) -> str:
    """What an Orb tap says: the same as its colour. It names the message or question that
    colours it."""
    lang = hm.languages.resolve(language)
    t = hm.languages.text
    state = hm.orb_state(thinking=False, admin=admin, level=level)
    if state == "emergency" and hm.status.emergency is not None:
        what = t(lang, f"emergency.{hm.status.emergency.value}")
        return t(lang, "say.emergency", what=what)
    if state == "paused":
        return t(lang, "say.paused")
    if state in ("attention", "learning"):
        wanted = ("emergency", "important") if state == "attention" else ("learning",)
        for note in await hm.notifications():
            concerns = str(note.get("concerns", "home"))
            if note["kind"] not in wanted or concerns.startswith("user:"):
                continue
            if concerns == "admins" and not admin:
                continue
            text = t(lang, str(note["text_key"]), **json.loads(str(note["params"])))
            return t(lang, f"say.{state}", what=text)
    if state == "sleeping":
        return t(lang, "say.sleeping")
    if state == "secured":
        return t(lang, "say.secured")  # the alarm is on (E-539)
    return t(lang, "say.calm")


class Session:
    """One open screen of one person."""

    def __init__(self, request: web.Request, ws: web.WebSocketResponse) -> None:
        self.hm: HouseMind = request.app[APP_KEY]
        self.ws = ws
        self._answered = False  # did the current message already get an answer on screen?
        self.user_id, self.name = user_of(request)
        # Outside HA, or in a demo house (nothing real), the person watching is the administrator.
        self.trusted_local = not request.app[INGRESS_ONLY_KEY] or request.app[DEMO_KEY]
        self.play: Play | None = request.app.get(PLAY_KEY)  # None in a real house (B-187)
        self.viewer: views.Viewer | None = None
        self.ha_language: str | None = None
        self.browser_language: str | None = None
        self.tablet: str | None = None  # this screen is a wall tablet in that area (E-134)
        self.mic = False  # this screen may listen (a microphone, a safe connection: B-83)
        self.listening = False
        self.audio = bytearray()  # what was said, only until HA heard it – never kept
        self.talking = Conversation()  # her open question and the last sentences (E-349)
        self.heard_now: str | None = None  # what was just said aloud, for her reply
        self.missed = 0  # times in a row she did not catch what was said
        self.pending_rules: dict[str, dict[str, Any]] = {}
        # A step to set, or the automation that steers it, waiting for yes or no (E-317).
        self.pending_steps: dict[str, dict[str, Any]] = {}
        # Vesta asks what waits for an answer, one at a time (E-252, B-96).
        self.ask: dict[str, Any] | None = None  # the question offered to this screen
        self.answered_at: datetime | None = None  # just answered one: "Ik heb nog een vraag"
        self.asks_quiet_until: datetime | None = None  # closed her window: not now
        self.pending_open: dict[str, str] | None = None  # "Zal ik het openen?" (E-244)
        self.tap_notice: str | None = None  # a notice she mentioned on a tap, as her question
        # The newest picture, in this screen's own time (see push_soon).
        self._pushing: asyncio.Task[None] | None = None
        # What this screen has, part by part (S4): a screen that asks gets only what changed.
        self.partial = False
        self._sent: dict[str, str] = {}
        self._stale = False
        self._pushed_at = 0.0  # when this screen last got a picture (event loop time)
        # Brein › Apparaten as this screen has it: each device's state as last sent. None until
        # the list was asked; from then on only what changed follows by itself (E-472).
        self._device_states: dict[str, str] | None = None
        # Test het huis (E-474): the room this screen tests and the situation of Doe alsof –
        # only on this screen, gone when it closes (design 70.2).
        self.test_area: str | None = None
        self.test_chosen: list[str] = []
        # The lamps whose Standen window is open on this screen (E-516): let go when it closes.
        self.holding: set[str] = set()

    async def refresh(self) -> views.Viewer:
        viewer = await views.load_viewer(self.hm, self.user_id, self.name, self.trusted_local)
        viewer.ha_language = self.ha_language
        viewer.browser_language = self.browser_language
        if self.tablet is not None:
            # A shared screen: the calmest level of who is home (E-22), HA's language (E-25),
            # nobody's personal settings and no house settings.
            levels = self.hm._levels_home()
            viewer.level = min(levels) if levels else 1
            viewer.is_admin = False
            viewer.preferences, viewer.consents = {}, {}
            viewer.ha_language = self.hm.config.language if self.hm.config else None
            viewer.browser_language = None
            viewer.tablet = self.tablet
        self.viewer = viewer
        return viewer

    @property
    def lang(self) -> str:
        return self.viewer.language(self.hm) if self.viewer else "en"

    async def push(self) -> None:
        if self.ws.closed:
            return
        viewer = await self.refresh()
        snapshot = await views.snapshot(self.hm, viewer)
        if self.play is not None and self.tablet is None:
            snapshot["demo"] = self.play.live()  # the demo bar: the house's own time (E-321)
        message: dict[str, Any] = {"type": "snapshot", "snapshot": snapshot}
        if self.partial:
            # Only the parts that changed for this screen (S4): the whole picture is up to
            # 721 KB, of which 340 KB texts that never change.
            parts = {key: _as_json(key, value) for key, value in snapshot.items()}
            if self._sent:
                message["partial"] = True
                message["snapshot"] = {
                    key: snapshot[key] for key, sent in parts.items() if self._sent.get(key) != sent
                }
                message["gone"] = [key for key in self._sent if key not in parts]
            self._sent = parts
        await self.ws.send_json(message)
        await self._device_changes()
        await self._offer_ask(viewer)

    async def _device_changes(self) -> None:
        """Brein › Apparaten follows the house (E-472): a screen that has the list gets each
        device's new state by itself – only what changed, a few dictionary lookups, so it costs
        a large house nothing."""
        if self._device_states is None or self.hm.world is None:
            return
        states = self.hm.world.states
        changed: dict[str, str] = {}
        for entity_id, sent in self._device_states.items():
            state = states.get(entity_id)
            now = state.state if state else ""
            if now != sent:
                changed[entity_id] = now
        if changed:
            self._device_states.update(changed)
            await self.send({"type": "data", "what": "device_states", "data": changed})

    def push_soon(self) -> None:
        """The newest picture, in this screen's own time: a screen that stopped reading never
        holds up the house, not even an emergency."""
        self._stale = True
        if self._pushing is None or self._pushing.done():
            self._pushing = asyncio.create_task(self._push_newest())

    async def _push_newest(self) -> None:
        loop = asyncio.get_running_loop()
        while self._stale and not self.ws.closed:
            # A busy house – a sensor every few milliseconds – gives a screen at most five
            # pictures a second; the newest is always the last (code QA 3, punt 11).
            wait = self._pushed_at + PUSH_GAP_S - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._stale = False
            try:
                await self.push()
            except (ConnectionError, RuntimeError):
                return
            self._pushed_at = loop.time()

    async def _offer_ask(self, viewer: views.Viewer) -> None:
        """The most important question that waits for this person, in Vesta's words (E-252).
        The app shows it when the person is done with what they are doing."""
        now = self.hm.clock.now()
        if await self.hm.setting("vesta_window") is False:
            # An administrator chose it for the whole house: her questions wait in Logboek, a
            # tap on her still asks them; no window by itself (E-350, E-450).
            if self.ask is not None:
                await self.send({"type": "vesta_ask_gone", "key": self.ask["key"]})
                self.ask = None
            return
        if self.ask is not None:
            if await self.hm.vesta_ask_open(self.user_id, self.ask):
                return  # one at a time: it stays until it is answered or closed (E-464)
            await self.send(
                {"type": "vesta_ask_gone", "key": self.ask["key"]}
            )  # answered elsewhere
            self.ask = None
        asks = await self.hm.vesta_asks(
            self.user_id, viewer.level, viewer.is_admin, self.tablet is not None
        )
        if not asks or (self.asks_quiet_until is not None and now < self.asks_quiet_until):
            return
        ask = asks[0]
        text = await self.hm.vesta_ask_text(self.user_id, self.lang, ask)
        follow = self.answered_at is not None and now - self.answered_at < ASK_FOLLOW
        if follow:
            text = f"{self.hm.vesta_says(self.lang, 'next_question', self.user_id)} {text}"
        actions = [*ask["actions"], *(["why"] if ask["kind"] == "proposal" else [])]
        self.ask = {**ask, "text": text, "actions": actions}  # Waarom? as a button (E-349)
        await self.send(
            {
                "type": "vesta_ask",
                **self._ask_message(self._asked_from(self.ask)),
                "voice": self.hm.vesta_name(self.lang),
                "follow": follow,
            }
        )

    # -- the conversation (E-349, chapter 32) ----------------------------------------------

    def _ai_on(self) -> bool:
        return self.hm.ai.status() == "on"

    @staticmethod
    def _asked_from(ask: dict[str, Any]) -> Asked:
        """A question of her window as her open question in the conversation."""
        return Asked(
            str(ask["kind"]),
            str(ask["key"]),
            tuple(ask["actions"]),
            str(ask["text"]),
            proposal=ask.get("proposal"),
            notification=ask.get("notification"),
        )

    def _ask_message(self, asked: Asked) -> dict[str, Any]:
        """Her question with its buttons, as the window shows it (E-252)."""
        return {
            "key": asked.key,
            "kind": asked.kind,
            "text": asked.text,
            "actions": list(asked.actions),
            "proposal": asked.proposal,
            "notification": asked.notification or 0,
        }

    def _notice_word(self, text: str, offered: tuple[str, ...]) -> tuple[str, bool] | None:
        """Words for a notice's answers without an AI: 'negeer maar' is Niet meer tonen,
        'later' Later, 'ik kijk ernaar' Ik kijk ernaar – in every language. Without Niet meer
        tonen on offer (not an administrator, E-412), 'negeer maar' is Ik kijk ernaar."""
        said = plain(text)
        for action in NOTICE_ANSWERS:
            words = self.hm.languages.text(self.lang, f"talk.words.{action}")
            for phrase in str(words).split(","):
                phrase = plain(phrase)
                if phrase and (said == phrase or said.startswith(phrase + " ")):
                    if action == "mute" and action not in offered:
                        return ("seen", False) if "seen" in offered else None
                    return action, False
        return None

    def _labels(self, asked: Asked) -> dict[str, str]:
        """The words on her buttons, as the window shows them."""
        t = self.hm.languages.text
        out: dict[str, str] = {}
        for action in asked.actions:
            if action == "why":
                key = "ui.vesta.why_ask"
            elif action.startswith("later_"):
                key = f"ui.notice.later.{action[6:]}"  # how long, as on the card (E-372)
            elif asked.kind in ("proposal", "thumbs"):
                key = f"notify.answer.{action}"
            elif asked.kind == "task":
                key = f"ui.notifications.{action}"
            elif asked.kind == "choices":
                key = f"ui.vesta.choice.{action}"
            elif asked.kind == "knowledge":
                key = f"ui.knowledge.{action}"  # Klopt, Klopt niet (B-360)
            elif asked.kind == "ai_choice":
                out[action] = action  # the AI's own words
                continue
            else:
                key = f"ui.{action}"  # yes and no
            out[action] = t(self.lang, key)
        return out

    def _choices(self, asked: Asked) -> list[dict[str, str]]:
        kind = "ai" if asked.kind == "ai_choice" else "tap"
        return [
            {"id": action, "text": label, "kind": kind}
            for action, label in self._labels(asked).items()
        ]

    def _keep(self, asked: Asked) -> dict[str, Any]:
        """Her buttons stay while her question is open (B-228): what goes along with a say."""
        if asked.kind == "confirm":
            return {"confirm": asked.token}
        if asked.kind == "offer" and self.pending_open is not None:
            return {"offer": self.pending_open}
        if asked.kind in ("choices", "ai_choice"):
            return {"choices": self._choices(asked)}
        return {"ask": self._ask_message(asked)}

    def _yes_no(self) -> tuple[list[str], list[str]]:
        t = self.hm.languages.text
        return tuple(  # type: ignore[return-value]
            [w.strip() for w in t(self.lang, f"talk.words.{code}").split(",") if w.strip()]
            for code in ("yes", "no")
        )

    async def _heard(self, text: str, spoken: bool) -> None:
        """Everything said or typed to her: first her open question (E-349). Without an AI
        only her choices answer it – the words on a button, or yes or no – and she never
        guesses; what she knows for sure she answers, and her question stays open."""
        now = self.hm.clock.now()
        asked = self.talking.current(now)
        if asked is None and self.ask is not None and self.ask.get("shown"):
            # It stays on the screen until it is answered, also after a long wait (E-464).
            asked = self._asked_from(self.ask)
            self.talking.ask(asked, now)
        self.talking.said("you", text, now)
        heard = text if spoken else None
        if asked is not None:
            yes, no = self._yes_no()
            found = choice_of(text, asked, self._labels(asked), yes, no)
            if found is None and asked.notification:
                # "negeer maar", "later" (E-381, B-280)
                found = self._notice_word(text, tuple(asked.actions))
            if found is not None:
                self.missed = 0
                await self._chosen(asked, found[0], found[1], heard, spoken)
                return
            viewer = self.viewer or await self.refresh()
            sure = await self.hm.talk_sure(text, self.lang, viewer.level, self.user_id)
            if sure is not None:
                self.missed = 0
                await self._log_talk(text, sure["text"])
                self.talking.said("vesta", sure["text"], now)
                extra = {"voice": self.hm.vesta_name(self.lang), "heard": text} if spoken else {}
                await self.say(sure["text"], **extra, **self._keep(asked))
                if spoken:
                    await self._voice(sure["text"])
                return
            if not self._ai_on():
                await self._not_a_choice(asked, heard, spoken)
                return
        self.missed = 0
        await self._talk(text, spoken)

    async def _not_a_choice(
        self, asked: Asked, heard: str | None, spoken: bool, lead: str = ""
    ) -> None:
        """Not one of her choices: she names them again and, when it was said aloud, listens
        once more (B-228). ``lead``: why not with the AI, first (B-231)."""
        words = [plain(label) for label in self._labels(asked).values()]
        joined = (
            f"{', '.join(words[:-1])} {self.hm.languages.text(self.lang, 'ask.or')} {words[-1]}"
            if len(words) > 1
            else "".join(words)
        )
        said = self.hm.vesta_fact(self.lang, "ask_choices", self.user_id, choices=joined)
        said = f"{lead} {said}".strip()
        self.talking.said("vesta", said, self.hm.clock.now())
        self.missed += 1
        extra = {"voice": self.hm.vesta_name(self.lang), "heard": heard} if spoken else {}
        await self.say(
            said, listen=spoken and self.mic and self.missed < 2, **extra, **self._keep(asked)
        )
        await self._voice(said)

    async def _chosen(
        self,
        asked: Asked,
        action: str,
        plain_no: bool,
        heard: str | None,
        spoken: bool,
        ai_text: str | None = None,
    ) -> None:
        """One of her choices, said, typed or tapped: the same way as its button."""
        name = self.hm.vesta_name(self.lang)
        if action == "why" and asked.proposal is not None:
            said = await self.hm.vesta_why_asked(self.user_id, self.lang, asked.proposal)
            self.talking.said("vesta", said, self.hm.clock.now())
            extra = {"voice": name, "heard": heard} if spoken else {}
            await self.say(said, **extra, **self._keep(asked))
            await self._voice(said)
            return
        self.talking.answered()
        self.heard_now = heard
        if asked.kind == "ai_choice":
            await self._talk(action, spoken)  # the AI's own choice, as your next words
        elif asked.kind == "choices":
            await self._choice(action)
        elif asked.kind == "offer":
            offer, self.pending_open = self.pending_open, None
            if action == "yes" and offer is not None:
                await self.send({"type": "open", **offer})
            else:
                reply = self.hm.vesta_says(self.lang, "answer_no", self.user_id)
                await self.say(reply, **({"voice": name, "heard": heard} if spoken else {}))
                if spoken:
                    await self._voice(reply)
        elif asked.kind == "confirm" and asked.token is not None:
            step = self.pending_steps.pop(asked.token, None)
            if step is not None:
                await self._answer_step(step, action == "yes", spoken)
            else:
                await self.on_confirm_rule({"token": asked.token, "yes": action == "yes"})
        elif asked.kind == "proposal" and asked.proposal is not None:
            await self._answer_proposal(
                asked.proposal,
                Answer(action),
                ai_text is None,
                said_no=plain_no,
                key=asked.key,
            )
        elif asked.notification:
            via = "vesta" if ai_text is None else "ai"
            await self.on_notification({"id": asked.notification, "action": action, "via": via})
        self.heard_now = None
        if ai_text:
            await self.say(ai_text, ai=True, **({"voice": name, "heard": heard} if spoken else {}))
            if spoken:
                await self._voice(ai_text)

    async def _choice(self, action: str) -> None:
        """A choice after a tap without an AI (B-229): she tells, or the app opens it."""
        if action in ("pause", "help"):
            await self.send({"type": "choice", "choice": action})
            return
        viewer = self.viewer or await self.refresh()
        if action == "todo":
            said = await self.hm.knowing.todo(
                self.lang, viewer.level, self.user_id, viewer.is_admin
            )
        else:
            line = await self.hm.vesta_line(
                self.user_id, viewer.level, viewer.is_admin, self.lang, Moment.TAP
            )
            said = line.text
        asked = Asked("choices", "choices", self._tap_choices(viewer), said)
        self.talking.ask(asked, self.hm.clock.now())  # the choices stay for the next one
        extra = {"heard": self.heard_now} if self.heard_now else {}
        await self.say(
            said,
            voice=self.hm.vesta_name(self.lang),
            choices=self._choices(asked),
            **extra,
        )
        await self._voice(said)

    def _tap_choices(self, viewer: views.Viewer) -> tuple[str, ...]:
        """What she offers after a tap without an AI (B-229): no "Moet ik nog iets doen?" on
        level 1 or a wall tablet, where nothing waits for one person."""
        mine = viewer.level >= 2 and self.tablet is None
        return ("status", *(["todo"] if mine else []), "pause", "help")

    async def on_choice(self, data: dict[str, Any]) -> None:
        """A tap on one of her choices after a tap without an AI."""
        action = data.get("choice")
        if action in ("status", "todo"):
            self.talking.answered("choices")
            await self._choice(str(action))

    async def on_ask_why(self, data: dict[str, Any]) -> None:
        """Waarom? under her question (E-349, B-228)."""
        asked = self.talking.current(self.hm.clock.now())
        if asked is not None and asked.key == data.get("key") and asked.proposal is not None:
            await self._chosen(asked, "why", False, None, False)

    async def on_vesta_closed(self, data: dict[str, Any]) -> None:
        """Her window closed: the conversation is over (E-349)."""
        self.talking.end()

    def _intro_answer(self, step: str, heard: str) -> int | bool | None:
        """What a spoken answer in the introduction means: a level for the first step ("niveau
        twee", "betrokken", "3"), else yes or no. None: not understood."""
        said = normalise(heard).split()
        if not said:
            return None
        t = self.hm.languages.text
        if step == "level":
            numbers = [w.strip() for w in t(self.lang, "talk.words.numbers").lower().split(",")]
            names = [t(self.lang, f"levels.{key}").lower() for _, key in LEVELS]
            for word in said:
                if word.isdigit() and 1 <= int(word) <= 4:
                    return int(word)
                for level, group in enumerate(numbers[:4], start=1):
                    if word in group.split("|"):  # "een|één": both are 1
                        return level
            for level, name in enumerate(names, start=1):
                if name and name in normalise(heard):
                    return level
            return None
        yes = set(t(self.lang, "talk.words.yes").lower().replace(",", " ").split())
        no = set(t(self.lang, "talk.words.no").lower().replace(",", " ").split())
        if said[0] in yes:
            return True
        if said[0] in no:
            return False
        return None

    async def on_vesta_read(self, data: dict[str, Any]) -> None:
        """Her voice for a text of the tour or the introduction (E-249, E-253): only those
        texts, never anything sent along."""
        key = data.get("key")
        if not isinstance(key, str) or not key.startswith(("ui.tour.", "ui.intro.vesta.")):
            return
        text = self.hm.languages.text(self.lang, key, name=self.hm.vesta_name(self.lang))
        if text != key:
            await self._voice(text)

    async def on_offer_closed(self, data: dict[str, Any]) -> None:
        self.pending_open = None  # "Nee" or the window closed
        self.talking.answered("offer")

    async def on_ask_shown(self, data: dict[str, Any]) -> None:
        """Her window shows the question: her voice (17.5, E-349). Not asked again within the
        hour once it is answered or closed; until then it stays (E-464)."""
        if self.ask is None or data.get("key") != self.ask["key"]:
            return
        self.ask["shown"] = True
        self.hm.asked(self.user_id, self.ask["key"])
        self.talking.ask(self._asked_from(self.ask), self.hm.clock.now())
        await self._voice(str(self.ask["text"]))

    async def on_ask_closed(self, data: dict[str, Any]) -> None:
        """Her window was closed without an answer: it stays under Notifications, and she
        does not ask the next one right away."""
        if self.ask is not None and data.get("key") == self.ask["key"]:
            self.ask = None
            self.talking.answered(str(data.get("key")))
            self.asks_quiet_until = self.hm.clock.now() + ASK_QUIET

    async def _answered_ask(self, key: str, topic: str) -> None:
        """An answer in her window: she says so in her own words, then the next question."""
        self.ask = None
        self.talking.answered(key)
        self.answered_at = self.hm.clock.now()
        text = self.hm.vesta_says(self.lang, topic, self.user_id)
        if text:
            heard = {"heard": self.heard_now} if self.heard_now else {}
            await self.say(text, voice=self.hm.vesta_name(self.lang), **heard)
            await self._voice(text)

    async def _ask_over(self) -> None:
        """The question offered here is answered, before the next one comes: gone from this
        screen, also when it still waited for its moment there."""
        assert self.ask is not None
        await self.send({"type": "vesta_ask_gone", "key": self.ask["key"]})
        self.ask, self.answered_at = None, self.hm.clock.now()

    async def send(self, message: dict[str, Any]) -> None:
        if not self.ws.closed:
            await self.ws.send_json(message)

    async def say(self, text: str, **extra: Any) -> None:
        self._answered = True
        await self.send({"type": "say", "text": text, **extra})

    async def toast(self, decision_id: int, decision: Decision) -> None:
        """A short message after a try on Voorstellen, with Why? and Undo (E-135, E-513). Not on
        level 1."""
        viewer = self.viewer or await self.refresh()
        if viewer.level < 2 or self.hm.explainer is None:
            return
        line = self.hm.explain(decision, 2, self.lang)[0]
        self._answered = True
        await self.send({"type": "toast", "id": decision_id, "text": line, "undo": True})

    # -- messages -------------------------------------------------------------------------

    async def handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if not isinstance(kind, str) or not kind.isidentifier():
            return
        handler = getattr(self, f"on_{kind}", None)
        if handler is None:
            return
        self._answered = False
        await handler(data)
        if kind in OWN_CHANGES:
            await self.push()  # other changes reach every screen through the brain's update
        # Everything that saves or does something in the background confirms it – unless it
        # already said so.
        confirm = "ui.saved" if kind in SAVES else "ui.done" if kind in DONE else None
        if kind == "integration" and data.get("do") not in ("name", "forward"):
            confirm = None  # placing and restarting show their own steps
        if kind == "set_pref" and data.get("key") in ("tour", "seen_version", "brain_tab"):
            # The end of the tour, "what is new" read, a tab in Brein: nothing to confirm.
            confirm = None
        if confirm and not self._answered:
            text = self.hm.languages.text(self.lang, confirm)
            await self.send({"type": "saved", "text": text, "brief": True})

    async def on_hello(self, data: dict[str, Any]) -> None:
        for key, attr in (("haLanguage", "ha_language"), ("browserLanguage", "browser_language")):
            value = data.get(key)
            if isinstance(value, str) and 0 < len(value) <= 16:
                setattr(self, attr, value)
        tablet = data.get("tablet")
        self.tablet = tablet if isinstance(tablet, str) and tablet in self.hm.model.areas else None
        self.mic = data.get("mic") is True
        self.partial, self._sent = data.get("partial") is True, {}  # the next picture whole
        if self.ha_language and not self.tablet:
            await self._keep_ha_language(self.ha_language)

    async def _keep_ha_language(self, language: str) -> None:
        """The language Home Assistant shows this person, kept with them: their push messages
        go in it, also while the app is closed (20.21). A wall tablet speaks for the house."""
        row = await self.hm.db.fetchone(
            "SELECT preferences FROM people WHERE user_id = ?", (self.user_id,)
        )
        if row is None:
            return
        prefs = json.loads(row["preferences"] or "{}")
        if prefs.get("ha_language") == language:
            return
        prefs["ha_language"] = language
        await self.hm.db.execute(
            "UPDATE people SET preferences = ? WHERE user_id = ?",
            (json.dumps(prefs), self.user_id),
        )
        await self.hm._refresh_people()

    async def on_set_level(self, data: dict[str, Any]) -> None:
        """Your own level; above the highest an administrator allows for residents only for
        an administrator (QA 3, gat 3)."""
        viewer = self.viewer or await self.refresh()
        top = 4 if viewer.is_admin else await views.resident_top_level(self.hm)
        if data.get("level") in (1, 2, 3, 4) and int(data["level"]) <= top:
            await self.hm.db.execute(
                "UPDATE people SET level = ? WHERE user_id = ?", (int(data["level"]), self.user_id)
            )
            await self.hm._refresh_people()

    async def on_pause(self, data: dict[str, Any]) -> None:
        """Pause the house or a part. Longer than an hour only for an administrator; a wall
        tablet never is one (QA 3, gat 3). Who paused goes into the Logboek."""
        minutes = data.get("minutes")
        if not (minutes is None or (isinstance(minutes, int) and -1 <= minutes <= 7 * 24 * 60)):
            return
        viewer = self.viewer or await self.refresh()
        who = None if self.tablet is not None else self.user_id
        held: int | None = minutes
        domain = data.get("domain")
        if domain in DOMAINS:
            for ha_domain in DOMAINS[str(domain)]:
                held = await self.hm.pause_domain(ha_domain, minutes, who, viewer.is_admin)
        elif domain is None:
            held = await self.hm.pause(minutes, who, viewer.is_admin)
        else:
            return
        if held != minutes:
            await self._tell("pause.admin_longer")

    async def on_orb_tap(self, data: dict[str, Any]) -> None:
        """Vesta says in a sentence or two what matters now (E-225), in her own words. Without
        an AI she ends with her choices, as buttons, never an open question (E-349, B-229); a
        question of hers has its buttons here too (B-228, E-464). This is the moment she listens:
        after a tap (E-464)."""
        viewer = self.viewer or await self.refresh()
        choices = not self._ai_on()
        line = await self.hm.vesta_line(
            self.user_id,
            viewer.level,
            viewer.is_admin,
            self.lang,
            Moment.TAP,
            listen=self.mic,
            offer=self.mic,  # she can listen here: first "what can I do for you?"
            choices=choices,
        )
        now = self.hm.clock.now()
        self.talking.end()  # a tap: a new conversation (E-236)
        self.tap_notice = None
        actions = await self.hm.proposal_actions(line.question) if line.question else None
        extra: dict[str, Any] = {}
        notice = await self._notice_said(line.said)
        if line.question is not None and actions:
            asked = Asked(
                "proposal", f"p{line.question}", tuple(actions), line.text, proposal=line.question
            )
            self.talking.ask(asked, now)
            extra["ask"] = self._ask_message(asked)
        elif notice is not None:
            # A notice she mentions is her question, with its own answers (E-381, B-280).
            notice_id, kind, answers = notice
            asked = Asked(kind, f"tap-n{notice_id}", answers, line.text, notification=notice_id)
            self.talking.ask(asked, now)
            self.tap_notice = asked.key
            extra["ask"] = self._ask_message(asked)
        elif line.choices:
            asked = Asked("choices", "choices", self._tap_choices(viewer), line.text)
            self.talking.ask(asked, now)
            extra["choices"] = self._choices(asked)
        else:
            self.talking.said("vesta", line.text, now)
        await self.say(
            line.text,
            voice=self.hm.vesta_name(self.lang),
            # then she listens for a few seconds (E-225), also for her choices (E-349)
            listen=self.mic and (line.listens or bool(extra)),
            **extra,
        )
        self.missed = 0
        await self._voice(line.text)

    async def _ask_how_long(self, notification_id: int) -> None:
        """Later on a notice she mentioned: until when, as on the card (E-372, B-280)."""
        text = self.hm.languages.text(self.lang, "ui.notice.later_title")
        asked = Asked(
            "task", f"tap-n{notification_id}", LATER_ANSWERS, text, notification=notification_id
        )
        self.talking.ask(asked, self.hm.clock.now())
        name = self.hm.vesta_name(self.lang)
        await self.say(text, voice=name, ask=self._ask_message(asked))
        await self._voice(text)

    async def _notice_said(self, said: Any) -> tuple[int, str, tuple[str, ...]] | None:
        """The notice she just mentioned on a tap, with its kind and answers: Ik kijk ernaar,
        Later and Niet meer tonen; an emergency only Ik kijk ernaar (E-381). A question with its
        own answers, such as yes and no, has those buttons (E-464)."""
        for _topic, _variant, key, _asks in said:
            if not str(key).startswith("n:"):
                continue
            notice = int(str(key).split(":")[1])
            row = await self.hm.db.fetchone(
                "SELECT * FROM notifications WHERE id = ? AND resolved_at IS NULL", (notice,)
            )
            if row is None:
                continue
            if row["kind"] == "emergency":
                return notice, "task", ("seen",)
            admin = await self._admin()
            item = views.notice_item(self.hm, self.lang, dict(row), admin, self.user_id)
            if item is not None and item["actions"] in (["snooze", "dismiss"], ["dismiss"]):
                # Niet meer tonen is for the whole house: an administrator's (E-412). A notice
                # for information has no Later, as on its card (QA 3, schermen 27).
                answers: tuple[str, ...] = NOTICE_ANSWERS if admin else ("seen", "snooze")
                if item["actions"] == ["dismiss"]:
                    answers = tuple(a for a in answers if a != "snooze")
                return notice, "task", answers
            viewer = self.viewer or await self.refresh()
            asks = await self.hm.vesta_asks(
                self.user_id, viewer.level, admin, self.tablet is not None, every=True
            )
            for ask in asks:
                if ask["key"] == f"n{notice}":
                    return notice, str(ask["kind"]), tuple(ask["actions"])
        return None

    async def _voice(self, text: str) -> None:
        """Her voice follows her words, on this device only (B-82)."""
        audio = await self.hm.vesta_audio(text, self.lang)
        if audio:
            await self.send({"type": "voice", "audio": audio})

    async def on_listen_start(self, data: dict[str, Any]) -> None:
        self.audio.clear()
        self.listening = self.mic

    async def on_listen_cancel(self, data: dict[str, Any]) -> None:
        """Stop in the Vesta window: what was said is thrown away, nothing is heard."""
        self.listening = False
        self.audio = bytearray()

    async def on_mic(self, data: dict[str, Any]) -> None:
        """The microphone did not work on this screen: Vesta no longer invites here (B-85)."""
        if data.get("ok") is False:
            self.mic = False
        elif data.get("ok") is True:
            self.mic = True  # switched on after her tips: try again

    def on_audio(self, chunk: bytes) -> None:
        """Sound from the microphone while Vesta listens: at most 8 seconds."""
        if self.listening and len(self.audio) + len(chunk) <= MAX_AUDIO:
            self.audio.extend(chunk)

    async def on_listen_end(self, data: dict[str, Any]) -> None:
        """What was said goes to HA's speech recognition, then on as a question (B-83)."""
        if not self.listening:
            return
        self.listening = False
        sound, self.audio = bytes(self.audio), bytearray()  # nothing is kept
        heard = await self.hm.hear(sound, self.lang) if len(sound) >= MIN_AUDIO else None
        step = data.get("intro")
        if step in INTRO_STEPS:
            # An answer said aloud in the introduction (E-253): a level, or yes or no.
            value = self._intro_answer(str(step), heard or "")
            await self.send({"type": "intro_heard", "step": step, "value": value, "heard": heard})
            if value is None:
                text = self.hm.vesta_says(self.lang, "not_heard", self.user_id)
                await self._voice(text)
            return
        if not heard:
            if len(sound) >= MIN_AUDIO:
                self.missed += 1
                text = self.hm.vesta_says(self.lang, "not_heard", self.user_id)
                # She asks once more; after that the conversation ends (E-240).
                await self.say(
                    text, voice=self.hm.vesta_name(self.lang), listen=self.mic and self.missed < 2
                )
                await self._voice(text)
            return
        await self.on_talk({"text": heard, "heard": True})

    async def on_talk(self, data: dict[str, Any]) -> None:
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return
        await self._heard(text, data.get("heard") is True)

    async def _talk(self, text: str, spoken: bool) -> None:
        """A question or command to her, with the conversation for an AI (E-349). ``spoken``:
        asked by voice, so she answers by voice (E-223)."""
        viewer = self.viewer or await self.refresh()
        line = await views.status_line(self.hm, self.lang)
        reply = await self.hm.talk(
            text,
            self.lang,
            viewer.level,
            self.user_id,
            line,
            conversation=self.talking,
            on_partial=self._partial,
        )
        await self._log_talk(text, reply["text"])
        now = self.hm.clock.now()
        asked = self.talking.current(now)
        if asked is not None and reply.get("away"):
            # The AI is not there now: she says so, and keeps to her choices (E-349).
            await self._not_a_choice(asked, text if spoken else None, spoken, str(reply["away"]))
            return
        if asked is not None and reply.get("reply") in asked.actions:
            # With an AI: your own words to her question, understood – the same way as its
            # button, and what happens goes through the safety gate (B-233).
            await self._chosen(
                asked,
                str(reply["reply"]),
                False,
                text if spoken else None,
                spoken,
                ai_text=str(reply["text"]),
            )
            return
        self.talking.said("vesta", reply["text"], now)
        extra: dict[str, Any] = (
            {"voice": self.hm.vesta_name(self.lang), "heard": text} if spoken else {}
        )
        if isinstance(reply.get("more"), dict):
            extra["more"] = reply["more"]  # Lees meer: the part of the manual or Help (E-246)
        if reply.get("via_ai"):
            extra["ai"] = True  # a small "via AI" under her words (E-247)
        offer = reply.get("open")
        self.pending_open = offer if isinstance(offer, dict) else None
        if self.pending_open is not None:
            extra["offer"] = self.pending_open  # the yes and no buttons in her window
            self.talking.ask(Asked("offer", "offer", ("yes", "no"), reply["text"]), now)
        choices = [str(c) for c in reply.get("choices") or []]
        if choices:
            # The AI asks back (B-232): its choices as buttons; a choice goes back to it.
            asked_ai = Asked("ai_choice", "ai", tuple(choices), reply["text"])
            self.talking.ask(asked_ai, now)
            extra["choices"] = self._choices(asked_ai)
            if asked is not None and asked.notification:
                extra.update(self._keep(asked))  # the notice's own answers stay too (E-384)
        elif asked is not None and self.pending_open is None and not reply.get("proposals"):
            extra.update(self._keep(asked))  # her question stays open, with its buttons
        step = reply.get("confirm_step")
        if isinstance(step, dict):
            # Setting a step is for an administrator, on the level where the screen offers it
            # (20.10): a whole part from Betrokken, a room or a skill in a room on Expert.
            if not viewer.is_admin:
                reply = {**reply, "text": self.hm.languages.text(self.lang, "talk.step.admin_only")}
            elif viewer.level < (4 if step["detailed"] else 3):
                reply = {**reply, "text": self.hm.languages.text(self.lang, "talk.step.level")}
            else:
                token = secrets.token_urlsafe(8)
                self.pending_steps = {token: step}
                await self._say_confirm(reply["text"], token, **extra)
                if spoken:
                    await self._voice(reply["text"])
                return
        confirm = reply.get("confirm_rule")
        if confirm is not None and not await self._may_make_rules():
            # A wall tablet does not know who speaks: no house rule there (B-171, E-103).
            reply = {**reply, "text": self.hm.languages.text(self.lang, "talk.rule_where")}
            confirm = None
        if confirm is not None:
            token = secrets.token_urlsafe(8)
            self.pending_rules[token] = confirm
            await self._say_confirm(reply["text"], token, **extra)
        else:
            await self.say(reply["text"], **extra)
        if spoken:
            await self._voice(reply["text"])
        await self._ask_ai_proposal(reply, spoken)

    async def _partial(self, text: str) -> None:
        """An AI's answer as it is written (E-159): her window shows it growing, and her
        answer replaces it."""
        await self.send({"type": "partial", "text": text})

    async def _ask_ai_proposal(self, reply: dict[str, Any], spoken: bool) -> None:
        """Something to do, from the AI (B-233): only a proposal (B-17), which she asks right
        away with its buttons; it happens after your yes, through the safety gate."""
        for proposal_id in reply.get("proposals") or []:
            actions = await self.hm.proposal_actions(int(proposal_id))
            if not actions:
                continue
            await self.hm.talked_about(self.talking, int(proposal_id))  # E-421
            ask = {"kind": "proposal", "proposal": int(proposal_id), "key": f"p{proposal_id}"}
            said = await self.hm.vesta_ask_text(self.user_id, self.lang, ask)
            asked = Asked(
                "proposal", f"p{proposal_id}", tuple(actions), said, proposal=int(proposal_id)
            )
            self.talking.ask(asked, self.hm.clock.now())
            await self.say(
                said,
                voice=self.hm.vesta_name(self.lang),
                ask=self._ask_message(asked),
            )
            await self._voice(said)
            return

    async def _say_confirm(self, text: str, token: str, **extra: Any) -> None:
        """A yes or no question in her window (a house rule, a step): her open question."""
        self.talking.ask(
            Asked("confirm", f"c:{token}", ("yes", "no"), text, token=token), self.hm.clock.now()
        )
        await self.say(text, confirm=token, **extra)

    async def _log_talk(self, question: str, answer: str) -> None:
        await self.hm.db.execute(
            "INSERT INTO conversations(at, user_id, question, answer) VALUES(?,?,?,?)",
            (self.hm.clock.now().isoformat(), self.user_id, question, answer),
        )

    async def _may_make_rules(self, rule: Any = None) -> bool:
        """A house rule – also 'Altijd zo' or 'Nooit zo' as a rule – from level 3 (E-103,
        E-133), and never on a shared wall tablet (QA 2, B-171). One for a whole room, a kind
        of device or the whole house only an administrator makes or removes (QA 3, gat 3)."""
        viewer = self.viewer or await self.refresh()
        if viewer.level < 3 or self.tablet is not None:
            return False
        scope = getattr(rule, "scope_type", "entity")
        return viewer.is_admin or scope == "entity"

    async def on_rule_text(self, data: dict[str, Any]) -> None:
        if not await self._may_make_rules():
            return
        text = data.get("text")
        if isinstance(text, str) and text.strip() and len(text) <= 300:
            result = await self.hm.understand_rule_text(text, self.lang)
            if not result["ok"]:
                await self.say(result["message"])
                return
            await self._confirm_rule(result)

    async def on_rule_build(self, data: dict[str, Any]) -> None:
        """The rule builder (choices instead of text, E-87)."""
        if not await self._may_make_rules():
            return
        rule = build_rule(self.hm, data)
        if rule is None:
            return
        result = {
            "ok": True,
            "rule": rule,
            "read_back": read_back(rule, self.lang, self.hm.languages, self.hm.model),
            "conflicts": [c.id for c in self.hm.rules.conflicts(rule)],
        }
        await self._confirm_rule(result)

    async def _confirm_rule(self, result: dict[str, Any]) -> None:
        if not await self._may_make_rules(result.get("rule")):
            await self._tell("rules.admin_scope")  # a room or the whole house (QA 3, gat 3)
            return
        token = secrets.token_urlsafe(8)
        self.pending_rules[token] = result
        text = self.hm.languages.text(
            self.lang, "talk.rule_confirm", rule=result["read_back"].rstrip(".")
        )
        if result["conflicts"]:
            text = f"{text} {self.hm.languages.text(self.lang, 'talk.rule_conflict')}"
        await self._say_confirm(text, token)

    async def _answer_step(self, payload: dict[str, Any], yes: bool, spoken: bool = False) -> None:
        """Yes or no to setting a step or to the automation that steers it (E-317); her reply,
        and the next question with its own yes and no."""
        if not await self._admin():
            return
        said, follow = await stepwish.answer(self.hm, payload, yes, self.user_id, self.lang)
        extra: dict[str, Any] = {"voice": self.hm.vesta_name(self.lang)} if spoken else {}
        if follow is not None:
            token = secrets.token_urlsafe(8)
            self.pending_steps = {token: follow}
            await self._say_confirm(said, token, **extra)
        else:
            await self.say(said, **extra)
        if spoken:
            await self._voice(said)

    async def on_confirm_rule(self, data: dict[str, Any]) -> None:
        self.talking.answered(f"c:{data.get('token')}")
        step = self.pending_steps.pop(str(data.get("token")), None)
        if step is not None:  # her yes/no buttons also answer a step (E-317)
            await self._answer_step(step, data.get("yes") is True)
            return
        pending = self.pending_rules.pop(str(data.get("token")), None)
        if pending is None:
            return
        if data.get("yes") is True and await self._may_make_rules(pending.get("rule")):
            done = await self.hm.keep_rule(pending, self.user_id)  # a change replaces (E-420)
            await self.say(self.hm.languages.text(self.lang, done))
        elif data.get("yes") is True and await self._may_make_rules():
            await self._tell("rules.admin_scope")  # a room or the whole house: an administrator
        else:
            await self.say(self.hm.languages.text(self.lang, "talk.rule_cancelled"))

    async def on_rule_remove(self, data: dict[str, Any]) -> None:
        rule_id = data.get("id")
        if not isinstance(rule_id, int):
            return
        rule = next((r for r in self.hm.rules.rules if r.id == rule_id), None)
        if rule is not None and await self._may_make_rules(rule):
            await self.hm.rules.remove(self.hm.db, rule_id)

    async def on_answer(self, data: dict[str, Any]) -> None:
        proposal_id, code = data.get("proposal"), data.get("answer")
        if not isinstance(proposal_id, int) or code not in {a.value for a in Answer}:
            return
        await self._answer_proposal(proposal_id, Answer(str(code)), data.get("via") == "vesta")

    async def _answer_proposal(
        self,
        proposal_id: int,
        answer: Answer,
        via_vesta: bool,
        said_no: bool = False,
        key: str | None = None,
    ) -> None:
        """Doe het, Nu niet, Altijd zo, Nooit zo – or a plain "nee" (said_no, 20.9) – from the
        list, from her window, or said to her (E-252, E-349)."""
        if answer is Answer.FROM_NOW and self.tablet is not None:
            answer = Answer.DO  # for good is not for a wall tablet: nobody knows who stands there
        proposal = await self.hm.proposals.get(proposal_id)
        if self.ask and self.ask.get("proposal") == proposal_id:
            key = str(self.ask["key"])
            await self._ask_over()
        mine = self.talking.asked
        if mine is not None and mine.proposal == proposal_id:
            key = key or mine.key
            self.talking.answered()
        result = await self.hm.answer(proposal_id, answer, self.user_id, said_no=said_no)
        if not result["ok"]:
            await self.say(self.hm.languages.text(self.lang, "ui.proposal.already_answered"))
            return
        if result.get("executed"):
            # Done after your yes: the conversation is about it now (E-421).
            await self.hm.talked_about(self.talking, proposal_id, result.get("decision"))
        if key is not None and via_vesta and not result.get("suggest_rule"):
            yes = answer in (Answer.DO, Answer.ALWAYS, Answer.FROM_NOW)
            await self._answered_ask(key, "answer_yes" if yes else "answer_no")
        # 'Altijd zo' / 'Nooit zo' becomes a house rule only from level 3 and not on a wall
        # tablet; below that it was a strong lesson, and that is all (E-133, B-171).
        if result.get("suggest_rule") and proposal is not None and await self._may_make_rules():
            rule = self.hm.rule_from_answer(proposal, answer)
            if rule is not None and await self._may_make_rules(rule):
                skill = self.hm.languages.text(self.lang, f"skills.{rule.skill}")
                area = self.hm.model.areas.get(rule.scope_id)
                text = self.hm.languages.text(
                    self.lang,
                    f"rules.skill_{rule.effect}",
                    skill=skill,
                    area=area.name if area else "",
                )
                await self._confirm_rule(
                    {
                        "ok": True,
                        "rule": rule,
                        "read_back": text,
                        "conflicts": [c.id for c in self.hm.rules.conflicts(rule)],
                    }
                )

    async def on_undo(self, data: dict[str, Any]) -> None:
        if isinstance(data.get("id"), int):
            result = await self.hm.undo(int(data["id"]), self.user_id)
            key = "talk.undone" if result["ok"] else "talk.cannot_undo"
            await self.say(self.hm.languages.text(self.lang, key))

    async def on_why(self, data: dict[str, Any]) -> None:
        if isinstance(data.get("id"), int):
            viewer = self.viewer or await self.refresh()
            lines = await self.hm.why_stored(int(data["id"]), viewer.level, self.lang, plain=True)
            # Vesta explains it in her window (E-252), in plain names (E-248); from level 2
            # with 👍/👎 under it (E-298).
            text = " ".join(lines) if lines else self.hm.why_not("", self.lang, plain=True)
            # Only under something it did itself: a thumb on what it left teaches nothing. Also
            # under an older action the brain no longer has in memory.
            stored = await self.hm.stored_decision(int(data["id"]))
            done = stored is not None and stored.executed
            extra = {"thumbs": int(data["id"])} if lines and done and viewer.level >= 2 else {}
            await self.say(text, window=True, **extra)

    async def on_thumbs(self, data: dict[str, Any]) -> None:
        """👍/👎 on one decision (E-298), from level 2."""
        viewer = self.viewer or await self.refresh()
        good = data.get("good")
        if viewer.level < 2 or not isinstance(good, bool):
            return
        decision = data.get("id")
        done = await self.hm.feedback(
            good, decision if isinstance(decision, int) else None, self.user_id
        )
        if not done["ok"]:
            return  # nothing it did itself: no thanks for a thumb that taught nothing
        if data.get("via") == "vesta":  # in her window: she thanks in her own words
            text = self.hm.vesta_says(self.lang, "answer_thumbs", self.user_id)
            await self.say(text, window=True, voice=self.hm.vesta_name(self.lang))
            await self._voice(text)
        else:
            await self._tell("ui.thumbs.thanks")

    async def on_why_not(self, data: dict[str, Any]) -> None:
        entity_id = data.get("entity")
        if isinstance(entity_id, str) and entity_id in self.hm.model.entities:
            # Shown right under the device that was tapped: a bubble at the bottom of a long
            # list goes unseen.
            text = await self.hm.why_device(entity_id, self.lang)
            why = {"entity": entity_id, "text": text}
            await self.send({"type": "data", "what": "why", "data": why})

    async def on_notification(self, data: dict[str, Any]) -> None:
        assert self.hm.router is not None
        notification_id, action = data.get("id"), data.get("action")
        if not isinstance(notification_id, int):
            return
        row = await self.hm.db.fetchone(
            "SELECT topic, kind, concerns FROM notifications WHERE id = ?", (notification_id,)
        )
        if row is None:
            return
        # Only who it is for may answer, put off or close it (QA 2, L3): a message for the
        # administrators is theirs, one for a person is that person's (20.5).
        concerns = str(row["concerns"] or "home")
        if row["kind"] != "emergency" and (
            (concerns == "admins" and not await self._admin())
            or (concerns.startswith("user:") and concerns[5:] != self.user_id)
        ):
            return
        await self.hm.router.acknowledged(notification_id, self.user_id)
        topic = str(row["topic"])
        if str(action).startswith("later_"):
            data, action = {**data, "for": str(action)[6:]}, "snooze"  # how long, in her window
        told = False  # she says one thing back
        if data.get("via") == "vesta" and self.tap_notice == f"tap-n{notification_id}":
            # Her answer to a notice she mentioned on a tap (E-381): Later first asks how long,
            # as on the card; then she says it in her own words.
            if action == "snooze" and not data.get("for"):
                await self._ask_how_long(notification_id)
                return
            self.tap_notice = None
            told = True
            pending = self.ask if self.ask and self.ask["notification"] == notification_id else None
            if pending is not None:
                await self._ask_over()
            await self._answered_ask(
                f"tap-n{notification_id}", ANSWER_SAYS.get(str(action), "ask_later")
            )
        asked = self.ask if self.ask and self.ask.get("notification") == notification_id else None
        if asked is not None:
            await self._ask_over()
            if data.get("via") == "vesta" and not told:
                await self._answered_ask(
                    str(asked["key"]), ANSWER_SAYS.get(str(action), "ask_later")
                )
        if topic.startswith("automation:") and action in ("yes", "no", "later"):
            if await self._admin():
                await self.hm.answer_automation(
                    topic.split(":", 1)[1], action == "yes", self.user_id, later=action == "later"
                )
            return
        if topic.startswith("ready:") and action in ("yes", "no", "later"):
            # One yes (E-460): whoever sees the question, but not a wall tablet: nobody knows who
            # stands there, and this is for good.
            if self.tablet is None:
                await self.hm.answer_ready(topic, str(action), self.user_id)
            return
        if topic.startswith("house:cooking:") and action in ("yes", "no"):
            # "Zal ik hem uitzetten?" (E-61, B-327): anyone home may answer it.
            done = await self.hm.answer_cooking(topic, action == "yes", self.user_id)
            if action == "yes" and not done["ok"]:
                await self._tell("ui.cooking_not_off")
            return
        if topic.startswith("room_kind:") and action in ("choose", "no"):
            # What kind of room is this? (E-486): Kiezen opens the list, Nu niet closes it.
            if await self._admin():
                if action == "choose":
                    # Wat ik weet in Brein, at this room (E-524).
                    section = f"knowledge:area:{topic.split(':', 1)[1]}:role"
                    await self.send({"type": "open", "sheet": "readiness", "section": section})
                else:
                    await self.hm.answer_room_kind(topic)
            return
        if topic.startswith("tidy:") and action == "view":
            # 'Bekijken' (B-245): Home Assistant netjes, also on Carefree and Informed.
            if await self._admin():
                await self.send({"type": "open", "sheet": "settings", "section": "tidy"})
            return
        if topic.startswith("thumbs:") and action in ("good", "bad"):
            # "Was dit goed?" (E-298): the answer is a thumb on that decision – thanks, or
            # honestly that it can no longer be found (never a plain "Gedaan.").
            done = await self.hm.feedback(
                action == "good", int(topic.split(":", 1)[1]), self.user_id
            )
            await self._tell("ui.thumbs.thanks" if done["ok"] else "ui.thumbs.gone")
            return
        if topic.startswith(("label_lamp:", "label_system:")) and action in ("yes", "no"):
            if await self._admin():
                await self.hm.answer_label(topic, action == "yes")  # E-345
            return
        if topic.startswith("rule_suggest:") and action in ("yes", "no"):
            if await self._admin():  # a house rule is an administrator's (E-417)
                await self.hm.answer_rule_suggestion(topic, action == "yes", self.user_id)
            return
        if topic.startswith("unusual:") and action in ("yes", "no"):
            await self.hm.answer_unusual(topic, action == "yes", self.user_id)  # E-416
            return
        if topic.startswith("knowledge:") and action in ("yes", "no"):
            if await self._admin():  # Klopt or Klopt niet, on Geïnformeerd (B-360)
                await self.hm.answer_knowledge(topic.split(":", 1)[1], action == "yes")
            return
        if topic == "calendar_consent" and action in ("yes", "no"):
            if await self._admin():
                await self.hm.answer_calendar(action == "yes")  # one yes for the house (20.19)
            return
        if topic == "care_consent" and action in ("yes", "no"):
            await self.hm.answer_care(self.user_id, action == "yes")  # their own yes (M-8)
            return
        if topic == "vacation_suggestion" and action in ("yes", "no"):
            await self.hm.answer_vacation(action == "yes", self.user_id)  # E-286
            return
        if topic.startswith("alarm_self_offer:") and action in ("yes", "no"):
            if await self._admin():  # only an administrator gives the standing yes (E-540)
                await answer_alarm(self.hm, topic, str(action), self.user_id)
            return
        if topic.startswith("alarm_arm:") and action in ("yes", "no", "plan_yes", "plan_no"):
            code = "yes" if action in ("yes", "plan_yes") else "no"
            await answer_alarm(self.hm, topic, code, self.user_id)  # E-188
            return
        if topic == "alarm_choice":
            if await self._admin():
                await answer_alarm(self.hm, topic, str(action), self.user_id)  # E-311
            return
        if topic.startswith("restore:") and action in ("yes", "no"):
            # Back on after a part was set back from Autonomous (E-255).
            if await self._admin():
                await self.hm.answer_restore(topic.split(":", 1)[1], action == "yes", self.user_id)
            return
        if action == "snooze" and row["kind"] == "emergency":
            # An emergency stays while it lasts, for everyone (QA 3, gat 4): no screen offers
            # Later for it, and the server does not take it either.
            await self._tell("ui.notice.emergency_stays")
        elif action == "snooze":
            # "Later" (E-372): 1, 4 or 8 hours or tomorrow 08:00 – for this person: this card,
            # and no new message of the same kind until then (not in the app, not on the
            # phone). Only a card for this person alone goes away for the house (QA 3, gat 4).
            now = self.hm.clock.now()
            until = later_until(data.get("for"), now, views._local(self.hm, now))
            await self.hm.router.later(self.user_id, kind_key(topic), until, now)
            if concerns == f"user:{self.user_id}":
                await self.hm.router.snooze(notification_id, until)
            await self.push()
        elif action == "mute" and row["kind"] != "emergency" and await self._admin():
            # "Niet meer tonen" (E-372, E-412): for the whole house, by an administrator, and
            # only what the card itself offers.
            full = await self.hm.db.fetchone(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            )
            item = views.notice_item(
                self.hm, self.lang, dict(full or {}), await self._admin(), self.user_id
            )
            offered = (item or {}).get("mute", [])
            # "Negeer maar" to her names no line: all of what she said – the notice itself, or
            # each camera line in it, never every camera (E-382, B-281).
            key = data.get("key")
            chosen = [c for c in offered if (c["key"] == key if key else c["key"] != ALL_CAMERAS)]
            now = self.hm.clock.now()
            for choice in chosen:
                await self.hm.router.mute(str(choice["key"]), choice["label"], now)
            if chosen:
                if not data.get("via"):
                    await self._tell("ui.notice.muted_done")  # in her window she says it herself
                await self.push()
        elif action == "unmute":
            await self.on_unmute(data)
        elif action in ("dismiss", "seen") and row["kind"] != "emergency":
            # Klaar, or Ik kijk ernaar in her window: closed, and not again while its cause
            # stays (E-383, B-282).
            await self.hm.close_notice(str(row["topic"]))

    async def on_unmute(self, data: dict[str, Any]) -> None:
        """ "Weer tonen" (E-372, E-412): from Logboek or Huisinstellingen, by an
        administrator, for the whole house."""
        if self.hm.router is None or not isinstance(data.get("key"), str):
            return
        if not await self._admin():
            return
        if await self.hm.router.unmute(str(data["key"]), self.hm.clock.now()):
            await self._tell("ui.notice.unmuted_done")
            await self.push()
            await self.send(
                {
                    "type": "data",
                    "what": "muted",
                    "data": views.muted_list(self.hm, self.lang),
                }
            )

    async def on_integration(self, data: dict[str, Any]) -> None:
        """The integration in HA (B-06, B-27, E-167 – E-170): administrators only."""
        if not await self._admin():
            return
        do = data.get("do")
        if do == "place":
            result = await self.hm.place_integration(replace_foreign=data.get("replace") is True)
            key = (
                "integration.placed"
                if result["ok"]
                else "integration.foreign"
                if result.get("reason") == "foreign"
                else "integration.not_available"
            )
            await self.say(self.hm.languages.text(self.lang, key))
        elif do == "link" and data.get("yes") is True:
            # "Koppelen": one yes for placing, a restart of HA if needed, and finishing it in HA
            # (20.23) – nobody has to click in Home Assistant.
            result = await self.hm.link_ha(replace_foreign=data.get("replace") is True)
            key = {
                "done": "integration.linked",
                "restarting": "integration.linking_restart",
                "foreign": "integration.foreign",
                "not_available": "integration.not_available",
                "restart_failed": "integration.restart_failed",
            }.get(str(result["step"]), "integration.link_failed")
            await self.say(self.hm.languages.text(self.lang, key))
        elif do == "restart" and data.get("yes") is True:
            ok = await self.hm.restart_ha()
            await self.say(
                self.hm.languages.text(
                    self.lang, "integration.restarting" if ok else "integration.restart_failed"
                )
            )
        elif do == "forward" and (
            data.get("value") in ("off", "ai")
            or (
                isinstance(data.get("value"), str)
                and str(data["value"]).startswith("agent:")
                and str(data["value"])[6:] in self.hm.forward_agents()  # QA 2, B-169
            )
        ):
            await self.hm.db.set_setting("assist_forward", data["value"])
        elif do == "pipeline":  # Vesta's own Assist pipeline, after a yes (E-280)
            ok = await setting_up.make_pipeline(self.hm, adopt=data.get("adopt") is True)
            await self._tell("ui.setup.pipeline_made" if ok else "ui.setup.pipeline_failed")
        # No separate name for Assist: she has one name, set with Vesta (E-265).
        await self.send(
            {"type": "data", "what": "integration", "data": await self.hm.integration_status()}
        )

    async def on_scene(self, data: dict[str, Any]) -> None:
        """Start a moment in a room (E-106) – a person's request through the gate."""
        area, scene = data.get("area"), data.get("id")
        if isinstance(area, str) and isinstance(scene, str):
            await self.hm.activate_scene(area, scene, self.user_id)

    async def on_scene_save(self, data: dict[str, Any]) -> None:
        viewer = self.viewer or await self.refresh()
        area, name = data.get("area"), data.get("name")
        if viewer.level >= 3 and isinstance(area, str) and isinstance(name, str):
            await self.hm.save_scene(area, name[:40])

    async def on_scene_remove(self, data: dict[str, Any]) -> None:
        viewer = self.viewer or await self.refresh()
        scene = data.get("id")
        if viewer.level >= 3 and isinstance(scene, str):
            await self.hm.remove_scene(scene)

    async def on_day_note(self, data: dict[str, Any]) -> None:
        """Today or tomorrow is a day that differs – or a normal day again (E-89)."""
        viewer = self.viewer or await self.refresh()
        kind, offset = data.get("kind"), data.get("offset", 0)
        if viewer.level < 2 or offset not in (0, 1):
            return
        if kind is not None and kind not in KINDS:
            return
        await self.hm.note_day(int(offset), kind, "said")

    async def on_set_pref(self, data: dict[str, Any]) -> None:
        key, value = data.get("key"), data.get("value")
        viewer = self.viewer or await self.refresh()
        prefs, consents = dict(viewer.preferences), dict(viewer.consents)
        if key in views.PREF_CHOICES and value in views.PREF_CHOICES[str(key)]:
            prefs[str(key)] = value
        elif key == "language" and (value == "auto" or value in self.hm.languages.languages):
            prefs["language"] = value
        elif key == "kinds" and isinstance(value, dict):
            prefs["kinds"] = {
                k: bool(value.get(k, True)) for k in ("important", "info", "learning")
            }
        elif key == "max_per_day" and isinstance(value, int) and 0 <= value <= 50:
            prefs["max_per_day"] = value
        elif (
            key == "questions_per_day"
            and isinstance(value, int)
            and 0 <= value <= 20
            and viewer.level >= 4
        ):
            prefs["questions_per_day"] = value  # the question budget, level 4 (E-294)
        elif key == "onboarded" and isinstance(value, bool):
            prefs["onboarded"] = value
            prefs.setdefault("seen_version", __version__)  # new with this version (E-150)
        elif key == "seen_version" and value == __version__:
            prefs["seen_version"] = value  # what is new: read (E-150)
        elif key == "tour" and isinstance(value, list):
            # The levels this person saw the tour for (E-249), on every device the same.
            prefs["tour"] = sorted({int(v) for v in value if isinstance(v, int) and 1 <= v <= 4})
        elif key == "briefing" and isinstance(value, dict) and viewer.level >= 2:
            prefs["briefing"] = {k: bool(value.get(k, True)) for k in PARTS}
        elif key == "bedtime_check" and isinstance(value, bool) and viewer.level >= 2:
            prefs["bedtime_check"] = value
        elif (
            key == "spoken_per_day"
            and isinstance(value, int)
            and 0 <= value <= 20
            and viewer.level >= 4
        ):
            prefs["spoken_per_day"] = value
        elif (
            key == "report_day" and isinstance(value, int) and 0 <= value <= 6 and viewer.level >= 2
        ):
            prefs["report_day"] = int(value)
        elif key == "emergency_push" and isinstance(value, bool):
            consents["emergency_push"] = value
        elif key == "bundle_at" and _clock_text(value) and viewer.level >= 2:
            prefs["bundle_at"] = value  # when the day's bundle comes (B-30, E-126)
        elif key in ("quiet_from", "quiet_until") and viewer.level >= 4:
            if value == "":
                prefs.pop(str(key), None)  # learned again (E-290)
            elif _clock_text(value):
                prefs[str(key)] = value
            else:
                return
        else:
            return
        await self.hm.db.execute(
            "UPDATE people SET preferences = ?, consents = ? WHERE user_id = ?",
            (json.dumps(prefs), json.dumps(consents), self.user_id),
        )
        await self.hm._refresh_people()
        if key == "onboarded" and value is True and viewer.is_admin:
            await start_over.introduced(self.hm)  # "Nog niet actief" ends here (E-275)

    # -- administrators only (E-82) --------------------------------------------------------

    async def _admin(self) -> bool:
        viewer = self.viewer or await self.refresh()
        return viewer.is_admin

    async def on_house_setting(self, data: dict[str, Any]) -> None:
        if not await self._admin():
            return
        key, value = data.get("key"), data.get("value")
        if key == "max_step" and isinstance(value, dict):
            caps = {}
            for domain, step in value.items():
                for ha_domain in DOMAINS.get(str(domain), ()):
                    if step != "autonomous":
                        caps[ha_domain] = step
            await self.hm.set_house_setting("admin_max_step", caps)
        elif key == "parts" and isinstance(value, dict):
            # Off for a core part only on Expert (E-399); an experimental part from Betrokken.
            viewer = self.viewer or await self.refresh()
            await self.hm.set_parts(value, self.user_id, expert=viewer.level >= 4)
        elif key == "house_language" and isinstance(value, str):
            # The language of the house (E-320): what changed, and her voice if it did.
            before = await self.hm.setting("vesta_voice")
            if not await self.hm.set_house_setting(key, value):
                await self._tell("ui.settings.vesta.house_language_failed")
                return
            t = self.hm.languages.text
            name = t(value, "format.language_name")
            await self._tell("ui.settings.vesta.house_language_done", chosen=name)
            if before and not await self.hm.setting("vesta_voice"):
                voice = self.hm.speech.voice
                said = (voice.voice or voice.engine) if voice is not None else "–"
                await self._tell("ui.settings.vesta.house_language_voice", voice=said)
        elif isinstance(key, str) and key in HOUSE_KEYS:
            if not await self.hm.set_house_setting(key, value):
                await self._tell("ui.not_saved")  # never 'Bewaard.' for what was not kept
        else:
            await self._tell("ui.not_saved")

    async def on_ai_config(self, data: dict[str, Any]) -> None:
        if not await self._admin():
            return
        raw = data.get("config")
        if not isinstance(raw, dict):
            return
        config = AIConfig.from_dict(
            {
                "enabled": bool(raw.get("enabled")),
                "provider": str(raw.get("provider", "anthropic"))[:32],
                "base_url": (str(raw.get("baseUrl") or "")[:200] or None),
                "model_routine": (str(raw.get("modelRoutine") or "")[:80] or None),
                "model_strong": (str(raw.get("modelStrong") or "")[:80] or None),
                "monthly_budget": max(0.0, min(1000.0, float(raw.get("monthlyBudget") or 0))),
                # Expert: a model per task (E-49); only the known tasks, short names.
                "task_models": {
                    str(k): str(v)[:80]
                    for k, v in (raw.get("taskModels") or {}).items()
                    if str(k) in AI_TASKS and isinstance(v, str) and v.strip()
                }
                if isinstance(raw.get("taskModels"), dict)
                else self.hm.ai.config.task_models,
                "consent_at": self.hm.ai.config.consent_at,
                "consent_by": self.hm.ai.config.consent_by,
                "consent_for": self.hm.ai.config.consent_for,
                "images": self.hm.ai.config.images,
                "images_by": self.hm.ai.config.images_by,
            }
        )
        # Camera images only for an AI task of Home Assistant in the house, and only with this
        # administrator's own yes; anything else turns it off (E-424).
        wants = bool(raw.get("images"))
        local = config.provider == "home_assistant" and (
            self.hm.ai_task_place(config.base_url or "") == "local"
        )
        if not (wants and local):
            config.images, config.images_by = False, None
        elif not config.images:
            config.images, config.images_by = True, self.user_id
        if config.provider not in AI_PROVIDERS:
            return
        if config.provider == "home_assistant" and config.base_url not in {
            t["id"] for t in self.hm.ai_tasks()
        }:
            return  # only an AI task that is there
        key = data.get("key")
        await self.hm.ai.configure(
            config, api_key=str(key)[:300] if isinstance(key, str) and key else None
        )

    async def on_energy_setting(self, data: dict[str, Any]) -> None:
        """The energy goal, how prices are read and the peak limit (E-66, E-71): administrators
        only."""
        energy = self.hm.energy
        if energy is None or not await self._admin():
            return
        s = energy.settings
        goal = data.get("goal", s.goal)
        price_mode = data.get("priceMode", s.price_mode)
        if goal not in ("cost", "own_power", "co2", "mix") or price_mode not in (
            "auto",
            "market",
            "all_in",
        ):
            return
        peak = data.get("peakLimitKw", s.peak_limit_kw)
        settings = replace(
            s,
            goal=str(goal),
            price_mode=str(price_mode),
            peak_limit_kw=None if peak in (None, "", 0) else _bounded(peak, 1, 50, 10.0),
        )
        await energy.save_settings(settings)
        energy.replan(self.hm.world.states if self.hm.world else {}, self.hm.clock.now(), True)

    async def on_give_back(self, data: dict[str, Any]) -> None:
        if await self._admin() and isinstance(data.get("yes"), bool):
            await self.hm.give_back(bool(data["yes"]), self.user_id)

    # -- setting up (E-277 – E-282) ---------------------------------------------------------

    async def on_setup_start(self, data: dict[str, Any]) -> None:
        """ "Inrichten": in the language HA shows this administrator (E-281)."""
        if await self._admin():
            shown = self.ha_language or (self.hm.config.language if self.hm.config else None)
            await setting_up.begin(self.hm, self.user_id, shown)
            await self.push()

    async def on_setup_step(self, data: dict[str, Any]) -> None:
        step = data.get("step")
        if await self._admin() and isinstance(step, str):
            await setting_up.goto(self.hm, step)

    async def on_setup_done(self, data: dict[str, Any]) -> None:
        """Done – not while the history is still read (E-282)."""
        if await self._admin() and await setting_up.finish(self.hm) == "reading":
            await self._tell("ui.setup.wait")

    # -- starting again (E-271 – E-275) ------------------------------------------------------

    async def _tell(self, key: str, **params: Any) -> None:
        self._answered = True
        text = self.hm.languages.text(self.lang, key, **params)
        await self.send({"type": "saved", "text": text, "brief": key in BRIEF})

    def _names(self, entity_ids: list[str]) -> str:
        model = self.hm.model.entities
        return ", ".join(model[e].name if e in model else e for e in entity_ids)

    async def on_intro_again(self, data: dict[str, Any]) -> None:
        """The introduction once more, with the answers of now (E-272)."""
        if await self._admin():
            await start_over.intro_again(self.hm, self.user_id)
            await self.push()

    async def on_relearn(self, data: dict[str, Any]) -> None:
        """Forget what was learned, after a yes (E-273)."""
        if not await self._admin() or data.get("yes") is not True:
            return
        result = await start_over.relearn(self.hm, self.user_id)
        if result["ok"]:
            await self._tell("ui.restart.learn.done")
        else:
            await self._tell("ui.restart.failed", names=self._names(result["failed"]))

    async def on_factory_reset(self, data: dict[str, Any]) -> None:
        """Back to the factory settings, after the typed word (E-274)."""
        if not await self._admin():
            return
        word = data.get("word")
        result = await start_over.factory_reset(
            self.hm, word if isinstance(word, str) else "", self.lang, self.user_id
        )
        if result["ok"]:
            await self._tell("ui.restart.factory.busy")
        elif result["reason"] == "word":
            word = start_over.the_word(self.hm, self.lang)
            await self._tell("ui.restart.factory.wrong", word=word)
        else:
            await self._tell("ui.restart.failed", names=self._names(result["failed"]))

    async def on_forget_me(self, data: dict[str, Any]) -> None:
        """'Vergeet mij', after a yes (E-300): only one's own data, never someone else's."""
        if data.get("yes") is not True:
            return
        if await self.hm.forget_me(self.user_id):
            await self._tell("ui.my_data.forgotten")
            self.viewer = None  # next time: new, with the short introduction
            await self.push()

    async def on_house_mode(self, data: dict[str, Any]) -> None:
        """Vacation, guests or home again, with one tap in Vesta's window (E-286)."""
        mode = data.get("mode")
        if mode in ("home", "vacation", "guests") and await self.hm.set_house_mode(str(mode)):
            lang = self.hm.languages.resolve(self.lang)
            await self.say(self.hm.languages.text(lang, f"talk.mode_{mode}"))

    async def on_admin_step(self, data: dict[str, Any]) -> None:
        """An administrator sets a step themselves, after a yes (20.10): a part from level 3;
        a skill in a room, a room or a device only on Expert."""
        viewer = self.viewer or await self.refresh()
        key, step = data.get("key"), data.get("step")
        if not viewer.is_admin or viewer.level < 3 or not isinstance(key, str):
            return
        detailed = "@" in key or key.startswith("area:") or key in self.hm.model.entities
        if detailed and viewer.level < 4:
            return
        if step is not None and not isinstance(step, str):
            return
        ok = await self.hm.set_admin_step(key, step, self.user_id)
        await self._tell("ui.ready.own.done" if ok else "ui.ready.own.not_done")

    async def on_care_withdraw(self, data: dict[str, Any]) -> None:
        """The person care is about takes back their yes, in Mijn gegevens (QA 2, M-8)."""
        if await self.hm.answer_care(self.user_id, False):
            await self._tell("ui.my_data.care_withdrawn")

    async def on_forget_person(self, data: dict[str, Any]) -> None:
        """An administrator lets HouseMind forget someone who left Home Assistant (QA 2, M-9),
        after a yes – only someone who is no longer a user there."""
        user_id = data.get("id")
        if not await self._admin() or not isinstance(user_id, str):
            return
        if user_id not in {p["id"] for p in await views.departed(self.hm)}:
            return
        if await self.hm.forget_me(user_id):
            await self._tell("ui.settings.departed_forgotten")

    async def on_forget_pair(self, data: dict[str, Any]) -> None:
        """'Vergeet dit' for one skill in one room, after a yes (E-94, 20.13)."""
        viewer = self.viewer or await self.refresh()
        skill, area = data.get("skill"), data.get("area")
        if not viewer.is_admin or viewer.level < 3:
            return
        if not isinstance(skill, str) or not isinstance(area, str):
            return
        if await self.hm.forget_pair(skill, area):
            await self._tell("ui.ready.forget.done")

    async def on_thresholds(self, data: dict[str, Any]) -> None:
        """The thresholds of one part, on Expert (E-297); no values is "Standaard"."""
        viewer = self.viewer or await self.refresh()
        part, values = data.get("part"), data.get("values")
        if not viewer.is_admin or viewer.level < 4 or not isinstance(part, str):
            return
        if values is not None and not isinstance(values, dict):
            return
        ok = await self.hm.set_thresholds(part, values)
        await self._tell("ui.settings.thresholds.saved" if ok else "ui.settings.thresholds.wrong")

    async def on_snapshot_restore(self, data: dict[str, Any]) -> None:
        """Put back what was learned on an earlier day, on Expert, after a yes (20.13)."""
        viewer = self.viewer or await self.refresh()
        day = data.get("day")
        if not viewer.is_admin or viewer.level < 4 or data.get("yes") is not True:
            return
        if not isinstance(day, str):
            return
        ok = await self.hm.restore_snapshot(day)
        await self._tell("ui.settings.snapshots.done" if ok else "ui.settings.snapshots.failed")

    async def on_tidy(self, data: dict[str, Any]) -> None:
        """Home Assistant netjes (E-351 – E-360, B-243 – B-245): 'Nu nakijken', switching off
        after a yes and back on, 'Bewust uit', and the choices – also 'Laat aan', 'Laat staan'
        and 'Ik maak back-ups buiten Home Assistant' (B-240, B-241). Only administrators; every
        switch goes through the safety gate."""
        if not await self._admin():
            return
        do = data.get("do")
        key, target = str(data.get("key") or ""), str(data.get("target") or "")
        uid = self.user_id
        if do == "check":
            if not await self.hm.tidy_check():
                await self._tell("ui.tidy.wait")
        elif do in ("off", "off_all"):
            result = await self.hm.tidy_off(key, uid)
            await self._tell("ui.tidy.done.off" if result["ok"] else "ui.tidy.done.not")
        elif do == "on":
            result = await self.hm.tidy_on(target, uid)
            await self._tell("ui.tidy.done.on" if result["ok"] else "ui.tidy.done.not")
        elif do == "all_on":
            failed = await self.hm.tidy_all_on(uid)
            await self._tell("ui.tidy.done.on" if not failed else "ui.tidy.done.not")
        elif do in ("leave", "not_duplicate", "keep_on", "keep", "own_backups", "keep_backups"):
            ok = await self.hm.tidy_choose(key, str(do), uid)
            await self._tell("ui.tidy.done.saved" if ok else "ui.tidy.done.not")
        elif do == "undo_choice":
            await self.hm.tidy_undo_choice(key)
            await self._tell("ui.tidy.done.undone")
        elif do == "mark":
            ok = await self.hm.tidy_mark(
                target,
                str(data.get("how") or ""),
                uid,
                data.get("start"),
                data.get("end"),
                data.get("until"),
            )
            await self._tell("ui.tidy.done.saved" if ok else "ui.tidy.done.not")
        elif do == "unmark":
            await self.hm.tidy_unmark(target)
            await self._tell("ui.tidy.done.undone")
        elif do == "review_ok":
            await self.hm.tidy_review_ok(target)
            await self._tell("ui.tidy.done.saved")
        else:
            return
        await self.send({"type": "data", "what": "tidy", "data": tidy_view(self.hm)})

    async def on_automation(self, data: dict[str, Any]) -> None:
        """Switch an existing automation off (after a yes) or back on (E-42). Never delete.
        From the row in Klaar voor autonomie? (E-317, ``row``): off with its other rows along,
        on after a step back, or the no to either ("leave" it on, "keep" it off)."""
        entity_id, turn = data.get("entity"), data.get("turn")
        if not await self._admin() or not isinstance(entity_id, str):
            return
        if data.get("row") is True and turn in ("off", "on", "leave", "keep"):
            await self._automation_row(entity_id, str(turn))
            return
        if turn not in ("off", "on"):
            return
        if turn == "off":
            result = await self.hm.turn_off_automation(entity_id, self.user_id)
        else:
            result = await self.hm.restore_automation(entity_id, self.user_id)
        key = "ui.automations.done_off" if turn == "off" else "ui.automations.done_on"
        if not result["ok"]:
            key = "ui.automations.not_done"
        await self.say(self.hm.languages.text(self.lang, key))

    async def _automation_row(self, entity_id: str, turn: str) -> None:
        """The answer in the row (E-317); what happened, in her words."""
        reading = self.hm.automations.readings.get(entity_id)
        name = reading.automation.name if reading is not None else entity_id
        if turn == "off":
            result = await self.hm.turn_off_automation(entity_id, self.user_id, along=True)
            key = "done_off"
        elif turn == "on":
            result = await self.hm.restore_automation(entity_id, self.user_id, reason="step_down")
            key = "done_on"
        elif turn == "leave":
            result = await self.hm.leave_automation(entity_id, self.user_id)
            key = "left_on"
        else:
            result = await self.hm.keep_automation_off(entity_id, self.user_id)
            key = "kept_off"
        await self._tell(f"ui.ready.steer.{key if result['ok'] else 'not_done'}", name=name)

    async def on_knowledge(self, data: dict[str, Any]) -> None:
        """Wat ik weet (E-207, E-524): Klopt, Klopt niet or another role for a room, Terug naar
        te bevestigen (E-525), and "Nu bekijken" – administrators on Betrokken and Expert."""
        viewer = self.viewer or await self.refresh()
        if not viewer.is_admin or viewer.level < 3:
            return
        do, key, role = data.get("do"), data.get("id"), data.get("role")
        if do == "look" and self.hm.ai.status() != "on":
            await self._tell("ui.knowledge.ai_off")  # AI is turned on at its own place only
        elif do == "look":
            looks = self.hm.read_house_soon(now=True)  # queued; the house goes on (E-414)
            await self._tell("ui.knowledge.looking" if looks else "ui.knowledge.not_now")
        elif do == "device_yes" and isinstance(data.get("ids"), list):
            # Klopt for everything of one device in one go (E-432, design 51.7).
            keys = [k for k in data["ids"] if isinstance(k, str)][:200]
            done = [await self.hm.answer_knowledge(k, True) for k in keys]
            if not all(done):
                await self._tell("ui.knowledge.not_done")
        elif do in ("yes", "no", "role") and isinstance(key, str):
            if do == "role" and not isinstance(role, str):
                return
            chosen = str(role) if do == "role" else None
            if not await self.hm.answer_knowledge(key, do == "yes", chosen):
                await self._tell("ui.knowledge.not_done")
        elif do == "back" and isinstance(key, str):
            # Terug naar te bevestigen (E-525): an answer taken back.
            if not await self.hm.reopen_knowledge(key):
                await self._tell("ui.knowledge.not_done")
        else:
            return
        await self.send(
            {
                "type": "data",
                "what": "house_knowledge",
                "data": views.house_knowledge(self.hm, self.lang),
            }
        )

    async def on_history_again(self, data: dict[str, Any]) -> None:
        """Read the history again (E-257): administrators; what it taught before is replaced."""
        if await self._admin() and self.hm.learning_allowed:
            self.hm.start_history()
            await self.push()

    async def on_ai_consent(self, data: dict[str, Any]) -> None:
        if await self._admin():
            await self.hm.ai.give_consent(self.user_id, yes=data.get("yes", True) is not False)

    async def on_play(self, data: dict[str, Any]) -> None:
        """'Speel het huis' (E-321): only in a demo house, never on a wall tablet. In the real
        house there is no Play, so nothing here can touch it (B-187)."""
        if self.play is None or self.tablet is not None:
            return
        try:
            await self.play.control(data)
        except Busy:
            await self._tell("play.busy")
            return
        except Locked:
            await self._tell("play.locked")  # the house plays: Stop first (B-188)
            return
        viewer = self.viewer or await self.refresh()
        view = await self.play.view(viewer, self.user_id, self.lang)
        await self.send({"type": "data", "what": "play", "data": view})
        await self._device_changes()  # also a measurement, which waits for the next round

    async def _tester(self) -> bool:
        """Test het huis is for an administrator on Expert, never on a wall tablet (B-388)."""
        viewer = self.viewer or await self.refresh()
        return viewer.is_admin and viewer.level >= 4 and self.tablet is None

    async def on_test(self, data: dict[str, Any]) -> None:
        """A tap in Test het huis (E-474, E-475, E-518): another room, a situation of Speel het
        na, Doe het echt, or a test kept, put back or stopped."""
        if not await self._tester():
            return
        do = data.get("do")
        area = data.get("area")
        if do == "area" and isinstance(area, str) and area in self.hm.model.areas:
            self.test_area, self.test_chosen = area, []
        elif do == "pretend" and self.test_area is not None and isinstance(data.get("key"), str):
            self.test_chosen = self.hm.test_choose(
                self.test_area, self.test_chosen, str(data["key"]), data.get("on") is True
            )
        elif do == "clear":
            self.test_chosen = []
        elif do == "play" and self.test_area is not None:
            # Doe het echt (E-518): all of it for real, back after a minute.
            said = await self.hm.test_play(
                self.lang, self.test_area, self.test_chosen, self.user_id
            )
            if said is not None:
                self._answered = True
                await self.send({"type": "saved", "text": said, "brief": False})
        elif do in ("keep", "back") and isinstance(data.get("id"), int):
            if do == "keep":
                await self.hm.test_play_keep(int(data["id"]))  # Laat zo
            else:
                await self.hm.test_play_back(int(data["id"]))  # Zet terug
        elif do == "quarter" and self.test_area is not None and isinstance(data.get("part"), str):
            minutes = data.get("minutes")
            await self.hm.test_quarter(  # a testkwartier (E-477)
                self.test_area,
                str(data["part"]),
                minutes if isinstance(minutes, int) else 15,
                self.user_id,
            )
        elif do == "stop" and isinstance(data.get("id"), int):
            await self.hm.test_quarter_end(int(data["id"]))
        elif do == "stop_all":
            await self.hm.test_stop_all()  # Stop test en zet alles terug (E-478)
        await self._send_test()

    async def on_levels(self, data: dict[str, Any]) -> None:
        """Standen voor lampen (E-490 – E-493, B-413): an administrator on Expert saves, puts
        back or tries the standen of a lamp, or tries one stand in a whole room. While the
        window of a lamp is open, HouseMind keeps off it (E-516). Never on a wall tablet, as
        Test het huis."""
        if not await self._tester():
            return
        do = data.get("do")
        lamp = data.get("id")
        stand = data.get("stand")
        hm = self.hm
        if do == "hold" and isinstance(lamp, str) and lamp in hm.model.entities:
            hm.levels_hold(lamp)
            self.holding.add(lamp)
            return
        if do == "release" and isinstance(lamp, str):
            self.holding.discard(lamp)
            await hm.levels_release(lamp)
            return
        if do == "save" and isinstance(lamp, str):
            # One Bewaren for the whole window (E-582): the standen – after Terug naar
            # startwaarden back to where they come from – and the wait before off, if it
            # changed. What could not be kept is said, never 'Bewaard.' for it.
            if data.get("reset") is True and lamp in hm.model.entities:
                await hm.levels_reset(lamp)
                stands = True
            else:
                stands = await hm.levels_save(lamp, data.get("levels"))
            wait = await hm.off_wait_save(lamp, data.get("wait")) if "wait" in data else None
            if stands:
                key = "ui.stands.saved_not_wait" if wait is False else "ui.stands.saved"
            else:
                key = "ui.stands.not_saved_wait_kept" if wait else "ui.stands.not_saved"
            self._answered = True
            await self.send({"type": "saved", "text": hm.languages.text(self.lang, key)})
        elif do == "reset" and isinstance(lamp, str):
            await hm.levels_reset(lamp)
        elif do == "wait" and isinstance(lamp, str):
            # Uit na … zonder beweging (E-575): minutes 0 – 30, or None to learn it again.
            ok = await hm.off_wait_save(lamp, data.get("minutes"))
            key = "ui.stands.wait.saved" if ok else "ui.stands.wait.not_saved"
            self._answered = True
            await self.send({"type": "saved", "text": hm.languages.text(self.lang, key)})
        elif do in ("try", "try_room") and isinstance(stand, int) and not isinstance(stand, bool):
            area = data.get("area")
            if do == "try_room" and isinstance(area, str) and area in hm.model.areas:
                lamps = hm.stand_lamps(area)
            elif do == "try" and isinstance(lamp, str) and lamp in hm.model.entities:
                lamps = [lamp]
            else:
                return
            said = await hm.levels_try(self.lang, lamps, stand, self.user_id, data.get("levels"))
            if said is not None:
                self._answered = True
                await self.send({"type": "saved", "text": said, "brief": False})
            return  # the lamp itself shows it; the list follows by itself (E-472)
        elif do == "back":
            ids, area = data.get("ids"), data.get("area")
            if isinstance(area, str) and area in hm.model.areas:
                await hm.levels_back(hm.stand_lamps(area))
            elif isinstance(ids, list):
                await hm.levels_back([str(x) for x in ids])
            return
        else:
            return
        await self.on_get({"what": "devices"})

    async def _send_test(self) -> None:
        view = self.hm.test_view(self.lang, self.test_area, self.test_chosen)
        self.test_area = view["area"]
        await self.send({"type": "data", "what": "test", "data": view})

    async def on_get(self, data: dict[str, Any]) -> None:
        what = data.get("what")
        viewer = self.viewer or await self.refresh()
        result: Any = None
        if what == "devices" and viewer.level >= 3:
            result = views.devices(self.hm, self.lang)
            self._device_states = {row["id"]: row["state"] for row in result}  # E-472
        elif what == "settings" and viewer.is_admin:
            result = await views.house_settings(self.hm, self.lang)
        elif what == "energy_settings" and viewer.is_admin:
            result = views.energy_settings(self.hm)
        elif what == "integration" and viewer.is_admin:
            result = await self.hm.integration_status()
        elif what == "tidy" and viewer.is_admin:
            result = tidy_view(self.hm)  # Home Assistant netjes (E-351, B-245)
        elif what == "ai_preview" and viewer.is_admin:
            result = await views.ai_preview(self.hm, self.lang, self.user_id)
        elif what == "muted" and viewer.is_admin:
            # What the house switched off with "Niet meer tonen" (E-412), in Huisinstellingen.
            result = views.muted_list(self.hm, self.lang)
        elif what == "house_knowledge" and viewer.is_admin and viewer.level >= 3:
            # Wat HouseMind van je huis weet (E-207), in Huisinstellingen.
            result = views.house_knowledge(self.hm, self.lang)
        elif what == "test" and await self._tester():
            await self._send_test()  # Test het huis (E-474)
            return
        elif what == "later" and viewer.level >= 3:
            result = later(self.hm, self.lang)  # Straks, in Brein › Ritme
        elif what == "my_data":
            result = await self.hm.my_data(self.user_id, self.lang)  # only one's own (E-300)
        elif what == "play" and self.play is not None and self.tablet is None:
            result = await self.play.view(viewer, self.user_id, self.lang)  # demo only (E-321)
        elif what == "readiness":
            # "Klaar voor autonomie?" as a sheet, also on levels without Brein (E-256).
            result = await readiness(self.hm, self.lang, viewer.level, viewer.is_admin)
        elif what == "logbook" and viewer.level >= 2:
            # One timeline (E-263): done, notifications, conversations, briefing, report.
            result = await logbook(
                self.hm, viewer, self.user_id, self.lang, data.get("period"), data.get("day")
            )  # the period chosen in Logboek (E-323)
        if result is not None:
            await self.send({"type": "data", "what": what, "data": result})


# What Huisinstellingen may change through house_setting, besides the keys above that need more
# (E-82: administrators only). A key the screen sends that is not here was lost on the way, while
# the screen said 'Bewaard.' – Beveiliging and leren uit de geschiedenis were (version 0.3.23).
# What a screen may change in Huisinstellingen: from the one table (code QA 3, punt 1).
HOUSE_KEYS = house_settings.SCREEN_KEYS

OWN_CHANGES = frozenset(
    {
        "energy_setting",
        "hello",
        "set_level",
        "set_pref",
        "notification",
        "confirm_rule",
        "rule_remove",
        "ai_config",
        "ai_consent",
    }
)
INTRO_STEPS = ("level", "emergency", "learning", "pets")  # spoken answers in the introduction
LEVELS = ((1, "carefree"), (2, "informed"), (3, "involved"), (4, "expert"))
ASK_FOLLOW = timedelta(minutes=2)  # a question this soon after an answer: "Ik heb nog een vraag"
ASK_QUIET = timedelta(minutes=15)  # her window closed on a question: no next one for a while
# A notice she mentions on a tap (E-381): its answers, how long Later is, and what she says then.
NOTICE_ANSWERS = ("seen", "snooze", "mute")
LATER_ANSWERS = ("later_1h", "later_4h", "later_8h", "later_morning")
# What she says back to an answer in her window; Ik kijk ernaar, Niet meer tonen (E-403).
ANSWER_SAYS = {
    "yes": "answer_yes",
    "no": "answer_no",
    "dismiss": "ask_done",
    "seen": "ask_seen",
    "mute": "ask_muted",
    "good": "answer_thumbs",
    "bad": "answer_thumbs",
}

# Short confirmations: plain text in the header, gone after 2 seconds (E-174). Everything else
# told – an error, an explanation – stays a notice with Oké.
BRIEF = frozenset(
    {
        "ui.ready.own.done",
        "ui.ready.forget.done",
        "ui.settings.thresholds.saved",
        "ui.settings.snapshots.done",
        "ui.thumbs.thanks",
        "ui.setup.pipeline_made",
    }
)

# Messages that save a setting, and ones that do something: both get a short confirmation.
SAVES = frozenset(
    {
        "set_level",
        "set_pref",
        "house_setting",
        "ai_config",
        "ai_consent",
        "energy_setting",
        "integration",
        "scene_save",
        "knowledge",
    }
)
DONE = frozenset(
    {
        "pause",
        "scene",
        "scene_remove",
        "rule_remove",
        "day_note",
        "give_back",
        "automation",
        "notification",
        "answer",
    }
)
AI_PROVIDERS = (
    "anthropic",
    "gemini",
    "openai",
    "openrouter",
    "ollama",
    "openai_compatible",
    "home_assistant",  # the AI task already set up in Home Assistant (E-422)
)


def _clock_text(value: Any) -> bool:
    """A time of day as "HH:MM"."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hours, minutes = value[:2], value[3:]
    return hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60


def _bounded(value: Any, low: float, high: float, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool) and low <= value <= high:
        return float(value)
    return default


def build_rule(hm: HouseMind, data: dict[str, Any]) -> HouseRule | None:
    scope_type = data.get("scopeType")
    scope_id = str(data.get("scope") or "*")
    category = data.get("category")
    if scope_type == "area" and scope_id not in hm.model.areas:
        return None
    if scope_type == "entity" and scope_id not in hm.model.entities:
        return None
    if scope_type not in ("area", "entity", "house"):
        return None
    if category == "heat":
        value = data.get("value")
        if not isinstance(value, int | float) or not 5 <= value <= 35:
            return None
        return HouseRule(scope_type, scope_id, "limit", "heat", float(value))
    if category not in ("on", "off", "open", "close"):
        return None

    def parse(value: Any) -> time | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None

    return HouseRule(
        scope_type,
        scope_id,
        "never",
        category,
        None,
        parse(data.get("timeFrom")),
        parse(data.get("timeTo")),
    )


async def websocket(request: web.Request) -> web.WebSocketResponse:
    if len(request.app[SESSIONS_KEY]) >= MAX_SESSIONS:
        raise web.HTTPServiceUnavailable(text="too many screens")  # QA 3, gat 10
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
    await ws.prepare(request)
    session = Session(request, ws)
    request.app[SESSIONS_KEY].add(session)
    window, count = 0.0, 0
    try:
        async for msg in ws:
            if msg.type is WSMsgType.BINARY:
                session.on_audio(msg.data)  # the microphone while Vesta listens
                continue
            if msg.type is not WSMsgType.TEXT:
                continue
            # At most so many messages a second from one screen (QA 3, gat 10): a person taps
            # a few; more is a script, and the rest of that second is left unread.
            now = asyncio.get_running_loop().time()
            if now - window >= 1:
                window, count = now, 0
            count += 1
            if count > MAX_PER_SECOND:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                try:
                    await session.handle(data)
                except Exception:
                    _LOGGER.exception("Message %s failed", data.get("type"))
    finally:
        request.app[SESSIONS_KEY].discard(session)
        if session._pushing is not None:
            session._pushing.cancel()
        for lamp in list(session.holding):  # a screen that went away lets go (E-516)
            try:
                await session.hm.levels_release(lamp)
            except Exception:
                _LOGGER.exception("Letting go of %s failed", lamp)
    return ws


async def manual_page(request: web.Request) -> web.Response:
    """The formatted manual as its own page, for the iframe in Help (E-324, B-192):
    ?thema=licht|donker follows the app's theme, ?tekst=groot its large text."""
    hm = request.app[APP_KEY]
    lang = request.match_info["lang"]
    source = hm.library.manuals.get(lang)
    if source is None:
        raise web.HTTPNotFound()
    theme = {"licht": "light", "light": "light", "donker": "dark", "dark": "dark"}.get(
        request.query.get("thema", "").lower()
    )
    document = manual.page(
        source,
        hm.vesta_name(lang),
        theme,
        request.query.get("tekst") == "groot",
        request.app[FONTS_KEY],
        hm.languages.text(lang, "ui.manual.title"),
    )
    etag = f'"{hashlib.sha256(document.encode()).hexdigest()[:20]}"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers=headers)
    headers["Content-Security-Policy"] = manual.csp(source, CSP)
    response = web.Response(text=document, content_type="text/html", headers=headers)
    response.enable_compression()  # about 460 kB, 90 kB on the way
    return response


async def health(request: web.Request) -> web.Response:
    """For Home Assistant's watchdog (M-34): a round of thinking that stopped is no health, so
    Home Assistant starts the App again."""
    hm = request.app[APP_KEY]
    last = hm.status.last_tick
    if last is not None and hm.clock.now() - last > HEALTH_STALE:
        return web.json_response({"status": "stuck", "orb": hm.status.orb}, status=503)
    return web.json_response({"status": "ok", "orb": hm.status.orb})


async def diagnostics_package(request: web.Request) -> web.Response:
    """The diagnostics package (E-98) – for administrators, as a file to share."""
    hm = request.app[APP_KEY]
    user_id, name = user_of(request)
    trusted = not request.app[INGRESS_ONLY_KEY] or request.app[DEMO_KEY]
    viewer = await views.load_viewer(hm, user_id, name, trusted)
    if not viewer.is_admin:
        raise web.HTTPForbidden(text="administrators only")
    return web.json_response(
        await hm.diagnostics(),
        headers={"Content-Disposition": 'attachment; filename="housemind-diagnostics.json"'},
        dumps=lambda data: json.dumps(data, indent=2, ensure_ascii=False),
    )


async def my_data_file(request: web.Request) -> web.Response:
    """'Bewaar als bestand' (E-300): only the data of who asks, as a readable file."""
    hm = request.app[APP_KEY]
    user_id, _ = user_of(request)
    data = await hm.export_me(user_id)
    day = hm.clock.now().date().isoformat()
    return web.json_response(
        data,
        headers={"Content-Disposition": f'attachment; filename="housemind-my-data-{day}.json"'},
        dumps=lambda value: json.dumps(value, indent=2, ensure_ascii=False),
    )


async def export_copy(request: web.Request) -> web.Response:
    """The settings file (E-296, E-274): for administrators, readable, no keys or secrets –
    also 'Eerst een kopie bewaren' before the factory settings."""
    hm = request.app[APP_KEY]
    user_id, name = user_of(request)
    trusted = not request.app[INGRESS_ONLY_KEY] or request.app[DEMO_KEY]
    viewer = await views.load_viewer(hm, user_id, name, trusted)
    if not viewer.is_admin:
        raise web.HTTPForbidden(text="administrators only")
    return web.json_response(
        await settings_file.make(hm),
        headers={"Content-Disposition": f'attachment; filename="{settings_file.name(hm)}"'},
        dumps=lambda data: json.dumps(data, indent=2, ensure_ascii=False),
    )


async def read_settings(request: web.Request) -> web.Response:
    """Reading a settings file back in (E-296): administrators on Expert. First what will
    change; only with ``apply`` true (after the yes in the app) is anything replaced."""
    hm = request.app[APP_KEY]
    user_id, name = user_of(request)
    trusted = not request.app[INGRESS_ONLY_KEY] or request.app[DEMO_KEY]
    viewer = await views.load_viewer(hm, user_id, name, trusted)
    if not viewer.is_admin or viewer.level < 4:
        raise web.HTTPForbidden(text="administrators on Expert only")
    try:
        body = await request.json()
    except ValueError as err:
        raise web.HTTPBadRequest(text="not JSON") from err
    if not isinstance(body, dict):
        raise web.HTTPBadRequest()
    data = body.get("file")
    if body.get("apply") is True:
        return web.json_response(await settings_file.apply(hm, data))
    return web.json_response(await settings_file.preview(hm, data))


def create_app(
    hm: HouseMind,
    frontend: Path,
    ingress_only: bool,
    demo: bool = False,
    play: Play | None = None,
) -> web.Application:
    app = web.Application(middlewares=[security])
    app[APP_KEY] = hm
    app[INGRESS_ONLY_KEY] = ingress_only
    app[DEMO_KEY] = demo
    app[SESSIONS_KEY] = set()
    if play is not None and demo:  # only a demo house can be played (E-321, B-187)
        app[PLAY_KEY] = play
    app[FONTS_KEY] = manual.fonts(frontend / "assets")

    async def close_screens(_: web.Application) -> None:
        """At a stop the screens are told to go: they come back by themselves – also to a fresh
        HouseMind after the factory settings (E-274), without waiting a minute."""
        for session in [*app[SESSIONS_KEY]]:
            if not session.ws.closed:
                await session.ws.close(code=WSCloseCode.GOING_AWAY, message=b"restart")

    app.on_shutdown.append(close_screens)

    async def broadcast() -> None:
        # Never wait for a screen: each gets the newest picture in its own time.
        for session in [*app[SESSIONS_KEY]]:
            if session.ws.closed:
                app[SESSIONS_KEY].discard(session)
            else:
                session.push_soon()

    def forwarding() -> bool:
        """A demo house jumps ahead (E-321): no message per action, no spoken words."""
        return PLAY_KEY in app and app[PLAY_KEY].forwarding

    async def toast(decision_id: int, decision: Decision) -> None:
        if decision.request.layer is not Layer.TRIAL:
            # Only after a try while the rule is still on Voorstellen (E-513): what is on
            # Autonoom, a house rule or a yes HouseMind just does, without a message on the
            # screen. The Logboek has it, with Why? and Undo.
            return
        if forwarding() or decision.request.facts.get("tried"):
            return  # a Proef of a stand: the lamp and its window show it (E-491)
        for session in [*app[SESSIONS_KEY]]:
            try:
                await session.toast(decision_id, decision)
            except (ConnectionError, RuntimeError):
                app[SESSIONS_KEY].discard(session)

    async def vesta(area_id: str, text: str, audio: str | None, name: str) -> None:
        """Vesta speaks in a room by herself: a wall tablet there is its speaker (E-223)."""
        if forwarding():
            return
        for session in [*app[SESSIONS_KEY]]:
            if session.tablet != area_id:
                continue
            try:
                await session.send({"type": "say", "text": text, "voice": name})
                if audio:
                    await session.send({"type": "voice", "audio": audio})
            except (ConnectionError, RuntimeError):
                app[SESSIONS_KEY].discard(session)

    async def vesta_person(user_id: str, text: str, audio: str | None, name: str) -> None:
        """Vesta says something by herself to one person: on their open app (not a wall
        tablet); the app plays her voice only while it is open and visible (E-223)."""
        if forwarding():
            return
        for session in [*app[SESSIONS_KEY]]:
            if session.user_id != user_id or session.tablet:
                continue
            try:
                await session.send({"type": "say", "text": text, "voice": name})
                if audio:
                    await session.send({"type": "voice", "audio": audio, "byItself": True})
            except (ConnectionError, RuntimeError):
                app[SESSIONS_KEY].discard(session)

    async def update() -> None:
        # During a long jump in a demo house the screens follow its progress, not every change:
        # rebuilding every screen for each change makes a jump slow.
        if not forwarding():
            await broadcast()

    hm.on_update(update)
    if PLAY_KEY in app:
        app[PLAY_KEY].on_change = broadcast  # a jump's progress reaches every screen
    hm.on_action(toast)
    hm.on_vesta(vesta)
    hm.on_vesta_person(vesta_person)
    hm.tablet_areas = lambda: {s.tablet for s in app[SESSIONS_KEY] if s.tablet}
    app.router.add_get("/api/ws", websocket)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/diagnostics", diagnostics_package)
    app.router.add_get("/api/export", export_copy)
    app.router.add_post("/api/settings-file", read_settings)
    app.router.add_get("/api/my-data", my_data_file)
    app.router.add_get(r"/manual/{lang:[a-z]{2}(?:-[A-Za-z]+)?}", manual_page)
    bridge.routes(app, APP_KEY)  # only for the integration in Home Assistant
    if (frontend / "index.html").exists():

        async def index(request: web.Request) -> web.FileResponse:
            # Always the newest page after an update, never a cached one with the old app; the
            # assets themselves have their version in their name.
            return web.FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

        app.router.add_get("/", index)
        app.router.add_static("/assets", frontend / "assets")
    return app
