"""One manual, the formatted one, also in the app (E-324, B-192): the app shows the same page
as online, without internet, as its own page in Help. From the user's side: the page that
comes back, what it may run, and what it says. Each test fails without it; what it checks is
in its docstring."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.runtime import HouseMind
from housemind.server.web import create_app
from tests.test_vesta import _house

ROOT = Path(__file__).resolve().parents[1]


def _hashes(document: str) -> set[str]:
    return {
        f"'sha256-{base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()}'"
        for s in re.findall(r"<script>(.*?)</script>", document, re.S)
    }


async def test_the_app_shows_the_formatted_manual_and_only_its_own_scripts_run(
    tmp_path: Path,
) -> None:
    """The manual in the app was a plain text inside Help; the formatted page has its own
    menu and two small scripts, which the CSP of the app stopped. Now it is its own page: the
    same page as online, with Escape added, and a CSP that lets exactly those scripts run."""
    hm, _ha, _clock = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        response = await client.get("/manual/nl")
        assert response.status == 200
        document = await response.text()
        assert document.lstrip().lower().startswith("<!doctype html>")
        assert 'class="inhoud"' in document  # its own menu, as online
        assert "housemind:manual-close" in document  # Escape closes the window in the app
        csp = response.headers["Content-Security-Policy"]
        allowed = set(re.findall(r"'sha256-[^']+'", csp))
        assert allowed == _hashes(document) and len(allowed) == 3, csp
        assert "frame-ancestors 'self'" in csp and "default-src 'self'" in csp
        assert response.headers["Cache-Control"] == "no-cache"
        again = await client.get("/manual/nl", headers={"If-None-Match": response.headers["ETag"]})
        assert again.status == 304
        # Help and the rest of the app keep the strict CSP.
        assert "sha256" not in (await client.get("/api/health")).headers["Content-Security-Policy"]
    await hm.stop()


async def test_the_manual_needs_no_internet(tmp_path: Path) -> None:
    """Without internet the manual must work (E-238, E-15): no address outside the house, no
    font from elsewhere. The app's own fonts come along, so it looks as online."""
    hm, _ha, _clock = await _house(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc.css").write_text(
        "body{}@font-face{font-family:Sora;font-weight:600;src:url(./sora-latin-600.woff2)}"
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        document = await (await client.get("/manual/nl")).text()
    assert not re.search(r"https?://", document)
    assert "<link" not in document and "<script src" not in document
    assert "src:url(../assets/sora-latin-600.woff2)" in document
    await hm.stop()


async def test_the_manual_follows_the_app_her_name_theme_and_large_text(tmp_path: Path) -> None:
    """The manual follows the app (E-324): the name the administrator gave Vesta (E-239) – in
    the text and the labels, never in an id or a script –, the theme and large text (E-46)."""
    hm, _ha, _clock = await _house(tmp_path)
    hm._vesta_name = "Juno"
    source = (ROOT / "knowledge" / "manual" / "nl.html").read_text(encoding="utf-8")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        response = await client.get("/manual/nl?thema=donker&tekst=groot")
        document = await response.text()
    text = re.sub(r"<script>.*?</script>|<style>.*?</style>|<[^>]+>", " ", document, flags=re.S)
    assert "Juno" in text and not re.search(r"\bVesta\b", text)
    assert not re.search(r'aria-label="[^"]*\bVesta\b', document)
    assert re.findall(r'\sid="([^"]+)"', document) == re.findall(r'\sid="([^"]+)"', source)
    assert _hashes(source) <= _hashes(document)  # her scripts untouched, so they still run
    assert document.startswith('<!doctype html>\n<html data-theme="dark"')
    assert "<style>html{zoom:1.125}</style>" in document
    plain = await _get(hm, tmp_path, "/manual/nl")
    assert "html{zoom:" not in plain and 'data-theme="' not in plain.split(">", 2)[1]
    await hm.stop()


async def test_a_language_without_the_formatted_manual_gets_the_plain_one_in_a_page(
    tmp_path: Path,
) -> None:
    """Dutch, English, German and French are the formatted page; a language that has only the
    plain manual (an <article> with a heading per part) gets it in the same window, as a page
    with its contents on top (E-324). A language without any manual has none, and the manual is only
    reachable through Home Assistant like the rest."""
    hm, _ha, _clock = await _house(tmp_path)
    for lang in ("nl", "en", "de", "fr"):
        assert (
            (ROOT / "knowledge" / "manual" / f"{lang}.html")
            .read_text()
            .startswith("<!doctype html>")
        ), lang
    hm.library.manuals["es"] = (
        '<article lang="es"><h2 id="wat">Qu\'est-ce que HouseMind ?</h2><p>Une maison.</p>'
        '<h3 id="wat-plus">Plus</h3><p>Encore.</p></article>'
    )
    document = await _get(hm, tmp_path, "/manual/es?thema=licht")
    assert document.startswith("<!doctype html>") and 'data-theme="light"' in document
    contents = re.search(r"<nav[^>]*>(.*?)</nav>", document, re.S)
    assert contents is not None
    assert re.findall(r'href="#([^"]+)"', contents.group(1)) == ["wat", "wat-plus"]
    assert "Qu&#x27;est-ce que HouseMind ?" in contents.group(1)
    assert "housemind:manual-close" in document  # Escape here too
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        assert (await client.get("/manual/it")).status == 404
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=True))) as client:
        assert (await client.get("/manual/nl")).status == 403
    await hm.stop()


async def _get(hm: HouseMind, folder: Path, path: str) -> str:
    async with TestClient(TestServer(create_app(hm, folder, ingress_only=False))) as client:
        return await (await client.get(path)).text()
