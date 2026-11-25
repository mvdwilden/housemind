"""What the Help and the manual say, for Vesta (E-246, E-238, B-94).

At start every Help question (four languages, per level) and every part of the manual that ships
with the app (``knowledge/manual/<language>.html``, with an id per chapter and section) becomes
a piece. A question is searched on its words – without the small words of its language, and
with the start of long words, so "microfoon" also finds "microfoons". No search service: it all
stays in the house. ``help-links.yaml`` says which part of the manual tells more about each Help
question ("Lees meer").

The manual comes in two forms (E-324, B-194): the formatted one, the same page as online, with a
``<section id>`` per chapter and a ``<div class="sub" id>`` per section; and the plain one, an
``<article>`` with an ``<h2 id>`` or ``<h3 id>`` per part, for a language that has no formatted
one yet.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_HEADING = re.compile(r"<h([23])[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</h\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
STRONG = 5.0  # a clear hit: her answer comes from the Help itself
WEAK = 2.0  # something close: she names it, or an AI may use it


@dataclass(frozen=True, slots=True)
class Piece:
    key: str  # "help:l2:q18" or "manual:vesta-typen"
    language: str
    title: str
    text: str
    source: str  # "help" or "manual"
    level: int = 0  # the Help level it belongs to
    more: str = ""  # the manual section that tells more (Lees meer)


@dataclass(slots=True)
class Library:
    pieces: dict[str, list[Piece]] = field(default_factory=dict)  # per language
    manuals: dict[str, str] = field(default_factory=dict)  # language -> HTML of the manual
    stop: dict[str, set[str]] = field(default_factory=dict)
    _index: dict[str, list[tuple[Piece, Counter[str], Counter[str]]]] = field(default_factory=dict)
    _df: dict[str, Counter[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, languages: Any, directories: Iterable[Path], name: str = "Vesta") -> Library:
        library = cls()
        links: dict[str, dict[str, str]] = {}
        for directory in directories:
            folder = directory / "manual"
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.html")):
                library.manuals[path.stem] = path.read_text(encoding="utf-8")
            linked = folder / "help-links.yaml"
            if linked.is_file():
                raw = yaml.safe_load(linked.read_text(encoding="utf-8")) or {}
                for level, questions in raw.items():
                    for question, section in (questions or {}).items():
                        links.setdefault(str(level), {})[str(question)] = str(section)
        for code, texts in languages.languages.items():
            stop = texts.get("talk.know.stop", "")
            library.stop[code] = {w.strip().lower() for w in str(stop).split(",") if w.strip()}
            pieces: list[Piece] = []
            for key, question in texts.items():
                found = re.fullmatch(r"help\.(l(\d))\.q(\d+)", key)
                if not found:
                    continue
                level, number = found.group(1), found.group(3)
                answer = texts.get(f"help.{level}.a{number}", "")
                pieces.append(
                    Piece(
                        f"help:{level}:q{number}",
                        code,
                        str(question).replace("{name}", name),
                        str(answer).replace("{name}", name),
                        "help",
                        int(found.group(2)),
                        links.get(level, {}).get(f"q{number}", "")
                        if code in library.manuals
                        else "",
                    )
                )
            pieces += library._sections(code)
            library.pieces[code] = pieces
        library._build_index()
        return library

    def _sections(self, code: str) -> list[Piece]:
        """The manual's parts, each with its title and text."""
        source = self.manuals.get(code)
        if not source:
            return []
        return [
            Piece(f"manual:{part_id}", code, title, body, "manual")
            for part_id, title, body in manual_parts(source)
        ]

    def place(self, piece: Piece) -> tuple[str, str] | None:
        """Where a piece is in the manual: the part's id and title. A Help question is at the
        part ``help-links.yaml`` names for it – the Help is the manual (E-506), its short
        questions are not shown any more. None when the manual has no such part."""
        if piece.source == "manual":
            return piece.key.split(":", 1)[1], piece.title
        if not piece.more:
            return None
        part = next(
            (p for p in self.pieces.get(piece.language, []) if p.key == f"manual:{piece.more}"),
            None,
        )
        return piece.more, part.title if part is not None else piece.title

    # -- searching -------------------------------------------------------------------------

    def _terms(self, text: str, code: str) -> list[str]:
        stop = self.stop.get(code, set())
        out = []
        for word in _WORD.findall(text.lower()):
            word = word.strip("'’-")
            if len(word) < 3 or word in stop:
                continue
            out.append(word[:6] if len(word) > 6 else word)  # "microfoon" ~ "microfoons"
        return out

    def _build_index(self) -> None:
        for code, pieces in self.pieces.items():
            rows = []
            df: Counter[str] = Counter()
            for piece in pieces:
                title = Counter(self._terms(piece.title, code))
                body = Counter(self._terms(piece.text, code))
                rows.append((piece, title, body))
                df.update(set(title) | set(body))
            self._index[code] = rows
            self._df[code] = df

    def search(
        self, question: str, language: str, limit: int = 3, level: int = 4
    ) -> list[tuple[float, Piece]]:
        """The pieces that fit a question best, with a score (STRONG, WEAK). Help from a level
        above the asker's is left out, as in the Help itself (QA 2, M-14); the manual is for
        everyone."""
        code = language if language in self._index else language.split("-")[0]
        rows = self._index.get(code, [])
        if not rows:
            return []
        terms = set(self._terms(question, code))
        total = len(rows)
        df = self._df[code]
        scored = []
        for piece, title, body in rows:
            if piece.level > level:
                continue
            score, found = 0.0, 0
            for term in terms:
                if term not in title and term not in body:
                    continue
                found += 1
                idf = math.log(1 + total / (1 + df[term]))
                score += idf * (3.0 if term in title else 1.0)  # the question itself counts most
            if found:
                # How much of the question fits counts too: one shared word is no answer.
                coverage = found / len(terms)
                scored.append((score * coverage**2 / max(1.0, math.sqrt(len(terms))) * 2, piece))
        scored.sort(key=lambda pair: -pair[0])
        return scored[:limit]

    def fits(self, question: str, language: str, piece: Piece) -> bool:
        """Whether a hit is a real answer (E-349, B-230): a real question of at least three
        words, whose own words are in the piece – two of them when it has more, and most of
        them. One or two words ("graag", "liever niet") are never enough."""
        code = language if language in self._index else language.split("-")[0]
        terms = set(self._terms(question, code))
        row = next((r for r in self._index.get(code, []) if r[0] is piece), None)
        if row is None or not terms or len(_WORD.findall(question)) < 3:
            return False
        found = sum(1 for term in terms if term in row[1] or term in row[2])
        return found >= min(2, len(terms)) and found * 2 >= len(terms)

    def section(self, language: str, section_id: str) -> Piece | None:
        for piece in self.pieces.get(language, []):
            if piece.key == f"manual:{section_id}":
                return piece
        return None


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def formatted(source: str) -> bool:
    """The formatted manual is a whole page; the plain one only its text (B-194)."""
    return source.lstrip()[:15].lower() == "<!doctype html>"


