"""The watchdog (E-329, B-204): when HouseMind goes silent, give back what it had set.

With every answer HouseMind hands over the list of energy devices it set itself, with the
action that puts each one back. This list is kept in Home Assistant's own storage, so it also
survives a restart of Home Assistant. When HouseMind does not answer for five minutes, the
watchdog carries the list out once, tells the administrators (K5) and forgets the list; at its
next answer HouseMind hears that it was done. Only these actions are ever carried out.

Plain Python, without Home Assistant's own modules: the integration gives it what it needs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)
WAIT = timedelta(minutes=5)
ALLOWED = frozenset(
    {
        "select.select_option",
        "switch.turn_on",
        "switch.turn_off",
        "number.set_value",
        "water_heater.set_temperature",
    }
)
# Never given back, whatever HouseMind asks (QA 3, gat 5): what could open a garage door,
# gate or door, a lock, an alarm or a tap. Words in the entity id, in the languages HouseMind
# speaks; better once too often – HouseMind only ever lists energy devices here.
NEVER = (
    "garage", "gate", "door", "lock", "alarm", "valve", "opener",
    "deur", "poort", "hek", "slot", "kraan", "klep",
    "tor", "tuer", "tür", "schloss", "ventil",
    "porte", "portail", "serrure", "vanne",
)  # fmt: skip
TEXTS = {
    "en": (
        "HouseMind did not respond",
        "HouseMind has not responded for 5 minutes. I put back what it had set itself: {what}.",
    ),
    "nl": (
        "HouseMind reageerde niet",
        "HouseMind reageert al 5 minuten niet. Ik heb teruggezet wat het zelf had ingesteld: "
        "{what}.",
    ),
    "de": (
        "HouseMind hat nicht reagiert",
        "HouseMind reagiert seit 5 Minuten nicht. Ich habe zurückgestellt, was es selbst "
        "eingestellt hatte: {what}.",
    ),
    "fr": (
        "HouseMind n'a pas répondu",
        "HouseMind ne répond plus depuis 5 minutes. J'ai remis ce qu'il avait réglé lui-même : "
        "{what}.",
    ),
}


def valid(items: Any) -> list[dict[str, Any]]:
    """Only well-formed items with an allowed action."""
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        entity_id, action, data = item.get("entity_id"), item.get("action"), item.get("data")
        if (
            isinstance(entity_id, str)
            and isinstance(action, str)
            and action in ALLOWED
            and entity_id.split(".", 1)[0] == action.split(".", 1)[0]
            and isinstance(data, dict)
            and not any(word in entity_id.lower() for word in NEVER)
        ):
            out.append({"entity_id": entity_id, "action": action, "data": dict(data)})
    return out


class Watchdog:
    def __init__(
        self,
        now: Callable[[], datetime],
        save: Callable[[dict[str, Any]], Awaitable[None]],
        call: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        notify: Callable[[str, str], Awaitable[None]],
        language: Callable[[], str],
        names: Callable[[str], str],
    ) -> None:
        self._now, self._save, self._call = now, save, call
        self._notify, self._language, self._names = notify, language, names
        self.items: list[dict[str, Any]] = []
        self.done_at: str | None = None
        self.last_contact = now()  # after a start: five minutes from now

    def load(self, stored: Any) -> None:
        stored = stored if isinstance(stored, dict) else {}
        self.items = valid(stored.get("items"))
        done = stored.get("done_at")
        self.done_at = done if isinstance(done, str) else None

    async def _store(self) -> None:
        await self._save({"items": self.items, "done_at": self.done_at})

    async def contact(self, items: Any) -> None:
        """HouseMind answered: start counting again, keep its newest list."""
        self.last_contact = self._now()
        fresh = valid(items)
        if fresh != self.items or self.done_at is not None:
            self.items, self.done_at = fresh, None
            await self._store()

    async def check(self) -> bool:
        """Carry the list out once after five silent minutes. True when it did."""
        if not self.items or self._now() - self.last_contact < WAIT:
            return False
        done = []
        for item in self.items:
            domain, service = item["action"].split(".", 1)
            try:
                await self._call(domain, service, {**item["data"], "entity_id": item["entity_id"]})
                done.append(self._names(item["entity_id"]))
            except Exception:  # one device that fails never stops the others
                _LOGGER.exception("Could not give back %s", item["entity_id"])
        self.items, self.done_at = [], self._now().isoformat()
        await self._store()
        language = self._language().split("-", 1)[0]
        title, text = TEXTS.get(language, TEXTS["en"])
        await self._notify(title, text.format(what=", ".join(done) or "–"))
        return True
