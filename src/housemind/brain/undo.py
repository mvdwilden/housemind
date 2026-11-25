"""Undo (E-92): put a device back the way it was before HouseMind changed it."""

from __future__ import annotations

from datetime import datetime

from housemind.core.model import State
from housemind.safety.gate import ActionRequest, Layer

_ON_OFF = ("light", "switch", "fan", "humidifier")


def inverse(
    request: ActionRequest, previous: State, now: datetime, user_id: str | None
) -> ActionRequest | None:
    """The request that restores ``previous``; ``None`` when that is not possible."""
    entity_id = request.entity_id
    domain = entity_id.split(".", 1)[0]
    attrs = previous.attributes
    action: str | None = None
    data: dict[str, object] = {}
    if domain in _ON_OFF:
        if previous.state == "off":
            action = f"{domain}.turn_off"
        elif previous.state == "on":
            action = f"{domain}.turn_on"
            brightness = attrs.get("brightness")
            if domain == "light" and isinstance(brightness, int | float):
                data["brightness_pct"] = max(1, round(float(brightness) * 100 / 255))
            if domain == "fan" and isinstance(attrs.get("percentage"), int | float):
                action, data = "fan.set_percentage", {"percentage": attrs["percentage"]}
    elif domain == "cover":
        position = attrs.get("current_position")
        if isinstance(position, int | float) and request.action == "cover.set_cover_position":
            action, data = "cover.set_cover_position", {"position": int(position)}
        elif previous.state in ("open", "opening"):
            action = "cover.open_cover"
        elif previous.state in ("closed", "closing"):
            action = "cover.close_cover"
    elif domain in ("climate", "water_heater"):
        temperature = attrs.get("temperature")
        if isinstance(temperature, int | float):
            action, data = f"{domain}.set_temperature", {"temperature": temperature}
    if action is None:
        return None
    return ActionRequest(
        Layer.PERSON_ANSWER,
        entity_id,
        action,
        data,
        reason="undo",
        skill=request.skill,
        area_id=request.area_id,
        created_at=now,
        user_id=user_id,
        facts={"undo": True},
    )
