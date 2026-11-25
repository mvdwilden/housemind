"""The energy meter: what the house took, gave back, made and paid per quarter and per day.

Counters come from the meters in HA's energy settings (kWh, always increasing). Without
counters the live power is integrated. The cost uses the quarter's price, with tax and VAT from
the country file when the price entity gives market prices (E-69).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from housemind.core.model import State
from housemind.energy.country import CountryRules
from housemind.energy.setup import EnergySetup, Reading
from housemind.memory.database import Database


def _number(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "").lower()
    if unit == "wh":
        value /= 1000
    elif unit == "mwh":
        value *= 1000
    elif unit == "kw":
        value *= 1000  # power in W
    return value


def total(states: dict[str, State], entity_ids: list[str]) -> float | None:
    values = [_number(states.get(e)) for e in entity_ids]
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def power(states: dict[str, State], entity_id: str | None) -> float | None:
    return _number(states.get(entity_id)) if entity_id else None


def live(states: dict[str, State], readings: list[Reading]) -> float | None:
    """The first reading that has a value now, in W; the phases of a meter only when each has
    one, so a missing phase never makes the grid look smaller."""
    for reading in readings:
        values = [v for e in reading if (v := power(states, e)) is not None]
        if reading and len(values) == len(reading):
            return sum(values)
    return None


@dataclass(slots=True)
class Quarter:
    start: datetime
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    solar_kwh: float = 0.0
    battery_in_kwh: float = 0.0
    battery_out_kwh: float = 0.0
    car_kwh: float = 0.0
    house_w: float = 0.0
    price: float | None = None
    export_price: float | None = None

    @property
    def cost(self) -> float:
        buy = self.import_kwh * (self.price or 0.0)
        sell = self.export_kwh * (self.export_price if self.export_price is not None else 0.0)
        return buy - sell


@dataclass(slots=True)
class Meter:
    db: Database
    samples: list[dict[str, float]] = field(default_factory=list)
    counters: dict[str, float] = field(default_factory=dict)
    quarter_start: datetime | None = None

    def sample(self, setup: EnergySetup, states: dict[str, State]) -> None:
        """Called every tick: live power, for quarters without counters and the profiles."""
        row: dict[str, float] = {}
        for key, value in (
            ("grid", live(states, setup.readings("grid"))),
            ("solar", live(states, setup.readings("solar"))),
            ("battery", live(states, setup.readings("battery"))),
            ("car", power(states, setup.charger_power)),
        ):
            if value is not None:
                row[key] = value
        self.samples.append(row)

    def _counters(self, setup: EnergySetup, states: dict[str, State]) -> dict[str, float]:
        found = {}
        for key, entities in (
            ("import", setup.grid_import),
            ("export", setup.grid_export),
            ("solar", setup.solar_energy),
            ("battery_in", setup.battery_in),
            ("battery_out", setup.battery_out),
        ):
            value = total(states, entities)
            if value is not None:
                found[key] = value
        return found

    def close(
        self,
        setup: EnergySetup,
        states: dict[str, State],
        start: datetime,
        price: float | None,
        export_price: float | None,
    ) -> Quarter:
        """End the quarter that began at ``start`` and start the next one."""
        quarter = Quarter(start, price=price, export_price=export_price)
        now_counters = self._counters(setup, states)
        hours = 0.25

        def mean(key: str) -> float | None:
            values = [s[key] for s in self.samples if key in s]
            return sum(values) / len(values) if values else None

        def delta(key: str) -> float | None:
            if key in now_counters and key in self.counters:
                return max(0.0, now_counters[key] - self.counters[key])
            return None

        grid, solar, battery, car = mean("grid"), mean("solar"), mean("battery"), mean("car")
        quarter.import_kwh = delta("import") or (
            max(0.0, grid) * hours / 1000 if grid is not None else 0.0
        )
        quarter.export_kwh = delta("export") or (
            max(0.0, -grid) * hours / 1000 if grid is not None else 0.0
        )
        quarter.solar_kwh = delta("solar") or (
            max(0.0, solar) * hours / 1000 if solar is not None else 0.0
        )
        quarter.battery_in_kwh = delta("battery_in") or (
            max(0.0, battery) * hours / 1000 if battery is not None else 0.0
        )
        quarter.battery_out_kwh = delta("battery_out") or (
            max(0.0, -battery) * hours / 1000 if battery is not None else 0.0
        )
        quarter.car_kwh = max(0.0, car) * hours / 1000 if car is not None else 0.0
        # What the house itself used, without battery and car (for the profile).
        house_kwh = (
            quarter.import_kwh
            - quarter.export_kwh
            + quarter.solar_kwh
            - quarter.battery_in_kwh
            + quarter.battery_out_kwh
            - quarter.car_kwh
        )
        quarter.house_w = max(0.0, house_kwh * 1000 / hours)
        self.counters = now_counters
        self.samples = []
        return quarter

    async def store(
        self, quarter: Quarter, day: str, outdoor_c: float | None, planning: bool
    ) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO energy_quarters(start, import_kwh, export_kwh, solar_kwh, "
            "battery_in_kwh, battery_out_kwh, car_kwh, house_w, price, export_price, cost) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                quarter.start.isoformat(),
                quarter.import_kwh,
                quarter.export_kwh,
                quarter.solar_kwh,
                quarter.battery_in_kwh,
                quarter.battery_out_kwh,
                quarter.car_kwh,
                quarter.house_w,
                quarter.price,
                quarter.export_price,
                quarter.cost,
            ),
        )
        await self.db.execute(
            "INSERT INTO energy_days(day, import_kwh, export_kwh, solar_kwh, car_kwh, cost, "
            "outdoor_sum, outdoor_n, price_sum, price_n, planning) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
            "import_kwh = import_kwh + excluded.import_kwh, "
            "export_kwh = export_kwh + excluded.export_kwh, "
            "solar_kwh = solar_kwh + excluded.solar_kwh, car_kwh = car_kwh + excluded.car_kwh, "
            "cost = cost + excluded.cost, outdoor_sum = outdoor_sum + excluded.outdoor_sum, "
            "outdoor_n = outdoor_n + excluded.outdoor_n, "
            "price_sum = price_sum + excluded.price_sum, price_n = price_n + excluded.price_n, "
            "planning = max(planning, excluded.planning)",
            (
                day,
                quarter.import_kwh,
                quarter.export_kwh,
                quarter.solar_kwh,
                quarter.car_kwh,
                quarter.cost,
                outdoor_c or 0.0,
                1 if outdoor_c is not None else 0,
                quarter.price or 0.0,
                1 if quarter.price is not None else 0,
                int(planning),
            ),
        )

    async def days(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM energy_days ORDER BY day DESC LIMIT ?", (limit,)
        )
        out = []
        for r in rows:
            day = dict(r)
            n = int(day["outdoor_n"] or 0)
            day["outdoor_c"] = day["outdoor_sum"] / n if n else None
            day["degree_days"] = max(0.0, 18 - day["outdoor_c"]) if n else None
            m = int(day["price_n"] or 0)
            day["avg_price"] = day["price_sum"] / m if m else None
            out.append(day)
        return out


def prices_for(
    country: CountryRules,
    raw: float | None,
    market: bool,
    day_of: datetime,
    export_raw: float | None,
) -> tuple[float | None, float | None]:
    """The price a household pays for a kWh, and what a kWh given back is worth (E-69)."""
    if raw is None:
        return None, None
    buy = country.all_in(raw) if market else raw
    if country.net_metering(day_of.date()):
        return buy, buy  # netted: a kWh given back is worth a kWh taken
    if export_raw is not None:
        sell = export_raw
    else:
        sell = raw if market else raw / (1 + country.vat) - country.energy_tax_per_kwh
    return buy, sell  # at a negative price giving back costs money
