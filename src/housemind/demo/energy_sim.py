"""Simple, plausible energy physics for a demo house (E-48, E-99).

Not a precise model – just enough for HouseMind to see a believable day: the sun by season and
clouds, the house's own use over the day, a house that holds its warmth (a heat pump that
needs more when it is colder, and less for a while after it preheated), a boiler that cools
down after showers and faster when it is hotter, a home battery that follows its mode, a car
that is away
on workdays, and market prices per quarter with morning and evening peaks and a midday dip.
Tomorrow's prices are known after 13:00, like a day-ahead market.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.model import Source
from housemind.ha.fake import FakeHomeAssistant

QUARTER = timedelta(minutes=15)


def day_length_hours(day: date) -> float:
    doy = day.timetuple().tm_yday
    return 12.0 + 4.4 * math.cos(2 * math.pi * (doy - 172) / 365)


def sun_strength(day: date) -> float:
    """Relative height of the midday sun (0.3 in winter … 1.0 in summer)."""
    doy = day.timetuple().tm_yday
    return 0.65 + 0.35 * math.cos(2 * math.pi * (doy - 172) / 365)


def clear_sky(local: datetime) -> float:
    """0…1: how much sun there would be without clouds at this moment."""
    length = day_length_hours(local.date())
    noon = 13.0 if local.dst() else 12.5
    rise, set_ = noon - length / 2, noon + length / 2
    hour = local.hour + local.minute / 60
    if not rise < hour < set_:
        return 0.0
    return math.sin(math.pi * (hour - rise) / length) * sun_strength(local.date())


def outdoor_c(local: datetime) -> float:
    doy = local.timetuple().tm_yday
    season = 10.5 - 7.5 * math.cos(2 * math.pi * (doy - 20) / 365)
    daily = 3.5 * math.sin(2 * math.pi * (local.hour + local.minute / 60 - 9) / 24)
    return season + daily


def house_w(local: datetime, weekend: bool) -> float:
    """The house's own use, without heat pump, boiler, battery and car."""
    h = local.hour + local.minute / 60
    base = 230.0
    morning = 650 * math.exp(-(((h - (8.5 if weekend else 7.2)) / 1.1) ** 2))
    noon = (350 if weekend else 120) * math.exp(-(((h - 12.5) / 1.5) ** 2))
    evening = 900 * math.exp(-(((h - 18.6) / 1.6) ** 2))
    late = 300 * math.exp(-(((h - 21.5) / 1.2) ** 2))
    return base + morning + noon + evening + late


