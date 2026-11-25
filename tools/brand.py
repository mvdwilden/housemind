"""Make the App's icon and logo from their SVG sources in docs/brand (E-166).

    uv run python tools/brand.py

Writes ha_app_stable/icon.png (1:1, what HA shows in the App store) and logo.png (about 5:2,
on the App's page), plus the favicon for the App and the website. The name is set in Sora,
the font the App ships, so the logo looks the same everywhere. Needs Playwright's Chromium
(``uv run playwright install chromium``).
"""

from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "brand"
SORA = ROOT / "frontend/node_modules/@fontsource/sora/files/sora-latin-600-normal.woff2"
RENDERS = (  # source, target, width, height
    ("mark.svg", ROOT / "ha_app_stable" / "icon.png", 256, 256),
    ("logo.svg", ROOT / "ha_app_stable" / "logo.png", 500, 200),
    ("mark.svg", BRAND / "mark-512.png", 512, 512),
)


async def render() -> None:
    font = base64.b64encode(SORA.read_bytes()).decode()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for source, target, width, height in RENDERS:
            page = await browser.new_page(viewport={"width": width, "height": height})
            svg = (BRAND / source).read_text()
            await page.set_content(
                "<style>@font-face{font-family:Sora;font-weight:600;"
                f"src:url(data:font/woff2;base64,{font}) format('woff2')}}"
                "html,body{margin:0;background:transparent}svg{display:block;"
                f"width:{width}px;height:{height}px}}</style>{svg}"
            )
            await page.evaluate("document.fonts.ready")
            await page.screenshot(path=str(target), omit_background=True)
            await page.close()
            print(f"{target.relative_to(ROOT)} ({width}×{height})")
        await browser.close()
    for folder in (ROOT / "frontend" / "public", ROOT / "docs"):
        folder.mkdir(exist_ok=True)
        shutil.copy2(BRAND / "mark.svg", folder / "favicon.svg")


if __name__ == "__main__":
    asyncio.run(render())
