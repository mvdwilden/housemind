"""Shared energy types: the administrator's settings and one forecast quarter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

# The bounds the screen keeps the administrator's numbers in (E-66); a settings file gets the
# same (QA 2, M-2). The home battery and the car have none: HouseMind does not steer them (E-363).
BOUNDS: dict[str, tuple[float, float]] = {}
CHOICES: dict[str, tuple[Any, ...]] = {
    "goal": ("cost", "own_power", "co2", "mix"),
    "price_mode": ("auto", "market", "all_in"),
}


@dataclass(slots=True)
class EnergySettings:
    """Chosen by an administrator (E-66); sensible defaults. A value an earlier version saved
    for the home battery or the car is skipped when read (E-363)."""

    goal: str = "cost"  # cost | own_power | co2 | mix
    peak_limit_kw: float | None = None
    price_mode: str = "auto"  # auto | market | all_in
    comfort_band_c: float = 1.0
    boiler_max_c: float = 60.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> EnergySettings:
        known = {k: v for k, v in (raw or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def within(cls, raw: Any, base: EnergySettings) -> EnergySettings:
        """From a settings file (QA 2, M-2): every value within the same bounds as on the
        screen, else the house's own."""
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = {}
        for key, (low, high) in BOUNDS.items():
            value = raw.get(key)
            if (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and low <= value <= high
            ):
                out[key] = float(value)
        for key, allowed in CHOICES.items():
            if raw.get(key) in allowed:
                out[key] = raw[key]
        peak = raw.get("peak_limit_kw")
        if peak is None or (isinstance(peak, int | float) and 1 <= peak <= 50):
            out["peak_limit_kw"] = None if peak is None else float(peak)
        return replace(base, **out)


@dataclass(frozen=True, slots=True)
class Slot:
    """What HouseMind expects for one quarter."""

    start: datetime
    buy: float | None  # what a kWh from the grid costs (all-in)
    sell: float | None  # what a kWh given back is worth
    solar_w: float
    house_w: float
