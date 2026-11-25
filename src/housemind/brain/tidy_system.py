"""Home Assistant tidy, part C (E-356 – E-358, B-240 – B-242): automations that have not run
for a long time, the system (backup, database, disk) and what changed after an update. Pure
rules: the round in ``parts/tidy.py`` gives them what HouseMind already has, plus Home
Assistant's system health and backups, read once a night and on 'Nu nakijken'."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from housemind.brain.tidy import Finding
from housemind.core.model import HouseModel, State
from housemind.ha.adapter import HAConfigEntry

UNUSED_AFTER = timedelta(days=90)  # E-356
SEASON_AFTER = timedelta(days=365)  # a season's automation waits a year
BACKUP_OLD = timedelta(days=7)  # important from then on (B-241)
DISK_INFO, DISK_FULL = 0.85, 0.95
# The margin in backups (E-397, B-314): important when fewer than 2 more fit, info below 4.
ROOM_IMPORTANT, ROOM_INFO = 2, 4
LOOSE_AFTER = timedelta(days=30)  # a loose backup this old is a card (E-397)
LOCAL_AGENTS = ("hassio.local", "backup.local")  # this device's own place for backups
DB_GROWS = 100 * 1024**2  # bytes a day: something to look at (info)
DB_KEEP = 14  # nightly sizes kept
GROWTH_DAYS = 7  # the growth a day is the average over this many days
BUSIEST = 3  # entities named that change most often
# When HouseMind looks again after an update, counted from the new connection (B-242).
UPDATE_CHECKS = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
    timedelta(hours=24),
)
UPDATE_KEEP = timedelta(days=30)
PARTS = ("core", "os", "supervisor")  # what an update is of
_UNITS = {
    "B": 1.0,
    "KB": 1e3,
    "MB": 1e6,
    "GB": 1e9,
    "TB": 1e12,
    "KIB": 2.0**10,
    "MIB": 2.0**20,
    "GIB": 2.0**30,
    "TIB": 2.0**40,
}
_SIZE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([KMGT]?I?B)\s*$", re.IGNORECASE)


# -- automations that have not run for a long time (B-240) ---------------------------------


@dataclass(frozen=True, slots=True)
class Auto:
    """One automation as the rule needs it."""

    entity_id: str
    name: str
    on: bool
    last_ran: datetime | None  # Home Assistant's last_triggered
    first_seen: datetime | None  # when HouseMind first saw it
    config_id: str | None = None
    words: str = ""  # its name, labels and categories, in lower case: for a season


def is_season(words: str, season: Iterable[str]) -> bool:
    """A word for a season at the start of a word: 'kerstverlichting', 'Zomertijd rolluik'."""
    return any(re.search(rf"(?<!\w){re.escape(w)}", words) for w in season if w)


def unused(
    autos: Iterable[Auto], now: datetime, season: Iterable[str], skip: set[str]
) -> list[Finding]:
    """On but not run for 90 days, or off and not run for 90 days (E-356, as the administrator
    chose on 27-10-2026: Home Assistant starts 'off since' again at every restart). Never run:
    counted from when HouseMind first saw it. A season's automation waits a year."""
    words = [w for w in season if w]
    out: list[Finding] = []
    for a in autos:
        if a.entity_id in skip:
            continue
        since = a.last_ran or a.first_seen
        if since is None:
            continue
        in_season = is_season(a.words, words)
        if now - since < (SEASON_AFTER if in_season else UNUSED_AFTER):
            continue
        kind = "unused_on" if a.on else "unused_off"
        out.append(
            Finding(
                f"{kind}:{a.entity_id}",
                kind,
                a.entity_id,
                a.name,
                since,
                detail={
                    "days": (now - since).days,
                    "never": a.last_ran is None,
                    "season": in_season,
                    "config_id": a.config_id,
                    "kind": "automation",
                },
            )
        )
    return sorted(out, key=lambda f: (f.since, f.name))


# -- the system (B-241) ----------------------------------------------------------------------


