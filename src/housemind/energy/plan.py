"""The energy plan per quarter for the coming 36 hours (H10.2, B-34, B-37).

Explainable rules instead of a black box, fast enough for a small computer:

- boiler: extra warm in the cheapest two hours or on surplus sun, only when that pays after the
  warmth that leaks away, and only as warm as needed until the next cheap moment (E-374,
  E-375); the legionella cycle stays, unless the boiler does it itself (E-377)
- heat pump: preheat by the comfort band before an expensive peak, only when that pays after the
  warmth the house loses meanwhile (E-376)
- appliances that allow a remote start: in the cheapest two hours (E-64)
- negative prices: shift use first, then limit what is given back (E-70)
- peak limit: keep the quarter's power below the limit (E-71)
- the administrator's goal (E-66): lowest cost (default), own power first, least CO₂ (own sun
  first as well: Home Assistant has no forecast of the grid's CO₂), or a mix

The home battery and the car are not in it: HouseMind only watches them (E-363, B-250). A
battery that steers itself is not in the expected grid either – HouseMind does not know what it
will do.

The plan is pure: the same forecast gives the same plan. Each step carries a reason key and
values for the plain-language explanation (E-73).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from housemind.energy.heat import BOILER_KWH_PER_C, HOUSE_KWH_PER_C, kept, worth
from housemind.energy.model import EnergySettings, Slot
from housemind.energy.prices import QUARTER

Q_H = 0.25  # hours in a quarter
BOILER_W = 2000.0
PREHEAT_W = 600.0
APPLIANCE_W = 600.0
WINDOW = 8  # two hours of quarters
SPREAD = 0.10  # per kWh: the price difference from which shifting is worth it


@dataclass(frozen=True, slots=True)
class Start:
    """Where the plan starts from."""

    boiler_c: float | None = None
    boiler_base: float | None = None
    heat_base: float | None = None
    appliances_ready: tuple[str, ...] = ()
    month_peak_kw: float | None = None
    legionella_due: bool = False
    has_boiler: bool = False
    has_heat_pump: bool = False
    has_export_limit: bool = False
    capacity_minimum_kw: float | None = None
    # Storing warmth (design 36): per quarter of the slots, the degrees the boiler is expected
    # to use and to heat by itself; how fast extra warmth leaks away (share per hour); the
    # boiler's own limit; whether its temperature is known; whether learning is still young;
    # and the parts that rest because they did not pay (E-378).
    boiler_use: tuple[float, ...] = ()
    boiler_heat: tuple[float, ...] = ()
    boiler_loss: float = 0.25 / 30
    boiler_max_c: float | None = None
    boiler_sensed: bool = True
    house_loss: float = 0.05
    young: bool = True
    resting: frozenset[str] = frozenset()


@dataclass(slots=True)
class Step:
    device: str  # boiler | heat_pump | appliance | export
    action: str  # heat | preheat | start | limit
    start: datetime
    end: datetime
    why: str
    params: dict[str, Any] = field(default_factory=dict)
    value: float | None = None
    target: str | None = None  # entity for appliances
    saving: float = 0.0


@dataclass(slots=True)
class Plan:
    made: datetime
    slots: list[Slot]
    steps: list[Step]
    boiler_w: list[float]
    grid_w: list[float]

    def now(self, moment: datetime) -> dict[str, Step]:
        """The step of every device at this moment."""
        found: dict[str, Step] = {}
        for step in self.steps:
            if step.start <= moment < step.end:
                key = step.device if step.device != "appliance" else f"appliance:{step.target}"
                found.setdefault(key, step)
        return found


def _prices(slots: list[Slot]) -> tuple[list[float], list[float]]:
    known = [s.buy for s in slots if s.buy is not None]
    fallback = sorted(known)[len(known) // 2] if known else 0.25
    buy, sell = [], []
    last_buy, last_sell = fallback, fallback
    for s in slots:
        last_buy = s.buy if s.buy is not None else last_buy
        last_sell = s.sell if s.sell is not None else last_sell
        buy.append(last_buy)
        sell.append(last_sell)
    return buy, sell


def _effective(buy: list[float], sell: list[float], surplus_w: list[float]) -> list[float]:
    """What an extra kWh costs: the grid price, or – with sun to spare – what giving it back
    would have earned."""
    return [s if surplus_w[i] > 500 else buy[i] for i, s in enumerate(sell)]


SUN_W = 500.0  # surplus sun from which a quarter counts as "own power"
# Own sun first, whatever the price: a quarter without sun weighs this much more (€/kWh).
NO_SUN_WEIGHT = 1.0


def _goal_cost(goal: str, buy: list[float], sell: list[float], surplus: list[float]) -> list[float]:
    """The weight of using a kWh in each quarter, by the goal (E-66). Lowest cost: what it costs.
    Own power and least CO₂: own sun first, then the cheapest grid quarters. Mix: sun at half."""
    effective = _effective(buy, sell, surplus)
    if goal in ("own_power", "co2"):
        return [0.0 if surplus[i] > SUN_W else buy[i] + NO_SUN_WEIGHT for i in range(len(buy))]
    if goal == "mix":
        return [effective[i] * (0.5 if surplus[i] > SUN_W else 1.0) for i in range(len(buy))]
    return effective


def _blocks(flags: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, on in enumerate([*flags, False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((start, i))
            start = None
    return out


def _cheapest_window(cost: list[float], first: int, last: int, length: int) -> int | None:
    best, best_cost = None, None
    for i in range(first, max(first, last - length + 1)):
        avg = sum(cost[i : i + length]) / length
        if best_cost is None or avg < best_cost:
            best, best_cost = i, avg
    return best


def make_plan(slots: list[Slot], start: Start, settings: EnergySettings, made: datetime) -> Plan:
    n = len(slots)
    times = [s.start for s in slots]
    buy, sell = _prices(slots)
    house = [s.house_w for s in slots]
    solar = [s.solar_w for s in slots]
    surplus = [max(0.0, solar[i] - house[i]) for i in range(n)]
    cost = _goal_cost(settings.goal, buy, sell, surplus)
    steps: list[Step] = []
    boiler_w = [0.0] * n
    extra_w = [0.0] * n
    spread = SPREAD
    horizon_24 = min(n, 96)

    def at(i: int) -> datetime:
        return times[min(i, n - 1)] if i < n else times[-1] + QUARTER

    # -- boiler (E-76, E-374 – E-377) ------------------------------------------------------
    if start.has_boiler and start.boiler_base is not None:
        _boiler(steps, boiler_w, start, settings, buy, sell, surplus, cost, horizon_24, at)

    # -- expensive peaks (for the heat pump) -------------------------------------------------
    ranked = sorted(buy[:horizon_24]) or [0.0]
    top_quarter = ranked[int(len(ranked) * 0.75)] if len(ranked) > 3 else ranked[-1]
    # A peak is in the top quarter of the day *and* clearly above the middle price.
    peak_level = max(top_quarter, ranked[len(ranked) // 2] + spread)
    peaks = _blocks([buy[i] >= peak_level and i < horizon_24 for i in range(n)])

    # -- heat pump preheat (E-76, E-376) ---------------------------------------------------
    if start.has_heat_pump and start.heat_base is not None and "heat_pump" not in start.resting:
        for a, b in peaks:
            first = max(0, a - WINDOW)
            if a - first < 4:
                continue
            now_price = sum(buy[first:a]) / (a - first)
            later = sum(buy[a:b]) / (b - a)
            hours = ((a + b) / 2 - (first + a) / 2) / 4
            pays = worth(
                now_price,
                later,
                kept(start.house_loss, hours),
                settings.comfort_band_c * HOUSE_KWH_PER_C,
                start.young,
            )
            if pays.ok:
                for i in range(first, a):
                    extra_w[i] += PREHEAT_W
                steps.append(
                    Step(
                        "heat_pump",
                        "preheat",
                        at(first),
                        at(a),
                        "heat_pump_peak",
                        {
                            "start": at(first),
                            "end": at(a),
                            "peak": at(a),
                            "peak_end": at(b),
                            **pays.params(),
                        },
                        value=settings.comfort_band_c,
                        saving=pays.gain,
                    )
                )
                break  # one preheat per plan is enough

    # -- appliances (E-64) -------------------------------------------------------------------
    for entity in start.appliances_ready if "appliance" not in start.resting else ():
        window = _cheapest_window(cost, 0, min(n, 48), WINDOW)
        if window is None:
            continue
        for i in range(window, min(n, window + WINDOW)):
            extra_w[i] += APPLIANCE_W
        # What waiting brings: the same run now or at the cheapest moment (E-379).
        kwh = APPLIANCE_W * WINDOW * Q_H / 1000
        saving = (sum(buy[:WINDOW]) - sum(buy[window : window + WINDOW])) / WINDOW * kwh
        steps.append(
            Step(
                "appliance",
                "start",
                at(window),
                at(window + 1),
                "appliance_cheapest",
                {"start": at(window), "gain": round(saving, 2)},
                target=entity,
                saving=max(0.0, saving),
            )
        )

    net = [house[i] + boiler_w[i] + extra_w[i] - solar[i] for i in range(n)]

    # -- grid, peak limit (E-71) and giving back at a negative price (E-70) -------------------
    grid = list(net)
    limit_kw = settings.peak_limit_kw
    if limit_kw is None and start.capacity_minimum_kw is not None:
        limit_kw = max(start.capacity_minimum_kw, start.month_peak_kw or 0.0)
    if limit_kw is not None:
        limit_w = limit_kw * 1000
        for i in range(n):
            excess = grid[i] - limit_w
            if excess > 0 and boiler_w[i] > 0:
                cut = min(excess, boiler_w[i])
                boiler_w[i] -= cut
                grid[i] -= cut
    if start.has_export_limit and "export" not in start.resting:
        for a, b in _blocks([sell[i] < 0 and grid[i] < 0 for i in range(n)]):
            steps.append(
                Step(
                    "export",
                    "limit",
                    at(a),
                    at(b),
                    "negative_price",
                    {"start": at(a), "end": at(b)},
                    value=0.0,
                )
            )
    steps.sort(key=lambda s: s.start)
    return Plan(made, slots, steps, boiler_w, grid)


def _boiler(
    steps: list[Step],
    boiler_w: list[float],
    start: Start,
    settings: EnergySettings,
    buy: list[float],
    sell: list[float],
    surplus: list[float],
    cost: list[float],
    horizon: int,
    at: Any,
) -> None:
    """Extra warm water only when it pays after what leaks away, and only as warm as needed
    until the next cheap moment (E-374, E-375); legionella when it is due (E-377). Without the
    boiler's temperature nothing can be learned: then only sun to spare or a negative price
    (B-279)."""
    n = len(buy)
    base = float(start.boiler_base or 0.0)
    window = _cheapest_window(cost, 0, horizon, WINDOW)
    if window is None:
        return
    end = min(n, window + WINDOW)
    sun = all(surplus[i] > BOILER_W for i in range(window, end))
    negative = sum(buy[window:end]) / max(1, end - window) < 0
    now_price = sum((sell if sun else buy)[window:end]) / max(1, end - window)
    if sun and settings.goal in ("own_power", "co2"):
        now_price = 0.0  # E-66: the administrator chose own sun first, whatever it earns
    elif sun and settings.goal == "mix":
        now_price /= 2
    # When the warmth would otherwise have been bought: the boiler's own heating, else its use.
    ahead = range(end, min(n, end + 96))
    weights = [_at(start.boiler_heat, i) for i in ahead]
    if sum(weights) < 0.5:
        weights = [_at(start.boiler_use, i) for i in ahead]
    if sum(weights) < 0.5:
        weights = [1.0 for _ in ahead]
    total = sum(weights) or 1.0
    later = sum(w * buy[i] for w, i in zip(weights, ahead, strict=True)) / total
    middle = (window + end) / 2
    hours = sum(w * (i - middle) / 4 for w, i in zip(weights, ahead, strict=True)) / total
    share = kept(start.boiler_loss, hours)
    cap = settings.boiler_max_c
    if start.boiler_max_c is not None:
        cap = min(cap, start.boiler_max_c)
    need = sum(_at(start.boiler_use, i) for i in ahead) or 10.0  # K4: a day's use, roughly
    extra = max(0.0, min(cap, base + need * (1 + start.boiler_loss * hours)) - base)
    if negative:
        extra = max(0.0, cap - base)
    pays = worth(now_price, later, share, extra * BOILER_KWH_PER_C, start.young)
    allowed = pays.ok and extra >= 1.0 and "boiler" not in start.resting
    if not start.boiler_sensed:
        allowed = (sun or negative) and "boiler" not in start.resting
    if start.legionella_due:  # always reaches 60 °C, whatever pays (E-377)
        hot, why = max(60.0, base + extra if allowed else base), "legionella"
        hot = min(hot, max(cap, 60.0))
    elif allowed:
        hot = min(cap, base + extra)
        why = "negative_price" if negative else ("boiler_sun" if sun else "boiler_cheap")
    else:
        return
    hot = round(hot * 2) / 2
    rise = max(0.0, hot - (start.boiler_c or base))
    quarters = max(1, min(WINDOW, int(rise * BOILER_KWH_PER_C / (BOILER_W * Q_H / 1000) + 0.999)))
    for i in range(window, min(n, window + quarters)):
        boiler_w[i] = BOILER_W
    params: dict[str, Any] = {"start": at(window), "end": at(end), "temperature": hot}
    if why != "legionella":
        params |= pays.params()
    steps.append(
        Step(
            "boiler",
            "heat",
            at(window),
            at(end),
            why,
            params,
            value=hot,
            saving=pays.gain if why != "legionella" else 0.0,
        )
    )


def _at(values: tuple[float, ...], i: int) -> float:
    return values[i] if i < len(values) else 0.0
