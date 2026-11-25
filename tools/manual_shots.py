"""The pictures in the manual, made again from a demo house in a real browser.

The manual (``knowledge/manual/<lang>.html``) carries its pictures inside it, one per language,
600 px wide: a phone 400 px wide at one and a half times the pixels, in the dark colour scheme.
When a screen changes, its picture should follow. This opens the demo house "extended", set up
and running, for its administrator – on Involved, or Expert where the picture shows Expert –,
takes each named picture in each language, makes it WebP (``cwebp``) and puts it
in the ``<img data-foto="…">`` of that manual, with its new height. The alt text is the manual's
own and stays.

Usage (a built frontend, Playwright's Chromium and cwebp)::

    uv run python tools/manual_shots.py                 # every picture it knows
    uv run python tools/manual_shots.py logboek ruimte  # only these
    uv run python tools/manual_shots.py --lang nl logboek  # one language
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aiohttp import web  # noqa: E402
from playwright.async_api import Page, async_playwright  # noqa: E402

from housemind import __version__  # noqa: E402
from housemind.brain import start_over  # noqa: E402
from housemind.core.clock import SimulatedClock  # noqa: E402
from housemind.demo.houses import load_demo_house  # noqa: E402
from housemind.runtime import HouseMind, Paths  # noqa: E402
from housemind.server.web import create_app  # noqa: E402

DIST = ROOT / "frontend" / "dist"
MANUAL = ROOT / "knowledge" / "manual"
LANGUAGES = (
    tuple(sys.argv[2].split(","))
    if len(sys.argv) > 2 and sys.argv[1] == "--lang"
    else ("nl", "en", "de", "fr")
)
WIDTH = 400  # CSS pixels; the picture is one and a half times that, 600 px
SCALE = 1.5
ADMIN = "demo-user-1"  # the demo house's administrator
AT = datetime(2026, 11, 25, 13, 45, tzinfo=ZoneInfo("Europe/Amsterdam"))
SETTLE = """() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"""


async def _tab(page: Page, tab: str) -> None:
    await page.locator(f'[data-tour="tab-{tab}"]:visible').first.click()
    await page.wait_for_timeout(700)


async def _clip(page: Page, selector: str, height: int, above: int = 12) -> bytes:
    """``height`` CSS pixels from just above the first ``selector``, the whole width."""
    target = page.locator(selector).first
    await target.wait_for(timeout=10000)
    await target.scroll_into_view_if_needed()
    await page.evaluate(SETTLE)
    box = await target.bounding_box()
    assert box is not None, selector
    top = max(0.0, box["y"] - above)
    return await page.screenshot(clip={"x": 0, "y": top, "width": WIDTH, "height": height})


async def logboek(page: Page) -> bytes:
    await _tab(page, "logbook")
    return await _clip(page, "main section, main article", 549)


async def brein_klaar(page: Page) -> bytes:
    await _tab(page, "brain")
    return await _clip(page, '[role="tablist"]', 544)


async def checkup_top(page: Page) -> bytes:
    await _tab(page, "tidy")
    return await _clip(page, "main h2:visible", 549)


async def ruimte(page: Page) -> bytes:
    await _tab(page, "home")
    name = re.compile(rf"^\s*{re.escape(_SAY['room'])}\b")
    await page.locator("button").filter(has_text=name).first.click()
    await page.wait_for_timeout(700)
    return await _clip(page, '[role="dialog"]', 535, above=0)


async def voorkeuren(page: Page) -> bytes:
    await page.locator('[data-tour="prefs"]:visible').first.click()
    await page.wait_for_timeout(500)
    await page.locator('[role="dialog"] button', has_text=_SAY["about_me"]).first.click()
    await page.wait_for_timeout(500)
    return await _clip(page, '[role="dialog"]', 653, above=0)


async def huis_expert(page: Page) -> bytes:
    await _tab(page, "home")
    await page.set_viewport_size({"width": WIDTH, "height": 691})
    await page.evaluate(
        """() => {
          const mind = document.querySelector('[data-tour="mind"]');
          mind.scrollIntoView({block: "start"});
          let box = mind.parentElement;
          while (box && box.scrollHeight <= box.clientHeight) box = box.parentElement;
          (box || document.scrollingElement).scrollTop -= 12;
        }"""
    )
    await page.wait_for_timeout(500)
    return await page.screenshot()


_SAY: dict[str, str] = {}  # the words on the screen in the language of this round
# Each picture and the level it is taken on: Involved, as most people use it, or Expert.
SHOTS: dict[str, tuple[Callable[[Page], Awaitable[bytes]], int]] = {
    "logboek": (logboek, 3),
    "brein-klaar": (brein_klaar, 3),
    "checkup-top": (checkup_top, 4),
    "ruimte": (ruimte, 3),
    "voorkeuren": (voorkeuren, 3),
    "huis-expert": (huis_expert, 4),
}


def _webp(png: bytes) -> tuple[bytes, int, int]:
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "shot.png", Path(tmp) / "shot.webp"
        source.write_bytes(png)
        cwebp = shutil.which("cwebp")
        if cwebp is None:
            raise SystemExit("cwebp is needed (brew install webp, apt install webp)")
        subprocess.run(  # noqa: S603 – our own files, a fixed tool
            [cwebp, "-quiet", "-q", "70", "-m", "6", str(source), "-o", str(target)], check=True
        )
        data = target.read_bytes()
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return data, width, height


def _put(lang: str, name: str, webp: bytes, width: int, height: int) -> None:
    path = MANUAL / f"{lang}.html"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'<img width="\d+" height="\d+" data-foto="'
        + re.escape(name)
        + r'" src="data:image/\w+;base64,[^"]+"'
    )
    new = (
        f'<img width="{width}" height="{height}" data-foto="{name}" '
        f'src="data:image/webp;base64,{base64.b64encode(webp).decode()}"'
    )
    text, count = pattern.subn(lambda _m: new, text)
    if count != 1:
        raise SystemExit(f"{lang}: data-foto={name} found {count} times")
    path.write_text(text, encoding="utf-8")


async def _house(data: Path, lang: str) -> tuple[HouseMind, web.AppRunner, str]:
    """The demo house in this language: its rooms and devices have names in it."""
    clock = SimulatedClock(AT.astimezone(UTC))
    demo = load_demo_house("extended", clock, lang)
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], data), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.set_setting("setup", "done")  # set up and running, as a house that lives with it
    await hm.settled()
    await start_over.maybe_activate(hm)  # "Ik kijk nu mee", as after setting up
    await hm.db.execute("DELETE FROM decisions")  # what it did before it was on
    hm.status.decisions.clear()
    for _ in range(3):
        await clock.advance(30)
        await hm.tick()
    await clock.advance(16 * 60)  # Check-up's first look, a quarter of an hour after the start
    hm._last_review = None
    await hm.tick()
    await hm.settled()
    # One message waits, the one after setting up; Check-up's own (an old backup) are its page's.
    for row in await hm.db.fetchall("SELECT topic FROM notifications WHERE resolved_at IS NULL"):
        if row["topic"] != "activated":
            await hm._resolve(str(row["topic"]))
    await hm.db.set_setting("vesta_window", False)  # her questions wait; the screens stay clear
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return hm, runner, f"http://127.0.0.1:{runner.addresses[0][1]}/"


async def main(names: list[str]) -> None:
    if not (DIST / "index.html").exists():
        raise SystemExit("Build the frontend first: cd frontend && npm run build")
    wanted = names or list(SHOTS)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for lang in LANGUAGES:
            with tempfile.TemporaryDirectory() as data:
                hm, runner, url = await _house(Path(data), lang)
                try:
                    _SAY["room"] = hm.model.areas["kitchen"].name
                    _SAY["about_me"] = hm.languages.text(lang, "ui.prefs.group.about_me")
                    prefs: dict[str, Any] = {
                        "onboarded": True,
                        "tour": [1, 2, 3, 4],
                        "seen_version": __version__,
                        "language": lang,
                    }
                    for name in wanted:
                        shoot, level = SHOTS[name]
                        await hm.db.execute(
                            "INSERT OR REPLACE INTO people(user_id, display_name, level, "
                            "preferences, created) VALUES(?,?,?,?,?)",
                            (ADMIN, "Admin", level, json.dumps(prefs), "2026-10-01"),
                        )
                        await hm._refresh_people()
                        context = await browser.new_context(
                            viewport={"width": WIDTH, "height": 1400},
                            device_scale_factor=SCALE,
                            color_scheme="dark",
                            locale=lang,
                            extra_http_headers={"X-Remote-User-Id": ADMIN},
                        )
                        page = await context.new_page()
                        await page.goto(url)
                        await page.wait_for_selector(".orb", timeout=15000)
                        await page.wait_for_timeout(800)
                        webp, width, height = _webp(await shoot(page))
                        _put(lang, name, webp, width, height)
                        print(f"{lang} {name}: {width}×{height}, {len(webp) // 1024} KB")
                        await context.close()
                finally:
                    await runner.cleanup()
                    await hm.stop()
        await browser.close()


if __name__ == "__main__":
    args = sys.argv[3:] if len(sys.argv) > 2 and sys.argv[1] == "--lang" else sys.argv[1:]
    asyncio.run(main(args))
