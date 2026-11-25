"""Every fixed text key the app asks for exists as a text in every language (found in the real
house: the tour's last button showed "ui.tour.done", because that key was also a tour step
with a title and a text, and YAML kept the last one)."""

from __future__ import annotations

import re
from pathlib import Path

from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
KEY = re.compile(r"""\bt\(\s*["']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["']""")


def test_every_text_key_in_the_app_is_a_text_in_every_language() -> None:
    languages = load_languages([ROOT / "knowledge" / "languages"])
    used: dict[str, str] = {}
    for path in (ROOT / "frontend" / "src").rglob("*"):
        if path.suffix in (".svelte", ".ts") and not path.name.endswith(".test.ts"):
            for key in KEY.findall(path.read_text(encoding="utf-8")):
                used.setdefault(key, path.name)
    assert len(used) > 200  # the scan finds the app's texts
    missing = [
        f"{code}: {key} ({where})"
        for code in ("nl", "en", "de", "fr")
        for key, where in sorted(used.items())
        if key not in languages.languages[code]
        # a counting text has a form for one and for more ("1 ruimte", "3 ruimtes")
        and not {f"{key}.one", f"{key}.other"} <= set(languages.languages[code])
    ]
    assert not missing, "\n".join(missing)
