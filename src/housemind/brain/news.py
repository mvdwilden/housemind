"""What is new, per level (E-150, QA 2 M-36): nothing on Carefree – it just works –, one
sentence on Informed, and a short list on Involved and Expert, in the person's own language.
Once per version, until the person has read it. The full change list, in English, stays in
Home Assistant's app store (E-44): for one version it can be hundreds of lines.

The words live in ``knowledge/news/<language>.yaml``, per version: ``one`` and ``list``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load(knowledge: list[Path]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per language, per version: what is new."""
    news: dict[str, dict[str, dict[str, Any]]] = {}
    for folder in knowledge:
        for path in sorted((folder / "news").glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            news.setdefault(path.stem, {}).update({str(k): v for k, v in raw.items()})
    return news


def view(
    news: dict[str, dict[str, dict[str, Any]]],
    lang: str,
    level: int,
    version: str,
) -> dict[str, Any] | None:
    """What one person sees, by level; None when there is nothing for them."""
    if level <= 1:
        return None
    entry = news.get(lang, {}).get(version) or news.get("en", {}).get(version)
    if not entry:
        return None
    out: dict[str, Any] = {"version": version}
    if level == 2:
        out["one"] = str(entry.get("one", ""))
    else:
        out["list"] = [str(item) for item in entry.get("list", [])]
    return out
