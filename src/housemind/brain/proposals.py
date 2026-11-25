"""Proposals and their four answers (E-133, B-16).

A proposal is a gate decision with outcome *proposed*. Answers:
- "do"     do it now (as a person's answer through the gate) – counts as approved;
- "not_now" skip this time – counts as rejected, lightly;
- "always" approved, strong; from level 3 it becomes a house rule (with confirmation);
- "never"  rejected, strong; from level 3 it becomes a house rule;
- "from_now" approved, strong, and the rule goes to Autonomous (E-460); on the card it replaces
  "always", which stays for a screen that has not updated yet.
One "no" is enough; the first answer counts (E-81).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Decision, Layer

PROPOSAL_LIFETIME = timedelta(minutes=15)
# After no answer or "Not now", the same question waits a while, not just the quarter of an
# hour a proposal stays open.
# The same question waits longer each time nobody wanted it: 3 hours, then a day, then a week
# (E-294).
ASK_AGAIN_STEPS = (timedelta(hours=3), timedelta(days=1), timedelta(days=7))
MAX_NEW_PER_DAY = 12
# What a "yes" carries along: the setting from before, such as a heat pump's preset before
# its "away", to put back when someone is home again.
KEPT_ON_YES = ("before", "stand")  # a stand stays in words after a yes (E-494)
SAME_NIGHT = timedelta(hours=12)


class Answer(StrEnum):
    DO = "do"
    NOT_NOW = "not_now"
    ALWAYS = "always"
    NEVER = "never"
    # E-460: from now on HouseMind does it itself; and someone did it by hand, the question open.
    FROM_NOW = "from_now"
    BY_HAND = "by_hand"


@dataclass(frozen=True, slots=True)
class Proposal:
    id: int
    at: datetime
    skill: str | None
    area_id: str | None
    entity_id: str
    action: str
    data: dict[str, Any]
    reason: str
    status: str
    context: str
    facts: dict[str, Any] = field(default_factory=dict)

    def request(self, layer: Any, now: datetime, user_id: str | None) -> ActionRequest:
        # Asked again – by Vesta, in the list of questions – with every fact of its reason, so
        # it is the same sentence as on the card. Done after a "yes", it is the person's own,
        # with only what is needed to put it back later.
        facts = {**self.facts, "context": self.context}
        if layer is Layer.PERSON_ANSWER:
            facts = {k: self.facts[k] for k in KEPT_ON_YES if k in self.facts}
            facts["context"] = self.context
        return ActionRequest(
            layer,
            self.entity_id,
            self.action,
            self.data,
            reason=self.reason,
            skill=self.skill,
            area_id=self.area_id,
            created_at=now,
            user_id=user_id,
            facts=facts,
        )


class Proposals:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self, decision: Decision, until: datetime | None = None, sleeping: bool = False
    ) -> int | None:
        """A new open proposal. ``until``: a question about later (the evening before
        waking up) stays open until then instead of a quarter of an hour. ``sleeping``: the
        house sleeps – a question that comes back never comes back then (E-294)."""
        r = decision.request
        existing = await self.db.fetchone(
            "SELECT id FROM proposals WHERE status = 'open' AND entity_id = ? AND action = ?",
            (r.entity_id, r.action),
        )
        if existing is not None:
            return None
        # The same question is the same skill for the same device (E-315): "Never" or "Not now"
        # to "lower at night" says nothing about "warm in the morning" on the same thermostat.
        skill = r.skill or ""
        never = await self.db.fetchone(
            "SELECT id FROM proposals WHERE entity_id = ? AND action = ? "
            "AND coalesce(skill, '') = ? AND answer = 'never'",
            (r.entity_id, r.action, skill),
        )
        if never is not None:
            return None  # "Never like this": it does not come back (E-294)
        # The same question comes back later each time nobody wanted it, in a row: no
        # answer or "Not now" (E-294). A "yes" in between starts the count again.
        earlier = await self.db.fetchall(
            "SELECT at, status, answer FROM proposals WHERE entity_id = ? AND action = ? "
            "AND coalesce(skill, '') = ? AND at >= ? ORDER BY at DESC",
            (r.entity_id, r.action, skill, (decision.at - timedelta(days=30)).isoformat()),
        )
        declined = 0
        for before in earlier:
            if before["status"] != "expired" and before["answer"] != "not_now":
                break
            declined += 1
        last = datetime.fromisoformat(str(earlier[0]["at"])) if declined else None
        if last is not None and sleeping and decision.at - last < SAME_NIGHT:
            # Not again in the same night (E-294). A question that only fits the night, such
            # as lower heating while asleep, may come back the next night.
            return None
        if last is not None:
            wait = ASK_AGAIN_STEPS[min(declined, len(ASK_AGAIN_STEPS)) - 1]
            if decision.at - last < wait:
                return None
        today = await self.db.fetchone(
            "SELECT count(*) AS n FROM proposals WHERE at >= ?",
            ((decision.at - timedelta(days=1)).isoformat(),),
        )
        if until is None and today is not None and int(today["n"]) >= MAX_NEW_PER_DAY:
            return None  # enough questions for one day
        await self.db.execute(
            "INSERT INTO proposals(at, skill, area_id, entity_id, action, data, reason, status, "
            "until) VALUES(?,?,?,?,?,?,?, 'open', ?)",
            (
                decision.at.isoformat(),
                r.skill,
                r.area_id,
                r.entity_id,
                r.action,
                json.dumps({"data": r.data, "facts": r.facts}),
                r.reason,
                until.astimezone(UTC).isoformat() if until is not None else None,
            ),
        )
        row = await self.db.fetchone("SELECT max(id) AS id FROM proposals")
        return int(row["id"]) if row else None

    @staticmethod
    def _from_row(row: Any) -> Proposal:
        payload = json.loads(row["data"])
        return Proposal(
            int(row["id"]),
            datetime.fromisoformat(row["at"]),
            row["skill"],
            row["area_id"],
            row["entity_id"],
            row["action"],
            payload.get("data", {}),
            row["reason"],
            row["status"],
            str(payload.get("facts", {}).get("context", "")),
            dict(payload.get("facts") or {}),
        )

    async def open(self, now: datetime) -> list[Proposal]:
        await self.db.execute(
            "UPDATE proposals SET status = 'expired' WHERE status = 'open' AND "
            "((until IS NULL AND at < ?) OR (until IS NOT NULL AND until < ?))",
            ((now - PROPOSAL_LIFETIME).isoformat(), now.astimezone(UTC).isoformat()),
        )
        rows = await self.db.fetchall("SELECT * FROM proposals WHERE status = 'open' ORDER BY at")
        return [self._from_row(r) for r in rows]

    async def same_question(self, proposal: Proposal) -> list[Proposal]:
        """Other open proposals of the same skill in the same room: one question for all."""
        if not proposal.skill or not proposal.area_id:
            return []
        rows = await self.db.fetchall(
            "SELECT * FROM proposals WHERE status = 'open' AND skill = ? AND area_id = ? "
            "AND id != ?",
            (proposal.skill, proposal.area_id, proposal.id),
        )
        return [self._from_row(r) for r in rows]

    async def expire(self, proposal_id: int) -> None:
        """No answer needed any more: its part went off (E-400)."""
        await self.db.execute(
            "UPDATE proposals SET status = 'expired' WHERE id = ? AND status = 'open'",
            (proposal_id,),
        )

    async def get(self, proposal_id: int) -> Proposal | None:
        row = await self.db.fetchone("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        return self._from_row(row) if row else None

    async def answer(
        self, proposal_id: int, answer: Answer, user_id: str | None, now: datetime
    ) -> Proposal | None:
        """Record the first answer; later answers are ignored (E-81)."""
        changed = await self.db.execute(
            "UPDATE proposals SET status = 'answered', answered_at = ?, answered_by = ?, "
            "answer = ? WHERE id = ? AND status = 'open'",
            (now.isoformat(), user_id, answer.value, proposal_id),
        )
        if not changed:
            return None
        return await self.get(proposal_id)
