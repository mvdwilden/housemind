"""How far the solar forecast was off (B-225, E-346): the margin Expert sees around the sun.

Home Assistant's solar forecast gives one number per hour, never a range. So HouseMind keeps,
for every hour with sun of the last 14 days, what was forecast and what was measured. The margin
is the 10th and 90th percentile of measured ÷ forecast. Without enough hours there is no margin:
nothing is ever guessed. It stays in the App (E-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.memory.database import Database

KEEP = timedelta(days=14)
MIN_HOURS = 20  # fewer measured hours with sun: no margin
MIN_FORECAST_WH = 200.0  # a dark hour says nothing about how far off the forecast is


@dataclass(slots=True)
class SolarMargin:
    db: Database
    # Per hour: what was forecast and what was measured, in Wh.
    hours: dict[str, tuple[float, float]] = field(default_factory=dict)
    _hour: datetime | None = None
    _measured_wh: float = 0.0

    async def load(self) -> None:
        raw = await self.db.get_setting("solar_margin", {}) or {}
        self.hours = {str(k): (float(v[0]), float(v[1])) for k, v in raw.items()}

    async def quarter(
        self, start: datetime, solar_kwh: float, forecast: dict[datetime, float]
    ) -> None:
        """A measured quarter; when its hour is complete, keep it beside the forecast."""
        hour = start.replace(minute=0, second=0, microsecond=0)
        if self._hour != hour:
            self._hour, self._measured_wh = hour, 0.0
        self._measured_wh += solar_kwh * 1000
        if start.minute < 45:
            return
        planned = forecast.get(hour)
        if planned is None or planned < MIN_FORECAST_WH:
            return
        self.hours[hour.isoformat()] = (planned, self._measured_wh)
        oldest = (hour - KEEP).isoformat()
        self.hours = {k: v for k, v in self.hours.items() if k > oldest}
        await self.db.set_setting("solar_margin", self.hours)

    def band(self) -> tuple[float, float] | None:
        """(low, high) as factors on the forecast, or None without enough hours."""
        ratios = sorted(measured / planned for planned, measured in self.hours.values())
        if len(ratios) < MIN_HOURS:
            return None

        def at(share: float) -> float:
            return ratios[min(len(ratios) - 1, int(share * len(ratios)))]

        return round(at(0.1), 2), round(at(0.9), 2)
