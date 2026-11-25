"""Build the website (E-146, E-149) from docs/site-src/content.yaml into docs/ for GitHub Pages.

One page per language (English at the root), each with a simple and a technical view. Level
names and hints come from knowledge/languages, so the site says exactly what the app says.

Usage: ``python tools/build_site.py``
"""

from __future__ import annotations

import html
from pathlib import Path
from string import Template
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LEVELS = ("carefree", "informed", "involved", "expert")
SOURCE = DOCS / "site-src"
ORB = (SOURCE / "orb.svg").read_text(encoding="utf-8").strip()
PAGE = Template((SOURCE / "page.html").read_text(encoding="utf-8"))
SCRIPT = f"<script>\n{(SOURCE / 'views.js').read_text(encoding='utf-8').strip()}\n</script>"


def e(text: Any) -> str:
    return html.escape(str(text))


def level_texts(code: str) -> list[tuple[str, str]]:
    raw = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{code}.yaml").read_text())
    levels = raw.get("levels", {})
    return [(str(levels[k]), str(levels.get(f"{k}_hint", ""))) for k in LEVELS]


def statement(code: str) -> tuple[str, str]:
    """The privacy statement (E-165), from the same language file as the App's. Where it speaks
    of her, the site uses her default name: a house may give her another one."""
    raw = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{code}.yaml").read_text())
    voice = yaml.safe_load((ROOT / "knowledge" / "vesta" / f"{code}.yaml").read_text())
    p = {k: str(v).replace("{name}", str(voice["name"])) for k, v in raw["privacy"].items()}
    parts = [f"<p>{e(p['intro'])}</p>"]
    for key in ("stored", "out", "see", "rights"):
        parts.append(f"<h3>{e(p[key + '_title'])}</h3>")
        items = [line[2:] for line in str(p[key]).split("\n") if line.startswith("- ")]
        rest = [line for line in str(p[key]).split("\n") if line and not line.startswith("- ")]
        parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>")
        parts += [f"<p>{e(x)}</p>" for x in rest]
    parts.append(f'<p class="small">{e(p["contact"])}</p>')
    return str(p["title"]), "".join(parts)


def page(code: str, t: dict[str, Any], site: dict[str, Any], codes: list[str]) -> str:
    prefix = "" if code == "en" else "../"
    langs = " ".join(
        f'<a href="{prefix}{"" if c == "en" else c + "/"}index.html"'
        f'{" aria-current=page" if c == code else ""} lang="{c}">'
        f"{e(site['languages'][c]['name'])}</a>"
        for c in codes
    )
    levels = "".join(f"<dt>{e(n)}</dt><dd>{e(h)}</dd>" for n, h in level_texts(code))
    what = "".join(f"<li>{e(x)}</li>" for x in t["what"])
    steps = "".join(f"<li>{e(x)}</li>" for x in t["steps"])
    tech = "".join(f"<dt>{e(a)}</dt><dd>{e(b)}</dd>" for a, b in t["tech"])
    return PAGE.substitute(
        lang=code,
        title=e(t["title"]),
        intro=e(t["intro"]),
        prefix=prefix,
        langs=langs,
        orb=ORB,
        tagline=e(t["tagline"]),
        add=e(site["add_repository"]),
        install=e(t["install_button"]),
        simple=e(t["simple"]),
        technical=e(t["technical"]),
        what_title=e(t["what_title"]),
        what=what,
        levels_title=e(t["levels_title"]),
        levels=levels,
        privacy_title=e(t["privacy_title"]),
        privacy=e(t["privacy"]),
        statement_title=e(statement(code)[0]),
        statement=statement(code)[1],
        steps_title=e(t["steps_title"]),
        steps=steps,
        tech=tech,
        repo=e(site["repository"]),
        disclaimer=e(t["disclaimer"]),
        support=e(t["support"]),
        script=SCRIPT,
    )


def build() -> list[Path]:
    site = yaml.safe_load((DOCS / "site-src" / "content.yaml").read_text(encoding="utf-8"))
    codes = list(site["languages"])
    written = [DOCS / "style.css"]
    (DOCS / "style.css").write_text(
        (SOURCE / "style.css").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for code, texts in site["languages"].items():
        target = DOCS / "index.html" if code == "en" else DOCS / code / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page(code, texts, site, codes), encoding="utf-8")
        written.append(target)
    return written


if __name__ == "__main__":
    for path in build():
        print(f"wrote {path.relative_to(ROOT)}")
