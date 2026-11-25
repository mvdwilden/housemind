"""Which Home Assistant versions this HouseMind was tested with (E-142, E-155).

Versions are compared by year and month ("2026.10.2" and "2026.10.0b3" are both 2026.10): HA
releases once a month, and that is what the release check tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_MONTH = re.compile(r"^(\d{4})\.(\d{1,2})(?:\D|$)")
FILE = "tested_ha.yaml"


def month_of(version: str) -> tuple[int, int] | None:
    match = _MONTH.match(version.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


@dataclass(frozen=True, slots=True)
class Compatibility:
    tested: frozenset[tuple[int, int]] = frozenset()
    oldest: tuple[int, int] | None = None

    def status(self, version: str) -> str:
        """ "tested", "newer" (than anything tested), "older" (not supported) or "unknown"."""
        month = month_of(version)
        if month is None or not self.tested:
            return "unknown"
        if self.oldest is not None and month < self.oldest:
            return "older"
        if month > max(self.tested):
            return "newer"
        return "tested"

    def oldest_text(self) -> str:
        return f"{self.oldest[0]}.{self.oldest[1]}" if self.oldest else ""


def load_compatibility(paths: list[Path]) -> Compatibility:
    """The shipped list, extended by a knowledge update (a later path adds to it)."""
    tested: set[tuple[int, int]] = set()
    oldest: tuple[int, int] | None = None
    for base in paths:
        path = base / FILE
        if not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        for version in raw.get("tested") or []:
            month = month_of(str(version))
            if month is not None:
                tested.add(month)
        if raw.get("oldest") is not None:
            oldest = month_of(str(raw["oldest"])) or oldest
    return Compatibility(frozenset(tested), oldest)
