"""The support block (E-233) in the App's info page (README) and in the changelog HA shows at an
update: a thank-you, why support helps, a button per way to support from the one list (E-138),
the counts and the question for a star. Only ways with a link show; until the maker fills the
list when publishing, the block stays empty. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_support", ROOT / "tools" / "build_support.py")
assert SPEC and SPEC.loader
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def test_the_readme_and_the_changelog_carry_the_block_from_the_one_list() -> None:
    """Both files hold exactly the block docs/support.yaml makes (run tools/build_support.py)."""
    for path, text in support.build().items():
        assert path.read_text(encoding="utf-8") == text, f"run tools/build_support.py ({path.name})"
        assert support.START in text and support.END in text


def test_only_ways_with_a_link_show_with_the_counts_and_a_star(tmp_path: Path) -> None:
    """Filled in when publishing: a button per way that has a link, the sponsor and star counts,
    the thank-you and the question for a star. Empty: nothing at all."""
    data = yaml.safe_load((ROOT / "docs" / "support.yaml").read_text(encoding="utf-8"))
    assert support.block(data) == f"{support.START}\n{support.END}"
    data["owner"] = "maker"
    data["ways"][0]["link"] = "https://github.com/sponsors/maker"
    data["ways"][1]["link"] = "https://example.org/coffee"
    made = support.block(data)
    assert "https://github.com/sponsors/maker" in made and "https://example.org/coffee" in made
    assert "-Ko--fi-" not in made and "PayPal" not in made  # no link: not shown
    assert "github/stars/maker/housemind" in made and "github/sponsors/maker" in made
    assert data["text"]["thanks"] in made and data["text"]["star"] in made
