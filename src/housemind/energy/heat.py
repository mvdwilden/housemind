"""Warmth that is stored, and whether storing it pays (design 36, B-268, B-269, B-274, B-279).

Extra warmth bought now – a hotter boiler, a preheated house – is only worth it when that warmth
would have cost more later, after what leaks away meanwhile (E-374, E-376). The rule needs no
kWh, only proportions: the price now, the price at the moment the warmth would otherwise have
been bought, and the share that is still there by then.

What HouseMind learns, from what Home Assistant already gives:

- the boiler's temperature per quarter: how many degrees are used (showers, taps) per kind of day
  and quarter, how fast it cools down when nobody uses it, and when it heats by itself;
- a room's temperature when the heat pump rests above the wish: how fast the house cools down
  for every degree it is warmer inside than outside.

Until a week is learned, safe standard values (K4) and a larger margin are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from housemind.memory.database import Database

SETTING = "heat_model"
# K1: a block is only worth it with at least € 0.02 and 10 % more back than it costs.
MIN_GAIN = 0.02
MIN_RATIO = 1.10
YOUNG_RATIO = 1.25  # less than LEARN_DAYS learned: a larger margin
LEARN_DAYS = 7
# K4: until learned – 200 litres (0.23 kWh per °C), 2 kW, 0.25 °C per hour at 50 °C, and an
# average house (three kWh of warmth per °C, a heat pump giving three times its electricity,
# losing about 5 % of the extra warmth per hour).
BOILER_KWH_PER_C = 0.23
BOILER_LOSS = 0.25 / 30  # per hour, for each °C above the room (20 °C)
HOUSE_KWH_PER_C = 1.0  # electricity for one °C warmer inside
HOUSE_LOSS = 0.05  # per hour: the share of the extra warmth that leaks away
ROOM_C = 20.0
ALPHA = 0.1


def kept(loss_per_hour: float, hours: float) -> float:
    """The share of extra warmth still there after ``hours``."""
    return math.exp(-max(0.0, loss_per_hour) * max(0.0, hours))


@dataclass(frozen=True, slots=True)
class Worth:
    ok: bool
    gain: float  # € for the block, after the loss
    now: float  # € per kWh now (the price, or what giving back would have earned)
    later: float  # € per kWh when it would otherwise have been bought
    kept: float  # share still there by then

    def params(self) -> dict[str, Any]:
        return {
            "gain": round(self.gain, 2),
            "now_price": round(self.now, 3),
            "later_price": round(self.later, 3),
            "kept_pct": round(self.kept * 100),
        }


def worth(now: float, later: float, kept_share: float, kwh: float, young: bool = False) -> Worth:
    """The rule (B-268): it pays when what comes back later beats what it costs now by the
    margin (K1). A negative price always pays."""
    back = later * kept_share
    gain = (back - now) * kwh
    if now < 0:
        return Worth(True, gain, now, later, kept_share)
    ratio = YOUNG_RATIO if young else MIN_RATIO
    ok = gain >= MIN_GAIN and back >= now * ratio
    return Worth(ok, gain, now, later, kept_share)


@dataclass(slots=True)
class HeatModel:
    """What HouseMind learned about storing warmth in this house (B-269, B-274)."""

    db: Database
    boiler_loss: float = BOILER_LOSS
    boiler_loss_n: int = 0
    house_loss: float = HOUSE_LOSS
    house_loss_n: int = 0
    first: str = ""  # when learning began
    _boiler_last: tuple[datetime, float] | None = None
    _room_last: tuple[datetime, float, float] | None = None  # time, inside, wish
    _saved: str = field(default="")

    async def load(self) -> None:
        raw = await self.db.get_setting(SETTING) or {}
        for key in ("boiler_loss", "house_loss"):
            if isinstance(raw.get(key), int | float):
                setattr(self, key, float(raw[key]))
        for key in ("boiler_loss_n", "house_loss_n"):
            if isinstance(raw.get(key), int):
                setattr(self, key, int(raw[key]))
        self.first = str(raw.get("first") or "")
        self._saved = repr(self._dump())

    def _dump(self) -> dict[str, Any]:
        keys = ("boiler_loss", "boiler_loss_n", "house_loss", "house_loss_n", "first")
        return {k: getattr(self, k) for k in keys}

    async def save(self) -> None:
        dump = self._dump()
        if repr(dump) != self._saved:
            self._saved = repr(dump)
            await self.db.set_setting(SETTING, dump)

    def young(self, now: datetime) -> bool:
        """Less than a week learned: standard values and a larger margin (K4)."""
        if not self.first:
            return True
        return now - datetime.fromisoformat(self.first) < timedelta(days=LEARN_DAYS)

    def boiler_quarter(
        self, now: datetime, temp: float | None, heating_ours: bool, quiet: bool
    ) -> tuple[float, float] | None:
        """A quarter of the boiler: (degrees used, degrees it heated by itself), or None when
        there is nothing to learn. ``quiet``: a night quarter, nobody uses water."""
        if temp is None:
            self._boiler_last = None
            return None
        if not self.first:
            self.first = now.isoformat()
        last, self._boiler_last = self._boiler_last, (now, temp)
        if last is None or not timedelta(minutes=10) <= now - last[0] <= timedelta(minutes=20):
            return None
        hours = (now - last[0]).total_seconds() / 3600
        delta = temp - last[1]
        leak = self.boiler_loss * max(0.0, last[1] - ROOM_C) * hours
        if delta > 0.2:
            return 0.0, (0.0 if heating_ours else delta + leak)
        if quiet and -1.0 < delta <= 0 and last[1] > ROOM_C + 5:
            rate = -delta / hours / (last[1] - ROOM_C)
            self.boiler_loss += max(ALPHA, 1 / (self.boiler_loss_n + 2)) * (rate - self.boiler_loss)
            self.boiler_loss_n += 1
        return max(0.0, -delta - leak), 0.0

    def room_quarter(
        self, now: datetime, inside: float | None, outside: float | None, wish: float | None
    ) -> None:
        """A quarter of the room with the heat pump: while it is warmer than wished, the heat
        pump rests and the house cools down – that rate is the loss of extra warmth (B-274)."""
        if inside is None or outside is None or wish is None:
            self._room_last = None
            return
        if not self.first:
            self.first = now.isoformat()
        last, self._room_last = self._room_last, (now, inside, wish)
        if last is None or not timedelta(minutes=10) <= now - last[0] <= timedelta(minutes=20):
            return
        hours = (now - last[0]).total_seconds() / 3600
        above = last[1] - last[2]
        gap = last[1] - outside
        if above > 0.3 and gap > 3 and inside < last[1]:
            # The extra warmth above the wish leaks away at this rate (share per hour).
            rate = (last[1] - inside) / hours / gap
            self.house_loss += max(ALPHA, 1 / (self.house_loss_n + 2)) * (rate - self.house_loss)
            self.house_loss_n += 1
