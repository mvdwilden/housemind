"""The energy manager: setup, meter, profiles and forecasts per quarter (H10.1, E-65, E-72)."""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import Clock
from housemind.core.model import WHOLE_HOUSE, Area, HouseModel, State
from housemind.energy.battery import find_batteries
from housemind.energy.country import CountryRules
from housemind.energy.heat import (
    BOILER_KWH_PER_C,
    BOILER_LOSS,
    HOUSE_KWH_PER_C,
    HOUSE_LOSS,
    HeatModel,
    kept,
)
from housemind.energy.integrations import Integrations
from housemind.energy.ledger import Block, Ledger
from housemind.energy.meter import Meter, Quarter, prices_for
from housemind.energy.model import EnergySettings, Slot
from housemind.energy.own import Ownership
from housemind.energy.plan import Plan, Start, Step, make_plan
from housemind.energy.prices import (
    QUARTER,
    PriceBasis,
    PriceWords,
    current_price,
    parse_series,
    per_quarter,
    price_basis,
    quarter_floor,
)
from housemind.energy.profile import Profiles
from housemind.energy.savings import base_load_w, expected_for, fit
from housemind.energy.setup import EnergySetup, detect
from housemind.energy.solar_margin import SolarMargin
from housemind.ha.adapter import HomeAssistant
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Layer
from housemind.world.calendar import day_type

_LOGGER = logging.getLogger(__name__)

HORIZON = timedelta(hours=36)
# HouseMind no longer steers the home battery or the car (E-363, B-248): what it still had set
# on them goes back once, and what it learned about them is wiped (K2).
LEGACY_SKILLS = frozenset({"energy_battery", "energy_charging"})
LEGACY_SETTINGS = (
    "battery_calibration",
    "battery_remote",
    "battery_calibrate",
    "departures",
    "charge_once",
)
INFO_ONLY = "energy_info_only"
DAY_START = "energy_day_start"
DEFAULT_HOUSE_W = 400.0