def size_bytes(text: Any) -> float | None:
    """'1508.73 MiB' or '62.3 GB' as bytes; None when it is no size."""
    if isinstance(text, int | float):
        return float(text)
    found = _SIZE.match(str(text or ""))
    if found is None:
        return None
    number = float(found.group(1).replace(",", "."))
    return number * _UNITS[found.group(2).upper()]


def backup_tile(backups: list[dict[str, Any]] | None, now: datetime, own: bool) -> dict[str, Any]:
    """The newest backup that holds Home Assistant itself, from any place; a backup of one
    app does not count. 7 days or older is important – unless the administrator makes backups
    outside Home Assistant (B-244), then only the age stays."""
    if backups is None:
        return {"state": "unknown"}
    dates = []
    for b in backups:
        if not b.get("homeassistant_included"):
            continue
        try:
            dates.append(datetime.fromisoformat(str(b.get("date"))))
        except ValueError:
            continue
    if not dates:
        return {"state": "own" if own else "none", "heavy": not own}
    newest = max(dates)
    age = now - newest
    state = "own" if own else ("old" if age >= BACKUP_OLD else "ok")
    return {"state": state, "at": newest.isoformat(), "days": age.days, "heavy": state == "old"}


def growth_per_day(sizes: dict[str, float]) -> float | None:
    """The growth a day, the average over the last 7 days of nightly sizes (by date)."""
    days = sorted(sizes)[-(GROWTH_DAYS + 1) :]
    if len(days) < 2:
        return None
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days
    return (sizes[days[-1]] - sizes[days[0]]) / span if span > 0 else None


def busiest(counts: dict[str, int], seconds: float, n: int = BUSIEST) -> list[dict[str, Any]]:
    """The entities whose state changes most often, with every how many seconds. HouseMind
    counts the changes it gets; which of them Home Assistant does not keep, it does not know."""
    top = sorted(((e, c) for e, c in counts.items() if c > 0), key=lambda kv: (-kv[1], kv[0]))
    return [{"entity": e, "every": seconds / c} for e, c in top[:n] if seconds > 0]


def database_tile(
    health: dict[str, Any], sizes: dict[str, float], busy: list[dict[str, Any]]
) -> dict[str, Any]:
    """How big the database is and how much it grows a day (more than 100 MB: info)."""
    info = (health.get("recorder") or {}).get("info") or {}
    size = size_bytes(info.get("estimated_db_size"))
    if size is None:
        return {"state": "unknown"}
    growth = growth_per_day(sizes)
    return {
        "state": "grows" if growth is not None and growth > DB_GROWS else "ok",
        "bytes": size,
        "engine": info.get("database_engine"),
        "growth": growth,
        "busiest": busy,
    }


def disk_tile(health: dict[str, Any], backup: float | None = None) -> dict[str, Any]:
    """How full the disk is, from the Supervisor's part of the system health. With the size of
    a backup also how many more fit (E-397, B-314): important when fewer than 2 fit or from
    95 %, info when fewer than 4 fit or from 85 % – whichever comes first. Without a
    Supervisor: unknown."""
    info = (health.get("hassio") or {}).get("info") or {}
    total, used = size_bytes(info.get("disk_total")), size_bytes(info.get("disk_used"))
    if not total or used is None:
        return {"state": "unknown"}
    share = used / total
    free = max(0.0, total - used)
    fits = free / backup if backup else None
    if share >= DISK_FULL or (fits is not None and fits < ROOM_IMPORTANT):
        state = "full"
    elif share >= DISK_INFO or (fits is not None and fits < ROOM_INFO):
        state = "filling"
    else:
        state = "ok"
    tile: dict[str, Any] = {
        "state": state,
        "used": used,
        "total": total,
        "usedText": str(info.get("disk_used")),  # as Home Assistant says it
        "totalText": str(info.get("disk_total")),
        "share": share,
        "free": free,
        "heavy": state == "full",
    }
    if fits is not None:
        tile["backup"] = backup
        tile["room"] = int(fits)  # rounded down: 'Nog ruimte voor ongeveer 9 back-ups'
    return tile


