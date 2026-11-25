"""The manual in the app as its own page (E-324, B-192): the formatted manual, the same page as
online, without internet. The app shows it in an iframe in Help; the server only adds what this
house and this screen need – Vesta's name, the theme, large text, the app's own fonts, Escape –
and a CSP that lets the page's own scripts run and nothing else. A language that has only the
plain manual gets a simple page around it, with its contents on top."""

from __future__ import annotations

import base64
import hashlib
import html
import re
from pathlib import Path

from housemind.brain.library import formatted, manual_for, manual_parts

LARGE = 1.125  # the app's --text-scale for large text (E-46); the manual sizes in px, so zoom
# Escape in the manual closes the window in the app: the key stays in the iframe otherwise.
ESCAPE = (
    'addEventListener("keydown",function(e){if(e.key==="Escape"&&parent!==window)'
    'parent.postMessage("housemind:manual-close",location.origin)});'
)
_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
_FONT = re.compile(r"@font-face\{[^}]*\}")


def page(source: str, name: str, theme: str | None, large: bool, fonts: str, title: str) -> str:
    """The page for one screen: theme is "light" or "dark" (None: the device decides)."""
    document = manual_for(source, name)
    if not formatted(document):
        document = _wrap(document, title)
    if theme in ("light", "dark"):
        document = re.sub(r"<html\b", f'<html data-theme="{theme}"', document, count=1)
    extra = fonts + (f"<style>html{{zoom:{LARGE}}}</style>" if large else "")
    document = document.replace("</head>", f"{extra}</head>", 1)
    end = document.rfind("</body>")
    return f"{document[:end]}<script>{ESCAPE}</script>{document[end:]}"


def csp(source: str, base: str) -> str:
    """The app's CSP, with the hash of each script the manual itself ships and of the Escape
    script the server adds: those run, no other – also not one that a name or anything else
    put into the page on the way (QA 3, gat 2)."""
    scripts = dict.fromkeys([*_SCRIPT.findall(source), ESCAPE])  # each once, in order
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()}'"
        for script in scripts
    )
    return base.replace("script-src 'self'", f"script-src 'self' {hashes}", 1)


def fonts(assets: Path) -> str:
    """The app's own fonts for the manual (Sora, Figtree, JetBrains Mono, as online): the
    @font-face rules of the built app, pointing at its files. Without a built app, none."""
    rules: list[str] = []
    for sheet in sorted(assets.glob("index-*.css")) if assets.is_dir() else []:
        rules += _FONT.findall(sheet.read_text(encoding="utf-8"))
    if not rules:
        return ""
    return "<style>" + "".join(rules).replace("url(./", "url(../assets/") + "</style>"


def _wrap(article: str, title: str) -> str:
    """The plain manual as a page: its contents on top, the colours of the app."""
    contents = "".join(
        f'<li class="{"sub" if level == "3" else "top"}">'
        f'<a href="#{part_id}">{html.escape(text)}</a></li>'
        for level, part_id, text in _headings(article)
    )
    return (
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{PLAIN_STYLE}</style>\n</head>\n<body>\n"
        f'<nav aria-label="{html.escape(title)}"><ol>{contents}</ol></nav>\n'
        f"<main>{article}</main>\n</body>\n</html>\n"
    )


def _headings(article: str) -> list[tuple[str, str, str]]:
    titles = {part_id: title for part_id, title, _ in manual_parts(article)}
    return [
        (level, part_id, titles.get(part_id, part_id))
        for level, part_id in re.findall(r'<h([23])[^>]*\bid="([^"]+)"', article)
    ]


PLAIN_STYLE = """
:root { --surface: #10162a; --raised: #172038; --line: #24304f; --text: #e7ebf6;
  --muted: #9aa6c4; --accent: #5ed4c4; color-scheme: dark; }
@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) { --surface: #ffffff;
  --raised: #eef1f7; --line: #d6dcea; --text: #121829; --muted: #4e5a77; --accent: #0b7a70;
  color-scheme: light; } }
:root[data-theme="light"] { --surface: #ffffff; --raised: #eef1f7; --line: #d6dcea;
  --text: #121829; --muted: #4e5a77; --accent: #0b7a70; color-scheme: light; }
body { margin: 0 auto; max-width: 760px; padding: 16px 20px 48px; background: var(--surface);
  color: var(--text); font: 16px/1.6 "Figtree", system-ui, sans-serif; overflow-wrap: anywhere; }
nav { border-bottom: 1px solid var(--line); padding-bottom: 12px; }
nav ol { list-style: none; margin: 0; padding: 0; }
nav li.sub { padding-left: 16px; font-size: 15px; }
a { color: var(--accent); }
h2 { font-family: "Sora", "Figtree", system-ui, sans-serif; font-size: 22px; margin: 28px 0 6px; }
h3 { font-size: 18px; margin: 18px 0 4px; }
h2, h3 { scroll-margin-top: 12px; }
table, pre { display: block; max-width: 100%; overflow-x: auto; }
img, svg { max-width: 100%; height: auto; }
"""
