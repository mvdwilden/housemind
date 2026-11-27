"""The App's changelog in Home Assistant (``ha_app_stable/CHANGELOG.md``), made from What's new.

The administrator's wish (01-11-2026): a changelog that is simple and readable. So per version
it says what the app itself says after an update – the English What's new in
``knowledge/news/en.yaml``: one sentence and a short list – from 0.4.0 on. A version may have a
picture, ``docs/app/images/changelog-<version>.jpg``; it goes into the page itself, as in the
Documentation tab, because Home Assistant cannot load a separate file of a local App.

Usage: ``python tools/changelog.py`` after What's new for a version is written. A version already
in the changelog keeps its date; a new one gets today's. tests/test_changelog.py checks that the
changelog is up to date.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NEWS = ROOT / "knowledge" / "news" / "en.yaml"
TARGET = ROOT / "ha_app_stable" / "CHANGELOG.md"
IMAGES = ROOT / "docs" / "app" / "images"
# What came before went, the administrator's choice: before 0.2.0 on 01-11-2026, before 0.4.0
# (the first version on GitHub) on 27-11-2026. Each new version comes on top.
FIRST = (0, 4, 0)
START = "<!-- support:start – made by tools/build_support.py from docs/support.yaml -->"
END = "<!-- support:end -->"
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) – (.+)$", re.M)


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def make(current: str, today: date | None = None) -> str:
    """The changelog, from What's new; dates and the support block come from ``current``."""
    dates = dict(HEADING.findall(current))
    block = re.search(re.escape(START) + r".*?" + re.escape(END), current, re.S)
    news = yaml.safe_load(NEWS.read_text(encoding="utf-8"))
    versions = sorted((v for v in news if _key(v) >= FIRST), key=_key, reverse=True)
    stamp = (today or date.today()).strftime("%d-%m-%Y")
    parts = ["# Changelog", block.group(0) if block else f"{START}\n{END}"]
    for version in versions:
        entry = news[version]
        lines = [f"## {version} – {dates.get(version, stamp)}", "", str(entry["one"]).strip(), ""]
        lines += [f"- {str(item).strip()}" for item in entry.get("list", [])]
        picture = IMAGES / f"changelog-{version}.jpg"
        if picture.exists():
            data = base64.b64encode(picture.read_bytes()).decode("ascii")
            lines += ["", f"![What's new in {version}](data:image/jpeg;base64,{data})"]
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def main() -> None:
    text = make(TARGET.read_text(encoding="utf-8"))
    TARGET.write_text(text, encoding="utf-8")
    print(f"{TARGET.relative_to(ROOT)}: {len(text.encode('utf-8'))} bytes")


if __name__ == "__main__":
    main()
