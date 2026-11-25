"""Build a local App folder to copy into Home Assistant's ``/addons`` share (Samba).

Until the first Beta has prebuilt images, this lets HouseMind be installed as a *local* App:
HA builds it on the device from this folder. The frontend must be built first
(``npm run build`` in ``frontend/``).

Usage: ``python tools/make_local_app.py [output_dir]`` (default ``dist/housemind``). For a beta
tester's package, ``tools/make_beta_package.py`` builds it as "HouseMind (beta)" (E-528).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Home Assistant builds a local App on the device, from a base image with Python for both
# architectures. Fixed here to one exact image (QA 3, gat 7): HA's own build.yaml takes no
# digest (it then falls back to a base without Python) and is deprecated, so the Dockerfile
# names the image itself and leaves the BUILD_FROM HA passes unused.
BASE_IMAGE = (
    "ghcr.io/home-assistant/base-python:3.13-alpine3.22"
    "@sha256:c07d5b77c095f8dac20143fa560f181892bbcad3c4d7611e285cfec9b34ed08e"
)
DOCKERFILE = f"""FROM {BASE_IMAGE}
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_LICENSES.md requirements.lock ./
COPY src/ ./src/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \\
    && pip install --no-cache-dir --no-deps . && rm -rf /root/.cache
COPY knowledge/ /app/knowledge/
COPY CHANGELOG.md /app/CHANGELOG.md
COPY integration/ /app/integration/
COPY frontend/ /app/frontend/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "housemind"]
"""


# github.com/mvdwilden/housemind is public (phase F of publishing): only then the local App
# links to it; before, a link would go nowhere.
PUBLIC = False


def build(output: Path, beta: bool = False) -> Path:
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("Build the frontend first: cd frontend && npm run build")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text(encoding="utf-8"))
    config.pop("image", None)  # local build on the device
    config["name"] = "HouseMind (beta)" if beta else "HouseMind (local)"
    config["slug"] = "housemind_local"  # the same App, so a newer folder updates it
    if not PUBLIC:  # no project page yet: no link that goes nowhere
        config.pop("url", None)
    config.pop("stage", None)  # one's own local App is no experiment for its own house
    (output / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (output / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    for name in (
        "pyproject.toml",
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_LICENSES.md",
        "requirements.lock",
    ):
        shutil.copy2(ROOT / name, output / name)
    # The App's own info page in Home Assistant: for the people who use it, without the parts
    # for developers (the administrator's wish, 22-10-2026); those stay in the README on GitHub.
    for name in ("README.md", "DOCS.md", "CHANGELOG.md", "icon.png", "logo.png", "apparmor.txt"):
        shutil.copy2(ROOT / "ha_app_stable" / name, output / name)
    shutil.copytree(ROOT / "ha_app_stable" / "translations", output / "translations")
    shutil.copytree(
        ROOT / "src", output / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    shutil.copytree(ROOT / "knowledge", output / "knowledge")
    shutil.copytree(
        ROOT / "integration", output / "integration", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(dist, output / "frontend")
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "housemind"
    print(f"Local App folder ready: {build(target)}")