@dataclass(slots=True)
class EnergyManager:
    db: Database
    ha: HomeAssistant
    clock: Clock
    country: CountryRules
    time_zone: str
    country_code: str | None = None
    setup: EnergySetup = field(default_factory=EnergySetup)
    settings: EnergySettings = field(default_factory=EnergySettings)
    meter: Meter | None = None
    profiles: Profiles | None = None
    quarter_start: datetime | None = None
    solar: dict[datetime, float] = field(default_factory=dict)  # hour -> Wh
    _solar_read: datetime | None = None
    last_quarter: Quarter | None = None
    plan: Plan | None = None
    since: datetime | None = None  # first measured quarter (start of the baseline)
    _plan_key: tuple[Any, ...] = ()
    _boiler_base: float | None = None
    # What HouseMind set itself, and what it was before (B-203): only that is ever put back –
    # never a limit a person or the law set (E-328).
    own: Ownership | None = None
    _saved: tuple[Any, ...] = ()  # what was last written to the database
    _heat_base: float | None = None
    _legionella_at: datetime | None = None
    managers: set[str] = field(default_factory=set)
    # What price entities say about what is in their price (E-304): from the knowledge files.
    price_words: PriceWords = field(default_factory=PriceWords)
    # What is different per integration, for writing sparingly (integrations.yaml, K3).
    integrations: Integrations = field(default_factory=Integrations)
    # What the energy screen read, with when it was read (code QA 3, punt 18).
    _read: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=dict)
    # How far the solar forecast was off, for the margin Expert sees (B-225).
    solar_margin: SolarMargin | None = None
    info_only: bool = False  # B-248 done: the battery and the car are only watched
    # What storing warmth costs in this house: boiler and room, learned (design 36, B-269).
    heat: HeatModel | None = None
    # What each block should bring and brought; parts that rest (36.7, 36.8, E-378).
    ledger: Ledger | None = None
    # The device meters at the start of the day, for what each used today (E-364, B-255).
    _day_start: dict[str, Any] = field(default_factory=dict)

    async def start(
        self,
        model: HouseModel,
        states: dict[str, State],
        words: dict[str, list[str]],
        managers: set[str] | None = None,
        price_words: PriceWords | None = None,
        integrations: Integrations | None = None,
    ) -> None:
        self.managers = set(managers or ())
        self.info_only = await self.db.get_setting(INFO_ONLY) is True
        self.price_words = price_words or PriceWords()
        self.integrations = integrations or Integrations()
        self.meter = Meter(self.db)
        self.profiles = Profiles(self.db)
        await self.profiles.load()
        self.solar_margin = SolarMargin(self.db)
        await self.solar_margin.load()
        self.heat = HeatModel(self.db)
        await self.heat.load()
        self.ledger = Ledger(self.db)
        await self.ledger.load()
        self.settings = EnergySettings.from_dict(await self.db.get_setting("energy"))
        since = await self.db.get_setting("energy_since")
        self.since = datetime.fromisoformat(since) if since else None
        legionella = await self.db.get_setting("legionella_at")
        self._legionella_at = datetime.fromisoformat(legionella) if legionella else None
        old = await self.db.get_setting("energy_own") or {}
        self._saved = (self._legionella_at,)
        self._day_start = await self.db.get_setting(DAY_START) or {}
        await self.refresh(model, states, words)
        self.own = Ownership(self.db)
        first = None
        if isinstance(old, dict) and "items" not in old:  # written by the first energy build
            first = {**old, "export_limit": self.setup.export_limit}
        await self.own.load(states, first)

    async def refresh(
        self, model: HouseModel, states: dict[str, State], words: dict[str, list[str]]
    ) -> None:
        try:
            prefs = await self.ha.energy_preferences()
        except Exception:
            _LOGGER.warning("Could not read Home Assistant's energy settings")
            prefs = {}
        self.setup = detect(model, states, prefs, words, self.managers)
        batteries = find_batteries(model, states, self.setup, words)
        self.setup.batteries = batteries
        if batteries:
            first = batteries[0]
            self.setup.battery_soc = self.setup.battery_soc or first.soc
            self.setup.battery_readings = list(
                dict.fromkeys(self.setup.battery_readings + first.readings())
            )
            self.setup.battery_power = self.setup.battery_power or first.power

    async def save_settings(self, settings: EnergySettings) -> None:
        self.settings = settings
        await self.db.set_setting("energy", asdict(settings))

    # -- prices ---------------------------------------------------------------------------

    def price_basis(self, states: dict[str, State]) -> PriceBasis:
        """What the price entity says is in its price (E-304)."""
        return price_basis(states.get(self.setup.price or ""), self.price_words)

    def _market(self, states: dict[str, State]) -> bool:
        """A bare market price gets the country's tax and VAT added (E-69). Only what the
        integration states, or what the administrator chose, counts – never a guess from how
        high the prices are, which can take a price with VAT for a bare one (E-304)."""
        if self.settings.price_mode != "auto":
            return self.settings.price_mode == "market"
        return self.price_basis(states).kind == "market"

    def raw_prices(
        self, states: dict[str, State], start: datetime, quarters: int
    ) -> list[float | None]:
        state = states.get(self.setup.price or "")
        fallback = current_price(state)
        if fallback is None:
            fallback = self.setup.fixed_price
        return per_quarter(parse_series(state), start, quarters, fallback)

    # -- every tick -----------------------------------------------------------------------

    async def _save_own(self) -> None:
        """What HouseMind changed itself survives a restart: the register (B-203), and the
        legionella moment, so a boiler without a temperature gets no boost every day."""
        if self.own is not None:
            await self.own.save()
        saved = (self._legionella_at,)
        if saved == self._saved:
            return
        self._saved = saved
        if self._legionella_at is not None:
            await self.db.set_setting("legionella_at", self._legionella_at.isoformat())

    # -- what HouseMind set itself (B-203, B-204) --------------------------------------------

    def owned(self) -> set[str]:
        return set(self.own.items) if self.own is not None else set()

    def watch_own(self, states: dict[str, State], now: datetime) -> list[str]:
        """Let go of what somebody else changed in the meantime (E-328)."""
        return self.own.check(states, now) if self.own is not None else []

    def done(self, request: ActionRequest, before: State | None, now: datetime) -> None:
        if self.own is not None:
            self.own.done(request, before, now)
        if (
            request.skill == "energy_appliance"
            and request.layer is not Layer.RESTORE
            and self.ledger is not None
            and self.plan is not None
        ):
            step = self.plan.now(now).get(f"appliance:{request.entity_id}")
            if step is not None:  # an appliance started: its line in the ledger (36.7)
                key = f"appliance:{request.entity_id}:{step.start.isoformat()}"
                self.ledger.open("appliance", key, step.start, step.end, step.saving)

    def give_back(
        self, states: dict[str, State], now: datetime, why: str, only: set[str] | None, area_of: Any
    ) -> list[ActionRequest]:
        """Put back what HouseMind set: when it pauses, stops or the plan no longer needs it."""
        area_of = _whole_house(area_of)
        if self.own is None:
            return []
        sign = CURRENCY_SIGNS.get(self.country.currency, self.country.currency)
        return [
            replace(request, facts={**request.facts, "currency": sign})
            for request in self.own.give_back(states, now, why, only, area_of)
        ]

    def fallback(self) -> list[dict[str, Any]]:
        """For the watchdog in the integration (B-204)."""
        return self.own.fallback() if self.own is not None else []

    def handed_back(self) -> None:
        """The watchdog gave everything back while HouseMind was silent (B-204)."""
        if self.own is not None:
            self.own.handed_back()

    def legacy_give_back(
        self, states: dict[str, State], now: datetime, area_of: Any
    ) -> list[ActionRequest]:
        """What HouseMind still had set on the battery or the charger goes back (B-248)."""
        if self.own is None:
            return []
        for entity_id, owned in list(self.own.items.items()):
            if owned.skill in LEGACY_SKILLS and entity_id not in states:
                self.own.items.pop(entity_id)  # gone from Home Assistant: nothing to put back
        legacy = self._legacy()
        return self.give_back(states, now, "info_only", legacy, area_of) if legacy else []

    def _legacy(self) -> set[str]:
        if self.own is None:
            return set()
        return {e for e, owned in self.own.items.items() if owned.skill in LEGACY_SKILLS}

    def legacy_left(self) -> bool:
        return bool(self._legacy())

    async def wipe_legacy(self) -> None:
        """What HouseMind learned and was told about steering the battery and the car (K2)."""
        for key in LEGACY_SETTINGS:
            await self.db.set_setting(key, None)
        self.info_only = True
        await self.db.set_setting(INFO_ONLY, True)

    async def tick(self, now: datetime, states: dict[str, State]) -> Quarter | None:
        """Sample live power; at each new quarter close the previous one (meter, profiles)."""
        assert self.meter is not None and self.profiles is not None
        await self._save_own()
        if not self.setup.has_any:
            return None
        await self._read_solar(now)
        await self._start_of_day(now, states)
        self.meter.sample(self.setup, states)
        current = quarter_floor(now)
        if self.quarter_start is None:
            self.quarter_start = current
            self.meter.counters = self.meter._counters(self.setup, states)
            self.meter.samples = []
            return None
        if current <= self.quarter_start:
            return None
        start = self.quarter_start
        raw = self.raw_prices(states, start, 1)[0]
        market = self._market(states)
        buy, sell = prices_for(self.country, raw, market, start, None)
        quarter = self.meter.close(self.setup, states, start, buy, sell)
        local = start.astimezone(self._tz)
        dtype = day_type(local.date(), self.country_code).value
        index = local.hour * 4 + local.minute // 15
        if self.since is None:
            self.since = start
            await self.db.set_setting("energy_since", start.isoformat())
        await self.meter.store(
            quarter, local.date().isoformat(), self.outdoor(states), self.planning(start)
        )
        await self.profiles.learn("house", dtype, index, quarter.house_w)
        await self._learn_heat(current, states, dtype, index)
        await self._book(current, states)
        if self.setup.solar_energy or self.setup.solar_power:
            await self.profiles.learn("solar", "all", index, quarter.solar_kwh * 4000)
            if self.solar_margin is not None:
                await self.solar_margin.quarter(start, quarter.solar_kwh, self.solar)
        new_day = current.astimezone(self._tz).date() != local.date()
        self.quarter_start = current
        self.last_quarter = quarter
        if new_day:
            await self._close_day(local.date().isoformat())
        return quarter

    async def _learn_heat(
        self, now: datetime, states: dict[str, State], dtype: str, index: int
    ) -> None:
        """Each quarter: what the boiler used and heated by itself, how fast it and the house
        cool down (B-269, B-274)."""
        assert self.heat is not None and self.profiles is not None
        s = self.setup
        if s.boiler:
            temp = _float(states.get(s.boiler), "current_temperature")
            ours = self.own is not None and self.own.owns(s.boiler)
            hour = now.astimezone(self._tz).hour
            found = self.heat.boiler_quarter(now, temp, ours, 1 <= hour < 5)
            if found is not None:
                used, heated = found
                await self.profiles.learn("boiler_use", dtype, index, used)
                await self.profiles.learn("boiler_heat", dtype, index, heated)
        if s.heat_pump:
            heat = states.get(s.heat_pump)
            self.heat.room_quarter(
                now,
                _float(heat, "current_temperature"),
                self.outdoor(states),
                _float(heat, "temperature"),
            )
        await self.heat.save()

    # -- the ledger: expected and measured per block (36.7, 36.8) ---------------------------

    def resting(self, now: datetime) -> frozenset[str]:
        return self.ledger.resting(now) if self.ledger is not None else frozenset()

    async def _book(self, now: datetime, states: dict[str, State]) -> None:
        """Open a line for each block HouseMind is carrying out, follow it, and measure it
        once its warmth has been used."""
        if self.ledger is None or self.plan is None or not self.planning(now):
            return
        s = self.setup
        for step in self.plan.steps:
            if not step.start <= now - QUARTER < step.end and not step.start <= now < step.end:
                continue
            key = f"{step.device}:{step.start.isoformat()}"
            doing = False
            data: dict[str, Any] = {}
            if step.device == "boiler" and step.why != "legionella":
                doing = self.own is not None and self.own.owns(s.boiler or "")
                data = {
                    "temp": _float(states.get(s.boiler or ""), "current_temperature"),
                    "sun": step.why == "boiler_sun",
                    "measure_at": (step.end + timedelta(hours=24)).isoformat(),
                }
            elif step.device == "heat_pump":
                heat = states.get(s.heat_pump or "")
                wish = _float(heat, "temperature")
                doing = (
                    wish is not None
                    and self._heat_base is not None
                    and (wish >= self._heat_base + 0.4)
                )
                peak_end = step.params.get("peak_end", step.end)
                data = {
                    "temp": _float(heat, "current_temperature"),
                    "band": float(step.value or 1.0),
                    "measure_at": peak_end.isoformat()
                    if isinstance(peak_end, datetime)
                    else step.end.isoformat(),
                }
            elif step.device == "export":
                doing = self.own is not None and self.own.owns(s.export_limit or "")
            found = self.ledger.find(key) or self.ledger.running(step.device, now)
            if found is None and doing:
                found = self.ledger.open(
                    step.device, key, step.start, step.end, step.saving, **data
                )
            if found is not None and step.device in ("boiler", "heat_pump"):
                entity = s.boiler if step.device == "boiler" else s.heat_pump
                temp = _float(states.get(entity or ""), "current_temperature")
                if temp is not None:
                    found.data["max"] = max(float(found.data.get("max") or temp), temp)
        for block in self.ledger.due(now):
            self.ledger.close(block, await self._measured(block), now)
        await self.ledger.save(now)

    async def _measured(self, block: Block) -> float:
        """What a block brought, on what was measured (36.7): an estimate, not a second house."""
        start, end = datetime.fromisoformat(block.start), datetime.fromisoformat(block.end)
        rows = await self.db.fetchall(
            "SELECT start, price, export_price, solar_kwh, house_w FROM energy_quarters "
            "WHERE start >= ? AND start < ? ORDER BY start",
            (start.isoformat(), (end + timedelta(hours=24)).isoformat()),
        )
        during = [r for r in rows if r["start"] < end.isoformat()]
        after = [r for r in rows if r["start"] >= end.isoformat()]

        def average(found: list[Any], key: str) -> float | None:
            values = [float(r[key]) for r in found if r[key] is not None]
            return sum(values) / len(values) if values else None

        if block.part == "boiler":
            first, top = block.data.get("temp"), block.data.get("max")
            paid = average(during, "export_price" if block.data.get("sun") else "price")
            later = average(after, "price")
            if first is None or top is None or paid is None or later is None:
                return block.expected
            hours = 12.0  # on average the stored warmth is used half a day later
            share = kept(self.heat.boiler_loss if self.heat else BOILER_LOSS, hours)
            return max(0.0, float(top) - float(first)) * BOILER_KWH_PER_C * (later * share - paid)
        if block.part == "heat_pump":
            first, top = block.data.get("temp"), block.data.get("max")
            measure_at = datetime.fromisoformat(str(block.data.get("measure_at") or block.end))
            peak = [r for r in after if r["start"] < measure_at.isoformat()]
            paid, later = average(during, "price"), average(peak, "price")
            if first is None or top is None or paid is None or later is None:
                return block.expected
            degrees = min(float(block.data.get("band") or 1.0), max(0.0, float(top) - float(first)))
            hours = (measure_at - start).total_seconds() / 3600 / 2
            share = kept(self.heat.house_loss if self.heat else HOUSE_LOSS, hours)
            return degrees * HOUSE_KWH_PER_C * (later * share - paid)
        if block.part == "export":
            avoided = 0.0
            for r in during:
                sell = r["export_price"]
                spare = float(r["solar_kwh"]) - float(r["house_w"]) * 0.25 / 1000
                if sell is not None and float(sell) < 0 and spare > 0:
                    avoided += spare * -float(sell)
            return avoided
        return block.expected  # an appliance: the plan's own estimate

    # -- savings (S-03) and base load (E-77) -----------------------------------------------

    async def _close_day(self, day: str) -> None:
        """A whole day is done: compare it with what it would have cost without HouseMind."""
        assert self.meter is not None
        days = await self.meter.days(400)
        first = self.since.astimezone(self._tz).date().isoformat() if self.since else None
        row = next((d for d in days if d["day"] == day), None)
        if row is None or not row["planning"]:
            return
        model = fit([d for d in days if d["day"] not in (first, day)])
        expected = expected_for(model, row) if model is not None else None
        if expected is None:
            return
        await self.db.execute(
            "UPDATE energy_days SET baseline_kwh = ?, baseline_cost = ? WHERE day = ?",
            (round(expected[0], 3), round(expected[1], 4), day),
        )

    async def _read_once(
        self, name: str, now: datetime, read: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """What the energy screen reads, once until a quarter is written, the day turns or
        measuring ends – not at every screen update (code QA 3, punt 18). A copy: whoever
        changes it does not change what is kept."""
        stamp = (self.db.energy_written, self._midnight(now), self.planning(now))
        kept = self._read.get(name)
        if kept is None or kept[0] != stamp:
            kept = self._read[name] = (stamp, await read())
        return copy.deepcopy(kept[1])

    async def savings(self, now: datetime) -> dict[str, Any]:
        return await self._read_once("savings", now, lambda: self._savings(now))

    async def _savings(self, now: datetime) -> dict[str, Any]:
        assert self.meter is not None
        days = await self.meter.days(400)
        compared = [d for d in days if d["planning"] and d["baseline_cost"] is not None]
        total = sum(float(d["baseline_cost"]) - float(d["cost"]) for d in compared)
        kwh = sum(float(d["baseline_kwh"] or 0) - float(d["import_kwh"]) for d in compared)
        return {
            "measuring": not self.planning(now),
            "daysLeft": self.baseline_days_left(now),
            "total": round(total, 2),
            "kwh": round(kwh, 1),
            "compared": len(compared),
            "days": [
                {
                    "day": d["day"],
                    "cost": round(float(d["cost"]), 2),
                    "expected": round(float(d["baseline_cost"]), 2),
                    "saving": round(float(d["baseline_cost"]) - float(d["cost"]), 2),
                }
                for d in sorted(compared, key=lambda d: d["day"])[-14:]
            ],
        }

    async def insights(self, now: datetime, states: dict[str, State]) -> dict[str, Any]:
        """Base load at night, what it costs a year, and the biggest users now (E-77)."""
        since = (now - timedelta(days=7)).isoformat()
        rows = await self.db.fetchall(
            "SELECT start, house_w, price FROM energy_quarters WHERE start >= ?", (since,)
        )
        night = [
            float(r["house_w"])
            for r in rows
            if 1 <= datetime.fromisoformat(r["start"]).astimezone(self._tz).hour < 5
        ]
        base = base_load_w(night)
        prices = sorted(float(r["price"]) for r in rows if r["price"] is not None)
        price = prices[len(prices) // 2] if prices else None
        users = []
        for entity_id in self.setup.users:
            state = states.get(entity_id)
            watts = _float(state) if state is not None else None
            if watts is not None and watts > 5:
                users.append((watts, entity_id))
        users.sort(reverse=True)
        return {
            "baseW": round(base) if base is not None else None,
            "baseYearKwh": round(base * 8.76) if base is not None else None,
            "baseYearCost": round(base * 8.76 * price) if base is not None and price else None,
            "users": [{"entity": e, "watts": round(w)} for w, e in users[:5]],
        }

    # -- now and today (E-364, B-252, B-255) ------------------------------------------------

    def _midnight(self, now: datetime) -> datetime:
        local = now.astimezone(self._tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    async def today(self, now: datetime) -> dict[str, Any]:
        """Today so far, from the closed quarters: kWh in and out, and what it cost."""
        return await self._read_once("today", now, lambda: self._today(now))

    async def _today(self, now: datetime) -> dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT SUM(import_kwh) AS import_kwh, SUM(export_kwh) AS export_kwh, "
            "SUM(solar_kwh) AS solar_kwh, SUM(battery_in_kwh) AS battery_in_kwh, "
            "SUM(battery_out_kwh) AS battery_out_kwh, SUM(car_kwh) AS car_kwh, "
            "SUM(cost) AS cost, COUNT(*) AS n FROM energy_quarters WHERE start >= ?",
            (self._midnight(now).astimezone(UTC).isoformat(),),
        )
        row = dict(rows[0]) if rows else {}
        return {k: float(v or 0.0) for k, v in row.items()}

    async def month_cost(self, now: datetime) -> float:
        month = now.astimezone(self._tz).strftime("%Y-%m")
        rows = await self.db.fetchall(
            "SELECT SUM(cost) AS cost FROM energy_days WHERE day LIKE ?", (f"{month}-%",)
        )
        return float(rows[0]["cost"] or 0.0) if rows else 0.0

    async def measured(self, now: datetime, hours: int = 12) -> list[dict[str, Any]]:
        """The closed quarters of today, at most the last ``hours``: what really happened, for
        the part of the chart before now (B-253). Power in watts: kWh per quarter × 4000."""
        since = max(self._midnight(now), now - timedelta(hours=hours)).astimezone(UTC)
        rows = await self.db.fetchall(
            "SELECT * FROM energy_quarters WHERE start >= ? ORDER BY start", (since.isoformat(),)
        )
        return [
            {
                "start": datetime.fromisoformat(r["start"]),
                "price": r["price"],
                "solar": float(r["solar_kwh"]) * 4000,
                "house": float(r["house_w"]),
                "battery": (float(r["battery_in_kwh"]) - float(r["battery_out_kwh"])) * 4000,
                "car": float(r["car_kwh"]) * 4000,
                "grid": (float(r["import_kwh"]) - float(r["export_kwh"])) * 4000,
            }
            for r in rows
        ]

    async def _start_of_day(self, now: datetime, states: dict[str, State]) -> None:
        """What each device meter showed when the day began – or when HouseMind first saw it
        today – so 'today' is the difference (E-77, B-255)."""
        if not self.setup.device_meters:
            return
        day = now.astimezone(self._tz).date().isoformat()
        same_day = self._day_start.get("day") == day
        values = dict(self._day_start.get("values") or {}) if same_day else {}
        changed = not same_day
        for entity_id in self.setup.device_meters:
            value = _float(states.get(entity_id))
            if entity_id not in values and value is not None:
                values[entity_id] = value
                changed = True
        if changed:
            self._day_start = {"day": day, "values": values}
            await self.db.set_setting(DAY_START, self._day_start)

    def users_today(self, states: dict[str, State]) -> list[tuple[str, float]]:
        """The biggest users of today in kWh, from the devices in the energy settings (E-364)."""
        start = self._day_start.get("values") or {}
        found = []
        for entity_id in self.setup.device_meters:
            now_value, then = _float(states.get(entity_id)), start.get(entity_id)
            if now_value is None or not isinstance(then, int | float):
                continue
            unit = str((states[entity_id].attributes or {}).get("unit_of_measurement") or "")
            used = (now_value - then) / (1000 if unit.lower() == "wh" else 1)
            if used > 0.01:
                found.append((entity_id, used))
        return sorted(found, key=lambda x: -x[1])[:5]

    async def _read_solar(self, now: datetime) -> None:
        if self._solar_read is not None and now - self._solar_read < timedelta(hours=1):
            return
        self._solar_read = now
        try:
            self.solar = await self.ha.solar_forecast()
        except Exception:
            self.solar = {}

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    def outdoor(self, states: dict[str, State]) -> float | None:
        for entity_id, state in states.items():
            if entity_id.startswith("weather."):
                value = state.attributes.get("temperature")
                if isinstance(value, int | float):
                    return float(value)
        return None

    # -- what HouseMind expects -----------------------------------------------------------

    def forecast(self, states: dict[str, State], now: datetime) -> list[Slot]:
        """Price, sun and own use for every quarter of the coming 36 hours."""
        assert self.profiles is not None
        start = quarter_floor(now)
        quarters = int(HORIZON / QUARTER)
        raw = self.raw_prices(states, start, quarters)
        market = self._market(states)
        slots = []
        for q in range(quarters):
            moment = start + q * QUARTER
            buy, sell = prices_for(self.country, raw[q], market, moment, None)
            local = moment.astimezone(self._tz)
            index = local.hour * 4 + local.minute // 15
            dtype = day_type(local.date(), self.country_code).value
            hour = moment.replace(minute=0, second=0, microsecond=0)
            if hour in self.solar:
                solar_w = self.solar[hour]
            else:
                solar_w = self.profiles.get("solar", "all", index) or 0.0
            house_w = self.profiles.get("house", dtype, index) or DEFAULT_HOUSE_W
            slots.append(Slot(moment, buy, sell, max(0.0, solar_w), max(0.0, house_w)))
        return slots

    # -- the plan and carrying it out (H10.2) ---------------------------------------------

    def planning(self, now: datetime) -> bool:
        """After the baseline the plan acts; before, HouseMind only measures (S-03)."""
        return self.since is not None and now - self.since >= BASELINE

    def baseline_days_left(self, now: datetime) -> int:
        if self.since is None:
            return BASELINE.days
        return max(0, BASELINE.days - (now - self.since).days)

    def _boost_done(self, target: float) -> bool:
        """The boiler target is not one of HouseMind's own boosts."""
        return target < min(self.settings.boiler_max_c, 60.0) - 0.4

    def _own_legionella(self, states: dict[str, State]) -> bool:
        """The boiler has its own legionella programme (E-377): a mode or a control."""
        s = self.setup
        if s.boiler_legionella:
            return True
        options = (states.get(s.boiler or "") or State("", "", {}, datetime.min)).attributes.get(
            "operation_list"
        )
        return any("legion" in str(o).lower() for o in options or [])

    def start_state(
        self, states: dict[str, State], now: datetime, slots: list[Slot] | None = None
    ) -> Start:
        s = self.setup
        boiler = states.get(s.boiler or "")
        boiler_target = _float(boiler, "temperature")
        if boiler_target is not None and (
            self._boiler_base is None or self._boost_done(boiler_target)
        ):
            self._boiler_base = boiler_target
        heat = states.get(s.heat_pump or "")
        heat_target = _float(heat, "temperature")
        if heat_target is not None and self.heat_offset(s.heat_pump or "") == 0.0:
            self._heat_base = heat_target
        current = _float(boiler, "current_temperature")
        if current is not None and current >= 60:
            self._legionella_at = now
        ready = []
        for appliance in s.appliances:
            state = states.get(appliance.ready)
            if state is not None and state.state == "on":
                ready.append(appliance.start)
        use: list[float] = []
        heated: list[float] = []
        for slot in slots or []:
            local = slot.start.astimezone(self._tz)
            dtype = day_type(local.date(), self.country_code).value
            index = local.hour * 4 + local.minute // 15
            if self.profiles is not None:
                use.append(self.profiles.get("boiler_use", dtype, index) or 0.0)
                heated.append(self.profiles.get("boiler_heat", dtype, index) or 0.0)
        model = self.heat
        own_legionella = self._own_legionella(states)
        return Start(
            boiler_c=current,
            boiler_base=self._boiler_base,
            heat_base=self._heat_base,
            appliances_ready=tuple(ready),
            month_peak_kw=self.last_quarter.import_kwh * 4 if self.last_quarter else None,
            legionella_due=not own_legionella
            and (self._legionella_at is None or now - self._legionella_at >= timedelta(days=7)),
            has_boiler=bool(s.boiler),
            has_heat_pump=bool(s.heat_pump),
            has_export_limit=bool(s.export_limit),
            capacity_minimum_kw=self.country.capacity_minimum_kw,
            boiler_use=tuple(use),
            boiler_heat=tuple(heated),
            boiler_loss=model.boiler_loss if model else BOILER_LOSS,
            boiler_max_c=_float(boiler, "max_temp"),
            boiler_sensed=current is not None,
            house_loss=model.house_loss if model else HOUSE_LOSS,
            young=model.young(now) if model else True,
            resting=self.resting(now),
        )

    def replan(self, states: dict[str, State], now: datetime, force: bool = False) -> Plan | None:
        """Plan again every quarter, and at once on new prices (B-37)."""
        if not self.setup.has_any:
            return None
        price = states.get(self.setup.price or "")
        tomorrow = bool(
            price
            and any(
                isinstance(v, list) and v for k, v in price.attributes.items() if "tomorrow" in k
            )
        )
        key = (quarter_floor(now), tomorrow)
        if not force and key == self._plan_key and self.plan is not None:
            return self.plan
        self._plan_key = key
        slots = self.forecast(states, now)
        self.plan = make_plan(slots, self.start_state(states, now, slots), self.settings, now)
        return self.plan

    def heat_offset(self, entity_id: str) -> float:
        """Comfort-band shift for the heat pump while the plan preheats (E-76)."""
        if not entity_id or entity_id != self.setup.heat_pump or self.plan is None:
            return 0.0
        now = self.clock.now()
        if not self.planning(now):
            return 0.0
        step = self.plan.now(now).get("heat_pump")
        return float(step.value or 0.0) if step is not None else 0.0

    def _block(self, skill: str, step: Step | None) -> str:
        """What one question and one situation of learning is about (E-343): one step of the
        plan (its end stays the same when the plan is made again each quarter)."""
        return f"{skill}:{step.action}:{step.end.isoformat()}" if step is not None else skill

    def block_end(self, skill: str, now: datetime) -> datetime | None:
        """Until when a "Do it" for this part of energy counts (E-343, B-217)."""
        device = {
            "energy_boiler": "boiler",
            "energy_heat_pump": "heat_pump",
            "energy_export": "export",
        }.get(skill)
        step = self.plan.now(now).get(device) if self.plan is not None and device else None
        return step.end if step is not None else None

    def requests(
        self, states: dict[str, State], now: datetime, area_of: Any
    ) -> list[ActionRequest]:
        """What must change right now to follow the plan – as action requests for the gate.
        What the plan no longer needs goes back to what it was before HouseMind (B-203)."""
        area_of = _whole_house(area_of)
        if not self.planning(now):
            # During the baseline HouseMind steers nothing (S-03, E-343).
            return self.give_back(states, now, "measuring", None, area_of)
        if self.plan is None:
            return []
        s = self.setup
        steps = self.plan.now(now)
        local = now.astimezone(self._tz)
        out: list[ActionRequest] = []
        sign = CURRENCY_SIGNS.get(self.country.currency, self.country.currency)

        def fmt(params: dict[str, Any]) -> dict[str, Any]:
            return {
                k: v.astimezone(self._tz).strftime("%H:%M") if isinstance(v, datetime) else v
                for k, v in params.items()
            }

        def request(
            entity: str,
            action: str,
            data: dict[str, Any],
            skill: str,
            step: Step | None,
            why: str,
            **facts: Any,
        ) -> None:
            area = area_of(entity)
            facts.setdefault("block", self._block(skill, step))
            out.append(
                ActionRequest(
                    Layer.PLANNER,
                    entity,
                    action,
                    data,
                    reason="energy",
                    skill=skill,
                    area_id=area.id if area else None,
                    created_at=now,
                    facts={
                        "why": step.why if step is not None else why,
                        "params": fmt(step.params) if step is not None else {},
                        "currency": sign,
                        "context": f"energy|{local.strftime('%H:%M')}",
                        **facts,
                    },
                )
            )

        def give_back(entity: str | None, why: str) -> None:
            if entity and self.own is not None and self.own.owns(entity):
                out.extend(self.give_back(states, now, why, {entity}, area_of))

        # Boiler (E-76): extra warm in its window, back to what it was afterwards.
        if s.boiler:
            step = steps.get("boiler")
            if step is not None and step.value:
                target = float(step.value)
                current_target = _float(states.get(s.boiler), "temperature")
                if current_target is not None and abs(current_target - target) >= 0.5:
                    request(
                        s.boiler,
                        "water_heater.set_temperature",
                        {"temperature": target},
                        "energy_boiler",
                        step,
                        "boiler_back",
                    )
            else:
                give_back(s.boiler, "boiler_back")

        # Appliances that allow a remote start (E-64).
        for appliance in s.appliances:
            step = steps.get(f"appliance:{appliance.start}")
            ready = states.get(appliance.ready)
            if step is not None and ready is not None and ready.state == "on":
                action = (
                    "button.press" if appliance.start.startswith("button.") else "switch.turn_on"
                )
                request(appliance.start, action, {}, "energy_appliance", step, "appliance_cheapest")

        # Giving back at a negative price (E-70): back to exactly what it was, never to the
        # maximum, so a lower limit set by a person or the law stays (E-328).
        if s.export_limit:
            step = steps.get("export")
            limit = states.get(s.export_limit)
            now_value = _float(limit)
            if step is not None:
                if limit is not None and now_value is not None and abs(now_value) > 0.5:
                    request(
                        s.export_limit,
                        "number.set_value",
                        {"value": 0.0},
                        "energy_export",
                        step,
                        "export_normal",
                        unit=str(limit.attributes.get("unit_of_measurement") or ""),
                    )
            else:
                give_back(s.export_limit, "export_normal")
        return out


BASELINE = timedelta(days=14)  # S-03: the first two weeks HouseMind only measures
CURRENCY_SIGNS = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF", "SEK": "kr", "DKK": "kr"}


def _float(state: State | None, attribute: str | None = None) -> float | None:
    if state is None:
        return None
    raw = state.attributes.get(attribute) if attribute else state.state
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _shows(state: State | None, action: str, data: dict[str, Any]) -> bool:
    """The entity already shows what this action would set."""
    if state is None:
        return False
    if action == "select.select_option":
        return state.state == str(data.get("option"))
    if action.endswith("turn_on"):
        return state.state == "on"
    if action.endswith("turn_off"):
        return state.state == "off"
    if action == "number.set_value":
        current = _float(state)
        return current is not None and abs(current - float(data.get("value", 0))) < 0.5
    return False


_HOUSE = Area(WHOLE_HOUSE, "")


def _whole_house(area_of: Any) -> Any:
    """An energy device without a room learns for the whole house (E-343): never Observe
    forever only because Home Assistant has no room for it."""
    if getattr(area_of, "whole_house", False):
        return area_of

    def find(entity_id: str) -> Area:
        return area_of(entity_id) or _HOUSE

    find.whole_house = True  # type: ignore[attr-defined]
    return find
