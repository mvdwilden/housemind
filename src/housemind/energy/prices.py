"""Prices per quarter of an hour from a price entity that is already in Home Assistant (E-72).

Price integrations put today's and tomorrow's prices in attributes, as lists of items with a
start time and a value, each with their own key names and units. HouseMind reads any such list
generically (no brands) and normalises to currency per kWh, per quarter of an hour.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from housemind.core.model import State

QUARTER = timedelta(minutes=15)
START_KEYS = ("start", "start_time", "startsAt", "starts_at", "from", "time", "datetime", "begin")
END_KEYS = ("end", "end_time", "endsAt", "ends_at", "till", "until", "to")
VALUE_KEYS = (
    "value",
    "price",
    "total",
    "price_per_kwh",
    "electricity_price",
    "marketPrice",
    "market_price",
    "energy_price",
)


@dataclass(frozen=True, slots=True)
class PriceSlot:
    start: datetime
    end: datetime
    price: float  # currency per kWh


def unit_factor(unit: str | None) -> float:
    """Factor from the entity's unit to currency per kWh (ct/kWh, €/MWh …)."""
    u = (unit or "").lower().replace(" ", "")
    factor = 1.0
    if "mwh" in u:
        factor = 1 / 1000
    elif "kwh" not in u and u.endswith("/wh"):
        factor = 1000.0
    if u.startswith(("ct", "c/", "cent", "¢", "p/")):
        factor /= 100
    return factor


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def parse_series(state: State | None) -> list[PriceSlot]:
    """All price slots found in the attributes of a price entity, in time order."""
    if state is None:
        return []
    factor = unit_factor(state.attributes.get("unit_of_measurement"))
    found: dict[datetime, tuple[datetime | None, float]] = {}
    for value in state.attributes.values():
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            start = _time(_first(item, START_KEYS))
            price = _first(item, VALUE_KEYS)
            if start is None or not isinstance(price, int | float) or isinstance(price, bool):
                continue
            found[start] = (_time(_first(item, END_KEYS)), float(price) * factor)
    if not found:
        return []
    starts = sorted(found)
    gaps = sorted(b - a for a, b in itertools.pairwise(starts) if b > a)
    step = gaps[len(gaps) // 2] if gaps else timedelta(hours=1)
    slots = []
    for i, start in enumerate(starts):
        end, price = found[start]
        if end is None or end <= start:
            end = starts[i + 1] if i + 1 < len(starts) else start + step
        slots.append(PriceSlot(start, end, price))
    return slots


def current_price(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state) * unit_factor(state.attributes.get("unit_of_measurement"))
    except ValueError:
        return None


def quarter_floor(moment: datetime) -> datetime:
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)


def per_quarter(
    slots: list[PriceSlot], start: datetime, quarters: int, fallback: float | None
) -> list[float | None]:
    """A price for every quarter from ``start``; unknown quarters use ``fallback``."""
    out: list[float | None] = []
    index = 0
    for q in range(quarters):
        moment = start + q * QUARTER
        while index < len(slots) and slots[index].end <= moment:
            index += 1
        if index < len(slots) and slots[index].start <= moment < slots[index].end:
            out.append(slots[index].price)
        else:
            out.append(fallback)
    return out


# -- what is in the price (E-304, B-153) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceWords:
    """From the knowledge files: the attributes and name words that say what a price holds."""

    extra_costs: tuple[str, ...] = ()
    vat: tuple[str, ...] = ()
    names: dict[str, tuple[str, ...]] = field(default_factory=dict)


def load_price_words(directories: Iterable[Path]) -> PriceWords:
    extra: list[str] = []
    vat: list[str] = []
    names: dict[str, list[str]] = {}
    for directory in directories:
        path = directory / "price_basis.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        extra += [str(k) for k in raw.get("extra_costs") or []]
        vat += [str(k) for k in raw.get("vat") or []]
        for kind, words in (raw.get("names") or {}).items():
            names.setdefault(str(kind), []).extend(str(w).lower() for w in words or [])
    return PriceWords(tuple(extra), tuple(vat), {k: tuple(v) for k, v in names.items()})


@dataclass(frozen=True, slots=True)
class PriceBasis:
    """What the integration says is in its price. ``kind``: "all_in" (energy tax and VAT in it),
    "market" (the bare market price), "no_extra" (nothing added to the market price; about VAT
    it says nothing unless ``vat`` is set), "extra" (costs were added), or "unknown"."""

    kind: str = "unknown"
    vat: bool | None = None


def _named(text: str, words: tuple[str, ...]) -> bool:
    return any(word and word in text for word in words)


def price_basis(state: State | None, words: PriceWords) -> PriceBasis:
    """Only what the price entity states – in its attributes or in its own name."""
    if state is None:
        return PriceBasis()
    attrs = state.attributes
    vat = next((attrs[k] for k in words.vat if isinstance(attrs.get(k), bool)), None)
    name = f"{attrs.get('friendly_name') or ''} {state.entity_id}".lower().replace("_", " ")
    if _named(name, words.names.get("all_in", ())):
        return PriceBasis("all_in", True if vat is None else vat)
    if _named(name, words.names.get("market", ())):
        return PriceBasis("market", vat)
    extra = next(
        (
            attrs[k]
            for k in words.extra_costs
            if isinstance(attrs.get(k), int | float) and not isinstance(attrs.get(k), bool)
        ),
        None,
    )
    if extra is not None:
        return PriceBasis("extra" if extra > 0 else "no_extra", vat)
    return PriceBasis("unknown", vat)
