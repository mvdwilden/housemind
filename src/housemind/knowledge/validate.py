"""Command line check of knowledge files: ``python -m housemind.knowledge.validate <dir>``."""

from __future__ import annotations

import sys
from pathlib import Path

from housemind.i18n.catalog import load_languages
from housemind.knowledge.loader import KnowledgeError, load_catalog


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path("knowledge")
    try:
        catalog, report = load_catalog([root / "catalog"])
        languages = load_languages(root / "languages")
    except KnowledgeError as err:
        print(f"ERROR {err}")
        return 1
    for warning in report.warnings:
        print(f"WARNING {warning}")
    missing = languages.missing_keys()
    for lang, keys in sorted(missing.items()):
        for key in sorted(keys):
            print(f"MISSING {lang}: {key}")
    print(
        f"OK {len(catalog.kinds)} device kinds in {len(report.files)} files; "
        f"languages: {', '.join(sorted(languages.languages))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
