"""The parts of HouseMind, each in its own module (code QA, C1): Vesta, the heat pump, the
existing automations, changes in Home Assistant and the round of thinking. Each part is a
mixin of HouseMind; for the type checker it names what it uses of HouseMind, and ``_hm``
passes it on as the HouseMind it is."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from housemind.runtime import HouseMind


class Part:
    """A part of HouseMind, mixed into it."""

    @property
    def _hm(self) -> HouseMind:
        """Itself, as the HouseMind it is part of."""
        return cast("HouseMind", self)
