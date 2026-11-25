"""Which temperature is the room's, and which is a device's own (E-209, E-210).

Not every temperature sensor is the room's: the one inside a wall socket, a radio chip, a
tablet's battery or an oven reads far too warm, yet calls itself "Temperature" too. A room's
temperature comes from what measures the room:

- **room**: a sensor on a device that only measures the air (temperature, humidity …), or a
  sensor without a device (made by hand in HA);
- **thermostat**: the current temperature of a climate entity;
- **secondary**: a sensor on a device that does more, such as a presence sensor – its own
  warmth makes it read high, so it only counts when a room has nothing better.

A sensor on a device that switches something (a socket, a relay, a dimmer) measures the
device itself: never the room, but worth watching for a socket that gets too hot (E-210).
Ovens, tanks, water, batteries, chips and the outdoors are never the room; which words mean
that comes from every language file (``daily_words.temperature_not_room``).

Where the rules are unsure – a room with more than one sensor, or only one that warms itself –
an administrator says which is the room's own (design 50.2, B-359); from then on that holds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from housemind.core.model import Entity, HouseModel, State
from housemind.knowledge.words import has_word as words_have

# Devices that do something: their temperature sensors are about the device, not the air.
CONTROLS = frozenset(
    {
        "light",
        "switch",
        "media_player",
        "fan",
        "cover",
        "lock",
        "vacuum",
        "lawn_mower",
        "water_heater",
        "humidifier",
        "valve",
        "siren",
        "alarm_control_panel",
        "camera",
        "climate",
    }
)
# Sockets, relays and dimmers: their own temperature can mean fire danger (E-210).
SWITCHING = frozenset({"switch", "light"})
# What a device that only measures the air may also have.
AIR = frozenset(
    {
        "temperature",
        "humidity",
        "pressure",
        "atmospheric_pressure",
        "carbon_dioxide",
        "co2",
        "illuminance",
        "pm1",
        "pm10",
        "pm25",
        "volatile_organic_compounds",
        "volatile_organic_compounds_parts",
        "aqi",
        "battery",
        "signal_strength",
        "voltage",
    }
)
SENSING_PEOPLE = frozenset({"occupancy", "motion", "presence"})
ROOM_RANGE = (5.0, 35.0)  # a room outside this is a broken or misplaced sensor


@dataclass(slots=True)
class Temperatures:
    room: dict[str, list[str]] = field(default_factory=dict)  # area → sensors and thermostats
    secondary: dict[str, list[str]] = field(default_factory=dict)  # area → only if no room
    devices: set[str] = field(default_factory=set)  # a socket's or relay's own temperature

    rooms_climate: set[str] = field(default_factory=set)  # climate entities heating a room

    def sources(self, area_id: str) -> list[str]:
        return self.room.get(area_id) or self.secondary.get(area_id, [])

    def of_room(self, area_id: str, states: Mapping[str, State]) -> float | None:
        """The room's temperature: the average of what measures it, if anything does."""
        values = [v for e in self.sources(area_id) if (v := reading(e, states)) is not None]
        return round(sum(values) / len(values), 1) if values else None

    def choose(self, area_id: str, sensor: str, use: bool) -> None:
        """A person's choice where the rules doubted (B-359): this sensor is the room's own –
        its thermostats still count –, or it is not the room's."""
        if use:
            climates = [e for e in self.room.get(area_id, []) if e.startswith("climate.")]
            self.room[area_id] = [sensor, *climates]
            self.secondary.pop(area_id, None)
            return
        for kind in (self.room, self.secondary):
            if sensor in kind.get(area_id, []):
                kind[area_id] = [e for e in kind[area_id] if e != sensor]
                if not kind[area_id]:
                    del kind[area_id]

    def of_air(self, area_id: str, states: Mapping[str, State]) -> float | None:
        """The air of the room by its own sensors only – not a thermostat, not a sensor that
        warms itself: what the room reaching its wish is measured by (E-315)."""
        values = [
            v
            for e in self.room.get(area_id, [])
            if not e.startswith("climate.") and (v := reading(e, states)) is not None
        ]
        return round(sum(values) / len(values), 1) if values else None


