"""Colour schemes as files (E-266, E-267, B-113, B-114).

Every colour scheme is one small YAML file, like the language and knowledge files: the shipped
ones in ``knowledge/themes/`` (they may come with a knowledge update), the administrator's own
in ``/config/housemind/themes/`` (B-28). An own file with the id of a shipped one goes first.

A scheme only comes into the list when it is readable (WCAG 2.2): text, quiet text, links and
the three status colours at least 4.5:1 on their backgrounds (7:1 for AAA), text on a primary
button too, and the edge of fields and the focus ring at least 3:1. A file that fails stays
out, with the reason, so the administrator can see why. The App looks at the folders every
minute (``signature``), so a new or changed file comes in without a restart. Vesta's own
colours never change; a scheme only says how strong her glow is.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)

# The roles of the design system (tokens.json), in the files' own Dutch words.
ROLES = (
    "inkt",  # the background
    "vlak",  # cards and fields
    "verhoogd",  # a button on a card, a chosen item
    "lijn",  # thin lines, decoration only
    "rand",  # the edge of fields and buttons
    "tekst",
    "gedempt",  # quiet text: hints and labels
    "accent",  # links, the primary button, the focus ring
    "op-accent",  # text on a primary button
    "nood",
    "belangrijk",
    "leren",
)
BASES = {"donker": "dark", "dark": "dark", "licht": "light", "light": "light"}
LEVELS = ("AA", "AAA")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class Rule:
    role: str
    on: tuple[str, ...]
    aa: float
    aaa: float


# What the App checks on every file (the same check as in the proposal of 19-10-2026).
RULES = (
    Rule("tekst", ("inkt", "vlak", "verhoogd"), 4.5, 7.0),
    Rule("gedempt", ("inkt", "vlak", "verhoogd"), 4.5, 7.0),
    Rule("accent", ("inkt", "vlak", "verhoogd"), 4.5, 7.0),
    Rule("op-accent", ("accent",), 4.5, 7.0),
    Rule("nood", ("vlak", "verhoogd"), 4.5, 7.0),
    Rule("belangrijk", ("vlak", "verhoogd"), 4.5, 7.0),
    Rule("leren", ("vlak", "verhoogd"), 4.5, 7.0),
    Rule("rand", ("inkt", "vlak"), 3.0, 3.0),  # also the focus ring: accent is checked above
)


def luminance(colour: str) -> float:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@dataclass(frozen=True, slots=True)
class Problem:
    """Why a file is not in the list. ``code``: unreadable, id, base, level, missing,
    not_a_colour or contrast; the other fields say where."""

    file: str
    code: str
    role: str | None = None
    on: str | None = None
    ratio: float | None = None
    need: float | None = None
    value: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "code": self.code,
            "role": self.role,
            "on": self.on,
            "ratio": self.ratio,
            "need": self.need,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class Scheme:
    id: str
    names: dict[str, str]
    hints: dict[str, str]
    base: str  # "dark" or "light"
    level: str  # "AA" or "AAA"
    colours: dict[str, str]
    glow: float  # how strong Vesta's glow is; 1 is normal
    order: int
    own: bool  # from the administrator's own folder
    file: str

    def name(self, language: str) -> str:
        return self.names.get(language) or self.names.get("en") or self.id

    def hint(self, language: str) -> str:
        return self.hints.get(language) or self.hints.get("en") or ""

    def view(self, language: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name(language),
            "hint": self.hint(language),
            "base": self.base,
            "level": self.level,
            "colours": dict(self.colours),
            "glow": self.glow,
            "own": self.own,
        }


@dataclass(slots=True)
class Schemes:
    by_id: dict[str, Scheme] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)

    def ordered(self) -> list[Scheme]:
        return sorted(self.by_id.values(), key=lambda s: (s.order, s.name("en").casefold()))


def check(colours: dict[str, str], level: str, file: str) -> list[Problem]:
    """The readability check of one scheme: every rule on every background it names."""
    problems = []
    for rule in RULES:
        need = rule.aaa if level == "AAA" else rule.aa
        for on in rule.on:
            ratio = contrast(colours[rule.role], colours[on])
            if ratio < need:
                problems.append(Problem(file, "contrast", rule.role, on, round(ratio, 2), need))
    return problems


def _texts(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        return {"en": raw}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, (str, int, float))}
    return {}


def read(path: Path, own: bool) -> tuple[Scheme | None, list[Problem]]:
    """One file: the scheme, or why it cannot be used."""
    file = f"{'own/' if own else ''}{path.name}"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None, [Problem(file, "unreadable")]
    if not isinstance(raw, dict):
        return None, [Problem(file, "unreadable")]
    scheme_id = str(raw.get("id") or path.stem).strip().lower()
    if not _ID.match(scheme_id):
        return None, [Problem(file, "id", value=scheme_id[:40])]
    base = BASES.get(str(raw.get("basis", raw.get("base", ""))).strip().lower())
    if base is None:
        return None, [Problem(file, "base", value=str(raw.get("basis", ""))[:20])]
    level = str(raw.get("niveau", raw.get("level", "AA"))).strip().upper()
    if level not in LEVELS:
        return None, [Problem(file, "level", value=level[:10])]
    given = raw.get("kleuren", raw.get("colours", {}))
    given = given if isinstance(given, dict) else {}
    colours: dict[str, str] = {}
    problems: list[Problem] = []
    for role in ROLES:
        value = given.get(role)
        if value is None:
            problems.append(Problem(file, "missing", role=role))
        elif not isinstance(value, str) or not _COLOUR.match(value.strip()):
            problems.append(Problem(file, "not_a_colour", role=role, value=str(value)[:20]))
        else:
            colours[role] = value.strip().upper()
    if problems:
        return None, problems
    problems = check(colours, level, file)
    if problems:
        return None, problems
    vesta: dict[str, Any] = raw["vesta"] if isinstance(raw.get("vesta"), dict) else {}
    try:
        glow = min(1.5, max(0.0, float(vesta.get("gloed", vesta.get("glow", 1.0)))))
    except (TypeError, ValueError):
        glow = 1.0
    try:
        order = int(raw.get("volgorde", raw.get("order", 100)) or 100)
    except (TypeError, ValueError):
        order = 100
    names = _texts(raw.get("naam", raw.get("name"))) or {"en": scheme_id}
    return Scheme(
        scheme_id,
        names,
        _texts(raw.get("uitleg", raw.get("hint"))),
        base,
        level,
        colours,
        glow,
        order,
        own,
        file,
    ), []


def _files(directory: Path) -> list[Path]:
    try:
        return sorted(
            p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()
        )
    except OSError:
        return []


def load_schemes(directories: Iterable[Path], own: Path | None = None) -> Schemes:
    """All schemes, later folders first: shipped → knowledge update → own (``own``)."""
    found = Schemes()
    for directory in directories:
        is_own = own is not None and directory == own
        for path in _files(directory):
            scheme, problems = read(path, is_own)
            if scheme is None:
                found.problems.extend(problems)
                for problem in problems:
                    _LOGGER.warning("Colour scheme %s left out: %s", problem.file, problem)
                continue
            found.by_id[scheme.id] = scheme
    return found


def signature(directories: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
    """What is in the folders now: a new, changed or removed file changes it."""
    seen = []
    for directory in directories:
        for path in _files(directory):
            try:
                stat = path.stat()
            except OSError:
                continue
            seen.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(seen)
