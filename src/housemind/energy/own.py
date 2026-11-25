"""What HouseMind changed itself in the energy part, and how to give it back (E-328, E-329, B-203).

One register for every energy device HouseMind steers: the value before HouseMind, the value it
set, and the action that puts the old value back. HouseMind only gives back what is still its
own: is the value something else now, then somebody else changed it – HouseMind lets go and
says so (E-328). The register survives a restart, so a HouseMind that comes back gives back
what it no longer needs (E-329), and the integration in Home Assistant gets the same list for
its watchdog (B-204).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from housemind.core.model import State
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Layer

SETTING = "energy_own"


@dataclass(slots=True)
class Owned:
    entity_id: str
    before: str  # the state before HouseMind, as Home Assistant showed it
    set_to: str  # what HouseMind set
    action: str  # the action that puts ``before`` back
    data: dict[str, Any]
    skill: str
    at: str  # ISO time of HouseMind's first change


def restore_of(
    action: str, data: dict[str, Any], before: State
) -> tuple[str, dict[str, Any]] | None:
    """The action that undoes ``action`` – None for what cannot be undone (a button)."""
    domain = action.split(".", 1)[0]
    if action == "select.select_option":
        return action, {"option": before.state}
    if domain in ("switch", "input_boolean") and action.endswith(("turn_on", "turn_off")):
        return f"{domain}.turn_{'on' if before.state == 'on' else 'off'}", {}
    if action == "number.set_value":
        value = _number(before.state)
        return (action, {"value": value}) if value is not None else None
    if action == "water_heater.set_temperature":
        value = before.attributes.get("temperature")
        return (action, {"temperature": value}) if isinstance(value, int | float) else None
    return None


def set_value(action: str, data: dict[str, Any], before: State) -> str:
    """The state the entity shows once ``action`` is done."""
    if action == "select.select_option":
        return str(data.get("option", ""))
    if action.endswith("turn_on"):
        return "on"
    if action.endswith("turn_off"):
        return "off"
    if action == "number.set_value":
        return str(data.get("value", ""))
    if action == "water_heater.set_temperature":
        return str(data.get("temperature", ""))
    return before.state


def _number(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _same(a: str, b: str) -> bool:
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return abs(x - y) < 0.5
    return a == b


def _shows(owned: Owned, state: State) -> str:
    if owned.action == "water_heater.set_temperature":
        return str(state.attributes.get("temperature", ""))
    return state.state


@dataclass(slots=True)
class Ownership:
    db: Database
    items: dict[str, Owned] = field(default_factory=dict)
    # Changed by somebody else while it was HouseMind's: shown on Involved and Expert (E-328).
    others: dict[str, str] = field(default_factory=dict)  # entity_id -> ISO time
    _saved: str = ""

    async def load(self, states: dict[str, State], old: dict[str, Any] | None = None) -> None:
        raw = await self.db.get_setting(SETTING) or {}
        for entity_id, item in (raw.get("items") or {}).items():
            if isinstance(item, dict):
                try:
                    self.items[entity_id] = Owned(entity_id=entity_id, **item)
                except TypeError:
                    continue
        self.others = {str(k): str(v) for k, v in (raw.get("others") or {}).items()}
        self._saved = repr(self._dump())
        if old:
            self._from_before(states, old)

    def _from_before(self, states: dict[str, State], old: dict[str, Any]) -> None:
        """The register of the first energy build: the export limit it lowered (B-203 takes it
        over)."""
        export = old.get("export_limit")
        before = old.get("export_before")
        if isinstance(before, int | float) and export and export in states:
            self.items.setdefault(
                export,
                Owned(
                    export,
                    str(before),
                    states[export].state,
                    "number.set_value",
                    {"value": float(before)},
                    "energy_export",
                    "",
                ),
            )

    def _dump(self) -> dict[str, Any]:
        return {
            "items": {
                k: {
                    "before": v.before,
                    "set_to": v.set_to,
                    "action": v.action,
                    "data": v.data,
                    "skill": v.skill,
                    "at": v.at,
                }
                for k, v in self.items.items()
            },
            "others": self.others,
        }

    async def save(self) -> None:
        dump = self._dump()
        if repr(dump) != self._saved:
            self._saved = repr(dump)
            await self.db.set_setting(SETTING, dump)

    def owns(self, entity_id: str) -> bool:
        return entity_id in self.items

    def before(self, entity_id: str) -> str | None:
        owned = self.items.get(entity_id)
        return owned.before if owned else None

    def done(self, request: ActionRequest, before: State | None, now: datetime) -> None:
        """HouseMind changed an energy device: remember what it was, unless this put it back."""
        entity_id = request.entity_id
        owned = self.items.get(entity_id)
        if request.layer is Layer.RESTORE:
            self.items.pop(entity_id, None)
            return
        if owned is not None:
            owned.set_to = set_value(request.action, request.data, before or _empty(entity_id))
            if _same(owned.set_to, owned.before):
                self.items.pop(entity_id, None)  # back where it was: nothing left to give back
            return
        if before is None or not before.available:
            return
        undo = restore_of(request.action, request.data, before)
        if undo is None:
            return
        self.others.pop(entity_id, None)
        self.items[entity_id] = Owned(
            entity_id,
            before.state
            if request.action != "water_heater.set_temperature"
            else str(before.attributes.get("temperature", "")),
            set_value(request.action, request.data, before),
            undo[0],
            undo[1],
            request.skill or "",
            now.isoformat(),
        )

    def check(self, states: dict[str, State], now: datetime) -> list[str]:
        """Let go of what somebody else changed in the meantime (E-328)."""
        let_go = []
        for entity_id, owned in list(self.items.items()):
            state = states.get(entity_id)
            if state is None or not state.available or not owned.set_to:
                continue
            if not _same(_shows(owned, state), owned.set_to):
                let_go.append(entity_id)
                self.items.pop(entity_id)
                self.others[entity_id] = now.isoformat()
        return let_go

    def handed_back(self) -> None:
        """The watchdog in Home Assistant gave everything back (B-204): nothing is ours."""
        self.items.clear()

    def give_back(
        self,
        states: dict[str, State],
        now: datetime,
        why: str,
        only: set[str] | None = None,
        area_of: Any = None,
    ) -> list[ActionRequest]:
        """Requests that put back what HouseMind set (Layer.RESTORE: past pause and people)."""
        out = []
        for entity_id, owned in self.items.items():
            if only is not None and entity_id not in only:
                continue
            state = states.get(entity_id)
            if state is None or not state.available:
                continue
            area = area_of(entity_id) if area_of is not None else None
            out.append(
                ActionRequest(
                    Layer.RESTORE,
                    entity_id,
                    owned.action,
                    dict(owned.data),
                    reason="give_back",
                    skill=owned.skill or None,
                    area_id=area.id if area else None,
                    created_at=now,
                    facts={"why": why, "own_restore": True, "params": {}},
                )
            )
        return out

    def fallback(self) -> list[dict[str, Any]]:
        """For the watchdog in the integration (B-204): what to do if HouseMind goes silent."""
        return [{"entity_id": k, "action": v.action, "data": v.data} for k, v in self.items.items()]


def _empty(entity_id: str) -> State:
    return State(entity_id, "", {}, datetime.min)
