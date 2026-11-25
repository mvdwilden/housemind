"""Labels and categories from Home Assistant as a hint (E-345, B-220): what HouseMind may do with
them. A hint fills in the part where HouseMind says "other" (in the views); operating something
waits for an administrator's yes – a switch with a label like "Lights" is asked about once
("Is het een lamp?") and becomes a lamp only after a yes (the role ``light``, E-109). Labels
that point to Home Assistant itself, the network or a tablet give one question per label: leave
those devices alone? Only what HouseMind itself calls "other" and no part uses: a lamp, curtain,
climate, alarm or the energy setup stays as it is. An answer holds for devices that get that label
later. The answers are kept in the setting ``label_answers``; the lamps in ``label_roles``; what a
label put on 'met rust laten' in ``label_left_alone``."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from typing import TYPE_CHECKING, Any

from housemind.brain.filters import part_of
from housemind.brain.notify import Kind
from housemind.core.model import HouseModel
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.labels import SYSTEM, LabelHints, entity_hint, hint
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.emergency import EmergencyReactions
from housemind.safety.gate import SafetyGate
from housemind.world.world import WorldState

if TYPE_CHECKING:
    from housemind.energy.manager import EnergyManager
    from housemind.i18n.catalog import Languages

LAMP, SYSTEM_TOPIC = "label_lamp:", "label_system:"
YES_NO = (("yes", "notify.answer.yes"), ("no", "notify.answer.no"))


class LabelsPart(Part):
    """Labels and categories from Home Assistant as a hint (E-345, B-220)."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        catalog: Catalog
        db: Database
        emergencies: EmergencyReactions | None
        energy: EnergyManager | None
        gate: SafetyGate | None
        label_hints: LabelHints
        languages: Languages
        model: HouseModel
        _raised: dict[str, Any]

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
        ) -> int | None: ...
        async def _resolve(self, topic: str) -> None: ...
        def _tracked(self, *prefixes: str) -> list[str]: ...
        async def set_house_setting(self, key: str, value: Any) -> bool: ...

    async def _label_answers(self) -> dict[str, bool]:
        raw = await self.db.get_setting("label_answers", {}) or {}
        return {str(k): bool(v) for k, v in raw.items()}

    async def _apply_label_roles(self) -> None:
        """The lamps an administrator confirmed (B-220), after every rebuild of the house, and
        a yes to leave a label's devices alone for devices that got it later."""
        assert self.gate is not None
        roles = await self.db.get_setting("label_roles", {}) or {}
        for entity_id, role in roles.items():
            if entity_id in self.model.entities:
                self.gate.state.roles[str(entity_id)] = str(role)
        answers = await self._label_answers()
        said_yes = [k for k, yes in answers.items() if yes and k.startswith(SYSTEM_TOPIC)]
        await self._leave_alone([k[len(SYSTEM_TOPIC) :] for k in said_yes])

    async def _leave_alone(self, label_ids: list[str]) -> None:
        """Put what these system labels cover on 'met rust laten', each device once: one an
        administrator takes back stays back. What a part has started to use since (a lamp, the
        energy setup) comes back by itself."""
        assert self.gate is not None
        excluded = self.gate.state.excluded
        done = set(await self.db.get_setting("label_left_alone", []) or [])
        new = {e for label in label_ids for e in self._system_entities(label)} - done - excluded
        energy = self._energy_entities()
        used = {e for e in done & excluded if not self._may_leave(e, energy)}
        if new:
            await self.db.set_setting("label_left_alone", sorted(done | new))
        if new or used:
            await self.set_house_setting("excluded", sorted((excluded | new) - used))

    def _lamp_candidates(self, answers: dict[str, bool]) -> list[tuple[str, str]]:
        """Switches HouseMind only watches whose labels say lamp: (entity, label name). Through
        the device's label only its one switch, on a device without a lamp: on a lamp, or next to
        other switches, a switch is a setting ('Do not disturb', a light program's switches)."""
        assert self.gate is not None
        roles, out = self.gate.state.roles, []
        switches: dict[str, int] = {}
        lit: set[str] = set()
        for entity in self.model.entities.values():
            if entity.hidden or entity.disabled or entity.entity_category:
                continue
            if entity.device_id:
                if entity.domain == "switch":
                    switches[entity.device_id] = switches.get(entity.device_id, 0) + 1
                elif entity.domain == "light":
                    lit.add(entity.device_id)
        for entity in sorted(self.model.entities.values(), key=lambda e: e.name):
            if entity.domain != "switch" or entity.hidden or entity.disabled:
                continue
            if entity.entity_category or entity.entity_id in roles:
                continue
            if (
                entity.entity_id in self.gate.state.openers  # a door, never a lamp (QA 3)
                or entity.entity_id in self.gate.state.excluded
                or f"{LAMP}{entity.entity_id}" in answers
            ):
                continue
            found = entity_hint(self.model, self.label_hints, entity)
            if found is None or found[0] != "lighting":
                continue
            own = entity_hint(self.model, self.label_hints, replace(entity, device_id=None))
            device = entity.device_id
            if own is None and (device in lit or switches.get(device or "", 0) != 1):
                continue
            out.append((entity.entity_id, found[1]))
        return out

    def _system_labels(self) -> list[str]:
        return [
            label.id
            for label in self.model.labels.values()
            if hint(self.label_hints, label.name, label.icon) == SYSTEM
        ]

    def _system_entities(self, label_id: str) -> set[str]:
        """The entities on the devices with this label (or with it themselves) that HouseMind
        may leave alone: no house devices, so it need not learn from them."""
        out, energy = set(), self._energy_entities()
        for entity in self.model.entities.values():
            device = self.model.devices.get(entity.device_id) if entity.device_id else None
            if label_id not in entity.labels and not (device and label_id in device.labels):
                continue
            if entity.hidden or entity.disabled or entity.entity_category:
                continue
            if self._may_leave(entity.entity_id, energy):
                out.add(entity.entity_id)
        return out

    def _may_leave(self, entity_id: str, energy: set[str]) -> bool:
        """Only what HouseMind calls "other" and no part uses: never a lamp, curtain, climate,
        alarm or media device, nothing with a role, nothing an emergency or the energy setup
        uses (also its meters)."""
        assert self.gate is not None
        if entity_id in self.gate.state.roles or part_of(entity_id, {}) != "other":
            return False
        if self.emergencies and self.emergencies.emergency_for(entity_id) is not None:
            return False
        return entity_id not in energy

    def _energy_entities(self) -> set[str]:
        """Every entity the energy setup names, however deep (meters, a battery's knobs)."""
        found: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if value in self.model.entities:
                    found.add(value)
            elif isinstance(value, list | tuple | set):
                for item in value:
                    walk(item)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif is_dataclass(value) and not isinstance(value, type):
                for f in fields(value):
                    walk(getattr(value, f.name))

        if self.energy is not None:
            walk(self.energy.setup)
        return found

    async def _ask_labels(self, snapshot: WorldState) -> None:
        """One question about a lamp and one about a system label at a time, to the
        administrators; each only once (B-220). A question that no longer fits is withdrawn."""
        if self.gate is None or not self.model.labels:
            return
        answers = await self._label_answers()
        lamps = self._lamp_candidates(answers)
        for topic in self._tracked(LAMP):
            if topic[len(LAMP) :] not in {e for e, _ in lamps}:
                await self._resolve(topic)
        if lamps and not any(t.startswith(LAMP) for t in self._raised):
            entity_id, label = lamps[0]
            await self._raise(
                Kind.LEARNING,
                f"{LAMP}{entity_id}",
                "notify.label_lamp",
                {"device": self.model.entities[entity_id].name, "label": label},
                snapshot,
                concerns="admins",
                actions=YES_NO,
            )
        done = set(await self.db.get_setting("label_left_alone", []) or [])
        left = {
            label_id: self._system_entities(label_id) - self.gate.state.excluded - done
            for label_id in self._system_labels()
            if f"{SYSTEM_TOPIC}{label_id}" not in answers
        }
        for topic in self._tracked(SYSTEM_TOPIC):
            if not left.get(topic[len(SYSTEM_TOPIC) :]):
                await self._resolve(topic)
        if any(t.startswith(SYSTEM_TOPIC) for t in self._raised):
            return
        for label_id, entities in left.items():
            if not entities:
                continue
            devices = {self.model.entities[e].device_id or e for e in entities}
            await self._raise(
                Kind.LEARNING,
                f"{SYSTEM_TOPIC}{label_id}",
                "notify.label_system",
                {"label": self.model.labels[label_id].name, "count": len(devices)},
                snapshot,
                concerns="admins",
                actions=YES_NO,
            )
            break

    async def answer_label(self, topic: str, yes: bool) -> bool:
        """An administrator's yes or no to a label question; kept, never asked again."""
        assert self.gate is not None
        answers = await self._label_answers()
        if topic.startswith(LAMP):
            entity_id = topic[len(LAMP) :]
            if entity_id not in self.model.entities:
                return False
            if yes:
                roles = await self.db.get_setting("label_roles", {}) or {}
                await self.db.set_setting("label_roles", {**roles, entity_id: "light"})
                self.gate.state.roles[entity_id] = "light"
        elif topic.startswith(SYSTEM_TOPIC):
            label_id = topic[len(SYSTEM_TOPIC) :]
            if label_id not in self.model.labels:
                return False
        else:
            return False
        await self.db.set_setting("label_answers", {**answers, topic: yes})
        if yes and topic.startswith(SYSTEM_TOPIC):
            await self._leave_alone([topic[len(SYSTEM_TOPIC) :]])
        await self._resolve(topic)
        return True

    def label_note(self, entity_id: str, lang: str) -> str:
        """'Ook herkend aan het label Lights.' where a label gave the part (B-221)."""
        assert self.gate is not None
        entity = self.model.entities.get(entity_id)
        if entity is None or part_of(entity_id, self.gate.state.roles) != "other":
            return ""
        found = entity_hint(self.model, self.label_hints, entity)
        if found is None or found[0] == SYSTEM:
            return ""
        return self.languages.text(lang, "ui.devices.label_hint", label=found[1])

    def labels_used(self) -> int:
        """How many labels and categories give a hint, for the line at setting up (B-221)."""
        named = [(label.name, label.icon) for label in self.model.labels.values()]
        named += [(c.name, c.icon) for c in self.model.categories.values()]
        return sum(1 for name, icon in named if hint(self.label_hints, name, icon) is not None)
