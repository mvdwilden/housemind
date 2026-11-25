"""Learned energy profiles per day type and quarter of the day (E-72, E-162).

Explainable: a running average of what was measured in that quarter on that kind of day, so a
forecast can always be told in plain words ("on workdays around 18:00 the house uses ~900 W").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from housemind.memory.database import Database

ALPHA = 0.2  # weight of a new measurement (about the last week of that quarter)
ALL_DAYS = "all"


@dataclass(slots=True)
class Profiles:
    db: Database
    values: dict[tuple[str, str, int], tuple[float, float]] = field(default_factory=dict)

    async def load(self) -> None:
        for row in await self.db.fetchall("SELECT * FROM energy_profiles"):
            self.values[(row["kind"], row["day_type"], int(row["quarter"]))] = (
                float(row["mean"]),
                float(row["weight"]),
            )

    async def learn(self, kind: str, day_type: str, quarter: int, value: float) -> None:
        for dtype in (day_type, ALL_DAYS):
            key = (kind, dtype, quarter)
            mean, weight = self.values.get(key, (value, 0.0))
            alpha = max(ALPHA, 1.0 / (weight + 1.0))  # the first values count fully
            mean = mean + alpha * (value - mean)
            self.values[key] = (mean, weight + 1.0)
            await self.db.execute(
                "INSERT INTO energy_profiles(kind, day_type, quarter, mean, weight) "
                "VALUES(?,?,?,?,?) ON CONFLICT(kind, day_type, quarter) DO UPDATE SET "
                "mean = excluded.mean, weight = excluded.weight",
                (kind, dtype, quarter, mean, weight + 1.0),
            )

    def get(self, kind: str, day_type: str, quarter: int) -> float | None:
        for dtype in (day_type, ALL_DAYS):
            found = self.values.get((kind, dtype, quarter))
            if found is not None:
                return found[0]
        return None

    def known(self, kind: str) -> int:
        return sum(1 for k in self.values if k[0] == kind and k[1] == ALL_DAYS)
