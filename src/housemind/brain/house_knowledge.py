"""What HouseMind knows of the house (E-205, design 50.2, B-343): findings as plain data.

One finding per subject and field: the role of a room or of a switch, what an automation
does in plain words, what a sensor measures, and where the rules were unsure (B-359): which
sensor is a room's temperature, whether a contact is a door or window of the house, whether a
"light" is a device's status light. Each has its value, where it came from (``ai``,
``behaviour`` – derived from what a device did, without AI (E-203) –, ``rules`` – the rules'
own guess where they doubt – or ``person``), how sure it is, and its status: ``suggested``
until an administrator says Klopt (``confirmed``) or Klopt niet (``rejected``). Confirmed and
rejected findings are never asked again.

This is the core's side of reading the house: it never waits for an AI (E-414). The findings
are loaded into memory at the start and after every change; the core only reads that memory.
A switch's role nobody confirmed (from AI or from behaviour) counts at once for watching and
learning, marked as such, and only after Klopt for proposing and acting (50.8); a room's role,
a sensor's kind and a choice where the rules doubt change what HouseMind decides, so they count
only after Klopt – until then the rules hold. A finding is never looser than the catalogue
(E-58): a role only chooses which kind of the catalogue a device is, with that kind's limits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from housemind.memory.database import Database

KINDS = ("area", "entity", "automation")  # automation also stands for a script
# role and summary (B-343); measures: what a sensor measures, from behaviour (E-203); where
# the rules doubt (B-359): a room's temperature sensor, a real opening, a status light; and an
# entity's function (E-427, design 51).
# and whether a room is a small room with one door, where its door says who is in (E-504).
FIELDS = (
    "role",
    "summary",
    "measures",
    "temperature",
    "opening",
    "status_led",
    "function",
    "one_door",
)
DOUBTS = ("temperature", "opening", "status_led", "one_door")
SOURCES = ("ai", "behaviour", "rules", "person")
SUGGESTED, CONFIRMED, REJECTED = "suggested", "confirmed", "rejected"
STATUSES = (SUGGESTED, CONFIRMED, REJECTED)


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing HouseMind knows of the house, with where it came from (E-205)."""

    kind: str  # area | entity | automation
    subject: str  # the area id, entity id or automation (script) entity id
    field: str  # role | summary | measures | temperature | opening | status_led | function
    # {"role": …, "why": …}, {"text": …, "part": …, "take_over": …}, {"measures": …},
    # {"sensor": …, "options": […]} or {"yes": True | False}; from behaviour or the rules also
    # what it was derived from ("rule", "facts"), from AI on a doubt the rules' own guess.
    value: dict[str, Any]
    source: str  # ai | behaviour | rules | person
    certainty: float | None
    status: str  # suggested | confirmed | rejected
    since: str  # when it got this status
    ai_log: int | None = None  # the line in the AI log it came from (E-169)

    @property
    def key(self) -> str:
        """One string for a finding, for the page and its answers: "area:hobby:role"."""
        return f"{self.kind}:{self.subject}:{self.field}"

    @property
    def from_ai(self) -> bool:
        """Still only AI's: watched and learned, never proposed or done (50.8)."""
        return self.source == "ai" and self.status == SUGGESTED

    @property
    def derived(self) -> bool:
        """Still only derived from behaviour (E-203): the same as from AI until a Klopt."""
        return self.source == "behaviour" and self.status == SUGGESTED


def split_key(key: str) -> tuple[str, str, str] | None:
    """The parts of a key like "area:hobby:role"; an entity id has a dot, never a colon."""
    kind, _, rest = key.partition(":")
    subject, _, field = rest.rpartition(":")
    if kind not in KINDS or field not in FIELDS or not subject:
        return None
    return kind, subject, field


