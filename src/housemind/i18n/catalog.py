"""Language files (E-24 t/m E-27, E-34).

Each language is one YAML file (``en.yaml``, ``nl.yaml`` …) with nested keys. English is the
base language: a missing text falls back to English (E-26). Adding a language means adding a
file – no code changes. Plural forms use ``key.one`` / ``key.other``.
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BASE_LANGUAGE = "en"


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    if isinstance(value, Mapping):
        for key, sub in value.items():
            if not isinstance(key, str):
                # YAML reads unquoted on/off/yes/no as booleans: a classic language-file trap.
                raise ValueError(f'key {key!r} under {prefix!r} must be quoted, e.g. "on":')
            _flatten(f"{prefix}.{key}" if prefix else key, sub, out)
    elif isinstance(value, str):
        out[prefix] = value
    else:
        raise ValueError(f"text for {prefix!r} must be a string")


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


_FORMATTER = string.Formatter()


@dataclass(slots=True)
class Languages:
    languages: dict[str, dict[str, str]] = field(default_factory=dict)
    _missing_seen: set[tuple[str, str]] = field(default_factory=set)
    _complete: dict[str, dict[str, str]] = field(default_factory=dict)
    # The word lists of every language per key (daily_words), made once (code QA 3, punt 4).
    daily: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def add(self, code: str, texts: dict[str, str]) -> None:
        self.languages.setdefault(code, {}).update(texts)
        self._complete.clear()
        self.daily.clear()

    def complete(self, language: str) -> dict[str, str]:
        """Every text of a language, a gap filled with English – for a screen, which then needs
        no English beside it (code QA 3, punt 12). The shipped languages have no gaps: then it
        is the language itself; an own language file in /config/housemind may have some."""
        lang = self.resolve(language)
        found = self._complete.get(lang)
        if found is None:
            own, base = self.languages.get(lang, {}), self.languages.get(BASE_LANGUAGE, {})
            found = own if base.keys() <= own.keys() else {**base, **own}
            self._complete[lang] = found
        return found

    def number(self, language: str | None, value: float, digits: int | None = None) -> str:
        """A number as this language writes it – "2,5" or "2.5" – with the decimal sign of
        its language file, so a new language needs no code (code QA 3, punt 8). ``digits``:
        that many after the sign; else as short as it is ("2.5", "3")."""
        text = f"{value:.{digits}f}" if digits is not None else f"{value:g}"
        return text.replace(".", self.text(language, "format.decimal"))

    def resolve(self, language: str | None) -> str:
        """Pick the best available language for an HA language code like ``nl-BE``."""
        if language:
            if language in self.languages:
                return language
            short = language.split("-", 1)[0].lower()
            if short in self.languages:
                return short
        return BASE_LANGUAGE

    def render(self, language: str | None, value: Any) -> Any:
        """A piece of text kept for later, written in the language of who reads or hears it,
        not of the house, so no sentence mixes two languages. ``{"t": key, "p": {...}}`` is
        one text; ``{"join": [...], "sep": ""}`` puts pieces after each other;
        ``{"list": [...]}`` makes "a, b and c"; ``{"lang": {code: text}}`` is a text already
        written in each language. Anything else stays as it is."""
        if isinstance(value, Mapping):
            if "lang" in value and isinstance(value["lang"], Mapping):
                written = value["lang"]
                found = written.get(self.resolve(language)) or written.get(BASE_LANGUAGE)
                return found if found is not None else next(iter(written.values()), "")
            if "t" in value:
                params = {k: self.render(language, v) for k, v in (value.get("p") or {}).items()}
                return self.text(language, str(value["t"]), **params)
            if "join" in value:
                return str(value.get("sep", "")).join(
                    str(self.render(language, v)) for v in value["join"]
                )
            if "list" in value:
                parts = [str(self.render(language, v)) for v in value["list"]]
                if len(parts) <= 1:
                    return "".join(parts)
                return ", ".join(parts[:-1]) + f" {self.text(language, 'ui.and')} " + parts[-1]
        return value

    def text(self, language: str | None, key: str, count: int | None = None, **params: Any) -> str:
        lang = self.resolve(language)
        params = {k: self.render(lang, v) for k, v in params.items()}
        lookup = key
        if count is not None:
            params.setdefault("count", count)
            plural = f"{key}.one" if count == 1 else f"{key}.other"
            # A text without one/other forms is used as it is, never shown as its key.
            if self._has(lang, plural) or not self._has(lang, key):
                lookup = plural
        template = self.languages.get(lang, {}).get(lookup)
        if template is None:
            self._missing_seen.add((lang, lookup))
            template = self.languages.get(BASE_LANGUAGE, {}).get(lookup, lookup)
        if "{" not in template and "}" not in template:
            return template  # most texts have nothing to fill in (code QA 3, punt 18)
        return _FORMATTER.vformat(template, (), _SafeDict(params))

    def _has(self, lang: str, key: str) -> bool:
        return key in self.languages.get(lang, {}) or key in self.languages.get(BASE_LANGUAGE, {})

    def missing_keys(self) -> dict[str, set[str]]:
        """Keys present in English but missing in another language (E-26, shown at level 4)."""
        base = set(self.languages.get(BASE_LANGUAGE, {}))
        return {
            code: base - set(texts)
            for code, texts in self.languages.items()
            if code != BASE_LANGUAGE and base - set(texts)
        }


# Words that protect (QA 2, B-168): a later directory (an own file, a knowledge update) may add
# to these lists, never shorten them – a garage door stays a garage door.
PROTECTIVE_WORDS = frozenset({"daily_words.garage_door", "daily_words.cooking"})
# Words that switch an emergency off for a sensor (rain is no leak, a freezer is no frost): only
# the shipped language files decide these, in every language.
EXEMPTING_WORDS = frozenset({"daily_words.not_leak", "daily_words.fridge"})


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split(",") if w.strip()]


def load_languages(directories: Path | Iterable[Path]) -> Languages:
    """Load every ``<code>.yaml`` in the given directories (later directories add or replace,
    except the words that keep someone safe: see PROTECTIVE_WORDS and EXEMPTING_WORDS)."""
    dirs = [directories] if isinstance(directories, Path) else list(directories)
    result = Languages()
    for index, directory in enumerate(dirs):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            flat: dict[str, str] = {}
            _flatten("", raw, flat)
            if index > 0:
                known = result.languages.get(path.stem, {})
                for key in EXEMPTING_WORDS & set(flat):
                    del flat[key]
                for key in PROTECTIVE_WORDS & set(flat) & set(known):
                    flat[key] = ", ".join(dict.fromkeys([*_words(known[key]), *_words(flat[key])]))
            result.add(path.stem, flat)
    return result
