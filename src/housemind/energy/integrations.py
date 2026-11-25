"""What is different per energy integration (E-341 as kept by K3, B-215): writing sparingly.

The export limit often sits on the same inverter as a battery: through the maker's cloud, or on
a device whose memory wears with every write. And grid code settings are never touched. The
knowledge file `integrations.yaml` names the integrations by their technical name (platform),
never shown in the app or the help.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class Integrations:
    cloud: set[str] = field(default_factory=set)
    flash: set[str] = field(default_factory=set)
    netcode: list[str] = field(default_factory=list)  # words of grid code settings


def load_integrations(directories: Iterable[Path]) -> Integrations:
    found = Integrations()
    for directory in directories:
        path = directory / "integrations.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for platform, traits in (raw.get("integrations") or {}).items():
            traits = traits if isinstance(traits, dict) else {}
            if traits.get("cloud"):
                found.cloud.add(str(platform))
            if traits.get("flash"):
                found.flash.add(str(platform))
        found.netcode.extend(_compact(w) for w in raw.get("netcode") or [])
    return found


def _compact(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())
