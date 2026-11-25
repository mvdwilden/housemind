"""Tips without steering (E-364, E-365, B-254): what the prices, the sun and the meters say.

HouseMind does not steer the home battery or the car (E-363), so it says what a person could do:
when charging the car is cheapest, when there is sun to spare, when a negative price comes, and
what stands out about the battery now. At most three, on the Energy tab; never a notification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from housemind.energy.model import Slot
from housemind.energy.prices import QUARTER

BLOCK = timedelta(hours=3)  # K1: the charging tip is one block of three hours
SPARE_W = 1500.0  # sun to spare from which a quarter counts
SPARE_FOR = timedelta(hours=1)  # at least this long
AHEAD = timedelta(hours=12)  # sun and negative prices: the coming twelve hours
CAR_AHEAD = timedelta(hours=24)
FLOW_W = 100.0  # the battery and the grid: more than this counts as flowing


@dataclass(frozen=True, slots=True)
class Tip:
    key: str  # cheap_car, sun_spare, negative, battery_grid_dear, battery_export_negative
    start: datetime | None = None
    end: datetime | None = None
    values: dict[str, Any] = field(default_factory=dict)


def cheapest_block(
    slots: list[Slot], start: datetime, end: datetime, length: timedelta = BLOCK
) -> tuple[datetime, datetime, float] | None:
    """The cheapest unbroken block with known prices between ``start`` and ``end``: its start,
    its end and its average price. Shorter than ``length`` when less time is left."""
    known = [
        s for s in slots if s.buy is not None and start <= s.start and s.start + QUARTER <= end
    ]
    if not known:
        return None
    count = max(1, min(int(length / QUARTER), len(known)))
    best: tuple[float, int] | None = None
    for i in range(len(known) - count + 1):
        window = known[i : i + count]
        if window[-1].start - window[0].start != (count - 1) * QUARTER:
            continue  # a gap in the prices
        average = sum(s.buy for s in window if s.buy is not None) / count
        if best is None or average < best[0] - 1e-9:
            best = (average, i)
    if best is None:
        return None
    first = known[best[1]]
    return first.start, first.start + count * QUARTER, best[0]


def _blocks(slots: list[Slot], test: Any) -> list[list[Slot]]:
    out: list[list[Slot]] = []
    for slot in slots:
        if test(slot):
            if out and out[-1][-1].start + QUARTER == slot.start:
                out[-1].append(slot)
            else:
                out.append([slot])
    return out


def tips(
    slots: list[Slot],
    now: datetime,
    has_car: bool,
    battery_w: float | None,
    grid_w: float | None,
) -> list[Tip]:
    """What stands out first, then the tips for the coming hours."""
    found: list[Tip] = []
    current = next((s for s in slots if s.start <= now < s.start + QUARTER), None)
    price = current.buy if current is not None else None
    today = sorted(s.buy for s in slots[:96] if s.buy is not None)
    if price is not None and battery_w is not None and grid_w is not None and today:
        dear = today[int(len(today) * 0.75)] if len(today) > 3 else today[-1]
        if battery_w > FLOW_W and grid_w > FLOW_W and price >= dear and price > 0:
            found.append(Tip("battery_grid_dear", values={"price": price}))
        elif battery_w < -FLOW_W and grid_w < -FLOW_W and price < 0:
            found.append(Tip("battery_export_negative", values={"price": price}))
    if has_car:
        block = cheapest_block(slots, now, now + CAR_AHEAD)
        if block is not None:
            found.append(Tip("cheap_car", block[0], block[1], {"price": block[2]}))
    ahead = [s for s in slots if now <= s.start < now + AHEAD]
    sunny = [
        b
        for b in _blocks(ahead, lambda s: s.solar_w - s.house_w >= SPARE_W)
        if len(b) * QUARTER >= SPARE_FOR
    ]
    if sunny:
        found.append(Tip("sun_spare", sunny[0][0].start, sunny[0][-1].start + QUARTER))
    negative = _blocks(ahead, lambda s: s.buy is not None and s.buy < 0)
    if negative:
        lowest = min(s.buy for s in negative[0] if s.buy is not None)
        block_start, block_end = negative[0][0].start, negative[0][-1].start + QUARTER
        found.append(Tip("negative", block_start, block_end, {"price": lowest}))
    return found[:3]