def manual_parts(source: str) -> list[tuple[str, str, str]]:
    """The parts of the manual in order: (id, title, text). Lees meer and Vesta use these ids,
    so they are the same in every language and every form."""
    if not formatted(source):
        found = list(_HEADING.finditer(source))
        out = []
        for i, heading in enumerate(found):
            end = found[i + 1].start() if i + 1 < len(found) else len(source)
            out.append(
                (heading.group(2), _plain(heading.group(3)), _plain(source[heading.end() : end]))
            )
        return out
    reader = _Parts()
    reader.feed(source)
    reader.close()
    return [
        (part.id, " ".join(part.title.split()), " ".join(part.text.split()))
        for part in reader.parts
    ]


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
_VOID |= {"track", "wbr"}  # tags without an end
_SKIP = {"script", "style", "svg", "template"}


@dataclass(slots=True)
class _Part:
    id: str
    title: str = ""
    text: str = ""


class _Parts(HTMLParser):
    """The text per part of the formatted manual: the innermost part an element is in. Scripts,
    styles, pictures and what is only decoration (aria-hidden) are no text: otherwise the menu
    script ends up in the last part."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[_Part] = []
        self._open: list[tuple[str, _Part | None, bool, bool]] = []  # tag, part, hidden, title

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        found = dict(attrs)
        part_id = found.get("id")
        part = None
        classes = (found.get("class") or "").split()
        if part_id and (tag in ("section", "article") or (tag == "div" and "sub" in classes)):
            part = _Part(part_id)
            self.parts.append(part)
        hidden = tag in _SKIP or found.get("aria-hidden") == "true"
        inner = self._part()
        title = tag in ("h2", "h3", "h4") and inner is not None and not inner.title
        self._open.append((tag, part, hidden, title))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                return

    def handle_data(self, data: str) -> None:
        if any(hidden for _, _, hidden, _ in self._open):
            return
        inner = self._part()
        if inner is None:
            return
        if any(title for _, _, _, title in self._open):
            inner.title += data
        else:
            inner.text += f" {data}"

    def _part(self) -> _Part | None:
        for _, part, _, _ in reversed(self._open):
            if part is not None:
                return part
        return None


def manual_for(source: str, name: str) -> str:
    """The manual with the name the administrator gave her, where it says Vesta (E-239): in the
    text and in what is read or shown from a tag (a label, a title), never in an id, a script
    or a style (their hashes are in the CSP)."""
    if not name or name == "Vesta":
        return source
    shown = html.escape(name, quote=True)  # never markup, whatever the name (QA 3, gat 2)

    def named(text: str) -> str:
        return _NAME.sub(lambda _: shown, text)

    pieces = _UNTOUCHED.split(source)
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            pieces[i] = named(piece)
        elif piece[:7].lower() not in ("<script", "<style>", "<style "):
            pieces[i] = _SHOWN.sub(lambda m: f'{m.group(1)}="{named(m.group(2))}"', piece)
    return "".join(pieces)


_NAME = re.compile(r"\bVesta\b")
_UNTOUCHED = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>|<[^>]+>)", re.S | re.I)
_SHOWN = re.compile(r'\b(aria-label|data-label|title|alt)="([^"]*)"')


def gist(text: str, sentences: int = 2, limit: int = 260) -> str:
    """The core of a Help answer: its first sentence or two."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = ""
    for part in parts[:sentences]:
        if out and len(out) + len(part) > limit:
            break
        out = f"{out} {part}".strip()
    return out


# A name the administrator gives her (E-239): letters in any script, digits, a space, a hyphen,
# an apostrophe or a dot – nothing that could be markup or code (QA 3, gat 2).
_GOOD_NAME = re.compile(r"[^\W_](?:[\w .'’-]*[^\W_.])?")
NAME_LENGTH = 24


def good_name(value: object) -> str | None:
    """The name, trimmed, if it may be hers; else ``None``."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not 0 < len(name) <= NAME_LENGTH or not _GOOD_NAME.fullmatch(name):
        return None
    return name
