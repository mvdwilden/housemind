"""The weekly house report (E-91, B-33): what HouseMind did, learned, saved and noticed.

Once a week (Sunday evening, each person can pick another day), per level: Informed gets two
short sentences, Involved a short list, Expert the list with a few extra numbers. Carefree
gets nothing – it just works (E-17). The numbers come from what HouseMind already keeps; the
report is stored so the brain screen can show the latest one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

Text = Callable[..., str]
REPORT_AT = (19, 30)  # local time on the chosen day; after the daily bundle (B-30)
DOMAINS = {
    "light": "lighting",
    "cover": "covers",
    "climate": "climate",
    "fan": "air",
    "valve": "water",
}


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def due(local: datetime, report_day: int, sent_week: str | None) -> bool:
    """Is this person's report due now? ``report_day``: 0 = Monday … 6 = Sunday."""
    if local.weekday() != report_day or (local.hour, local.minute) < REPORT_AT:
        return False
    return sent_week != week_key(local.date())


def domain_of(entity_id: str, skill: str | None) -> str:
    if skill and skill.startswith("energy_"):
        return "energy"
    return DOMAINS.get(entity_id.split(".", 1)[0], "other")


def lines(
    t: Text, lang: str, level: int, data: dict[str, Any], money: Callable[[float], str]
) -> list[str]:
    """The report as sentences for one level and language."""
    did: dict[str, int] = data.get("did", {})
    total = sum(did.values())
    undone = int(data.get("undone", 0))
    saved = data.get("saved")
    if level <= 1:
        return []
    if level == 2:
        if total == 0:
            out = [t(lang, "report.quiet")]
        else:
            out = [t(lang, "report.short", count=total, undone=undone)]
        if isinstance(saved, int | float) and saved > 0:
            out.append(t(lang, "report.saved_short", money=money(float(saved))))
        return out
    out = []
    if total:
        parts = ", ".join(
            f"{t(lang, f'report.domain.{d}')} {n}"
            for d, n in sorted(did.items(), key=lambda x: -x[1])
        )
        out.append(t(lang, "report.line.did", count=total, parts=parts))
    else:
        out.append(t(lang, "report.quiet"))
    out.append(t(lang, "report.line.undone", count=undone))
    for item in data.get("learned", [])[:4]:
        out.append(
            t(lang, f"report.line.learned_{item['step']}", skill=item["skill"], area=item["area"])
        )
    yes, no = int(data.get("yes", 0)), int(data.get("no", 0))
    if yes or no:
        out.append(t(lang, "report.line.asked", yes=yes, no=no))
    if data.get("measuring"):
        out.append(t(lang, "report.line.measuring", count=int(data.get("days_left", 0))))
    elif isinstance(saved, int | float):
        key = "report.line.saved" if saved >= 0 else "report.line.cost"
        out.append(t(lang, key, money=money(abs(float(saved)))))
    if data.get("days"):
        out.append(t(lang, "report.line.days", count=int(data["days"])))
    if data.get("water_l"):
        out.append(t(lang, "report.line.water", litres=int(data["water_l"])))
    if data.get("base_w"):
        out.append(t(lang, "report.line.base", watts=int(data["base_w"])))
    if data.get("updates"):
        out.append(t(lang, "report.line.updates", count=int(data["updates"])))
    if level >= 4:
        out.append(
            t(
                lang,
                "report.line.thoughts",
                proposed=int(data.get("proposed", 0)),
                denied=int(data.get("denied", 0)),
            )
        )
    return out


def since(local: datetime) -> datetime:
    """The start of the week the report is about: seven days back from now."""
    return local - timedelta(days=7)
