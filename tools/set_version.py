"""Set HouseMind's version in every place that holds it, at once (QA 2, L12).

Usage: ``python tools/set_version.py 0.2.0``. ``tests/test_packaging.py`` checks that they agree.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Each place, and the text around the version there.
PLACES = {
    "pyproject.toml": r'(?m)^(version = ")[^"]+(")',
    "src/housemind/__init__.py": r'(__version__ = ")[^"]+(")',
    "ha_app_stable/config.yaml": r'(?m)^(version: ")[^"]+(")',
    "frontend/package.json": r'(?m)^(  "version": ")[^"]+(")',
    "integration/custom_components/housemind/manifest.json": r'(?m)^(  "version": ")[^"]+(")',
}


def versions() -> dict[str, str]:
    found = {}
    for path, pattern in PLACES.items():
        match = re.search(pattern.replace('[^"]+', '([^"]+)'), (ROOT / path).read_text())
        found[path] = match.group(2) if match else ""
    return found


def set_version(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[ab]\d+)?", version):
        raise SystemExit(f"Not a version: {version}")
    for path, pattern in PLACES.items():
        file = ROOT / path
        text, n = re.subn(pattern, rf"\g<1>{version}\g<2>", file.read_text())
        if n != 1:
            raise SystemExit(f"No version found in {path}")
        file.write_text(text)
    lock = ROOT / "frontend" / "package-lock.json"  # npm keeps it there too
    data = json.loads(lock.read_text())
    data["version"] = data["packages"][""]["version"] = version
    lock.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    set_version(sys.argv[1])
    print(versions())
