"""HouseMind is a generic product: no person's name in what people read or in the code. The
author's name, taken from NOTICE, stood in the change list (which Home Assistant shows in its
app store), in code comments and in tests. It belongs in NOTICE and nowhere else."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ["ha_app_stable", "knowledge", "src", "tests", "integration", "frontend/src", "docs"]
FILES = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml"]
TEXT = {".py", ".md", ".yaml", ".yml", ".html", ".ts", ".svelte", ".json", ".toml", ".txt"}


def _author() -> list[str]:
    """The first name and the rest of the name in NOTICE's copyright line."""
    lines = (ROOT / "NOTICE").read_text(encoding="utf-8").splitlines()
    line = next(x for x in lines if "Copyright" in x)
    name = re.sub(r"^.*Copyright\s+\d{4}\s+", "", line).strip()
    first, _, rest = name.partition(" ")
    return [first, rest] if rest else [first]


def test_the_author_name_is_only_in_notice() -> None:
    """The change list gave examples like a lamp named after the author; code comments and
    tests used the names of the author's household. Now only NOTICE has the name."""
    parts = _author()
    assert parts and all(len(p) > 2 for p in parts)
    found = []
    paths = [ROOT / f for f in FILES] + [p for d in PLACES for p in (ROOT / d).rglob("*")]
    for path in paths:
        if not path.is_file() or path.suffix not in TEXT or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for part in parts:
            if re.search(rf"\b{re.escape(part)}\b", text):
                found.append(f"{path.relative_to(ROOT)}: {part}")
    assert found == []
