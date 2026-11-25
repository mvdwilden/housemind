"""Recognising which device kind an entity is (H5.3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from housemind.core.model import Entity
from housemind.knowledge.catalog import Catalog, DeviceKind, Match


class MatchQuality(StrEnum):
    SPECIFIC = "specific"
    GENERIC = "generic"  # known domain, unknown variant (E-156)
    UNKNOWN = "unknown"  # unknown domain: observe only


@dataclass(frozen=True, slots=True)
class Recognition:
    entity_id: str
    kind: DeviceKind | None
    quality: MatchQuality


def _fits(match: Match, entity: Entity, role: str | None) -> bool:
    if match.domain != entity.domain:
        return False
    if match.role is not None and match.role != role:
        return False
    if match.platforms and entity.platform not in match.platforms:
        return False
    if match.device_classes and entity.device_class not in match.device_classes:
        return False
    if match.color_modes_any:
        modes = entity.capabilities.get("supported_color_modes") or ()
        if not set(match.color_modes_any) & set(modes):
            return False
    return (entity.supported_features & match.required_features) == match.required_features


def recognise(catalog: Catalog, entity: Entity, role: str | None = None) -> Recognition:
    """Find the most specific device kind for an entity.

    ``role`` is what HouseMind knows from the house knowledge (e.g. a switch that powers a
    lamp). Without a fitting specific kind the domain's generic kind is used; without that,
    the entity is unknown and will only be observed.
    """
    candidates = [
        kind
        for kind in catalog.for_domain(entity.domain)
        if not kind.generic and _fits(kind.match, entity, role)
    ]
    if candidates:
        # Equally specific: a shipped kind goes before one a later layer added (QA 2, B-168).
        best = max(candidates, key=lambda k: (k.match.specificity(), k.shipped, k.key))
        return Recognition(entity.entity_id, best, MatchQuality.SPECIFIC)
    generic = catalog.generic_for(entity.domain)
    if generic is not None:
        return Recognition(entity.entity_id, generic, MatchQuality.GENERIC)
    return Recognition(entity.entity_id, None, MatchQuality.UNKNOWN)
