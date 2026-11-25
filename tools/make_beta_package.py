"""Make the package for a beta tester (E-528): one zip with the local App folder and an install
guide in four languages, to send to someone who wants to try HouseMind before it is published.

    python tools/make_beta_package.py [output_dir]   # default dist/

The frontend must be built first (``cd frontend && npm run build``). The App is called
"HouseMind (beta)" with the slug of the local App, so a newer package updates it in place. The
zip holds only what the App needs: no tests, no data and nothing of a real house.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "beta" / "install.html"
GUIDE_NAME = "Install HouseMind.html"
SKIP = {".DS_Store", "__pycache__"}


def _local_app() -> object:
    spec = importlib.util.spec_from_file_location(
        "make_local_app", ROOT / "tools" / "make_local_app.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guide(version: str, day: date) -> str:
    """The install guide with this version and date."""
    return (
        GUIDE.read_text(encoding="utf-8")
        .replace("{version}", version)
        .replace("{date}", day.isoformat())
    )


def build(output: Path, day: date | None = None) -> Path:
    """Write ``HouseMind-<version>-beta.zip`` into ``output`` and return its path."""
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text(encoding="utf-8"))
    version = str(config["version"])
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"HouseMind-{version}-beta.zip"
    with tempfile.TemporaryDirectory() as tmp:
        folder = _local_app().build(Path(tmp) / "housemind", beta=True)  # type: ignore[attr-defined]
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not SKIP & set(path.parts) and path.suffix != ".pyc":
                    archive.write(path, Path("housemind") / path.relative_to(folder))
            archive.writestr(GUIDE_NAME, guide(version, day or date.today()))
    return target


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"Beta package ready: {out} ({out.stat().st_size // 1024} KB, sha256 {digest[:16]}…)")
