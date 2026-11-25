"""Write the device catalogue in English, from HouseMind's own knowledge files.

Usage: ``python tools/build_device_catalogue.py [output.md]`` (default: docs/device-catalogue.md).

Everything comes from knowledge/catalog/*.yaml and the hard limits in the code, so the document
always says what this version of HouseMind really does. Only the short English description of
each device kind is written here; a kind without one is shown by its key.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from housemind import __version__  # noqa: E402
from housemind.i18n.catalog import load_languages  # noqa: E402
from housemind.knowledge.catalog import DeviceKind, ValueChoices, ValueRange, ValueText  # noqa: E402
from housemind.knowledge.loader import load_catalog  # noqa: E402

# Section per knowledge file, in reading order.
SECTIONS = [
    ("lighting", "Lighting", "Lamps, and switches or plugs that power a lamp."),
    ("covers", "Curtains, blinds, windows and doors", "Everything that opens and closes."),
    ("climate", "Climate", "Heating, cooling, ventilation, humidity and hot water."),
    ("energy", "Energy", "Feeding back and appliances; the home battery and the car only shown."),
    ("safety", "Safety", "Locks, alarms, valves, sirens, cameras and the sensors that warn."),
    (
        "generic",
        "Other devices",
        "The careful fallback for every kind of device in Home Assistant.",
    ),
    ("automations", "Existing automations", "The automations you already have in Home Assistant."),
]

KINDS = {
    "light.onoff": "Light that only switches on and off",
    "light.dimmable": "Dimmable light",
    "light.color_temperature": "Light with colour temperature or colour",
    "switch.light": "Switch or plug that powers a lamp",
    "light.generic": "Any other light",
    "cover.window_covering": "Curtain, blind, shade or shutter",
    "cover.awning": "Awning",
    "cover.window": "Motorised window",
    "cover.damper": "Ventilation damper",
    "cover.garage_gate_door": "Garage door, gate or door",
    "cover.generic": "Any other cover",
    "climate.thermostat": "Thermostat, heat pump or air conditioning",
    "fan.ventilation": "Fan or ventilation unit",
    "humidifier.any": "Humidifier or dehumidifier",
    "water_heater.any": "Water heater or boiler",
    "number.export_limit": "Limit on feeding back to the grid",
    "button.appliance_start": "Start button of an appliance (washing machine, dishwasher …)",
    "switch.appliance_start": "Start switch of an appliance",
    "lock.any": "Lock",
    "alarm_control_panel.any": "Alarm system",
    "valve.water": "Main water valve",
    "valve.gas": "Gas valve",
    "switch.cooking_off": "Power switch of a hob or oven (only off, after a person's yes)",
    "valve.generic": "Any other valve (garden tap, sprinkler …)",
    "siren.any": "Siren",
    "camera.any": "Camera",
    "binary_sensor.smoke": "Smoke detector",
    "binary_sensor.carbon_monoxide": "Carbon monoxide detector",
    "binary_sensor.gas": "Gas detector",
    "binary_sensor.moisture": "Water leak sensor",
    "binary_sensor.cold": "Frost sensor",
    "binary_sensor.presence": "Motion, occupancy or presence sensor",
    "binary_sensor.opening": "Door, window or garage door contact",
    "binary_sensor.generic": "Any other on/off sensor",
    "sensor.generic": "Any measuring sensor",
    "weather.generic": "Weather",
    "switch.generic": "Any other switch",
    "number.generic": "Any other number setting",
    "select.generic": "Any other choice setting",
    "button.generic": "Any other button",
    "event.generic": "Event (button presses, doorbell, detections)",
    "media_player.generic": "Media player",
    "vacuum.generic": "Robot vacuum",
    "lawn_mower.generic": "Robot lawn mower",
    "automation.existing": "An automation you made in Home Assistant",
    "switch.day_rhythm": "The switch of a group of lamps that follow the day",
}

SAFETY = {
    "free": ("Free", "HouseMind may switch it within the allowed values."),
    "careful": ("Careful", "Allowed, with limits on how often and how far it may change."),
    "safe_direction": (
        "Safe direction only",
        "Only towards safety: a lock only locks, a valve only closes, a window only closes.",
    ),
    "propose_only": ("Propose only", "HouseMind only asks; a person decides."),
    "never": ("Never", "HouseMind only reads it; it never switches it."),
}
STEPS = {"OBSERVE": "Observe", "PROPOSE": "Propose", "AUTONOMOUS": "Autonomous"}
INITIATIVE = {
    "house": "The house: HouseMind may take the initiative",
    "person": "A person: HouseMind only follows what people start",
    "shared": "Shared: HouseMind helps with what people use",
}
PEOPLE_WIN = {
    "until_area_empty": "until the room is empty",
    "until_situation_changes": "until the situation changes",
    "until_next_daypart": "until the next part of the day",
}
WHO = {"house": "House", "person": "Person", "shared": "Shared"}
# How each value is called, and its unit.
VALUES = {
    "brightness_pct": ("brightness", " %"),
    "color_temp_kelvin": ("colour temperature", " K"),
    "transition": ("transition", " s"),
    "position": ("position", " %"),
    "tilt_position": ("tilt", " %"),
    "temperature": ("temperature", " °C"),
    "humidity": ("humidity", " %"),
    "percentage": ("speed", " %"),
    "hvac_mode": ("mode", ""),
    "preset_mode": ("preset", ""),
    "option": ("option", ""),
    "value": ("value", ""),
}
HARD_LIMITS = [
    "Never turn an alarm off.",
    "Never open a lock.",
    "Never open a garage door, a gate or a door.",
    "Never make, change or delete your automations, scripts or scenes in Home Assistant. "
    "It only switches an automation off after your yes, when it takes over that job, and on "
    "again whenever you want.",
    "Never touch a device an administrator protected.",
    "Never a camera image: HouseMind does not look at, keep or send images.",
]


def _value(rule: ValueRange | ValueChoices | ValueText, unit: str = "") -> str:
    if isinstance(rule, ValueRange):
        low, high = (f"{v:g}" for v in (rule.minimum, rule.maximum))
        return f"{low}–{high}{unit}"
    if isinstance(rule, ValueChoices):
        return " / ".join(sorted(rule.options))
    return "one of the device's own options"


def _actions(kind: DeviceKind) -> str:
    if not kind.actions:
        return "Nothing: read only"
    parts = []
    for action in kind.actions:
        name = action.action.split(".", 1)[1].replace("_", " ")
        values = ", ".join(
            f"{VALUES.get(k, (k.replace('_', ' '), ''))[0]} {_value(v, VALUES.get(k, ('', ''))[1])}"
            for k, v in action.values.items()
        )
        extra = " (propose only)" if action.max_step.name == "PROPOSE" else ""
        parts.append(f"{name}{f' ({values})' if values else ''}{extra}")
    return "; ".join(parts)


def _recognised(kind: DeviceKind) -> str:
    m = kind.match
    bits = [f"`{m.domain}`"]
    if m.device_classes:
        bits.append("device class " + ", ".join(f"`{c}`" for c in m.device_classes))
    if m.color_modes_any:
        bits.append("colour modes " + ", ".join(f"`{c}`" for c in m.color_modes_any))
    if m.required_features:
        bits.append(f"features {m.required_features}")
    if m.role:
        bits.append(f"role `{m.role}` (HouseMind gives this role)")
    return "; ".join(bits)


def _skills(kind: DeviceKind, english: dict[str, str]) -> str:
    names = [english.get(f"skills.{s}", s.replace("_", " ")) for s in kind.skills]
    return ", ".join(names) if names else "–"


def _commit() -> str:
    for git in ("git", "/Library/Developer/CommandLineTools/usr/bin/git"):
        try:
            return subprocess.run(  # noqa: S603 – a fixed git command
                [git, "rev-parse", "--short", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            continue
    return "unknown"


def build() -> str:
    catalog, report = load_catalog([ROOT / "knowledge" / "catalog"])
    if report.warnings:
        raise SystemExit("\n".join(report.warnings))
    english = load_languages([ROOT / "knowledge" / "languages"]).languages["en"]
    files = {path.stem: path for path in (ROOT / "knowledge" / "catalog").glob("*.yaml")}
    in_file: dict[str, list[DeviceKind]] = {key: [] for key, _, _ in SECTIONS}
    for stem, path in files.items():
        keys = [
            line.split("key:", 1)[1].strip()
            for line in path.read_text().splitlines()
            if line.strip().startswith("- key:")
        ]
        in_file.setdefault(stem, []).extend(catalog.kinds[k] for k in keys if k in catalog.kinds)
    count = sum(len(kinds) for kinds in in_file.values())
    today = datetime.now(UTC).date().isoformat()
    out = [
        "# HouseMind – Device catalogue",
        "",
        f"*HouseMind {__version__}, commit `{_commit()}`, {today}. {count} kinds of device. "
        "Generated from HouseMind's own knowledge files, so it says what this version really "
        "does.*",
        "",
        "HouseMind recognises devices by Home Assistant's own terms – the domain, the device "
        "class, the colour modes and the supported features – and never by brand or model. For "
        "every kind of device, the catalogue says what HouseMind may do with it, how far it may "
        "go on its own, and what it learns. Every action, whatever its source, passes the same "
        "safety gate, and a person always wins: switch something yourself, and HouseMind leaves "
        "it alone for a while.",
        "",
        "## How to read the catalogue",
        "",
        "**Safety class** – how strict HouseMind is with a kind of device:",
        "",
        "| Class | Meaning |",
        "|---|---|",
        *[f"| {name} | {meaning} |" for name, meaning in SAFETY.values()],
        "",
        "**Highest step** – how far HouseMind may go once it has learned enough: *Observe* (it "
        "only watches and learns), *Propose* (it asks first) or *Autonomous* (it acts and "
        "tells). Every device starts at Observe; HouseMind earns each step per skill and per "
        "room, and an administrator can always set a lower limit.",
        "",
        "**Who decides** – "
        + "; ".join(f"*{WHO[k]}*: {v.split(': ', 1)[1]}" for k, v in INITIATIVE.items())
        + ".",
        "",
        "**A person wins** – after someone switches a device by hand, HouseMind leaves it alone "
        "for that long, at most the hours shown.",
        "",
        "## Hard limits",
        "",
        "These are in the code itself. No file, update or setting can loosen them:",
        "",
        *[f"- {limit}" for limit in HARD_LIMITS],
        "",
    ]
    for key, title, intro in SECTIONS:
        kinds = in_file.get(key) or []
        if not kinds:
            continue
        out += [
            f"## {title}",
            "",
            intro,
            "",
            "| Device | Recognised by | Safety class | Highest step | Who decides "
            "| What HouseMind may do | A person wins | Skills |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for kind in kinds:
            name = KINDS.get(kind.key, kind.key)
            if kind.experimental:
                name += " *(experimental: off until an administrator turns it on)*"
            wins = (
                f"{PEOPLE_WIN.get(kind.people_win, kind.people_win.replace('_', ' '))}, "
                f"at most {kind.people_win_max_hours:g} h"
                if kind.actions and kind.people_win_max_hours > 0
                else "–"
            )
            out.append(
                f"| {name} | {_recognised(kind)} | {SAFETY[kind.safety_class.value][0]} | "
                f"{STEPS[kind.max_step.name]} | {WHO[kind.initiative.value]} | {_actions(kind)} | "
                f"{wins} | {_skills(kind, english)} |"
            )
        out.append("")
    out += [
        "## Not in the catalogue",
        "",
        "A device that matches no specific kind gets the careful entry for its domain under "
        "*Other devices* – for most domains that means HouseMind only reads it. A domain "
        "HouseMind does not know at all is left alone. Knowledge updates can add kinds; they "
        "can never loosen a hard limit.",
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "device-catalogue.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(), encoding="utf-8")
    print(f"wrote {target}")