class HouseKnowledge:
    """The findings in memory; every change is written to the database at once."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.findings: dict[tuple[str, str, str], Finding] = {}

    async def load(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM house_knowledge")
        self.findings = {}
        for row in rows:
            try:
                value = json.loads(row["value"])
            except ValueError:
                continue
            if not isinstance(value, dict) or row["status"] not in STATUSES:
                continue
            finding = Finding(
                str(row["subject_kind"]),
                str(row["subject_id"]),
                str(row["field"]),
                value,
                str(row["source"]),
                float(row["certainty"]) if row["certainty"] is not None else None,
                str(row["status"]),
                str(row["since"]),
                int(row["ai_log"]) if row["ai_log"] is not None else None,
            )
            self.findings[(finding.kind, finding.subject, finding.field)] = finding

    # -- what the core reads ------------------------------------------------------------------

    def get(self, kind: str, subject: str, field: str) -> Finding | None:
        return self.findings.get((kind, subject, field))

    def known(self, kind: str, subject: str, field: str) -> bool:
        """Suggested, confirmed or rejected: then it is not asked again (E-208)."""
        return (kind, subject, field) in self.findings

    def of(self, kind: str, field: str) -> list[Finding]:
        return [f for (k, _, fl), f in sorted(self.findings.items()) if k == kind and fl == field]

    def decided(self, field: str) -> dict[str, bool]:
        """A yes-or-no doubt a person decided (B-359): Klopt keeps what was suggested, Klopt
        niet turns it round, their own answer is theirs. What nobody answered is not here:
        until then the rules hold."""
        out: dict[str, bool] = {}
        for (_, subject, fl), finding in sorted(self.findings.items()):
            yes = finding.value.get("yes")
            if fl == field and isinstance(yes, bool) and finding.status != SUGGESTED:
                out[subject] = yes if finding.status == CONFIRMED else not yes
        return out

    def chosen_temperatures(self) -> dict[str, tuple[str, bool]]:
        """Per room the sensor a person chose or confirmed (True), or turned down (False)."""
        out: dict[str, tuple[str, bool]] = {}
        for finding in self.of("area", "temperature"):
            sensor = finding.value.get("sensor")
            if isinstance(sensor, str) and finding.status != SUGGESTED:
                out[finding.subject] = (sensor, finding.status == CONFIRMED)
        return out

    # -- what changes it ------------------------------------------------------------------------

    async def suggest(
        self,
        kind: str,
        subject: str,
        field: str,
        value: dict[str, Any],
        certainty: float | None,
        now: datetime,
        ai_log: int | None,
        source: str = "ai",
        again: bool = False,
    ) -> Finding | None:
        """A new finding from AI, from behaviour or from the rules; what is already known
        stays as it is. ``again``: AI's pick where the rules doubt replaces their guess, as
        long as nobody answered it (B-359)."""
        found = self.get(kind, subject, field)
        if found is not None and not (again and found.status == SUGGESTED):
            return None
        finding = Finding(
            kind,
            subject,
            field,
            value,
            source,
            certainty,
            SUGGESTED,
            now.isoformat(),
            ai_log,
        )
        await self._write(finding)
        return finding

    async def answer(
        self,
        kind: str,
        subject: str,
        field: str,
        yes: bool,
        now: datetime,
        value: dict[str, Any] | None = None,
    ) -> Finding | None:
        """An administrator's Klopt or Klopt niet; with ``value`` another answer of their own
        (another role, another sensor): that is the person's, and confirmed. Also after an
        earlier answer (E-525): what HouseMind thought itself is kept in ``was``, so that
        "Terug naar te bevestigen" can always bring it back."""
        found = self.get(kind, subject, field)
        if value is not None:
            was = _guess(found)
            finding = Finding(
                kind,
                subject,
                field,
                {**value, "was": was} if was is not None else value,
                "person",
                None,
                CONFIRMED,
                now.isoformat(),
                found.ai_log if found else None,
            )
        elif found is None:
            return None
        else:
            status = CONFIRMED if yes else REJECTED
            finding = replace(found, status=status, since=now.isoformat())
        await self._write(finding)
        return finding

    async def reopen(self, kind: str, subject: str, field: str, now: datetime) -> bool:
        """Terug naar te bevestigen (E-525, B-449): an answer taken back. What HouseMind
        thought itself waits again for a Klopt – after a person's own choice the guess kept in
        ``was``. A choice of their own with nothing of HouseMind's under it goes: until it is
        looked at again, the rules hold."""
        found = self.get(kind, subject, field)
        if found is None or found.status == SUGGESTED:
            return False
        if found.source != "person":
            await self._write(replace(found, status=SUGGESTED, since=now.isoformat()))
            return True
        was = found.value.get("was")
        if isinstance(was, dict) and isinstance(was.get("value"), dict):
            source = was.get("source") if was.get("source") in SOURCES else "rules"
            certainty = was.get("certainty")
            await self._write(
                Finding(
                    kind,
                    subject,
                    field,
                    dict(was["value"]),
                    str(source),
                    float(certainty) if isinstance(certainty, int | float) else None,
                    SUGGESTED,
                    now.isoformat(),
                    found.ai_log,
                )
            )
            return True
        await self._delete(kind, subject, field)
        return True

    async def forget(self, kind: str, subject: str, field: str) -> bool:
        """A suggestion that turned out to rest on nothing (E-459) goes, so it can be looked at
        again; what a person answered stays."""
        found = self.get(kind, subject, field)
        if found is None or found.status != SUGGESTED:
            return False
        await self._delete(kind, subject, field)
        return True

    async def _delete(self, kind: str, subject: str, field: str) -> None:
        await self.db.execute(
            "DELETE FROM house_knowledge WHERE subject_kind = ? AND subject_id = ? AND field = ?",
            (kind, subject, field),
        )
        del self.findings[(kind, subject, field)]

    async def _write(self, finding: Finding) -> None:
        await self.db.execute(
            "INSERT INTO house_knowledge(subject_kind, subject_id, field, value, source, "
            "certainty, status, since, ai_log) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(subject_kind, subject_id, field) DO UPDATE SET value = excluded.value, "
            "source = excluded.source, certainty = excluded.certainty, "
            "status = excluded.status, since = excluded.since, ai_log = excluded.ai_log",
            (
                finding.kind,
                finding.subject,
                finding.field,
                json.dumps(finding.value, ensure_ascii=False),
                finding.source,
                finding.certainty,
                finding.status,
                finding.since,
                finding.ai_log,
            ),
        )
        self.findings[(finding.kind, finding.subject, finding.field)] = finding


def _guess(found: Finding | None) -> dict[str, Any] | None:
    """What HouseMind thought itself, under a person's own answer (E-525): the finding as it
    was, or the guess an earlier own answer kept."""
    if found is None:
        return None
    if found.source == "person":
        was = found.value.get("was")
        return was if isinstance(was, dict) else None
    value = {k: v for k, v in found.value.items() if k != "was"}
    return {"value": value, "source": found.source, "certainty": found.certainty}
