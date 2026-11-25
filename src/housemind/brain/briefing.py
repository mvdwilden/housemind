"""Morning briefing and bedtime check (E-107).

In the morning, when the house clearly wakes up: the weather, what is in the calendar today
and one sentence about energy – each part can be switched off per person. Before going to bed:
"is everything closed and off?". HouseMind reports, it never locks, opens or closes for this
(hard limits, E-31); lights left on in empty rooms go off by themselves anyway.
Calendar titles and the weather stay in the house (E-15).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from housemind.core.model import HouseModel, State
from housemind.knowledge.area_roles import SLEEPING_ROLES

Text = Callable[..., str]
PARTS = ("weather", "agenda", "energy")
OPENINGS = {"door", "window", "opening", "garage_door"}
GARAGE_COVERS = {"garage", "gate"}


def weather_entity(model: HouseModel, states: dict[str, State]) -> str | None:
    found = sorted(e for e in states if e.startswith("weather."))
    return found[0] if found else None


def _number(value: Any) -> int | None:
    return round(float(value)) if isinstance(value, int | float) else None


def weather_line(
    t: Text, lang: str, forecast: list[dict[str, Any]], current: State | None, local: datetime
) -> str | None:
    today = next(
        (day for day in forecast if str(day.get("datetime", ""))[:10] == local.date().isoformat()),
        forecast[0] if forecast else None,
    )
    condition = (today or {}).get("condition") or (current.state if current else None)
    if not condition or condition in ("unknown", "unavailable"):
        return None
    name = t(lang, f"weather.{condition}")
    high = _number((today or {}).get("temperature"))
    low = _number((today or {}).get("templow"))
    if high is not None and low is not None:
        line = t(lang, "briefing.weather", condition=name, low=low, high=high)
    else:
        now = _number(current.attributes.get("temperature")) if current else None
        if now is None:
            return None
        line = t(lang, "briefing.weather_now", condition=name, temp=now)
    rain = _number((today or {}).get("precipitation_probability"))
    if rain is not None and rain >= 50:
        line = f"{line} {t(lang, 'briefing.rain', pct=rain)}"
    return line


def agenda_line(
    t: Text, lang: str, events: list[dict[str, Any]], tz: Any, titles: bool = True
) -> str | None:
    """What is in the calendars today: the first timed event, or the first all-day one.
    ``titles=False``: only that there is something and when – what an AI may get (QA 2,
    B-173: never the title of an appointment)."""
    timed: list[tuple[datetime, str]] = []
    all_day: list[str] = []
    for event in events:
        start, title = str(event.get("start", "")), str(event.get("summary", "")).strip()
        if not title:
            continue
        if "T" in start:
            try:
                timed.append((datetime.fromisoformat(start).astimezone(tz), title))
            except ValueError:
                continue
        else:
            all_day.append(title)
    if timed:
        timed.sort()
        at, title = timed[0]
        return t(
            lang,
            "briefing.agenda" if titles else "briefing.agenda_ai",
            count=len(timed) + len(all_day),
            time=at.strftime("%H:%M"),
            title=title,
        )
    if all_day:
        return t(
            lang, "briefing.agenda_day" if titles else "briefing.agenda_day_ai", title=all_day[0]
        )
    return None


def findings(
    model: HouseModel,
    states: dict[str, State],
    area_roles: dict[str, str],
    occupied: set[str],
    openings: set[str] | None = None,
    not_lamps: set[str] | frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """What is still open or on: doors and windows, locks, the garage door, and lights in
    empty rooms that are not bedrooms – not a device's status light (E-426). As (kind, device
    name)."""
    found: list[tuple[str, str]] = []
    lit: set[str] = set()
    for entity in sorted(model.entities.values(), key=lambda e: e.name or e.entity_id):
        state = states.get(entity.entity_id)
        if state is None:
            continue
        name = entity.name or entity.entity_id
        if entity.domain == "binary_sensor" and entity.device_class in OPENINGS:
            if state.state == "on" and (openings is None or entity.entity_id in openings):
                found.append(("open", name))
        elif entity.domain == "lock" and state.state == "unlocked":
            found.append(("unlocked", name))
        elif entity.domain == "cover" and entity.device_class in GARAGE_COVERS:
            if state.state in ("open", "opening"):
                found.append(("open", name))
        elif entity.domain == "light" and state.state == "on" and entity.entity_id not in not_lamps:
            # A lamp by the group that steers it, once; nothing left alone (E-458).
            unit = model.unit(entity.entity_id)
            if unit is None or unit in lit:
                continue
            lit.add(unit)
            area = model.area_of(unit)
            if (
                area is not None
                and area.id not in occupied
                and area_roles.get(area.id) not in SLEEPING_ROLES
            ):
                own = model.entities.get(unit)
                found.append(("light", (own.name or unit) if own else name))
    return found


def findings_line(t: Text, lang: str, items: list[tuple[str, str]], key: str) -> str:
    parts = [t(lang, f"bedtime.item.{kind}", device=name) for kind, name in items[:4]]
    if len(items) > 4:
        parts.append(t(lang, "bedtime.item.more", count=len(items) - 4))
    joined = (
        ", ".join(parts[:-1]) + f" {t(lang, 'ui.and')} " + parts[-1] if len(parts) > 1 else parts[0]
    )
    return t(lang, key, items=joined)
