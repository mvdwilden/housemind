"""Standen voor lampen (E-490 – E-496, design 73): four standen per lamp – Gedimd, Normaal,
Helder and Maximaal – each with the brightness that feels like that stand on this lamp, and a
colour as far as the lamp can.

The brain reckons in felt percentages everywhere. The four standen are fixed points on that
scale (15, 35, 60 and 100); each lamp turns a felt percentage into its own by going straight
between its standen (B-406). The start values are those points themselves, so a lamp nobody
set does exactly what it always did.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from housemind.core.model import HouseModel, State

SETTING = "lamp_stands"  # not "light_levels": that is when it is dark enough (E-268)
# How long each lamp waits before it goes off once its room is empty (E-575): its own, not
# with the standen, as lamps of the same type share standen but never a wait.
WAIT_SETTING = "lamp_off_wait"
WAIT_MAX = 30  # minutes, as the learned wait (E-444)
STANDS = ("dim", "normal", "bright", "full")
FELT = (15.0, 35.0, 60.0, 100.0)  # where the four standen sit on the felt scale
START = (15, 35, 60, 100)
START_COLOURS: tuple[dict[str, Any], ...] = (
    {"kelvin": 2200},  # Gedimd begins warm (E-490)
    {"rhythm": True},
    {"rhythm": True},
    {"rhythm": True},
)
# The colours a stand may have on a lamp that can show colours: Home Assistant's own colour
# names, so the safety gate only lets a word from this row through (B-407).
COLOURS = (
    "orange",
    "red",
    "hotpink",
    "mediumpurple",
    "royalblue",
    "turquoise",
    "limegreen",
    "gold",
)
WHITE = 4000  # safety: full and white (E-495)
KELVIN = (2200, 6500)  # what a lamp that does not say its range is taken to do
COLOUR_MODES = frozenset({"hs", "rgb", "rgbw", "rgbww", "xy"})
DIM_MODES = frozenset({"brightness", "white", "color_temp"}) | COLOUR_MODES
SOURCES = ("own", "group", "type", "start")


@dataclass(frozen=True, slots=True)
class Standen:
    """The standen of one lamp."""

    pct: tuple[int, ...] = START
    colour: tuple[dict[str, Any], ...] = START_COLOURS
    dim: str = "keep"  # a lamp that only goes on and off, at Gedimd: "keep" or "off" (E-492)
    rhythm: bool = True  # follows HouseMind's own day's rhythm (E-496)

    def to_json(self) -> dict[str, Any]:
        return {
            "pct": list(self.pct),
            "colour": [dict(c) for c in self.colour],
            "dim": self.dim,
            "rhythm": self.rhythm,
        }


def _colour(raw: Any, low: int, high: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("rhythm") is True:
        return {"rhythm": True}
    kelvin = raw.get("kelvin")
    if isinstance(kelvin, int | float) and not isinstance(kelvin, bool):
        return {"kelvin": int(max(low, min(high, round(kelvin))))}
    name = raw.get("name")
    if isinstance(name, str) and name in COLOURS:
        return {"name": name}
    return None


def from_json(raw: Any, kelvin: tuple[int, int] = KELVIN) -> Standen | None:
    """Standen from the screen or the database, checked: four percentages from 1 to 100, each
    higher than the one before, and colours this lamp can show. None when it does not fit."""
    if not isinstance(raw, dict):
        return None
    pct = raw.get("pct", list(START))
    if (
        not isinstance(pct, list)
        or len(pct) != len(STANDS)
        or not all(isinstance(p, int | float) and not isinstance(p, bool) for p in pct)
    ):
        return None
    whole = tuple(round(p) for p in pct)
    if whole[0] < 1 or whole[-1] > 100 or any(b <= a for a, b in pairwise(whole)):
        return None
    colours = raw.get("colour", [dict(c) for c in START_COLOURS])
    if not isinstance(colours, list) or len(colours) != len(STANDS):
        return None
    checked = [_colour(c, *kelvin) for c in colours]
    if any(c is None for c in checked):
        return None
    dim = raw.get("dim", "keep")
    if dim not in ("keep", "off"):
        return None
    return Standen(
        whole, tuple(c for c in checked if c is not None), dim, raw.get("rhythm") is not False
    )


def stand_of(felt: float) -> int | None:
    """The stand a felt percentage is, when it is one of the four exactly."""
    for index, point in enumerate(FELT):
        if abs(felt - point) < 0.5:
            return index
    return None


def nearest(felt: float) -> int:
    """The stand nearest a felt percentage (E-576); halfway goes to the softer one."""
    return min(range(len(FELT)), key=lambda index: (abs(felt - FELT[index]), index))


def between(felt: float) -> tuple[int | None, int | None]:
    """The standen a felt percentage lies between: (None, 0) below Gedimd, (i, i) on one."""
    exact = stand_of(felt)
    if exact is not None:
        return exact, exact
    if felt < FELT[0]:
        return None, 0
    for index in range(len(FELT) - 1):
        if FELT[index] < felt < FELT[index + 1]:
            return index, index + 1
    return len(FELT) - 1, len(FELT) - 1


def capability(modes: Collection[str] | None) -> str:
    """What a lamp can: ``colour``, ``white`` (warm to white), ``dim`` or ``onoff``."""
    found = set(modes or ())
    if found & COLOUR_MODES:
        return "colour"
    if "color_temp" in found:
        return "white"
    if found & DIM_MODES:
        return "dim"
    return "onoff"


def _line(points: tuple[float, ...], into: tuple[float, ...], value: float) -> float:
    """Straight between neighbouring points: ``value`` on ``points`` read on ``into``."""
    value = max(points[0], min(points[-1], value))
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        if value <= b or index == len(points) - 2:
            if b == a:
                return into[index + 1]
            return into[index] + (value - a) * (into[index + 1] - into[index]) / (b - a)
    return into[-1]


class LightLevels:
    """The standen of the lamps in this house, and the working out with them."""

    def __init__(
        self,
        model: HouseModel,
        own: Mapping[str, Standen] | None = None,
        waits: Mapping[str, int] | None = None,
    ) -> None:
        self.model = model
        self.own: dict[str, Standen] = dict(own or {})
        self.waits: dict[str, int] = dict(waits or {})  # lamp → minutes (E-575)

    # -- the database ---------------------------------------------------------------------

    def load(self, raw: Any) -> None:
        self.own = {}
        if not isinstance(raw, dict):
            return
        for entity_id, value in raw.items():
            found = from_json(value, (1, 100000))  # the lamp's own range is checked on saving
            if found is not None:
                self.own[str(entity_id)] = found

    def dump(self) -> dict[str, Any]:
        return {e: s.to_json() for e, s in sorted(self.own.items())}

    def load_waits(self, raw: Any) -> None:
        self.waits = {}
        if isinstance(raw, dict):
            for entity_id, value in raw.items():
                minutes = wait_from(value)
                if minutes is not None:
                    self.waits[str(entity_id)] = minutes

    def dump_waits(self) -> dict[str, int]:
        return dict(sorted(self.waits.items()))

    # -- how long a lamp waits before it goes off (E-575) ---------------------------------

    def off_wait(self, entity_id: str) -> int | None:
        """The minutes this lamp waits once its room is empty, when someone set them: its own,
        or those of the group that steers it. None: the room's learned wait counts."""
        found = self.waits.get(entity_id)
        if found is not None:
            return found
        group = self.model.steered_by.get(entity_id)
        if group is not None and group != entity_id:
            return self.waits.get(group)
        return None

    # -- what a lamp is -------------------------------------------------------------------

    def _modes(self, entity_id: str, states: Mapping[str, State] | None = None) -> list[str]:
        entity = self.model.entities.get(entity_id)
        modes = entity.capabilities.get("supported_color_modes") if entity else None
        if not modes and states is not None and (state := states.get(entity_id)) is not None:
            modes = state.attributes.get("supported_color_modes")
        return [str(m) for m in modes or ()]

    def can(self, entity_id: str) -> str:
        return capability(self._modes(entity_id))

    def kelvin(self, entity_id: str) -> tuple[int, int]:
        """The warmest and the whitest this lamp can show."""
        entity = self.model.entities.get(entity_id)
        caps = entity.capabilities if entity else {}
        low, high = caps.get("min_color_temp_kelvin"), caps.get("max_color_temp_kelvin")
        if isinstance(low, int | float) and isinstance(high, int | float) and low < high:
            return int(low), int(high)
        return KELVIN

    def type_of(self, entity_id: str) -> tuple[str, str, str] | None:
        """The same kind of lamp: its make and model in Home Assistant, and what it can."""
        entity = self.model.entities.get(entity_id)
        if entity is None or entity.device_id is None:
            return None
        device = self.model.devices.get(entity.device_id)
        if device is None or not (device.manufacturer or device.model):
            return None
        return (device.manufacturer or "", device.model or "", self.can(entity_id))

    # -- which standen count --------------------------------------------------------------

    def of(self, entity_id: str) -> tuple[Standen, str, str | None]:
        """The standen of a lamp, where they come from, and the lamp they come from: its own,
        the group that steers it, a lamp of the same type, or the start (B-406)."""
        found = self.own.get(entity_id)
        if found is not None:
            return found, "own", entity_id
        group = self.model.steered_by.get(entity_id)
        if group is not None and group != entity_id and (found := self.own.get(group)):
            return found, "group", group
        kind = self.type_of(entity_id)
        if kind is not None:
            for other in sorted(self.own):
                if other != entity_id and self.type_of(other) == kind:
                    return self.own[other], "type", other
        return Standen(), "start", None

    def standen(self, entity_id: str) -> Standen:
        return self.of(entity_id)[0]

    def has_stands(self, entity_id: str) -> bool:
        """Someone gave this lamp standen – its own, its group's or its type's (E-576): then
        every rule takes one of the four, not a percentage of its own."""
        return self.of(entity_id)[1] != "start"

    def follows_rhythm(self, entity_id: str) -> bool:
        return self.standen(entity_id).rhythm

    def dim_off(self, entity_id: str) -> bool:
        return self.standen(entity_id).dim == "off"

    # -- the working out ------------------------------------------------------------------

    def real(self, entity_id: str, felt: float) -> int:
        """A felt percentage on this lamp."""
        pct = self.standen(entity_id).pct
        if pct == START:
            return max(1, min(100, round(felt)))
        value = _line((0.0, *FELT), (0.0, *(float(p) for p in pct)), float(felt))
        return max(1, min(100, round(value)))

    def felt(self, entity_id: str, real: float) -> float:
        """What this lamp's own percentage feels like on the scale of the brain."""
        pct = self.standen(entity_id).pct
        if pct == START:
            return float(real)
        return round(_line((0.0, *(float(p) for p in pct)), (0.0, *FELT), float(real)), 1)

    def colour_data(
        self,
        entity_id: str,
        stand: int,
        allowed: Collection[str],
        rhythm_kelvin: float | None = None,
        fallback_kelvin: float | None = None,
    ) -> dict[str, Any]:
        """The colour of a stand on this lamp, as far as it may be told (B-407): a fixed colour
        first, else the day's rhythm, else what the rule says."""
        colour = self.standen(entity_id).colour[stand]
        can = self.can(entity_id)
        if "name" in colour and can == "colour" and "color_name" in allowed:
            return {"color_name": colour["name"]}
        kelvin: float | None
        if "kelvin" in colour:
            kelvin = float(colour["kelvin"])
        else:
            kelvin = rhythm_kelvin if rhythm_kelvin is not None else fallback_kelvin
        if kelvin is None or "color_temp_kelvin" not in allowed:
            return {}
        low, high = self.kelvin(entity_id)
        return {"color_temp_kelvin": int(max(low, min(high, round(kelvin))))}

    def stand_data(
        self,
        entity_id: str,
        stand: int,
        allowed: Collection[str],
        rhythm_kelvin: float | None = None,
        fallback_kelvin: float | None = None,
    ) -> dict[str, Any]:
        """What a lamp is told for one of its standen: its own brightness and colour."""
        data: dict[str, Any] = {}
        if "brightness_pct" in allowed:
            data["brightness_pct"] = self.real(entity_id, FELT[stand])
        data.update(self.colour_data(entity_id, stand, allowed, rhythm_kelvin, fallback_kelvin))
        return data


