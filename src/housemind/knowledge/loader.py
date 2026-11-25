"""Loading knowledge files in layers (H5.4).

Layers, from first to last: shipped with the App → signed knowledge update → the user's own
files (B-28). A later layer may add new device kinds and may make existing ones *stricter*,
never looser (E-58). Hard limits live in code, not here (B-26).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from housemind.knowledge.catalog import (
    AllowedAction,
    Catalog,
    DeviceKind,
    Initiative,
    Match,
    SafetyClass,
    Step,
    ValueChoices,
    ValueRange,
    ValueRule,
    ValueText,
    is_looser,
)

_LOGGER = logging.getLogger(__name__)

_RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_CHOICES = {
    "type": "object",
    "required": ["one_of"],
    "additionalProperties": False,
    "properties": {"one_of": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
}
_TEXT = {
    "type": "object",
    "required": ["text"],
    "additionalProperties": False,
    "properties": {"text": {"const": True}},
}
_VALUE_RULE = {"oneOf": [_RANGE, _CHOICES, _TEXT]}

CATALOG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema", "kinds"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": 1},
        "kinds": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "match", "safety_class", "initiative", "max_step"],
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string", "pattern": r"^[a-z_]+\.[a-z0-9_]+$"},
                    "match": {
                        "type": "object",
                        "required": ["domain"],
                        "additionalProperties": False,
                        "properties": {
                            "domain": {"type": "string", "pattern": r"^[a-z_]+$"},
                            "device_classes": {"type": "array", "items": {"type": "string"}},
                            "color_modes_any": {"type": "array", "items": {"type": "string"}},
                            "required_features": {"type": "integer", "minimum": 0},
                            "role": {"type": "string"},
                            "platforms": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "safety_class": {"enum": [c.value for c in SafetyClass]},
                    "initiative": {"enum": [i.value for i in Initiative]},
                    "max_step": {"enum": ["observe", "propose", "autonomous"]},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["action"],
                            "additionalProperties": False,
                            "properties": {
                                "action": {"type": "string", "pattern": r"^[a-z_]+\.[a-z_]+$"},
                                "values": {"type": "object", "additionalProperties": _VALUE_RULE},
                                "max_step": {"enum": ["observe", "propose", "autonomous"]},
                                # Only a fixed emergency rule does it itself (E-542).
                                "emergency": {"type": "boolean"},
                            },
                        },
                    },
                    "min_interval_s": {"type": "number", "minimum": 0},
                    "max_changes_per_day": {"type": "integer", "minimum": 1},
                    "people_win": {
                        "enum": [
                            "until_situation_changes",
                            "until_area_empty",
                            "until_next_daypart",
                            "until_house_mode_changes",
                            "fixed_hours",
                        ]
                    },
                    "people_win_max_hours": {"type": "number", "minimum": 0},
                    "skills": {"type": "array", "items": {"type": "string"}},
                    "generic": {"type": "boolean"},
                    "experimental": {"type": "boolean"},
                },
            },
        },
    },
}

_VALIDATOR = Draft202012Validator(CATALOG_SCHEMA)
_STEPS = {"observe": Step.OBSERVE, "propose": Step.PROPOSE, "autonomous": Step.AUTONOMOUS}


class KnowledgeError(ValueError):
    """A knowledge file is not valid."""


@dataclass(slots=True)
class LoadReport:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_catalog_document(document: Any, origin: str) -> None:
    errors = sorted(_VALIDATOR.iter_errors(document), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors[:5]
        )
        raise KnowledgeError(f"{origin}: {details}")


def _kind_from_dict(raw: dict[str, Any]) -> DeviceKind:
    match_raw = raw["match"]
    match = Match(
        domain=match_raw["domain"],
        device_classes=tuple(match_raw.get("device_classes", ())),
        color_modes_any=tuple(match_raw.get("color_modes_any", ())),
        required_features=int(match_raw.get("required_features", 0)),
        role=match_raw.get("role"),
        platforms=tuple(match_raw.get("platforms", ())),
    )
    actions = []
    for a in raw.get("actions", ()):
        ranges: dict[str, ValueRule] = {}
        for name, rule in a.get("values", {}).items():
            if isinstance(rule, dict) and rule.get("text") is True:
                ranges[name] = ValueText()
                continue
            if isinstance(rule, dict):
                ranges[name] = ValueChoices(frozenset(rule["one_of"]))
                continue
            low, high = float(rule[0]), float(rule[1])
            if low > high:
                raise KnowledgeError(f"{raw['key']}: range {name} has min > max")
            ranges[name] = ValueRange(low, high)
        actions.append(
            AllowedAction(
                action=a["action"],
                values=ranges,
                max_step=_STEPS[a.get("max_step", "autonomous")],
                emergency=bool(a.get("emergency", False)),
            )
        )
    return DeviceKind(
        key=raw["key"],
        match=match,
        safety_class=SafetyClass(raw["safety_class"]),
        initiative=Initiative(raw["initiative"]),
        max_step=_STEPS[raw["max_step"]],
        actions=tuple(actions),
        min_interval_s=float(raw.get("min_interval_s", 0)),
        max_changes_per_day=raw.get("max_changes_per_day"),
        people_win=raw.get("people_win", "until_situation_changes"),
        people_win_max_hours=float(raw.get("people_win_max_hours", 4)),
        skills=tuple(raw.get("skills", ())),
        generic=bool(raw.get("generic", False)),
        experimental=bool(raw.get("experimental", False)),
    )


def merge_kind(base: DeviceKind, override: DeviceKind) -> tuple[DeviceKind, list[str]]:
    """Apply a later layer to an existing kind, refusing anything that loosens it."""
    warnings: list[str] = []
    result = base
    if override.match != base.match:
        warnings.append(f"{base.key}: recognition cannot be changed by a later layer")
    if is_looser(override.safety_class, base.safety_class):
        warnings.append(f"{base.key}: safety class cannot be loosened")
    else:
        result = replace(result, safety_class=override.safety_class)
    if override.max_step > base.max_step:
        warnings.append(f"{base.key}: maximum step cannot be raised")
    else:
        result = replace(result, max_step=override.max_step)
    if override.min_interval_s < base.min_interval_s:
        warnings.append(f"{base.key}: switching interval cannot be shortened")
    else:
        result = replace(result, min_interval_s=override.min_interval_s)
    kept: list[AllowedAction] = []
    for action in override.actions:
        original = base.allowed(action.action)
        if original is None:
            warnings.append(f"{base.key}: action {action.action} is not allowed by the base")
            continue
        narrowed: dict[str, ValueRule] = {}
        for name, rule in action.values.items():
            base_rule = original.values.get(name)
            if base_rule is None:
                warnings.append(f"{base.key}: value {name} is not allowed by the base")
                continue
            if isinstance(rule, ValueRange) and isinstance(base_rule, ValueRange):
                narrowed[name] = ValueRange(
                    max(rule.minimum, base_rule.minimum), min(rule.maximum, base_rule.maximum)
                )
            elif isinstance(rule, ValueChoices) and isinstance(base_rule, ValueChoices):
                narrowed[name] = ValueChoices(rule.options & base_rule.options)
            elif isinstance(rule, ValueChoices) and isinstance(base_rule, ValueText):
                narrowed[name] = rule  # a fixed list is stricter than any text
            else:
                warnings.append(f"{base.key}: value {name} changes type; kept the base")
                narrowed[name] = base_rule
        kept.append(
            AllowedAction(
                action.action,
                narrowed,
                min(action.max_step, original.max_step),
                action.emergency and original.emergency,  # never loosened
            )
        )
    if override.actions:
        result = replace(result, actions=tuple(kept))
    return result, warnings


def _may_overlap(new: Match, known: Match) -> bool:
    """Whether some entity could fit both matches (domain, role, class and colour modes)."""
    if new.domain != known.domain:
        return False
    if new.role is not None and known.role is not None and new.role != known.role:
        return False
    if new.device_classes and known.device_classes:
        if not set(new.device_classes) & set(known.device_classes):
            return False
    if new.color_modes_any and known.color_modes_any:
        if not set(new.color_modes_any) & set(known.color_modes_any):
            return False
    return True


def check_new_kind(shipped: Catalog, kind: DeviceKind) -> tuple[DeviceKind | None, list[str]]:
    """A kind that a later layer adds (an own file, a knowledge update) may only be stricter
    (QA 2, B-168). It may not take over a kind HouseMind ships – a "smoke detector" without the
    emergency reaction, a valve that may open: to make a known kind stricter, a file uses its
    key. And it is never looser than the general kind of its domain; without one (an unknown
    domain) it is not used: HouseMind only watches that (E-156)."""
    notes: list[str] = []
    for known in shipped.for_domain(kind.match.domain):
        if known.generic or not _may_overlap(kind.match, known.match):
            continue
        if kind.match.specificity() > known.match.specificity():
            return None, [
                f"{kind.key}: a new kind cannot take over {known.key}; "
                f"use the key {known.key} to make it stricter"
            ]
        if kind.match.specificity() == known.match.specificity():
            notes.append(  # a tie: the shipped kind wins it (matcher)
                f"{kind.key}: a new kind cannot take over {known.key}; "
                f"it is only used where {known.key} does not fit"
            )
    generic = shipped.generic_for(kind.match.domain)
    if generic is None:
        return None, [f"{kind.key}: no general kind for {kind.match.domain}; only watched"]
    base = replace(generic, key=kind.key, match=kind.match, generic=False)
    merged, warnings = merge_kind(base, kind)
    return replace(merged, skills=kind.skills, experimental=True, shipped=False), notes + warnings


def _yaml_files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.yaml") if p.is_file())


def load_catalog(layers: Iterable[Path]) -> tuple[Catalog, LoadReport]:
    """Load catalogue files from each layer directory in order."""
    catalog = Catalog()
    report = LoadReport()
    shipped = Catalog()  # the first layer as shipped: what later layers are checked against
    for layer_index, layer in enumerate(layers):
        if layer_index == 1:
            shipped.kinds = dict(catalog.kinds)
        for path in _yaml_files(layer):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                validate_catalog_document(document, str(path))
                kinds = [_kind_from_dict(raw) for raw in document["kinds"]]
            except (yaml.YAMLError, KnowledgeError) as err:
                if layer_index == 0:
                    raise KnowledgeError(f"shipped knowledge is broken: {err}") from err
                report.warnings.append(f"skipped {path}: {err}")
                _LOGGER.warning("Skipped knowledge file %s: %s", path, err)
                continue
            report.files.append(path)
            for kind in kinds:
                existing = catalog.kinds.get(kind.key)
                if existing is None and layer_index > 0:
                    checked, warnings = check_new_kind(shipped, kind)
                    report.warnings.extend(warnings)
                    if checked is not None:
                        catalog.add(checked)
                elif existing is None:
                    catalog.add(kind)
                else:
                    merged, warnings = merge_kind(existing, kind)
                    catalog.add(merged)
                    report.warnings.extend(warnings)
    return catalog, report
