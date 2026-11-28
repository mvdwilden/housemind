"""Rules follow the devices (E-485 – E-488, design 72). A room gets the rules of a device as
soon as the device is there; a rule goes when its devices are gone, and what HouseMind learned
for it is kept 30 days, for a device that comes back or a new one; a room HouseMind does not
know is asked about once. What a room has the devices for is in ``SkillEngine.can_do``."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.notify import Kind
from housemind.parts import Part

if TYPE_CHECKING:
    from housemind.brain.house_knowledge import HouseKnowledge
    from housemind.brain.learning import Learning
    from housemind.brain.notify import Person
    from housemind.brain.skills import SkillEngine
    from housemind.core.clock import Clock
    from housemind.core.model import HouseModel
    from housemind.memory.database import Database
    from housemind.world.world import WorldState

GONE = "rules_gone"  # setting: "skill@area" → since when its devices are gone
GONE_KEPT = timedelta(days=30)  # E-487: what was learned waits this long for a device
ASKED = "room_kind_asked"  # setting: the rooms HouseMind asked about once (E-486)
ROOM_KIND = "room_kind:"  # the question's topic, with the room
ROOM_KIND_ACTIONS = (("choose", "notify.answer.choose"), ("no", "notify.answer.not_now"))


class RulesFollowPart(Part):
    """Which rules the rooms have, and the rules whose devices went."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        admin_steps: dict[str, Any]
        area_roles: dict[str, str]
        clock: Clock
        db: Database

        async def setting(self, key: str) -> Any: ...

        knowledge: HouseKnowledge
        learning: Learning | None
        model: HouseModel
        skills: SkillEngine | None
        _people: list[Person]
        _raised: dict[str, Any]

        def _tracked(self, prefix: str) -> list[str]: ...
        async def _resolve(self, topic: str) -> None: ...
        async def _notify(self) -> None: ...
        async def _raise(
            self,
            kind: Kind,
            topic: str,
            text_key: str,
            params: dict[str, Any],
            snapshot: WorldState,
            concerns: str = "home",
            actions: tuple[tuple[str, str], ...] = (),
            safety: bool = False,
            push: dict[str, str] | None = None,
            to: list[Person] | None = None,
        ) -> int | None: ...

    # -- what the rooms have ------------------------------------------------------------------

    def rules_possible(self) -> frozenset[tuple[str, str]]:
        """Every rule a room has the devices for now: (skill, room)."""
        return self.skills.possible() if self.skills is not None else frozenset()

    def follows_devices(self, skill: str) -> bool:
        return self.skills is not None and self.skills.follows_devices(skill)

    def rule_there(self, skill: str, area_id: str) -> bool:
        """A rule is there: it does not follow devices, its room has them, or – for a rule
        HouseMind knows – a device of it is still in Home Assistant, also one HouseMind leaves
        alone (E-487): only a device taken out is gone."""
        if not self.follows_devices(skill) or (skill, area_id) in self.rules_possible():
            return True
        return self.skills is not None and self.skills.still_has(skill, area_id)

    # -- a rule whose devices went (E-487, B-402) ------------------------------------------------

    async def _rules_follow(self, snapshot: WorldState) -> None:
        """Since when a known rule has no devices; back within 30 days: it goes on where it
        was; after that it is forgotten, step and all, and an administrator's own step for it
        goes too. A device that is unavailable is still there (``can_do`` looks at the
        registry, not at the state)."""
        if self.learning is None or self.skills is None:
            return
        now = snapshot.now
        learning = self.learning
        known = set(learning.steps) | set(learning.first_seen)
        known |= {(k[0], k[1]) for k in learning.values} | {(k[0], k[1]) for k in learning.counts}
        stored = await self.db.get_setting(GONE, {}) or {}
        gone = {str(k): str(v) for k, v in stored.items()} if isinstance(stored, dict) else {}
        before = dict(gone)
        for skill, area_id in known:
            if not self.follows_devices(skill):
                continue
            key = f"{skill}@{area_id}"
            if self.rule_there(skill, area_id):
                gone.pop(key, None)
            else:
                gone.setdefault(key, now.isoformat())
        for key, since in sorted(gone.items()):
            skill, _, area_id = key.partition("@")
            if self.rule_there(skill, area_id):
                del gone[key]
            elif now - datetime.fromisoformat(since) >= GONE_KEPT:
                await learning.drop_pair(skill, area_id, now)
                await self._drop_admin_step(key)
                del gone[key]
        if gone != before:
            await self.db.set_setting(GONE, gone)

    async def _drop_admin_step(self, key: str) -> None:
        """An administrator's own step for a rule that is forgotten goes with it."""
        raw = dict(await self.setting("admin_step") or {})
        if raw.pop(key, None) is not None:
            await self.db.set_setting("admin_step", raw)
        self.admin_steps.pop(key, None)

    # -- a room HouseMind does not know (E-486, B-403) ------------------------------------------

    async def _unknown_rooms(self) -> list[str]:
        """Rooms whose kind HouseMind did not recognise and nobody chose, with something
        HouseMind can work on – by name."""
        chosen = {
            str(r["area_id"])
            for r in await self.db.fetchall(
                "SELECT area_id FROM areas WHERE role_source = 'person'"
            )
        }
        rooms = {a for _, a in self.rules_possible()}
        found = [
            area_id
            for area_id, role in self.area_roles.items()
            if role == "other"
            and area_id in self.model.areas
            and area_id not in chosen
            and area_id in rooms
        ]
        return sorted(found, key=lambda a: (self.model.areas[a].name.casefold(), a))

    async def _room_kinds(self, snapshot: WorldState) -> None:
        """Once per room, one question at a time, to administrators who see 'Wat HouseMind van
        je huis weet': what kind of room is this? Its line waits there, with the kinds and
        'Geen van deze'; an AI's idea, if there is one, stands there instead. The question goes
        when the room got a kind; 'Nu niet' closes it, and it is not asked again."""
        unknown = await self._unknown_rooms()
        for topic in self._tracked(ROOM_KIND):
            if topic[len(ROOM_KIND) :] not in unknown:
                await self._resolve(topic)
        for area_id in unknown:
            await self.knowledge.suggest(
                "area", area_id, "role", {"role": "other"}, None, snapshot.now, None, "rules"
            )
        if any(t.startswith(ROOM_KIND) for t in self._raised):
            return
        stored = await self.db.get_setting(ASKED, []) or []
        asked = {str(a) for a in stored} if isinstance(stored, list) else set()
        admins = [p for p in self._people if p.is_admin and p.level >= 3]
        waiting = [a for a in unknown if a not in asked]
        if not waiting or not admins:
            return
        area_id = waiting[0]
        raised = await self._raise(
            Kind.LEARNING,
            f"{ROOM_KIND}{area_id}",
            "notify.room_kind",
            {"area": self.model.areas[area_id].name},
            snapshot,
            "admins",
            ROOM_KIND_ACTIONS,
            to=admins,
        )
        if raised is not None:
            await self.db.set_setting(ASKED, sorted(asked | {area_id}))

    async def answer_room_kind(self, topic: str) -> None:
        """'Nu niet': the question closes; this room is not asked about again."""
        if topic.startswith(ROOM_KIND):
            await self._resolve(topic)
            await self._notify()  # gone from every screen at once (E-585)