def colour_now(attributes: Mapping[str, Any], allowed: Collection[str]) -> dict[str, Any]:
    """The colour a lamp shows now, as far as it may be told it again (E-577): its kelvin in
    colour temperature mode, else its hue and saturation. Empty when it cannot be given back."""
    mode = attributes.get("color_mode")
    hs = attributes.get("hs_color")
    kelvin = attributes.get("color_temp_kelvin")
    if (
        mode != "color_temp"
        and "hs_color" in allowed
        and isinstance(hs, list | tuple)
        and len(hs) == 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in hs)
    ):
        return {"hs_color": [round(float(hs[0]), 1), round(float(hs[1]), 1)]}
    if (
        "color_temp_kelvin" in allowed
        and isinstance(kelvin, int | float)
        and not isinstance(kelvin, bool)
    ):
        return {"color_temp_kelvin": int(kelvin)}
    return {}


def wait_from(raw: Any) -> int | None:
    """Whole minutes from 0 to WAIT_MAX, from the screen or the database; None otherwise."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    minutes = round(raw)
    return minutes if 0 <= minutes <= WAIT_MAX and abs(raw - minutes) < 1e-9 else None


def white(entity_id: str, levels: LightLevels | None, allowed: Collection[str]) -> dict[str, Any]:
    """Full and white for safety (E-495): white only where the lamp can show it."""
    data: dict[str, Any] = {}
    if "brightness_pct" in allowed:
        data["brightness_pct"] = 100
    if "color_temp_kelvin" in allowed:
        low, high = levels.kelvin(entity_id) if levels is not None else KELVIN
        data["color_temp_kelvin"] = max(low, min(high, WHITE))
    return data
