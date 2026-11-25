"""Measuring the saving honestly (S-03, E-101) and the base load (E-77).

The first two weeks HouseMind only measures. From those days it learns two simple things:

1. **how much** the house takes from the grid on a day, from the cold (degree days), the sun
   and whether it is a weekend – with the directions physics allows (colder never means less,
   more sun never means more); what the car charges counts one to one;
2. **how dear** the house buys: the price it paid compared with that day's average price.

After the baseline, each day is compared with "the same day without HouseMind": the expected
kWh at that day's average price times the baseline ratio. Extra use by HouseMind (battery
losses, extra hot water) counts as cost, buying at better moments counts as a saving. Shown
honestly, also when it turns out worse. No promised percentage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

MIN_DAYS = 7


@dataclass(frozen=True, slots=True)
class BaselineModel:
    fixed: float  # kWh per day from the grid for the house itself
    per_degree_day: float  # ≥ 0
    per_solar_kwh: float  # ≤ 0
    weekend: float
    price_ratio: float  # price paid ÷ the day's average price, during the baseline
    days: int

    def expected_kwh(
        self, degree_days: float | None, solar_kwh: float | None, weekend: bool, car_kwh: float
    ) -> float:
        own = (
            self.fixed
            + self.per_degree_day * (degree_days or 0.0)
            + self.per_solar_kwh * (solar_kwh or 0.0)
            + (self.weekend if weekend else 0.0)
        )
        return max(0.0, own) + car_kwh

    def expected_cost(self, kwh: float, avg_price: float) -> float:
        return kwh * avg_price * self.price_ratio


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination for a small system; ``None`` when it has no single answer."""
    size = len(vector)
    m = [[*row, vector[i]] for i, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-9:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(size):
            if r != col:
                factor = m[r][col] / m[col][col]
                m[r] = [a - factor * b for a, b in zip(m[r], m[col], strict=True)]
    return [m[i][size] / m[i][i] for i in range(size)]


def _least_squares(xs: list[list[float]], ys: list[float]) -> list[float] | None:
    k = len(xs[0])
    matrix = [[sum(x[i] * x[j] for x in xs) for j in range(k)] for i in range(k)]
    vector = [sum(x[i] * y for x, y in zip(xs, ys, strict=True)) for i in range(k)]
    return _solve(matrix, vector)


def _weekend(day: str) -> bool:
    return date.fromisoformat(day).weekday() >= 5


def fit(days: list[dict[str, Any]]) -> BaselineModel | None:
    """Least squares over whole baseline days, keeping the directions physics allows."""
    rows = [d for d in days if not d.get("planning") and d.get("avg_price")]
    if len(rows) < MIN_DAYS:
        return None
    ys = [float(d["import_kwh"]) - float(d.get("car_kwh") or 0.0) for d in rows]
    columns = {
        "degree_days": [float(d.get("degree_days") or 0.0) for d in rows],
        "solar": [float(d.get("solar_kwh") or 0.0) for d in rows],
        "weekend": [1.0 if _weekend(str(d["day"])) else 0.0 for d in rows],
    }
    names = [n for n in columns if len(set(columns[n])) > 1]  # only what varies
    coefficients: dict[str, float] = {}
    while True:
        xs = [[1.0, *(columns[n][i] for n in names)] for i in range(len(rows))]
        solved = _least_squares(xs, ys)
        if solved is None:
            names = names[:-1]
            continue
        found = dict(zip(["fixed", *names], solved, strict=True))
        wrong = [
            n
            for n in names
            if (n == "degree_days" and found[n] < 0) or (n == "solar" and found[n] > 0)
        ]
        if not wrong:
            coefficients = found
            break
        names = [n for n in names if n not in wrong]
    paid = sum(float(d["cost"]) + _export_value(d) for d in rows)
    at_average = sum(float(d["import_kwh"]) * float(d["avg_price"]) for d in rows)
    ratio = paid / at_average if at_average > 0 else 1.0
    return BaselineModel(
        fixed=coefficients.get("fixed", sum(ys) / len(ys)),
        per_degree_day=coefficients.get("degree_days", 0.0),
        per_solar_kwh=coefficients.get("solar", 0.0),
        weekend=coefficients.get("weekend", 0.0),
        price_ratio=ratio,
        days=len(rows),
    )


def _export_value(day: dict[str, Any]) -> float:
    """Cost is net of what was given back; add that back to get what was paid for import."""
    return (
        0.0
        if not day.get("export_kwh")
        else float(day["export_kwh"]) * float(day.get("avg_price") or 0.0)
    )


def expected_for(model: BaselineModel, day: dict[str, Any]) -> tuple[float, float] | None:
    """Expected kWh and cost of this day without HouseMind."""
    if not day.get("avg_price"):
        return None
    kwh = model.expected_kwh(
        day.get("degree_days"),
        day.get("solar_kwh"),
        _weekend(str(day["day"])),
        float(day.get("car_kwh") or 0.0),
    )
    cost = model.expected_cost(kwh, float(day["avg_price"])) - _export_value(day)
    return kwh, cost


def base_load_w(night_house_w: list[float]) -> float | None:
    """The base load: a low value of the house's own use at night (10th percentile)."""
    values = sorted(v for v in night_house_w if v > 0)
    if len(values) < 8:
        return None
    return values[len(values) // 10]
