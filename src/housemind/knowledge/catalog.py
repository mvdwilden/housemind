"""The device catalogue: what HouseMind knows about generic device kinds (E-54).

A device kind says how to recognise an entity (HA domain, device class, features, colour
modes), how risky it is (safety class, maximum step), who decides about its use (E-62),
which actions with which values are allowed (allow-list, E-109), switching limits (E-120)
and how long "people win" after manual control (B-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class SafetyClass(StrEnum):
    """Safety classes from the design (H4, E-58), from least to most strict."""

    FREE = "free"
    CAREFUL = "careful"
    SAFE_DIRECTION = "safe_direction"
    PROPOSE_ONLY = "propose_only"
    NEVER = "never"


class Step(IntEnum):
    """Autonomy steps (E-12). Ordered, so ``min`` gives the strictest."""

    OBSERVE = 0
    PROPOSE = 1
    AUTONOMOUS = 2


class Initiative(StrEnum):
    """Who decides about the use of a device (E-62)."""

    HOUSE = "house"
    PERSON = "person"
    SHARED = "shared"


_CLASS_ORDER = [
    SafetyClass.FREE,
    SafetyClass.CAREFUL,
    SafetyClass.SAFE_DIRECTION,
    SafetyClass.PROPOSE_ONLY,
    SafetyClass.NEVER,
]


def stricter(a: SafetyClass, b: SafetyClass) -> SafetyClass:
    """The stricter of two safety classes."""
    return a if _CLASS_ORDER.index(a) >= _CLASS_ORDER.index(b) else b


def is_looser(candidate: SafetyClass, reference: SafetyClass) -> bool:
    return _CLASS_ORDER.index(candidate) < _CLASS_ORDER.index(reference)


@dataclass(frozen=True, slots=True)
class ValueRange:
    minimum: float
    maximum: float

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True, slots=True)
class ValueChoices:
    options: frozenset[str]

    def contains(self, value: str) -> bool:
        return value in self.options


@dataclass(frozen=True, slots=True)
class ValueText:
    """Any short text, e.g. one of a device's own options (a battery mode). Home Assistant
    itself rejects an option the device does not have."""

    max_length: int = 64

    def contains(self, value: str) -> bool:
        return 0 < len(value) <= self.max_length


@dataclass(frozen=True, slots=True)
class ValuePair:
    """Two numbers, each in its own range: a colour as hue and saturation (``hs_color``), to
    give a lamp back the colour it had before a dim warning (E-577)."""

    first: ValueRange
    second: ValueRange

    def contains(self, value: Any) -> bool:
        if not isinstance(value, list | tuple) or len(value) != 2:
            return False
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in value):
            return False
        return self.first.contains(float(value[0])) and self.second.contains(float(value[1]))


ValueRule = ValueRange | ValueChoices | ValueText | ValuePair


@dataclass(frozen=True, slots=True)
class AllowedAction:
    """One allowed Home Assistant action for a device kind, e.g. ``light.turn_on``."""

    action: str  # "domain.service"
    values: dict[str, ValueRule] = field(default_factory=dict)
    max_step: Step = Step.AUTONOMOUS
    # A fixed emergency rule may do this itself, whatever the kind's own step (E-542: music
    # quiet when the alarm goes off). Only what the catalogue marks; never loosened by a layer.
    emergency: bool = False

    def accepts(self, data: dict[str, Any]) -> str | None:
        """Return ``None`` when ``data`` fits this action, else a reason code."""
        for key, value in data.items():
            rule = self.values.get(key)
            if rule is None:
                return f"value_not_allowed:{key}"
            if isinstance(rule, ValueRange):
                if not isinstance(value, int | float) or isinstance(value, bool):
                    return f"value_not_numeric:{key}"
                if not rule.contains(float(value)):
                    return f"value_out_of_range:{key}"
            elif isinstance(rule, ValuePair):
                if not rule.contains(value):
                    return f"value_out_of_range:{key}"
            elif not isinstance(value, str) or not rule.contains(value):
                return f"value_not_an_option:{key}"
        return None


@dataclass(frozen=True, slots=True)
class Match:
    """How to recognise a device kind from Home Assistant data."""

    domain: str
    device_classes: tuple[str, ...] = ()
    color_modes_any: tuple[str, ...] = ()
    required_features: int = 0
    role: str | None = None  # a role HouseMind assigns, e.g. "light" for a switch
    platforms: tuple[str, ...] = ()  # the integration that gives it

    def specificity(self) -> int:
        score = 1
        if self.platforms:
            score += 4
        if self.device_classes:
            score += 4
        if self.color_modes_any:
            score += 2
        if self.required_features:
            score += 1
        if self.role:
            score += 4
        return score


@dataclass(frozen=True, slots=True)
class DeviceKind:
    key: str
    match: Match
    safety_class: SafetyClass
    initiative: Initiative
    max_step: Step
    actions: tuple[AllowedAction, ...] = ()
    min_interval_s: float = 0.0
    max_changes_per_day: int | None = None
    people_win: str = "until_situation_changes"
    people_win_max_hours: float = 4.0
    skills: tuple[str, ...] = ()
    generic: bool = False  # the safe fallback entry for a whole domain (E-156)
    experimental: bool = False
    # Shipped with HouseMind; a kind a later layer added never wins a tie from it (QA 2).
    shipped: bool = True

    def allowed(self, action: str) -> AllowedAction | None:
        for allowed in self.actions:
            if allowed.action == action:
                return allowed
        return None


@dataclass(slots=True)
class Catalog:
    kinds: dict[str, DeviceKind] = field(default_factory=dict)

    def add(self, kind: DeviceKind) -> None:
        self.kinds[kind.key] = kind

    def for_domain(self, domain: str) -> list[DeviceKind]:
        return [k for k in self.kinds.values() if k.match.domain == domain]

    def generic_for(self, domain: str) -> DeviceKind | None:
        for kind in self.kinds.values():
            if kind.generic and kind.match.domain == domain:
                return kind
        return None
