"""Parts on and off (E-399 – E-401, B-320 – B-326): one list, per part Uit, Observeren,
Voorstellen or Autonoom. It replaces "Hoe ver HouseMind mag gaan" and the experimental ticks.

Off is asleep: HouseMind does nothing with the part – no acting, no proposals, no notices – and
does not learn from it. What it learned stays as it was, without fading; on again, it goes on
where it was. The emergency reactions are no part: they always work (E-80).
"""

from __future__ import annotations

from typing import Any

from housemind.brain.household import EXPERIMENTAL

__all__ = [
    "ALL",
    "CORE",
    "EXPERIMENTAL",
    "HA_DOMAINS",
    "STATES",
    "default",
    "from_before",
    "merged",
]

STATES = ("off", "observe", "propose", "autonomous")
CORE = ("lighting", "covers", "climate", "air", "water", "energy")
ALL = CORE + EXPERIMENTAL
# The Home Assistant domains of the parts HouseMind always limited per domain (E-12).
HA_DOMAINS = {"lighting": ("light",), "covers": ("cover",), "climate": ("climate",)}


def default(part: str) -> str:
    """A core part is on without a limit; an experimental part is off until it is chosen."""
    return "off" if part in EXPERIMENTAL else "autonomous"


def from_before(caps: dict[str, Any], experimental: list[Any]) -> dict[str, str]:
    """The list from the settings of before: the limit per Home Assistant domain, and the
    experimental parts that were ticked (B-320)."""
    chosen = {str(d) for d in experimental}
    out = {part: default(part) for part in ALL}
    for part, domains in HA_DOMAINS.items():
        steps = [str(caps[d]) for d in domains if str(caps.get(d)) in STATES]
        if steps:
            out[part] = min(steps, key=STATES.index)
    for part in EXPERIMENTAL:
        out[part] = "autonomous" if part in chosen else "off"
    return out


def merged(current: dict[str, str], change: Any, expert: bool) -> dict[str, str]:
    """A change to the list, as far as it is allowed: known parts and states only, and off
    for a core part only by an Expert administrator (E-399)."""
    out = {part: current.get(part, default(part)) for part in ALL}
    if not isinstance(change, dict):
        return out
    for part, state in change.items():
        if part not in out or state not in STATES:
            continue
        if state == "off" and part in CORE and not expert:
            continue
        out[part] = state
    return out
