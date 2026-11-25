"""What each block of the plan should bring, and what it brought (design 36.7, 36.8, E-378).

Every block HouseMind carried out gets a line: the part (boiler, heat_pump, appliance, export),
when, what it expected (36.2) and – once the warmth has been used – what the measurements say it
brought. That is an estimate on measurements, not a second house: so a part only rests after
three weeks that together cost money, never after one block (K2, K3). A resting part gets one
careful try a week after four weeks; when that pays, it is back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from housemind.memory.database import Database

SETTING = "energy_ledger"
KEEP = timedelta(days=60)
WINDOW = timedelta(days=21)  # K2: three weeks together
SPREAD = timedelta(days=14)  # ... with measured blocks over at least two of them
MIN_BLOCKS = 3
REST = timedelta(days=28)  # K2: four weeks, then a careful try a week
TRY_EVERY = timedelta(days=7)
PARTS = ("boiler", "heat_pump", "appliance", "export")


@dataclass(slots=True)
class Block:
    part: str
    key: str
    start: str
    end: str
    expected: float
    measured: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Ledger:
    db: Database
    blocks: list[Block] = field(default_factory=list)
    rest: dict[str, str] = field(default_factory=dict)  # part -> since (ISO)
    # New rests since the last look, for the administrators' message: (part, amount).
    news: list[tuple[str, float]] = field(default_factory=list)
    _saved: str = ""

    async def load(self) -> None:
        raw = await self.db.get_setting(SETTING) or {}
        for item in raw.get("blocks") or []:
            try:
                self.blocks.append(Block(**item))
            except TypeError:
                continue
        self.rest = {str(k): str(v) for k, v in (raw.get("rest") or {}).items()}
        self._saved = repr(self._dump())

    def _dump(self) -> dict[str, Any]:
        return {"blocks": [asdict(b) for b in self.blocks], "rest": self.rest}

    async def save(self, now: datetime) -> None:
        cut = (now - KEEP).isoformat()
        self.blocks = [b for b in self.blocks if b.end >= cut]
        dump = self._dump()
        if repr(dump) != self._saved:
            self._saved = repr(dump)
            await self.db.set_setting(SETTING, dump)

    def find(self, key: str) -> Block | None:
        return next((b for b in self.blocks if b.key == key), None)

    def open(
        self, part: str, key: str, start: datetime, end: datetime, expected: float, **data: Any
    ) -> Block:
        found = self.find(key)
        if found is not None:
            return found
        for block in self.blocks:  # the plan is made again each quarter: the same block
            if (
                block.part == part
                and block.measured is None
                and block.start <= start.isoformat() <= block.end
            ):
                block.end = max(block.end, end.isoformat())
                return block
        block = Block(part, key, start.isoformat(), end.isoformat(), expected, None, dict(data))
        self.blocks.append(block)
        return block

    def running(self, part: str, now: datetime) -> Block | None:
        """The block of this part that is going on now (not measured yet)."""
        moment = now.isoformat()
        for block in self.blocks:
            if block.part == part and block.measured is None and block.start <= moment:
                if moment <= block.end:
                    return block
        return None

    def due(self, now: datetime) -> list[Block]:
        """Blocks whose warmth has been used: time to see what they brought."""
        out = []
        for block in self.blocks:
            if block.measured is not None:
                continue
            until = datetime.fromisoformat(str(block.data.get("measure_at") or block.end))
            if now >= until:
                out.append(block)
        return out

    def close(self, block: Block, measured: float, now: datetime) -> None:
        block.measured = round(measured, 4)
        if block.part in self.rest and measured > 0:
            del self.rest[block.part]  # the careful try paid: back (36.8)
            return
        self._evaluate(block.part, now)

    def _evaluate(self, part: str, now: datetime) -> None:
        if part in self.rest:
            return
        since = now - WINDOW
        measured = [
            b
            for b in self.blocks
            if b.part == part
            and b.measured is not None
            and datetime.fromisoformat(b.start) >= since
        ]
        if len(measured) < MIN_BLOCKS:
            return
        first = min(datetime.fromisoformat(b.start) for b in measured)
        total = sum(b.measured or 0.0 for b in measured)
        if now - first >= SPREAD and total < 0:
            self.rest[part] = now.isoformat()
            self.news.append((part, total))

    def resting(self, now: datetime) -> frozenset[str]:
        """Parts that rest now – except a careful try once a week after four weeks."""
        out = set()
        for part, since in self.rest.items():
            started = datetime.fromisoformat(since)
            last = max(
                (datetime.fromisoformat(b.start) for b in self.blocks if b.part == part),
                default=None,
            )
            trying = now - started >= REST and (last is None or now - last >= TRY_EVERY)
            if not trying:
                out.add(part)
        return frozenset(out)

    def saved(self, part: str, now: datetime, days: int = 28) -> tuple[float, int]:
        """What this part brought in the last ``days``: (€, blocks measured)."""
        since = (now - timedelta(days=days)).isoformat()
        found = [b for b in self.blocks if b.part == part and b.start >= since]
        done = [b for b in found if b.measured is not None]
        return sum(b.measured or 0.0 for b in done), len(done)
