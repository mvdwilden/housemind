"""Area roles from area names, in any loaded language (E-105).

Without AI the role comes from words in the language files; an administrator can change it
from level 3 (stored in the database). Unknown names get the neutral role "other".
"""

from __future__ import annotations

import re

from housemind.core.model import Area
from housemind.i18n.catalog import Languages

ROLE_ORDER = (
    "child_bedroom",
    "guest_room",
    "bedroom",
    "bathroom",
    "toilet",
    "kitchen",
    "dining",
    "living",
    "hall",
    "office",
    "utility",
    "garage",
    "outside",
)
SLEEPING_ROLES = frozenset({"bedroom", "child_bedroom", "guest_room"})
# Rooms people pass through (E-520): someone coming in is someone up, but nobody stays there
# for long – a presence sensor that keeps seeing "someone" there at night is no one up.
PASSING_ROLES = frozenset({"hall"})


def role_words(languages: Languages) -> dict[str, list[str]]:
    words: dict[str, list[str]] = {role: [] for role in ROLE_ORDER}
    for texts in languages.languages.values():
        for role in ROLE_ORDER:
            raw = texts.get(f"area_roles.{role}")
            if raw:
                words[role].extend(w.strip().lower() for w in raw.split(",") if w.strip())
    return words


def _matches(word: str, haystack: str, compound: bool = False) -> bool:
    """A word matches at the start of a word; longer words also at the end of a compound
    (Dutch/German "hoofdslaapkamer"), so "den" never matches inside "garden". Words for the
    outside and the garage match at the end of a compound whatever their length: "Voortuin",
    "Achtertuin", "Backyard", so they never count as rooms indoors."""
    escaped = re.escape(word)
    if re.search(rf"\b{escaped}", haystack):
        return True
    return (compound or len(word) >= 6) and re.search(rf"{escaped}\b", haystack) is not None


COMPOUND_ROLES = frozenset({"outside", "garage"})


def area_role(area: Area, words: dict[str, list[str]]) -> str:
    haystack = f"{area.id.replace('_', ' ')} {area.name}".lower()
    for role in ROLE_ORDER:
        compound = role in COMPOUND_ROLES
        if any(_matches(word, haystack, compound) for word in words.get(role, ())):
            return role
    return "other"