def reading(entity_id: str, states: Mapping[str, State]) -> float | None:
    state = states.get(entity_id)
    if state is None:
        return None
    climate = entity_id.startswith("climate.")
    raw = state.attributes.get("current_temperature") if climate else state.state
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    low, high = ROOM_RANGE
    return value if low <= value <= high else None


def _room_climate(model: HouseModel, entity: Entity, words: list[str]) -> bool:
    """A thermostat or airco of a room – not a pool heat pump, a wine cooler, a fridge zone
    or a tank shown as climate, which never gets a room's setpoint."""
    if _has_word(model.words_of(entity), words):
        return False
    caps = entity.capabilities
    low, high = caps.get("min_temp"), caps.get("max_temp")
    if isinstance(low, int | float) and low < 4:
        return False  # a fridge or freezer zone
    if isinstance(high, int | float) and high > 40:
        return False  # a pool, a sauna, a hot-water tank
    modes = caps.get("hvac_modes")
    return not isinstance(modes, list) or bool(
        {"heat", "cool", "heat_cool", "auto"} & {str(m) for m in modes}
    )


def _has_word(text: str, words: Iterable[str]) -> bool:
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


def sort_temperatures(
    model: HouseModel,
    area_roles: Mapping[str, str],
    not_room: Iterable[str],
    chosen: Mapping[str, tuple[str, bool]] | None = None,
) -> Temperatures:
    """Every room's temperature sources; ``chosen``: per room the sensor a person chose or
    turned down where the rules doubted (B-359) – only while it is still in that room."""
    words = list(not_room)
    by_device: dict[str, list[Entity]] = defaultdict(list)
    for entity in model.entities.values():
        if entity.device_id is not None:
            by_device[entity.device_id].append(entity)
    out = Temperatures()
    for entity in model.entities.values():
        if entity.disabled:
            continue
        area = model.area_of(entity.entity_id)
        indoors = area is not None and area_roles.get(area.id) != "outside"
        if entity.domain == "climate":
            if indoors and area is not None and _room_climate(model, entity, words):
                out.room.setdefault(area.id, []).append(entity.entity_id)
                out.rooms_climate.add(entity.entity_id)
            continue
        if entity.domain != "sensor" or entity.device_class != "temperature":
            continue
        family = by_device.get(entity.device_id or "", [])
        controls = {e.domain for e in family if e.domain in CONTROLS}
        label = model.words_of(entity)
        elsewhere = _has_word(label, words)  # oven, tank, chip, fridge … also the device's name
        if controls:
            if not elsewhere and controls & SWITCHING and not controls - SWITCHING:
                out.devices.add(entity.entity_id)  # a socket or relay: watch it, never the room
            continue
        if entity.entity_category is not None or elsewhere or not indoors or area is None:
            continue
        only_air = all(
            e.domain != "sensor" or e.entity_category is not None or e.device_class in AIR
            for e in family
        )
        senses_people = any(
            e.domain == "binary_sensor" and e.device_class in SENSING_PEOPLE for e in family
        )
        kind = out.room if only_air and not senses_people else out.secondary
        kind.setdefault(area.id, []).append(entity.entity_id)
    for area_id, (sensor, use) in (chosen or {}).items():
        area = model.area_of(sensor) if sensor in model.entities else None
        if area is not None and area.id == area_id:
            out.choose(area_id, sensor, use)
    return out


def temperature_doubts(
    temperatures: Temperatures, states: Mapping[str, State]
) -> dict[str, tuple[str, list[str]]]:
    """Rooms where the rules are unsure which sensor is the room's own (design 50.2, B-359):
    more than one that only measures the air – now averaged –, or only one that warms itself,
    such as a presence sensor's. Per room the rules' own guess – the one closest to the middle
    of what they read – and every sensor it could be."""
    out: dict[str, tuple[str, list[str]]] = {}
    for area_id in sorted(set(temperatures.room) | set(temperatures.secondary)):
        own = [e for e in temperatures.room.get(area_id, []) if not e.startswith("climate.")]
        options = sorted(own if own else temperatures.secondary.get(area_id, []))
        if not options or len(own) == 1:
            continue
        read = {e: v for e in options if (v := reading(e, states)) is not None}
        middle = sorted(read.values())[len(read) // 2] if read else 0.0
        guess = min(options, key=lambda e: (e not in read, abs(read.get(e, 0.0) - middle), e))
        out[area_id] = (guess, options)
    return out
