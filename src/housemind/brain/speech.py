"""Speaking by itself (E-19, E-22, B-32).

HouseMind only speaks in a room where someone is, never while the house sleeps (except in an
emergency), at most a few times a day and at least 15 minutes apart. A shared speaker follows
the calmest level of everyone at home: with someone on Carefree at home, HouseMind says
nothing by itself. It speaks through a voice satellite (announce) or a speaker with text to
speech – a TV is never used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.core.model import HouseModel, State
from housemind.ha.adapter import HomeAssistant, Voice
from housemind.memory.database import Database

PER_DAY = {1: 0, 2: 3, 3: 6, 4: 6}  # B-32; level 4 may choose
GAP = timedelta(minutes=15)


def speakers(model: HouseModel) -> dict[str, str]:
    """Per room the device to speak through: a voice satellite first, else a speaker."""
    found: dict[str, tuple[int, str]] = {}
    for entity in sorted(model.entities.values(), key=lambda e: e.entity_id):
        area = model.area_of(entity.entity_id)
        if area is None:
            continue
        if entity.domain == "assist_satellite":
            rank = 0
        elif entity.domain == "media_player" and entity.device_class == "speaker":
            rank = 1
        else:
            continue
        if area.id not in found or rank < found[area.id][0]:
            found[area.id] = (rank, entity.entity_id)
    return {area: entity for area, (_, entity) in found.items()}


def tts_engine(model: HouseModel, states: dict[str, State]) -> str | None:
    engines = sorted(e for e in model.entities if e.startswith("tts.")) or sorted(
        e for e in states if e.startswith("tts.")
    )
    return engines[0] if engines else None


@dataclass(slots=True)
class Speech:
    db: Database
    ha: HomeAssistant
    devices: dict[str, str] = field(default_factory=dict)  # area → speaker
    tts: str | None = None
    voice: Voice | None = None  # Vesta's voice (E-230); a satellite keeps its own

    def can(self, area_id: str | None) -> bool:
        if area_id is None or area_id not in self.devices:
            return False
        device = self.devices[area_id]
        return (
            device.startswith("assist_satellite.") or self.tts is not None or self.voice is not None
        )

    async def allowed(
        self,
        area_id: str | None,
        levels_home: list[int],
        sleeping: bool,
        now: datetime,
        local: datetime,
        per_day_level4: int | None = None,
    ) -> bool:
        """May HouseMind say something by itself here, now (E-19, E-22, B-32)?"""
        if not self.can(area_id) or sleeping or not levels_home:
            return False
        calmest = min(levels_home)
        limit = PER_DAY.get(calmest, 0)
        if calmest == 4 and per_day_level4 is not None:
            limit = per_day_level4
        if limit <= 0:
            return False
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(now.tzinfo)
        row = await self.db.fetchone(
            "SELECT count(*) AS n, max(at) AS last FROM spoken "
            "WHERE at >= ? AND kind NOT IN ('emergency', 'greeting')",
            (start.isoformat(),),
        )
        if row is not None and int(row["n"]) >= limit:
            return False
        if row is not None and row["last"]:
            if now - datetime.fromisoformat(str(row["last"])) < GAP:
                return False
        return True

    async def say(
        self, area_id: str, message: str, kind: str, now: datetime, voice: Voice | None = None
    ) -> bool:
        """``voice``: hers in the language of this message, when it is not the house's (B-124)."""
        device = self.devices.get(area_id)
        if device is None:
            return False
        result = await self.ha.speak(device, message, self.tts, voice or self.voice)
        if result.ok:
            await self.db.execute(
                "INSERT INTO spoken(at, area_id, kind) VALUES(?,?,?)",
                (now.isoformat(), area_id, kind),
            )
        return result.ok
