"""The website (E-146, E-149): four languages, the same words as the app, built from one source."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_the_site_is_built_from_its_one_source_in_every_language(tmp_path: Path) -> None:
    content = yaml.safe_load((ROOT / "docs" / "site-src" / "content.yaml").read_text())
    langs = content["languages"]
    assert set(langs) == {"en", "nl", "de", "fr"}
    keys = set(langs["en"])
    assert all(set(texts) == keys for texts in langs.values())
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for code, texts in langs.items():
        page = module.page(code, texts, content, list(langs))
        built = ROOT / "docs" / ("index.html" if code == "en" else f"{code}/index.html")
        assert built.read_text() == page, f"run tools/build_site.py ({code} is out of date)"
        app = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{code}.yaml").read_text())
        assert app["levels"]["carefree"] in page  # the same words as in the app


def test_community_templates_exist() -> None:
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    assert {p.name for p in templates.glob("*.yml")} >= {
        "bug.yml",
        "idea.yml",
        "language.yml",
        "device.yml",
        "config.yml",
    }
    bug = yaml.safe_load((templates / "bug.yml").read_text())
    assert any(item.get("id") == "diagnostics" for item in bug["body"])