# -- backups: the margin, what Home Assistant keeps, loose ones (E-397, B-314) -------------


def local_size(backup: dict[str, Any]) -> float | None:
    """How much room a backup takes on this device: its size at this device's own place
    (``agents.<place>.size``). None when it is only somewhere else, such as the cloud."""
    agents = backup.get("agents")
    if isinstance(agents, dict):
        for agent in LOCAL_AGENTS:
            size = _dict(agents.get(agent)).get("size")
            if isinstance(size, int | float) and size > 0:
                return float(size)
        return None
    size = backup.get("size")  # an older Home Assistant: one size, on this device
    return float(size) if isinstance(size, int | float) and size > 0 else None


def on_device(backups: list[dict[str, Any]] | None) -> float | None:
    """What all backups together take on this device (E-451): a part of the disk ring. None
    when Home Assistant did not say which backups there are."""
    if backups is None:
        return None
    return sum(size for b in backups if (size := local_size(b)) is not None)


def _date(backup: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(backup.get("date")))
    except ValueError:
        return None


def backup_size(backups: list[dict[str, Any]] | None) -> float | None:
    """The measure for the margin: the newest automatic backup on this device; without one
    the newest on this device that holds Home Assistant itself. None: only percentages."""
    for automatic in (True, False):
        found = [
            (moment, size)
            for b in backups or []
            if (b.get("with_automatic_settings") is True or not automatic)
            and b.get("homeassistant_included")
            and (moment := _date(b)) is not None
            and (size := local_size(b)) is not None
        ]
        if found:
            return max(found)[1]
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def retention(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """What Home Assistant does with its automatic backups, from ``backup/config/info``: how
    often (``daily``, ``custom_days`` with the days, or ``never``) and how many it keeps
    (``copies``) or how many days (``keepDays``). A place's own rule wins for this device.
    Nothing else of the answer: never its password. None: not known."""
    if not isinstance(config, dict):
        return None
    schedule = _dict(config.get("schedule"))
    recurrence = schedule.get("recurrence")
    if recurrence not in ("daily", "custom_days", "never"):
        return None
    keep = _dict(config.get("retention"))
    agents = _dict(config.get("agents"))
    for agent in LOCAL_AGENTS:
        own = _dict(agents.get(agent)).get("retention")
        if isinstance(own, dict):
            keep = own
            break
    copies, days = keep.get("copies"), keep.get("days")
    return {
        "recurrence": recurrence,
        "days": [d for d in schedule.get("days") or [] if isinstance(d, str)],
        "copies": copies if isinstance(copies, int) and copies > 0 else None,
        "keepDays": days if isinstance(days, int) and days > 0 else None,
    }


def keeps_all(keep: dict[str, Any] | None) -> bool:
    """Home Assistant makes automatic backups and keeps them all: the disk fills by itself."""
    return (
        keep is not None
        and keep["recurrence"] != "never"
        and keep["copies"] is None
        and keep["keepDays"] is None
    )


def loose(
    backups: list[dict[str, Any]] | None, now: datetime, kept: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """The loose backups Home Assistant does not clear itself (made by hand or before an
    update of an app), on this device and 30 days or older; the oldest first. Not the ones
    the administrator keeps on purpose."""
    skip = set(kept)
    out = []
    for b in backups or []:
        if b.get("with_automatic_settings") is True:
            continue
        moment, size = _date(b), local_size(b)
        if moment is None or size is None or now - moment < LOOSE_AFTER:
            continue
        backup_id = str(b.get("backup_id") or "")
        if not backup_id or backup_id in skip:
            continue
        out.append(
            {
                "id": backup_id,
                "name": str(b.get("name") or backup_id),
                "date": moment.isoformat(),
                "size": size,
            }
        )
    return sorted(out, key=lambda x: x["date"])


# -- after an update (B-242) -----------------------------------------------------------------


def versions(core: str | None, states: dict[str, State]) -> dict[str, str]:
    """Home Assistant's version, and those of the operating system and the Supervisor from
    their update entities (no extra question to Home Assistant)."""
    found: dict[str, str] = {"core": core} if core else {}
    for entity_id, state in states.items():
        if not entity_id.startswith("update."):
            continue
        installed = state.attributes.get("installed_version")
        if not installed:
            continue
        url = str(state.attributes.get("release_url") or "")
        title = str(state.attributes.get("title") or "")
        if "home-assistant/operating-system" in url or title == "Home Assistant Operating System":
            found["os"] = str(installed)
        elif "home-assistant/supervisor" in url or title == "Home Assistant Supervisor":
            found["supervisor"] = str(installed)
    return found


def changed(before: dict[str, str], now: dict[str, str]) -> list[str]:
    """What was updated: a part whose version is known both times and differs."""
    return [p for p in PARTS if before.get(p) and now.get(p) and before[p] != now[p]]


def working(
    model: HouseModel, states: dict[str, State], entries: list[HAConfigEntry] | None
) -> tuple[list[str], list[str]]:
    """What works now: the entities that are switched on and not unavailable, and the
    integrations that are loaded. 'Unknown' is no fault here: many entities have no value until
    something happens."""
    entities = sorted(
        e
        for e, x in model.entities.items()
        if not x.disabled and e in states and states[e].state != "unavailable"
    )
    loaded = sorted(x.entry_id for x in entries or [] if x.state == "loaded")
    return entities, loaded


def compare(
    before: dict[str, Any],
    model: HouseModel,
    states: dict[str, State],
    entries: list[HAConfigEntry] | None,
    quiet: set[str],
) -> tuple[list[str], list[str]]:
    """What worked just before the update and does not now: entities, and integrations that
    no longer load. Something someone removed or switched off is no fault; what is 'Bewust uit'
    does not count."""
    lost: list[str] = []
    for entity_id in before.get("working") or []:
        entity = model.entities.get(entity_id)
        if entity is None or entity.disabled:
            continue
        if quiet & {entity_id, entity.device_id or "", entity.config_entry_id or ""}:
            continue
        state = states.get(entity_id)
        if state is None or state.state == "unavailable":
            lost.append(entity_id)
    lost_entries: list[str] = []
    if entries is not None:
        now_by_id = {x.entry_id: x for x in entries}
        lost_entries = [
            x
            for x in before.get("loaded") or []
            if x in now_by_id and now_by_id[x].state != "loaded" and x not in quiet
        ]
    return sorted(lost), sorted(lost_entries)


def check_due(record: dict[str, Any], now: datetime) -> bool:
    """A next look after 30 minutes, 2, 6 and 24 hours (B-242)."""
    done = len(record.get("checks") or [])
    if done >= len(UPDATE_CHECKS) or not record.get("before"):
        return False
    return now >= datetime.fromisoformat(str(record["at"])) + UPDATE_CHECKS[done]


def check(
    record: dict[str, Any], lost: list[str], lost_entries: list[str], now: datetime
) -> dict[str, Any]:
    """One look: what does not work since, and what was away at an earlier look and works
    again. After the last look the state from before is no longer needed."""
    ever = sorted(set(record.get("ever") or []) | set(lost))
    checks = [*(record.get("checks") or []), now.isoformat()]
    out = {
        **record,
        "checks": checks,
        "lost": lost,
        "lost_entries": lost_entries,
        "back": [e for e in ever if e not in lost],
        "ever": ever,
    }
    if len(checks) >= len(UPDATE_CHECKS):
        out.pop("before", None)
    return out


def next_check(record: dict[str, Any]) -> datetime | None:
    done = len(record.get("checks") or [])
    if done >= len(UPDATE_CHECKS) or not record.get("at"):
        return None
    return datetime.fromisoformat(str(record["at"])) + UPDATE_CHECKS[done]
