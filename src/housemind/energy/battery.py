"""Home batteries of every kind, only watched (E-363, E-364, B-251).

A house can have several home batteries – fixed or with a plug (a balcony battery) – and each is
shown on its own: how full it is and whether it charges or discharges. HouseMind never steers
them; it only reads what Home Assistant tells.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from housemind.core.model import Entity, HouseModel, State
from housemind.energy.setup import (
    _NOT,
    EnergySetup,
    Reading,
    _has_word,
    _label,
    _readings,
    battery_powers,
)


@dataclass(slots=True)
class Battery:
    """One home battery and the sensors it is read with."""

    name: str
    device_id: str | None = None
    soc: str | None = None
    power: str | None = None
    named: bool = False  # in Home Assistant's energy settings
    platform: str | None = None  # the integration
    # Every power sensor it can be read with, best first; ``power`` is the first.
    powers: list[str] = field(default_factory=list)

    def readings(self) -> list[Reading]:
        return [(p,) for p in self.powers] or ([(self.power,)] if self.power else [])


def find_batteries(
    model: HouseModel,
    states: dict[str, State],
    setup: EnergySetup,
    words: dict[str, list[str]],
) -> list[Battery]:
    """Every home battery in the house, each on its own (E-364)."""
    managed = setup.manager_entities
    usable = {
        e.entity_id: e
        for e in model.entities.values()
        if e.entity_id not in managed and not e.disabled and e.entity_category != "diagnostic"
    }
    # The batteries: devices with a level (%) that the energy settings name, else by words.
    devices: list[str | None] = []
    for entity_id in [*setup.battery_in, *setup.battery_out]:
        entity = usable.get(entity_id)
        if entity is not None and entity.device_id not in devices:
            devices.append(entity.device_id)
    named = set(devices)  # the administrator's choice in the energy settings: always kept
    socs: dict[str | None, str] = {}
    for entity in usable.values():
        if entity.domain != "sensor" or entity.device_class != "battery":
            continue
        label = _label(model, entity)
        if entity.device_id in devices or (
            _has_word(label, words["home_battery"]) and not _has_word(label, words["car"])
        ):
            socs.setdefault(entity.device_id, entity.entity_id)
            if entity.device_id not in devices:
                devices.append(entity.device_id)
    batteries: list[Battery] = []
    for device_id in devices:
        if device_id is None or device_id not in socs:
            continue
        device = model.devices.get(device_id)
        own = [e for e in usable.values() if e.device_id == device_id]
        battery = Battery(device.name if device else device_id, device_id, socs[device_id])
        battery.powers = battery_powers(model, own, words)
        battery.platform = next((e.platform for e in own if e.platform), None)
        battery.named = device_id in named
        batteries.append(battery)
    # A battery whose own power sensor gives nothing (offline) is read by the power sensor its
    # integration has for "the battery" on another device, such as the system it is part of –
    # only when that integration has just this one battery, so it is never two together.
    for battery in batteries:
        entry = usable[battery.soc or ""].config_entry_id if battery.soc in usable else None
        if entry is None or sum(_entry(usable, b) == entry for b in batteries) != 1:
            continue
        elsewhere = [
            e
            for e in usable.values()
            if e.config_entry_id == entry and e.device_id != battery.device_id
        ]
        battery.powers += [
            r[0]
            for r in _readings(model, elsewhere, words, "battery", _NOT["battery"], said_only=True)
            if len(r) == 1
        ]
    for battery in batteries:
        battery.power = battery.powers[0] if battery.powers else None
    return batteries


def _entry(usable: dict[str, Entity], battery: Battery) -> str | None:
    entity = usable.get(battery.soc or "")
    return entity.config_entry_id if entity is not None else None


def empty(battery: Battery, states: dict[str, State]) -> bool:
    """No battery, only an integration's storage sensors: not in the energy settings, and its
    level and power never had a value ("unknown" – one that is offline says "unavailable"). In
    the real house the solar panels' integration made "Opslagniveau" for a site without a
    battery. Asked now, not when the house is read: a real battery that is still "unknown" just
    after Home Assistant restarts shows as soon as it has a value."""
    if battery.named:
        return False
    readings = [e for e in (battery.soc, battery.power) if e]
    return bool(readings) and all(
        (state := states.get(e)) is not None and state.state == "unknown" for e in readings
    )
