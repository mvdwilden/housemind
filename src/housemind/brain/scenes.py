"""Scenes per house (E-106): recognisable moments – cooking, dinner, a film, work – with light
and atmosphere per room.

The start set comes from ``knowledge/scenes/*.yaml`` per room role, made with the lights the
room really has. People add their own by saving how a room looks now ("remember this").
A scene is HouseMind's own: it never creates a Home Assistant scene (E-03), and starting one
is a person's request through the same safety gate as everything else (E-110).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from housemind.core.model import Entity, State

Step = tuple[str, dict[str, Any]]  # action, data


@dataclass(frozen=True, slots=True)
class SceneSpec:
    key: str
    roles: tuple[str, ...]
    light: dict[str, Any] | None = None
    covers: str | None = None  # "close" or "open"


@dataclass(slots=True)
class Scene:
    id: str  # "start:movie" or "person:12"
    area_id: str
    key: str
    name: str | None  # a person's own name; start scenes are named by the language file
    source: str  # "start" or "person"
    steps: dict[str, Step] = field(default_factory=dict)


def load_start_scenes(directories: Iterable[Path]) -> dict[str, SceneSpec]:
    found: dict[str, SceneSpec] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key, spec in (raw.get("scenes") or {}).items():
                if not isinstance(spec, dict):
                    continue
                found[str(key)] = SceneSpec(
                    str(key),
                    tuple(str(r) for r in spec.get("in", ())),
                    dict(spec["light"]) if isinstance(spec.get("light"), dict) else None,
                    str(spec["covers"]) if spec.get("covers") in ("close", "open") else None,
                )
    return found


def derive(
    spec: SceneSpec,
    lights: list[tuple[Entity, set[str]]],
    covers: list[Entity],
) -> dict[str, Step]:
    """A start scene for one room: what each light (and window covering) should do."""
    steps: dict[str, Step] = {}
    for entity, values in lights:
        if spec.light is None:
            steps[entity.entity_id] = (f"{entity.domain}.turn_off", {})
            continue
        data = {k: v for k, v in spec.light.items() if k in values}
        steps[entity.entity_id] = (f"{entity.domain}.turn_on", data)
    if spec.covers:
        action = "cover.close_cover" if spec.covers == "close" else "cover.open_cover"
        for entity in covers:
            steps[entity.entity_id] = (action, {})
    return steps


def capture(
    states: dict[str, State],
    lights: list[tuple[Entity, set[str]]],
    covers: list[Entity],
) -> dict[str, Step]:
    """How a room looks now, as a scene of its own."""
    steps: dict[str, Step] = {}
    for entity, values in lights:
        state = states.get(entity.entity_id)
        if state is None or not state.available:
            continue
        if state.state != "on":
            steps[entity.entity_id] = (f"{entity.domain}.turn_off", {})
            continue
        data: dict[str, Any] = {}
        brightness = state.attributes.get("brightness")
        if "brightness_pct" in values and isinstance(brightness, int | float):
            data["brightness_pct"] = max(1, round(brightness * 100 / 255))
        kelvin = state.attributes.get("color_temp_kelvin")
        if "color_temp_kelvin" in values and isinstance(kelvin, int | float):
            data["color_temp_kelvin"] = int(kelvin)
        steps[entity.entity_id] = (f"{entity.domain}.turn_on", data)
    for entity in covers:
        state = states.get(entity.entity_id)
        if state is not None and state.state in ("open", "closed"):
            action = "cover.open_cover" if state.state == "open" else "cover.close_cover"
            steps[entity.entity_id] = (action, {})
    return steps


def to_json(steps: dict[str, Step]) -> str:
    return json.dumps({e: {"action": a, "data": d} for e, (a, d) in steps.items()})


def from_json(raw: str) -> dict[str, Step]:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(e): (str(s.get("action", "")), dict(s.get("data") or {}))
        for e, s in value.items()
        if isinstance(s, dict) and s.get("action")
    }


def scene_words(languages: Any, keys: Iterable[str]) -> dict[str, list[str]]:
    """``scenes.words.<key>`` from every language file."""
    found: dict[str, list[str]] = {}
    for key in keys:
        for texts in languages.languages.values():
            raw = texts.get(f"scenes.words.{key}")
            if raw:
                found.setdefault(key, []).extend(
                    w.strip().lower() for w in str(raw).split(",") if w.strip()
                )
    return found


def mentions(text: str, word: str) -> bool:
    t = " ".join(re.sub(r"[?!.,;]+", " ", text.lower()).split())
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", t) is not None
