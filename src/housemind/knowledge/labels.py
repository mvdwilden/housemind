"""Labels and categories from Home Assistant as a hint (E-345, B-219).

Many houses give devices, entities and automations labels ("Lights", "Presence") and
categories ("Curtains", "Heating"). HouseMind learns from them but never lets them lead: a label
only fills in the part where its own recognition says "other", and anything it would then
operate waits for an administrator's yes (B-220). Which part a label points to comes from the
words in its name and its pictogram (``knowledge/labels.yaml``); a label that fits two parts, or
labels that point different ways, give no hint. A house without labels works as before.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from housemind.core.model import Entity, HouseModel
from housemind.knowledge.words import plain as words_plain

SYSTEM = "system"  # Home Assistant itself, the network, a tablet: no house device


@dataclass(slots=True)
class LabelHints:
    parts: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)  # words, icons
    system: tuple[set[str], set[str]] = field(default_factory=lambda: (set(), set()))


def load_label_hints(directories: Iterable[Path]) -> LabelHints:
    """The words and pictograms of every knowledge folder together (a house may add its own)."""
    hints = LabelHints()
    for directory in directories:
        path = directory / "labels.yaml"
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for part, spec in (raw.get("parts") or {}).items():
            words, icons = hints.parts.setdefault(str(part), (set(), set()))
            words.update(_plain(str(w)) for w in (spec or {}).get("words") or ())
            icons.update(str(i) for i in (spec or {}).get("icons") or ())
        spec = raw.get("system") or {}
        hints.system[0].update(_plain(str(w)) for w in spec.get("words") or ())
        hints.system[1].update(str(i) for i in spec.get("icons") or ())
    return hints


def _plain(text: str) -> str:
    """Lower case, without accents, letters and digits only, one space between words:
    'Média (TV, Audio)' → 'media tv audio'."""
    return words_plain(text)  # one rule for every language (code QA 3, punt 4)


def _fits(name: str, icon: str | None, words: set[str], icons: set[str]) -> bool:
    plain = f" {_plain(name)} "
    return any(f" {w} " in plain for w in words if w) or (icon is not None and icon in icons)


def hint(hints: LabelHints, name: str, icon: str | None = None) -> str | None:
    """The part a label or category points to, SYSTEM, or None (no hint, or two)."""
    found = {p for p, (words, icons) in hints.parts.items() if _fits(name, icon, words, icons)}
    if _fits(name, icon, *hints.system):
        found.add(SYSTEM)
    return found.pop() if len(found) == 1 else None


def _one(hints: LabelHints, named: list[tuple[str, str | None]]) -> tuple[str, str] | None:
    """One part from several labels, with the name of a label that says it; labels that point
    different ways give none."""
    found = [(p, name) for name, icon in named if (p := hint(hints, name, icon)) is not None]
    parts = {p for p, _ in found}
    return found[0] if len(parts) == 1 else None


def label_names(model: HouseModel, entity: Entity) -> list[str]:
    """The labels of an entity and of its device, by name."""
    device = model.devices.get(entity.device_id) if entity.device_id else None
    ids = [*entity.labels, *(device.labels if device else ())]
    return sorted({model.labels[i].name for i in ids if i in model.labels})


def category_names(model: HouseModel, entity: Entity) -> list[str]:
    return sorted(
        {model.categories[c].name for c in entity.categories.values() if c in model.categories}
    )


def entity_hint(model: HouseModel, hints: LabelHints, entity: Entity) -> tuple[str, str] | None:
    """(part, label name) for an entity: its own labels first, then those of its device."""
    own = [(model.labels[i].name, model.labels[i].icon) for i in entity.labels if i in model.labels]
    if own and (found := _one(hints, own)) is not None:
        return found
    if own and any(hint(hints, n, i) for n, i in own):
        return None  # its own labels disagree: the device's do not decide it
    device = model.devices.get(entity.device_id) if entity.device_id else None
    theirs = [
        (model.labels[i].name, model.labels[i].icon)
        for i in (device.labels if device else ())
        if i in model.labels
    ]
    return _one(hints, theirs)


def automation_hint(model: HouseModel, hints: LabelHints, entity: Entity) -> tuple[str, str] | None:
    """(part, category or label name) for an automation, script or scene: its category first."""
    named = [
        (model.categories[c].name, model.categories[c].icon)
        for c in entity.categories.values()
        if c in model.categories
    ]
    return _one(hints, named) or entity_hint(model, hints, entity)