@dataclass(slots=True)
class EnergySim:
    ha: FakeHomeAssistant
    tz: ZoneInfo
    seed: int = 1
    battery_kwh: float = 10.0
    battery_max_w: float = 2500.0
    battery_efficiency: float = 0.92
    car_kwh: float = 60.0
    solar_peak_w: float = 5200.0
    boiler_w: float = 2200.0
    heat_loss_w_per_c: float = 140.0
    # The house holds its warmth: Wh to warm it up one degree, what the heat pump can give
    # (warmth, not electricity) and how many times more warmth than electricity (COP).
    heat_capacity_wh_per_c: float = 3000.0
    heat_max_w: float = 8000.0
    cop: float = 3.2
    indoor_c: float = 20.0
    counters: dict[str, float] = field(default_factory=dict)
    last: datetime | None = None
    washer_until: datetime | None = None
    # Without HouseMind's energy plan a person starts the washing machine as soon as it is
    # ready (the fair comparison in R2, E-380).
    person_starts: bool = False
    _calls_seen: int = 0  # the commands already looked at: one press starts one wash
    boiler_c: float = 49.0
    boiler_heating: bool = False  # once it starts, it heats until its target (a thermostat)
    car_soc: float = 55.0
    battery_soc: float = 64.0
    car_home: bool = True
    # What 'Speel het huis' set (E-321, B-191): it stays until 'Gewoon' (None).
    weather: str | None = None  # the weather's condition: sunny, cloudy, rainy …
    outdoor: float | None = None  # °C outside
    sky: float | None = None  # sun on the panels: 1.0 = clear … 0.15 = heavy clouds
    price: tuple[datetime, datetime, float] | None = None  # from, until, € per kWh
    _series_cache: dict[date, list[dict[str, Any]]] = field(default_factory=dict)

    # -- weather and prices ---------------------------------------------------------------

    def _day_rng(self, day: date, salt: str) -> random.Random:
        return random.Random(f"{self.seed}-{day.isoformat()}-{salt}")  # noqa: S311 - demo

    def cloud(self, day: date) -> float:
        """1.0 = clear, 0.15 = heavy clouds; the same all day."""
        if self.sky is not None:
            return self.sky
        draw: float = self._day_rng(day, "cloud").random()
        return 0.15 + 0.85 * math.pow(draw, 0.7)

    def spot(self, start_local: datetime) -> float:
        """Market price per kWh for the quarter starting at ``start_local``."""
        if self.price is not None and self.price[0] <= start_local < self.price[1]:
            return self.price[2]
        day = start_local.date()
        h = start_local.hour + start_local.minute / 60
        rng = self._day_rng(day, "price")
        level = 0.085 + rng.uniform(-0.02, 0.02)
        weekend = day.weekday() >= 5
        morning = 0.06 * math.exp(-(((h - 8.0) / 1.4) ** 2))
        evening = 0.11 * math.exp(-(((h - 19.0) / 1.8) ** 2))
        sun = clear_sky(start_local) * self.cloud(day)
        dip = 0.11 * sun
        night = -0.02 if h < 5 else 0.0
        noise = random.Random(f"{self.seed}-{start_local.isoformat()}").uniform(-0.006, 0.006)  # noqa: S311
        return round(
            level + morning + evening - dip + night - (0.012 if weekend else 0.0) + noise, 4
        )

    def forget_prices(self) -> None:
        """Another price or sun was set: today's and tomorrow's prices are made again."""
        self._series_cache.clear()

    def condition(self) -> str:
        return self.weather or "cloudy"

    def _series(self, day: date) -> list[dict[str, Any]]:
        if day in self._series_cache:
            return self._series_cache[day]
        start = datetime.combine(day, time(0, 0), tzinfo=self.tz)
        out = []
        for q in range(96):
            moment = start + q * QUARTER
            out.append(
                {
                    "start": moment.isoformat(),
                    "end": (moment + QUARTER).isoformat(),
                    "value": self.spot(moment),
                }
            )
        self._series_cache = {d: s for d, s in self._series_cache.items() if d >= day}
        self._series_cache[day] = out
        return out

    def _forecast(self, day: date) -> dict[datetime, float]:
        start = datetime.combine(day, time(0, 0), tzinfo=self.tz)
        rng = self._day_rng(day, "forecast")
        out = {}
        for h in range(24):
            moment = start + timedelta(hours=h)
            expected = self.solar_peak_w * clear_sky(moment + timedelta(minutes=30))
            out[moment] = round(expected * self.cloud(day) * rng.uniform(0.85, 1.15), 1)
        return out

    # -- one step -------------------------------------------------------------------------

    async def step(self, now: datetime) -> None:
        local = now.astimezone(self.tz)
        if self.last is None:
            self.last = now
            await self._prices(local)
            return
        dt_h = min(1.0, max(0.0, (now - self.last).total_seconds() / 3600))
        self.last = now
        if dt_h <= 0:
            return
        states = self.ha.states
        day = local.date()
        weekend = day.weekday() >= 5
        outside = outdoor_c(local) if self.outdoor is None else self.outdoor
        solar = self.solar_peak_w * clear_sky(local) * self.cloud(day)
        load = house_w(local, weekend)

        # Heat pump: the house loses warmth to outside; the heat pump gives what keeps it at
        # the wanted temperature. Preheated, the house is warmer than wanted and the heat pump
        # rests until it has cooled down again – warmth is stored, not only spent.
        heat = states.get("climate.heat_pump")
        if heat is not None:
            loss = self.heat_loss_w_per_c * max(0.0, self.indoor_c - outside)
            warmth = 0.0
            if heat.state in ("heat", "auto", "heat_cool"):
                wanted = float(heat.attributes.get("temperature") or 20)
                if self.indoor_c < wanted - 0.05:
                    gap = (wanted - self.indoor_c) * self.heat_capacity_wh_per_c / 0.5
                    warmth = min(self.heat_max_w, loss + gap)  # back to wanted in ~30 min
                elif self.indoor_c <= wanted + 0.05:
                    warmth = loss
            self.indoor_c += (warmth - loss) * dt_h / self.heat_capacity_wh_per_c
            load += warmth / self.cop

        # Boiler: showers cool it down; it heats when it is 4 °C below its target.
        boiler = states.get("water_heater.boiler")
        if boiler is not None:
            target = float(boiler.attributes.get("temperature") or 50)
            h = local.hour + local.minute / 60
            if 7.0 <= h < 7.0 + dt_h or 21.0 <= h < 21.0 + dt_h:
                self.boiler_c -= 9.0
            self.boiler_c -= 0.25 * dt_h * max(0.0, self.boiler_c - 20) / 30  # hotter: faster
            if self.boiler_c < target - 4 or (
                self.boiler_c < target and boiler.state == "performance"
            ):
                self.boiler_heating = True
            if self.boiler_heating and self.boiler_c < target:
                load += self.boiler_w
                self.boiler_c = min(target, self.boiler_c + 9.0 * dt_h)
            if self.boiler_c >= target:
                self.boiler_heating = False

        # Washing machine: started by HouseMind (button), or by a person – runs two hours.
        ready = states.get("binary_sensor.washer_remote_start")
        by_hand = self.person_starts and ready is not None and ready.state == "on"
        pressed = any(
            call[0] == "button.press" and call[1] == "button.washer_start"
            for call in self.ha.calls[self._calls_seen :]
        )
        self._calls_seen = len(self.ha.calls)
        if (by_hand or pressed) and self.washer_until is None:
            self.washer_until = now + timedelta(hours=2)
            await self._set("binary_sensor.washer_remote_start", "off")
        washer = 0.0
        if self.washer_until is not None:
            if now < self.washer_until:
                washer = 520.0
            else:
                self.washer_until = None
        load += washer

        # Car: away on workdays while resident 1 is away; comes back a bit emptier.
        person = states.get("person.resident_1")
        home = person is None or person.state == "home"
        if home and not self.car_home:
            self.car_soc = max(10.0, self.car_soc - self._day_rng(day, "trip").uniform(8, 16))
        self.car_home = home
        car = 0.0
        charger = states.get("switch.charge_point")
        if home and charger is not None and charger.state == "on" and self.car_soc < 100:
            amps = (
                float(states["number.charge_current"].state)
                if "number.charge_current" in states
                else 16.0
            )
            car = amps * 230.0
            self.car_soc = min(100.0, self.car_soc + car * dt_h / 1000 / self.car_kwh * 100)

        # Home battery: follows its mode.
        mode_state = states.get("select.home_battery_mode")
        mode = mode_state.state if mode_state else "self_consumption"
        surplus = solar - load - car
        if mode == "charge":
            battery = self.battery_max_w
        elif mode == "discharge":
            battery = -self.battery_max_w
        elif mode == "idle":
            battery = 0.0
        else:
            battery = max(-self.battery_max_w, min(self.battery_max_w, surplus))
        if "sensor.home_battery" not in states:
            battery = 0.0  # a house with solar panels but no home battery (the average house)
        if battery > 0 and self.battery_soc >= 100:
            battery = 0.0
        if battery < 0 and self.battery_soc <= 5:
            battery = 0.0
        stored = battery * dt_h / 1000 * (self.battery_efficiency if battery > 0 else 1.0)
        self.battery_soc = max(5.0, min(100.0, self.battery_soc + stored / self.battery_kwh * 100))

        grid = load + car + battery - solar
        self._count("import", max(0.0, grid) * dt_h / 1000)
        self._count("export", max(0.0, -grid) * dt_h / 1000)
        self._count("solar", solar * dt_h / 1000)
        self._count("battery_in", max(0.0, battery) * dt_h / 1000)
        self._count("battery_out", max(0.0, -battery) * dt_h / 1000)

        await self._set("sensor.grid_power", f"{grid:.0f}")
        # The current per phase at the meter: the house spread over three phases, the car on
        # the first (a charger without phase switching charges on one phase).
        rest = abs(grid - car) / 3 / 230.0
        for phase, amps in ((1, rest + car / 230.0), (2, rest), (3, rest)):
            await self._set(f"sensor.grid_current_l{phase}", f"{amps:.1f}")
        await self._set("sensor.solar_power", f"{solar:.0f}")
        await self._set("sensor.home_battery_power", f"{battery:.0f}")
        await self._set("sensor.home_battery", f"{self.battery_soc:.1f}")
        await self._set("sensor.charge_point_power", f"{car:.0f}")
        await self._set("sensor.car_battery", f"{self.car_soc:.1f}")
        await self._set("sensor.washer_power", f"{washer:.0f}")
        await self._set("binary_sensor.charge_point_plug", "on" if home else "off")
        for key, entity in (
            ("import", "sensor.grid_import"),
            ("export", "sensor.grid_export"),
            ("solar", "sensor.solar_energy"),
            ("battery_in", "sensor.home_battery_in"),
            ("battery_out", "sensor.home_battery_out"),
        ):
            await self._set(entity, f"{self.counters.get(key, 0.0):.3f}")
        if boiler is not None:
            await self._set(
                "water_heater.boiler",
                boiler.state,
                {"current_temperature": round(self.boiler_c * 2) / 2},
            )
        if heat is not None:
            await self._set(
                "climate.heat_pump", heat.state, {"current_temperature": round(self.indoor_c, 1)}
            )
        await self._set("weather.home", self.condition(), {"temperature": round(outside * 2) / 2})
        # Someone loads the washing machine on Saturday morning and allows a remote start.
        if (
            day.weekday() == 5
            and local.hour == 9
            and local.minute < 10
            and self.washer_until is None
        ):
            await self._set("binary_sensor.washer_remote_start", "on")
        await self._prices(local)

    def _count(self, key: str, kwh: float) -> None:
        self.counters[key] = self.counters.get(key, 0.0) + kwh

    async def _set(self, entity_id: str, state: str, attrs: dict[str, Any] | None = None) -> None:
        current = self.ha.states.get(entity_id)
        if current is None:
            return
        if current.state == state and all(
            current.attributes.get(k) == v for k, v in (attrs or {}).items()
        ):
            return
        await self.ha.simulate(entity_id, state, attrs, by=Source.PHYSICAL)

    async def prices_now(self, now: datetime) -> None:
        """Publish the prices at once (after 'Speel het huis' set another price)."""
        await self._prices(now.astimezone(self.tz))

    async def _prices(self, local: datetime) -> None:
        if "sensor.electricity_price" not in self.ha.states:
            return
        quarter = local.replace(minute=local.minute - local.minute % 15, second=0, microsecond=0)
        today = local.date()
        tomorrow = today + timedelta(days=1)
        published = local.time() >= time(13, 0)
        attrs = {
            "raw_today": self._series(today),
            "raw_tomorrow": self._series(tomorrow) if published else [],
        }
        self.ha.solar_forecast_wh = {**self._forecast(today), **self._forecast(tomorrow)}
        price = self.ha.states["sensor.electricity_price"]
        value = f"{self.spot(quarter):.4f}"
        if (
            price.state != value
            or not price.attributes.get("raw_today")
            or (published and not price.attributes.get("raw_tomorrow"))
        ):
            await self.ha.simulate("sensor.electricity_price", value, attrs, by=Source.PHYSICAL)
