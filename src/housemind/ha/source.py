"""Finding out who caused a change, from Home Assistant's context (E-115, H7.2)."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta

from housemind.core.clock import Clock
from housemind.core.model import Context, Source


class SourceTracker:
    """Remembers HouseMind's own actions and classifies every change.

    HouseMind marks an entity as "expected" just before it calls an action, and records the
    context id Home Assistant returns. A change carrying that context id – or carrying
    HouseMind's own user id for an expected entity – is HouseMind's own doing.

    An automation acts with the context of its run, which Home Assistant names when it starts
    (``automation_triggered``), with what started it as parent. A change in the run of the hand
    of the resident – a button or switch (E-448) – is whoever pressed it; any other run is an
    automation, also one a time or the sun started, which has no parent.
    """

    def __init__(self, clock: Clock, expect_window_s: float = 10.0, memory: int = 512) -> None:
        self._clock = clock
        self._window = timedelta(seconds=expect_window_s)
        self._memory = memory
        self._own_contexts: OrderedDict[str, None] = OrderedDict()
        self._expected: dict[str, datetime] = {}
        # What follows HouseMind's own command without a context of its own: the lamps of a
        # group it switched, a cover that reports "closed" when its motor stops.
        self._followups: dict[str, datetime] = {}
        self.own_user_id: str | None = None
        self.other_system_entities: set[str] = set()
        # Runs of an automation: which one, and the context that started it (E-448).
        self._runs: OrderedDict[str, tuple[str, str | None]] = OrderedDict()
        # Who caused recent changes, by their context: the switch that started a run.
        self._seen: OrderedDict[str, Source] = OrderedDict()
        self.hands: dict[str, Source] = {}  # automations that are the hand, and whose
        self.signals: set[str] = set()  # automations that give a sign with a lamp (E-461)

    def automation_ran(self, context_id: str, automation_id: str, parent_id: str | None) -> None:
        """Home Assistant started an automation; what it does carries this context."""
        self._runs[context_id] = (automation_id, parent_id)
        self._runs.move_to_end(context_id)
        while len(self._runs) > self._memory:
            self._runs.popitem(last=False)

    def run_of(self, context: Context | None) -> str | None:
        """The automation whose run made a change, if HouseMind heard it start."""
        run = self._runs.get(context.id) if context is not None else None
        return run[0] if run is not None else None

    def saw(self, context: Context | None, source: Source, real: bool) -> None:
        """A change and who caused it. A device coming back online (``real`` False) is no one
        pressing it, also when an automation follows it (E-448)."""
        if context is None:
            return
        self._seen[context.id] = source if real else Source.UNKNOWN
        self._seen.move_to_end(context.id)
        while len(self._seen) > self._memory:
            self._seen.popitem(last=False)

    def _by_run(self, context: Context) -> Source | None:
        """Who made a change an automation did (E-448): the hand of the resident passes on
        the person or switch that started it; any other run is an automation."""
        run = self._runs.get(context.id)
        if run is None:
            return None
        automation_id, parent_id = run
        if automation_id in self.signals:
            return Source.SIGNAL
        hand = self.hands.get(automation_id)
        if hand is None:
            return Source.AUTOMATION
        if parent_id is None or parent_id not in self._seen:
            return hand  # a remote, a button, an action in the app
        started = self._seen[parent_id]
        return started if started in (Source.PERSON, Source.PHYSICAL) else Source.AUTOMATION

    def expect(self, entity_id: str) -> None:
        self._expected[entity_id] = self._clock.now() + self._window

    def expect_followups(self, entity_ids: list[str], seconds: float) -> None:
        until = self._clock.now() + timedelta(seconds=seconds)
        for entity_id in entity_ids:
            self._followups[entity_id] = until

    def _is_followup(self, entity_id: str) -> bool:
        until = self._followups.get(entity_id)
        if until is None:
            return False
        if until < self._clock.now():
            del self._followups[entity_id]
            return False
        return True

    def record_own_context(self, context_id: str) -> None:
        self._own_contexts[context_id] = None
        self._own_contexts.move_to_end(context_id)
        while len(self._own_contexts) > self._memory:
            self._own_contexts.popitem(last=False)

    def _is_expected(self, entity_id: str) -> bool:
        until = self._expected.get(entity_id)
        if until is None:
            return False
        if until < self._clock.now():
            del self._expected[entity_id]
            return False
        return True

    def classify(self, entity_id: str, context: Context | None) -> Source:
        if context is None:
            return Source.UNKNOWN
        if context.id in self._own_contexts:
            return Source.HOUSEMIND
        if (
            self.own_user_id is not None
            and context.user_id == self.own_user_id
            and self._is_expected(entity_id)
        ):
            self.record_own_context(context.id)
            return Source.HOUSEMIND
        if context.user_id is None and context.parent_id is None and self._is_followup(entity_id):
            return Source.HOUSEMIND  # the lamps of a group, or a cover arriving
        by_run = self._by_run(context)
        if by_run is not None:
            return by_run
        if context.parent_id is not None:
            return Source.AUTOMATION
        if context.user_id is not None:
            return Source.PERSON
        if entity_id in self.other_system_entities:
            return Source.OTHER_SYSTEM
        return Source.PHYSICAL
