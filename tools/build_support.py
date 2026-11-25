"""The support block in the README and at the top of the changelog (E-233), from one data file:
docs/support.yaml (E-138). Only ways with a link show; with no link and no owner the block stays
empty. Run after changing the file; tests/test_support.py checks it is up to date."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "support.yaml"
PLACES = (
    ROOT / "README.md",
    ROOT / "docs" / "app" / "README.md",  # the App's info page, as written (tools/app_docs.py)
    ROOT / "ha_app_stable" / "README.md",  # the App's info page in Home Assistant
    ROOT / "ha_app_stable" / "CHANGELOG.md",
)
START = "<!-- support:start – made by tools/build_support.py from docs/support.yaml -->"
END = "<!-- support:end -->"
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def _badge(name: str) -> str:
    """A button picture with the name of the way to support (dashes doubled, as badges want)."""
    return f"https://img.shields.io/badge/-{quote(name.replace('-', '--'))}-informational"


def block(data: dict[str, Any]) -> str:
    """The block for this data: a thank-you, why it helps, a button per way, the counts and the
    question for a star – or nothing while there is no way to support yet."""
    ways = [w for w in data.get("ways") or [] if str(w.get("link") or "").strip()]
    owner = str(data.get("owner") or "").strip()
    if not ways and not owner:
        return f"{START}\n{END}"
    text = data.get("text") or {}
    lines = [START, f"### {text['title']}", "", f"{text['thanks']} {text['why']}", ""]
    if ways:
        buttons = [f"[![{w['name']}]({_badge(str(w['name']))})]({w['link']})" for w in ways]
        lines += [" ".join(buttons), ""]
    if owner:
        lines += [
            f"![Sponsors](https://img.shields.io/github/sponsors/{owner}) "
            f"![Stars](https://img.shields.io/github/stars/{owner}/housemind)",
            "",
            f"{text['star']} https://github.com/{owner}/housemind",
            "",
        ]
    lines.append(END)
    return "\n".join(lines)


def place(document: str, made: str, after: str) -> str:
    """Replace the block, or put it right after the line ``after`` the first time."""
    if BLOCK.search(document):
        return BLOCK.sub(lambda _: made, document, count=1)
    head, sep, rest = document.partition(after + "\n")
    return f"{head}{sep}\n{made}\n{rest}" if sep else f"{made}\n\n{document}"


def build(data_file: Path = DATA) -> dict[Path, str]:
    data = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    made = block(data)
    readme, source, info, changelog = PLACES
    return {
        readme: place(
            readme.read_text(encoding="utf-8"),
            made,
            "> Status: **in development (R4 – Broad)**. Try it with Demo mode first.",
        ),
        source: place(source.read_text(encoding="utf-8"), made, "# HouseMind"),
        info: place(info.read_text(encoding="utf-8"), made, "# HouseMind"),
        changelog: place(changelog.read_text(encoding="utf-8"), made, "# Changelog"),
    }


def main() -> int:
    for path, text in build().items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
