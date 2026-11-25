"""The link with Home Assistant (B-06, 20.23): the small integration HouseMind places, the
announcement and its secret, the bridge's contact, Assist and linking."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
import shutil
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from housemind import __version__
from housemind.ai.service import AIService
from housemind.brain import setting_up
from housemind.brain.conversation import Asked, Conversation, choice, plain
from housemind.brain.proposals import Answer, Proposals
from housemind.brain.rules import HouseRule
from housemind.brain.talk import (
    answer_word,
    intent,
    normalise,
)
from housemind.core.clock import Clock
from housemind.core.model import HouseModel
from housemind.energy.manager import EnergyManager
from housemind.ha.adapter import HAConfig, HomeAssistant
from housemind.ha.supervisor import announce, withdraw
from housemind.i18n.catalog import Languages
from housemind.memory.database import Database
from housemind.safety.gate import SafetyGate
from housemind.world.world import World

if TYPE_CHECKING:
    from housemind.runtime import HouseMind, Paths, Status

_LOGGER = logging.getLogger(__name__)

ANNOUNCE_AGAIN = timedelta(minutes=10)  # an old key in HA: the new one, at most this often
CONTACT_WINDOW = timedelta(seconds=90)
CONVERSATION_CONTROL = 1  # Home Assistant: a conversation agent that may control the house
FINISH_AGAIN = timedelta(minutes=1)  # finishing the link in HA: tried at most this often
FOREVER = datetime(9999, 1, 1, tzinfo=ZoneInfo("UTC"))
LINKING = timedelta(minutes=10)  # after "Koppelen": HouseMind finishes it within this time


class IntegrationLink:
    """The link with Home Assistant (B-06, 20.23): the small integration HouseMind places, the"""

    @property
    def _hm(self) -> HouseMind:
        """This part is mixed into HouseMind: itself, as the HouseMind it is."""
        return cast("HouseMind", self)

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _announced_at: datetime | None
        _assist_pending: dict[str, tuple[datetime, dict[str, Any], str]]
        _assist_talks: dict[str, Conversation]
        _finish_result: str | None
        _finish_tried: datetime | None

        def _language(self) -> str: ...

        ai: AIService
        gate: SafetyGate | None

        async def answer(
            self, proposal_id: int, answer: Answer, user_id: str | None, said_no: bool = False
        ) -> dict[str, Any]: ...

        bridge_refused: datetime | None
        bridge_seen: datetime | None
        clock: Clock
        config: HAConfig | None
        db: Database
        energy: EnergyManager | None
        ha: HomeAssistant
        languages: Languages
        linking_since: datetime | None
        model: HouseModel

        def orb_state(
            self,
            thinking: bool = True,
            admin: bool = True,
            level: int = 4,
            user_id: str | None = None,
        ) -> str: ...

        paths: Paths

        def paused_until(self) -> datetime | None: ...

        proposals: Proposals

        async def save_rule(
            self, rule: HouseRule, user_id: str | None, replace_ids: list[int] | None = None
        ) -> int: ...

        status: Status

        async def talk(
            self,
            text: str,
            language: str,
            level: int,
            user_id: str | None,
            status_line: str = "",
            channel: str = "app",
            conversation: Conversation | None = None,
        ) -> dict[str, Any]: ...
        def vesta_name(self, language: str) -> str: ...
        def vesta_says(self, language: str, topic: str, user_id: str | None = None) -> str: ...

        world: World | None

    async def brain_name(self) -> str:
        """Her name in Home Assistant and Assist: one name, the same as in the app (E-265,
        B-112) – Vesta, or the one the administrator gave her. A name chosen for the brain
        alone (E-170) is taken over once, see ``start``."""
        language = self.config.language if self.config else "en"
        return self.vesta_name(language)

    def _integration_file(self) -> Path | None:
        return self.paths.app_data / "integration.json" if self.paths.app_data else None

    async def _integration_record(self) -> dict[str, Any]:
        path = self._integration_file()
        if path is None:
            return dict(await self.db.get_setting("integration", {}) or {})
        try:
            raw = json.loads(await asyncio.to_thread(path.read_text))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    async def _save_integration_record(self, record: dict[str, Any]) -> None:
        path = self._integration_file()
        if path is None:
            await self.db.set_setting("integration", record)
        else:
            await asyncio.to_thread(path.write_text, json.dumps(record))

    def _earlier_secret(self) -> str | None:
        """Where the secret lived before: the real house's memory first, then this one."""
        places = [self.paths.app_data / "housemind.db"] if self.paths.app_data else []
        places.append(self.db.path)
        for place in places:
            if not place.exists():
                continue
            try:
                with contextlib.closing(sqlite3.connect(f"file:{place}?mode=ro", uri=True)) as c:
                    row = c.execute(
                        "SELECT value FROM settings WHERE key = 'integration_secret'"
                    ).fetchone()
            except sqlite3.Error:
                continue
            value = json.loads(row[0]) if row else None
            if isinstance(value, str) and len(value) >= 32:
                return value
        return None

    async def integration_secret(self) -> str:
        record = await self._integration_record()
        secret = record.get("secret")
        if isinstance(secret, str) and len(secret) >= 32:
            return secret
        secret = await asyncio.to_thread(self._earlier_secret) or secrets.token_urlsafe(32)
        record["secret"] = secret
        await self._save_integration_record(record)
        return secret

    def _discovery_file(self) -> Path | None:
        """HouseMind's own announcements in HA's discovery: kept apart, so the factory settings
        can take them back (an old one carries an old key)."""
        return self.paths.app_data / "discovery.json" if self.paths.app_data else None

    def _announced_ids(self) -> list[str]:
        path = self._discovery_file()
        try:
            raw = json.loads(path.read_text()) if path is not None else []
        except (OSError, ValueError):
            return []
        return [str(u) for u in raw if isinstance(u, str)] if isinstance(raw, list) else []

    def _keep_announced(self, ids: list[str]) -> None:
        path = self._discovery_file()
        if path is not None:
            path.write_text(json.dumps(ids))

    async def _announce(self, connection: dict[str, Any]) -> None:
        """Tell HA where the App is and with which key – after taking back the earlier
        announcements, so HA never offers HouseMind with an old key (20.23)."""
        token = self.paths.supervisor_token
        if not token:
            return
        earlier = await asyncio.to_thread(self._announced_ids)
        uuid = await announce(token, connection, earlier)
        if uuid is not None:
            await asyncio.to_thread(self._keep_announced, [uuid] if uuid else [])

    async def withdraw_announcements(self) -> None:
        """The factory settings and removing HouseMind: no announcement stays behind."""
        token = self.paths.supervisor_token
        if not token:
            return
        for uuid in await asyncio.to_thread(self._announced_ids):
            await withdraw(token, uuid)
        await asyncio.to_thread(self._keep_announced, [])

    async def _announce_again(self) -> None:
        """At every start, once the integration was placed: tell HA where the App is and
        with which secret. HA updates its entry by itself when something changed."""
        record = await self._integration_record()
        target = self._integration_target()
        if not record.get("placed") and target is not None and await self._placed_by_us(target):
            record["placed"] = True  # placed before this record existed
            await self._save_integration_record(record)
        if record.get("placed") and self.paths.supervisor_token:
            await self._announce(await self._connection())

    async def bridge_contact(self, ok: bool) -> None:
        """A call from the integration in HA, with the right key or not (20.23). An old key
        (HA keeps asking with it after the factory settings) gets the new one through
        discovery, at most every ten minutes; HA updates its entry by itself."""
        now = self.clock.now()
        if ok:
            self.bridge_seen = now
            return
        self.bridge_refused = now
        token = self.paths.supervisor_token
        if token and (self._announced_at is None or now - self._announced_at >= ANNOUNCE_AGAIN):
            self._announced_at = now
            await self._announce(await self._connection())

    def bridge_state_word(self) -> str:
        """Whether HA really reaches HouseMind now: "ok", "refused" (an old key) or "none"."""
        now = self.clock.now()
        seen, refused = self.bridge_seen, self.bridge_refused
        if (
            seen is not None
            and now - seen <= CONTACT_WINDOW
            and (refused is None or seen >= refused)
        ):
            return "ok"
        if refused is not None and now - refused <= CONTACT_WINDOW:
            return "refused"
        return "none"

    async def _connection(self) -> dict[str, Any]:
        secret = await self.integration_secret()
        return {"host": socket.gethostname(), "port": 8099, "secret": secret}

    async def bridge_state(self) -> dict[str, Any]:
        """What the integration shows in HA (E-96, E-97)."""
        assert self.world is not None
        snapshot = self.world.snapshot(self.clock.now())
        lang = self._language()
        paused = self.paused_until()
        health = "emergency" if self.status.emergency else "paused" if paused else "ok"
        # Never "Unknown" in HA (E-234): while HouseMind still measures, "Besparing" says so,
        # with the days left; a house without energy gets no such sensor.
        saving, saving_text = None, None
        energy = self.energy is not None and self.energy.setup.has_any
        if self.energy is not None and energy:
            savings = await self.energy.savings(snapshot.now)
            if savings["measuring"]:
                days = int(savings["daysLeft"])
                saving_text = self.languages.text(lang, "integration.measuring", count=days)
            else:
                saving = savings["total"]
        return {
            "version": __version__,
            "name": await self.brain_name(),
            "orb": self.orb_state(thinking=False),  # HA's entity: no flicker at every action
            "orb_text": self.languages.text(lang, f"orb.{self.orb_state(thinking=False)}"),
            # Why, in a few words – only what concerns the whole house (E-389, B-300): HA's
            # sensor is for everyone in Home Assistant.
            "reason": (await self._hm.vesta_why(lang, 4, False, None, for_ha=True) or {}).get(
                "text"
            ),
            "mode": snapshot.mode.value,
            "paused": paused is not None,
            "paused_until": paused.isoformat() if paused and paused < FOREVER else None,
            "health": health,
            "saving": saving,
            "saving_text": saving_text,
            "energy": energy,
            "currency": self.config.currency if self.config else "EUR",
            # The watchdog's list (B-204): what to give back if HouseMind goes silent.
            "fallback": self._fallback(),
        }

    def _fallback(self) -> list[dict[str, Any]]:
        """What the watchdog may give back: energy devices only, never a door opener or what an
        administrator protected (QA 3, gat 5) – the watchdog acts without the gate."""
        if self.energy is None:
            return []
        gate = self.gate
        never = (gate.state.openers | gate.state.protected_entities) if gate else set()
        return [item for item in self.energy.fallback() if item.get("entity_id") not in never]

    def watchdog_done(self) -> None:
        """The integration's watchdog gave everything back while HouseMind was silent."""
        if self.energy is not None:
            self.energy.handed_back()
            _LOGGER.warning("The watchdog in Home Assistant gave the energy devices back")

    async def assist(
        self, text: str, language: str, conversation_id: str | None, user_id: str | None
    ) -> dict[str, Any]:
        """A sentence from HA Assist (E-167). Direct commands HA handles itself; here come
        questions about the house, house rules by voice (read back, then yes or no, E-87) and
        everything else – forwarded only if the administrator allowed it."""
        lang = self.languages.resolve(language)
        t = self.languages.text
        now = self.clock.now()
        key = conversation_id or f"user:{user_id}"
        for old in [
            k for k, (at, _, _) in self._assist_pending.items() if now - at > timedelta(minutes=3)
        ]:
            del self._assist_pending[old]
        pending = self._assist_pending.pop(key, None)
        if pending is not None and pending[1].get("kind") in ("step", "automation"):
            # Setting a step and the automation that steers it, by voice (E-317).
            from housemind.brain import stepwish

            _, payload, said_lang = pending
            reply = answer_word(text, said_lang, self.languages)
            if reply in ("yes", "no"):
                spoken, follow = await stepwish.answer(
                    self._hm, payload, reply == "yes", user_id, said_lang
                )
                if follow is not None:
                    self._assist_pending[key] = (now, follow, said_lang)
                return {"text": spoken, "continue": follow is not None}
            pending = None
        if pending is not None:
            _, rule, rule_lang = pending
            words = normalise(text).split()
            yes = set(t(rule_lang, "talk.words.yes").lower().replace(",", " ").split())
            no = set(t(rule_lang, "talk.words.no").lower().replace(",", " ").split())
            if words and words[0] in yes:
                done = await self._hm.keep_rule(rule, user_id)  # a change replaces (E-420)
                return {"text": t(rule_lang, done), "continue": False}
            if words and words[0] in no:
                return {"text": t(rule_lang, "talk.rule_cancelled"), "continue": False}
        # Her question in this conversation first (E-349, B-234): only that one, not another.
        for old in [k for k, c in self._assist_talks.items() if c.current(now) is None]:
            if not self._assist_talks[old].turns:
                del self._assist_talks[old]
        talking = self._assist_talks.setdefault(key, Conversation())
        hers = talking.current(now)
        if hers is not None:
            mine = await self._assist_answer(talking, hers, text, lang, user_id)
            if mine is not None:
                return mine
        # A short answer to her question – also on a shared speaker, where "no" always counts
        # and "yes" only to what anyone may say yes to (E-292).
        said = (
            None
            if intent(text, lang, self.languages) or hers is not None
            else answer_word(text, lang, self.languages)
        )
        if said is not None:
            asked = await self.proposals.open(now)
            if not asked:
                return {"text": t(lang, "talk.no_question"), "continue": False}
            if said == "yes" and user_id is None:
                # A shared speaker or an automation gives no person: anyone in earshot could say
                # yes to a skylight or a mower. No still counts (QA 3, gat 6).
                return {"text": t(lang, "talk.yes_where"), "continue": False}
            newest = max(asked, key=lambda p: p.id)
            answer = {"yes": Answer.DO, "no": Answer.NOT_NOW, "not_now": Answer.NOT_NOW}[said]
            await self.answer(newest.id, answer, user_id, said_no=said == "no")
            topic = {"yes": "answer_yes", "no": "answer_no", "not_now": "ask_later"}[said]
            return {"text": self.vesta_says(lang, topic, user_id), "continue": False}
        row = await self.db.fetchone("SELECT level FROM people WHERE user_id = ?", (user_id,))
        level = int(row["level"]) if row else 2
        talking.said("you", text, now)
        result = await self.talk(text, lang, level, user_id, channel="assist", conversation=talking)
        follow = await self._assist_follow(talking, result, lang, user_id)
        if follow is not None:
            return follow
        wish = result.get("confirm_step")
        if wish is not None:
            users = {u.id: u for u in await self.ha.users()} if user_id else {}
            if user_id is None or user_id not in users or not users[user_id].is_admin:
                return {"text": t(lang, "talk.step.admin_only"), "continue": False}
            if level < (4 if wish["detailed"] else 3):
                return {"text": t(lang, "talk.step.level"), "continue": False}
            self._assist_pending[key] = (now, wish, lang)
            return {"text": result["text"], "continue": True}
        if result.get("confirm_rule") and user_id is None:
            # A shared speaker or satellite gives no user: a house rule is made where she
            # knows who you are (E-292).
            return {"text": t(lang, "talk.rule_where"), "continue": False}
        if result.get("confirm_rule") and level >= 3:
            self._assist_pending[key] = (now, result["confirm_rule"], lang)
            return {"text": result["text"], "continue": True}
        if result.get("confirm_rule"):
            return {"text": t(lang, "talk.rule_level"), "continue": False}
        return {"text": result["text"], "continue": False}

    def _assist_choices(self, asked: Asked, lang: str) -> dict[str, str]:
        t = self.languages.text
        if asked.kind == "ai_choice":
            return {c: c for c in asked.actions}
        return {
            a: t(lang, "ui.vesta.why_ask" if a == "why" else f"notify.answer.{a}")
            for a in asked.actions
        }

    def _assist_say_choices(self, asked: Asked, lang: str, user_id: str | None) -> str:
        """Without a screen she says her choices aloud (B-234)."""
        words = [plain(w) for w in self._assist_choices(asked, lang).values()]
        joined = (
            f"{', '.join(words[:-1])} {self.languages.text(lang, 'ask.or')} {words[-1]}"
            if len(words) > 1
            else "".join(words)
        )
        return self._hm.vesta_fact(lang, "ask_choices", user_id, choices=joined)

    async def _assist_answer(
        self, talking: Conversation, asked: Asked, text: str, lang: str, user_id: str | None
    ) -> dict[str, Any] | None:
        """An answer to the question she asked in this Assist conversation (E-349, B-234):
        one of her choices, said; else what she knows for sure, with her question still open;
        without an AI her choices again. None: with an AI, it goes to the AI."""
        hm = self._hm
        now = self.clock.now()
        if asked.kind == "ai_choice" or asked.proposal is None:
            return None  # the AI asked back: your answer goes to the AI with the conversation
        t = self.languages.text
        yes, no = (
            [w.strip() for w in t(lang, f"talk.words.{code}").split(",") if w.strip()]
            for code in ("yes", "no")
        )
        found = choice(text, asked, self._assist_choices(asked, lang), yes, no)
        talking.said("you", text, now)
        if found is not None:
            action, plain_no = found
            if action == "why":
                said = await hm.vesta_why_asked(user_id, lang, asked.proposal)
                talking.said("vesta", said, now)
                return {"text": said, "continue": True}
            talking.answered()
            result = await self.answer(asked.proposal, Answer(action), user_id, said_no=plain_no)
            if result.get("executed"):  # "zet dat terug" undoes it (E-421)
                await hm.talked_about(talking, asked.proposal, result.get("decision"))
            topic = "answer_yes" if action in ("do", "always") else "answer_no"
            reply = (
                self.vesta_says(lang, topic, user_id)
                if result["ok"]
                else t(lang, "ui.proposal.already_answered")
            )
            return {"text": reply, "continue": False}
        row = await self.db.fetchone("SELECT level FROM people WHERE user_id = ?", (user_id,))
        level = int(row["level"]) if row else 2
        sure = await hm.talk_sure(text, lang, level, user_id, channel="assist")
        if sure is not None:
            talking.said("vesta", sure["text"], now)
            return {"text": sure["text"], "continue": True}  # her question stays open
        if hm.ai.status() != "on":
            said = self._assist_say_choices(asked, lang, user_id)
            talking.said("vesta", said, now)
            return {"text": said, "continue": True}
        return None

    async def _assist_follow(
        self, talking: Conversation, result: dict[str, Any], lang: str, user_id: str | None
    ) -> dict[str, Any] | None:
        """What comes after an answer through Assist (E-349): her question answered by the
        AI's understanding, the AI asking back, or something to do – asked right away, done
        only after your yes (B-233). None: the answer as it is."""
        hm = self._hm
        now = self.clock.now()
        asked = talking.current(now)
        if asked is not None and asked.proposal is not None:
            if result.get("away"):
                said = f"{result['away']} {self._assist_say_choices(asked, lang, user_id)}"
                return {"text": said, "continue": True}
            if result.get("reply") in asked.actions and result["reply"] != "why":
                talking.answered()
                await self.answer(asked.proposal, Answer(str(result["reply"])), user_id)
                return {"text": result["text"], "continue": False}
        talking.said("vesta", result["text"], now)
        if result.get("choices"):
            talking.ask(Asked("ai_choice", "ai", tuple(result["choices"]), result["text"]), now)
            return {"text": result["text"], "continue": True}
        for proposal_id in result.get("proposals") or []:
            actions = await hm.proposal_actions(int(proposal_id))
            if not actions:
                continue
            ask = {"kind": "proposal", "proposal": int(proposal_id), "key": f"p{proposal_id}"}
            question = await hm.vesta_ask_text(user_id, lang, ask)
            await hm.talked_about(talking, int(proposal_id))  # E-421
            talking.ask(
                Asked("proposal", f"p{proposal_id}", tuple(actions), question, int(proposal_id)),
                now,
            )
            return {"text": f"{result['text']} {question}", "continue": True}
        return None

    def forward_agents(self) -> list[str]:
        """The conversation agents general questions may go to (E-167, QA 2 B-169): only ones
        that cannot control Home Assistant – HA's own agent and an AI agent with 'control' would
        be a way around the gate and the hard limits. Not HouseMind's own agent either."""
        found = []
        for entity_id, state in (self.world.states if self.world else {}).items():
            if not entity_id.startswith("conversation.") or "housemind" in entity_id:
                continue
            entity = self.model.entities.get(entity_id)
            if entity is not None and entity.platform in ("housemind", "conversation"):
                continue  # Vesta herself, or Home Assistant's own agent
            if entity_id == "conversation.home_assistant":
                continue
            features = state.attributes.get("supported_features")
            if not isinstance(features, int) or features & CONVERSATION_CONTROL:
                continue  # it may control the house, or it does not say
            found.append(entity_id)
        return sorted(found)

    async def _forward(self, text: str, lang: str, spoken: bool = True) -> str:
        """Not about the house: only with the administrator's permission (E-167 – E-169)."""
        setting = await self.db.get_setting("assist_forward", "off")
        if setting == "ai" and self.ai.status() == "on":
            answer = await self.ai.general(text, lang)
            if answer.ok:
                return answer.text
        elif isinstance(setting, str) and setting.startswith("agent:"):
            agent = setting[6:]
            if agent not in self.forward_agents():
                # It can control the house now (or is gone): never a way around the gate
                # (QA 2, B-169).
                return self.languages.text(lang, "talk.assist_unknown")
            reply = await self.ha.converse(agent, text, lang)
            await self.ai.log_forward(agent, text, reply)
            if reply:
                return reply
        if setting == "off" and not spoken:
            # On screen: where to turn it on – in her own group of the house settings.
            return self.languages.text(lang, "talk.general_off", name=self.vesta_name(lang))
        return self.languages.text(lang, "talk.assist_unknown")

    def _integration_source(self) -> Path | None:
        here = Path(__file__).resolve().parents[3] / "integration" / "custom_components"
        for root in (Path("/app/integration/custom_components"), here):
            if (root / "housemind" / "manifest.json").exists():
                return root / "housemind"
        return None

    def _integration_target(self) -> Path | None:
        config = self.paths.ha_config
        return config / "custom_components" / "housemind" if config is not None else None

    @staticmethod
    def _manifest_version(folder: Path | None) -> str | None:
        try:
            if folder is None:
                return None
            return str(json.loads((folder / "manifest.json").read_text())["version"])
        except (OSError, ValueError, KeyError):
            return None

    async def _placed_by_us(self, target: Path | None) -> bool:
        """Only an integration this App placed carries this App's secret."""
        try:
            if target is None:
                return False
            connection = json.loads((target / "connection.json").read_text())
        except (OSError, ValueError):
            return False
        return (
            isinstance(connection, dict)
            and connection.get("secret") == await self.integration_secret()
        )

    async def integration_status(self) -> dict[str, Any]:
        source, target = self._integration_source(), self._integration_target()
        # Also while HA restarts, as right after "Koppelen": the screen still gets a status;
        # the next look has it all again.
        try:
            actions = await self.ha.available_actions()
            speech: dict[str, Any] | None = await setting_up.speech_places(self._hm)
        except (ConnectionError, TimeoutError):
            actions, speech = {}, None
        installed = self._manifest_version(target)
        return {
            "available": source is not None and target is not None,
            "installed": installed,
            # Another "housemind" integration is there (e.g. from an earlier build): it is
            # only replaced after the administrator confirmed exactly that.
            "foreign": installed is not None and not await self._placed_by_us(target),
            "current": self._manifest_version(source),
            "loaded": "housemind" in actions,
            "setup": await self.ha.integration_setup("housemind") if installed else None,
            # Not only there, but really in touch: HA asks with the right key (20.23).
            "contact": self.bridge_state_word(),
            # "Koppelen" under way (restarting HA, finishing the link), and whether finishing
            # it here failed – then the screen shows the two clicks in HA (20.23).
            "linking": self.linking_since is not None
            and self.clock.now() - self.linking_since < LINKING
            and self.bridge_state_word() != "ok"
            and self._finish_result != "failed",
            "finish": self._finish_result,
            "speech": speech,  # cloud or in the house (20.19)
            "forward": await self.db.get_setting("assist_forward", "off"),
            "pipeline": await setting_up.pipeline_status(self._hm),  # Vesta's own (E-280)
            "name": await self.brain_name(),
            "agents": self.forward_agents(),
        }

    async def place_integration(self, replace_foreign: bool = False) -> dict[str, Any]:
        """Put the small integration into HA's custom_components (after an administrator's
        yes, B-06). HA loads it after one restart; nothing opens to the outside (E-86).
        An integration HouseMind did not place itself is never replaced without a yes."""
        source, target = self._integration_source(), self._integration_target()
        if source is None or target is None:
            return {"ok": False, "reason": "not_available"}
        if (
            self._manifest_version(target) is not None
            and not await self._placed_by_us(target)
            and not replace_foreign
        ):
            return {"ok": False, "reason": "foreign", **await self.integration_status()}
        connection = await self._connection()

        def copy() -> None:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            secret = target / "connection.json"
            secret.write_text(json.dumps(connection))
            secret.chmod(0o600)  # the bridge secret: not for other apps reading /config (QA 2, L2)

        await asyncio.to_thread(copy)
        record = await self._integration_record()
        record["placed"] = True
        await self._save_integration_record(record)
        await self._announce(connection)
        return {"ok": True, **await self.integration_status()}

    @staticmethod
    def _digest(folder: Path) -> str:
        """The integration's content, without the connection and Python's caches."""
        digest = hashlib.sha256()
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.name == "connection.json":
                continue
            digest.update(str(path.relative_to(folder)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    async def _update_integration(self) -> None:
        """A newer App brings a newer integration: replace the files, keep the connection.
        HA loads the new files at its next restart."""
        source, target = self._integration_source(), self._integration_target()
        installed, current = self._manifest_version(target), self._manifest_version(source)
        if source is None or target is None or installed is None:
            return
        if await asyncio.to_thread(self._digest, source) == await asyncio.to_thread(
            self._digest, target
        ):
            return
        if not await self._placed_by_us(target):
            return  # not ours: never touched without the administrator's yes
        await self.place_integration()
        _LOGGER.info("HouseMind integration updated from %s to %s", installed, current)

    async def restart_ha(self) -> bool:
        """Restart Home Assistant – only on an administrator's yes (B-27)."""
        return (await self.ha.restart()).ok

    async def link_ha(self, replace_foreign: bool = False) -> dict[str, Any]:
        """ "Koppelen" – one yes of an administrator for all of it (20.23): place the
        integration, finish setting it up in HA as an administrator would, and when HA does not
        know it yet, restart HA; after the restart HouseMind finishes it by itself
        (``_finish_link``). Gives the step it reached: "done", "restarting" or a reason."""
        placed = await self.place_integration(replace_foreign=replace_foreign)
        if not placed["ok"]:
            return {"ok": False, "step": str(placed.get("reason") or "not_available")}
        self.linking_since, self._finish_result = self.clock.now(), None
        result = await self._finish_now()
        if result == "restart":
            if not await self.restart_ha():
                return {"ok": False, "step": "restart_failed"}
            return {"ok": True, "step": "restarting"}
        return {"ok": result == "done", "step": result}

    async def _finish_now(self) -> str:
        self._finish_tried = self.clock.now()
        self._finish_result = await self.ha.finish_integration("housemind")
        _LOGGER.info("Finishing the link in Home Assistant: %s", self._finish_result)
        return self._finish_result

    async def _finish_link(self, now: datetime) -> None:
        """After "Koppelen" and HA's restart: finish setting the link up, so nobody has to
        click in HA (20.23). Only while that "Koppelen" is under way, at most every minute,
        and not once it failed – then the screen shows the two clicks in HA."""
        since = self.linking_since
        if since is None or now - since >= LINKING or self._finish_result == "failed":
            return
        if self._finish_tried is not None and now - self._finish_tried < FINISH_AGAIN:
            return
        if self.bridge_state_word() == "ok":
            self.linking_since = None  # linked
            return
        if await self.ha.integration_setup("housemind") == "configured":
            return  # HA has it: its first call comes within half a minute
        await self._finish_now()
