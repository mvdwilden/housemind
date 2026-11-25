"""Measuring the saving honestly (S-03) and the base load (E-77)."""

from __future__ import annotations

from datetime import date, timedelta

from housemind.energy.savings import base_load_w, expected_for, fit

START = date(2026, 10, 19)  # a Monday


def _day(
    i: int,
    kwh: float,
    dd: float,
    sun: float,
    car: float = 0.0,
    price: float = 0.25,
    **extra: object,
) -> dict[str, object]:
    return {
        "day": (START + timedelta(days=i)).isoformat(),
        "import_kwh": kwh + car,
        "car_kwh": car,
        "degree_days": dd,
        "solar_kwh": sun,
        "avg_price": price,
        "cost": (kwh + car) * price * 1.1,  # the house buys 10 % above the day's average
        "export_kwh": 0.0,
        "planning": False,
        **extra,
    }


def test_the_baseline_learns_cold_sun_weekend_and_how_dear_the_house_buys() -> None:
    weather = [(8, 4), (10, 2), (6, 9), (12, 1), (9, 5), (7, 7), (11, 3), (5, 10), (9, 2), (8, 8)]
    days = []
    for i, (dd, sun) in enumerate(weather):
        weekend = (START + timedelta(days=i)).weekday() >= 5
        own = 10 + 1.2 * dd - 0.8 * sun + (2.0 if weekend else 0.0)
        days.append(_day(i, own, dd, sun, car=7.0 if not weekend else 0.0))
    model = fit(days)
    assert model is not None and model.days == 10
    assert abs(model.fixed - 10) < 1e-6 and abs(model.per_degree_day - 1.2) < 1e-6
    assert abs(model.per_solar_kwh + 0.8) < 1e-6 and abs(model.weekend - 2.0) < 1e-6
    assert abs(model.price_ratio - 1.1) < 1e-9
    # A later day: the car charged 5 kWh; HouseMind bought at the day's average price.
    later = _day(21, 0.0, 9.0, 4.0, car=5.0, price=0.30)  # a Monday
    kwh, cost = expected_for(model, later)  # type: ignore[misc]
    assert abs(kwh - (10 + 1.2 * 9 - 0.8 * 4 + 5)) < 1e-6
    assert abs(cost - kwh * 0.30 * 1.1) < 1e-6


def test_physics_wins_over_noise() -> None:
    # Colder days happen to be cheaper here: the model refuses "colder means less".
    days = [_day(i, 20 - dd, dd, 5.0) for i, dd in enumerate((5, 6, 7, 8, 9, 10, 11, 12))]
    model = fit(days)
    assert model is not None and model.per_degree_day == 0.0


def test_too_few_days_and_plan_days_do_not_count() -> None:
    assert fit([_day(i, 20, 8, 4) for i in range(6)]) is None
    assert fit([_day(i, 20, 8, 4, planning=True) for i in range(10)]) is None


def test_base_load_is_the_low_night_use() -> None:
    assert base_load_w([300.0] * 5) is None  # too few quarters
    night = [180.0, 190.0, 175.0, 400.0, 185.0, 182.0, 900.0, 178.0, 181.0, 179.0, 183.0, 177.0]
    assert base_load_w(night) == 177.0
