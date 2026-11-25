"""Every house setting in one table (code QA 3, punt 1).

A setting was spread over five to seven places: its default where it was read, the list of
what a screen may change, the list of what goes in the settings file, the check where it was
written, and the screen. In 0.3.23 that went wrong for Beveiliging: the screen said Bewaard,
but the key was in no list. Here each setting has its default, whether a screen may change it,
whether it travels in the settings file, and for a plain one the check; the lists elsewhere are
made from this table, and a test holds every screen setting to being kept.

Only what people choose is here. What HouseMind keeps for itself (what it learned, when it last
looked) stays where it is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LEVELS = (1, 2, 3, 4)


@dataclass(frozen=True, slots=True)
class Setting:
    default: Any = None
    screen: bool = False  # Huisinstellingen may change it (through set_house_setting)
    in_file: bool = False  # it travels in the settings file (E-296)
    # "bool" or "level": checked here and written as it is; "" – its own check in
    # HouseMind.set_house_setting, often with something that follows (a voice, a pipeline).
    plain: str = ""
    part: str = ""  # "security": Huisinstellingen › Beveiliging (16.6)
    example: Any = None  # a good value, for the test that every screen setting is kept


TABLE: dict[str, Setting] = {
    # Travels in the settings file, in this order (E-296).
    "admin_max_step": Setting({}, in_file=True),
    "admin_step": Setting({}, in_file=True),
    "thresholds": Setting({}, in_file=True),
    "excluded": Setting([], screen=True, in_file=True, example=[]),
    "protected": Setting([], in_file=True),
    "pet_areas": Setting(None, screen=True, in_file=True, example=[]),  # None: the start
    "experimental": Setting([], screen=True, in_file=True, example=[]),
    "knowledge_updates": Setting(True, screen=True, in_file=True, plain="bool", example=False),
    "new_person_level": Setting(1, screen=True, in_file=True, plain="level", example=2),
    "resident_top_level": Setting(3, screen=True, in_file=True, plain="level", example=2),
    "phoneless_residents": Setting(False, screen=True, in_file=True, example=True),
    "lived_in": Setting(True, screen=True, in_file=True, plain="bool", example=False),
    "vesta_name": Setting(None, screen=True, in_file=True, example="Nova"),
    "vesta_voice": Setting(None, screen=True, in_file=True, example=None),
    "energy": Setting(None, in_file=True),
    "tidy_choices": Setting({}, in_file=True),
    "tidy_marks": Setting({}, in_file=True),
    # Only on the screen: a yes given in this house is asked again (QA 2, M-2).
    "consent_learning": Setting(False, screen=True, example=True),
    "history_learning": Setting(True, screen=True, example=False),
    "care": Setting(None, screen=True),
    "vesta_window": Setting(True, screen=True, plain="bool", example=False),
    "private_names": Setting([], screen=True, example=["Sam"]),
    "calendar": Setting(None, screen=True, example=True),
    # Huisinstellingen › Beveiliging (16.6): which alarm, the sounds, what opens.
    "alarm_entity": Setting(None, screen=True, part="security"),
    "camera_sounds": Setting([], screen=True, part="security", example=[]),
    "alarm_open_covers": Setting([], screen=True, part="security", example=[]),
    "alarm_self": Setting({}, screen=True, part="security", example={}),
    "alarm_signal_lamps": Setting([], screen=True, part="security", example=[]),
    "fire_open_covers": Setting([], screen=True, part="security", example=[]),
}

SCREEN_KEYS = frozenset(key for key, s in TABLE.items() if s.screen)
FILE_KEYS = tuple(key for key, s in TABLE.items() if s.in_file)
SECURITY_KEYS = tuple(key for key, s in TABLE.items() if s.part == "security")


def default(key: str) -> Any:
    """What a setting is until someone chose: a fresh copy, never the table's own."""
    value = TABLE[key].default
    return type(value)() if isinstance(value, dict | list) else value


def plain(key: str, value: Any) -> tuple[bool, Any]:
    """For a plain setting: whether ``value`` is good, and as what it is kept."""
    setting = TABLE.get(key)
    if setting is None or not setting.screen:
        return False, None
    if setting.plain == "bool":
        return isinstance(value, bool), value
    if setting.plain == "level":
        return (not isinstance(value, bool) and value in LEVELS), value
    return False, None
