"""The Info and Documentation tabs of the App in Home Assistant (``ha_app_stable/README.md``
and ``ha_app_stable/DOCS.md``).

The sources are ``docs/app/README.md`` and ``docs/app/DOCS.md`` with ordinary image links
(``images/home.jpg``). Home Assistant cannot load a picture from a separate file of a local App,
so this tool writes the pictures into the pages themselves. The pictures come from the demo
house, in English, on a phone (B-39: pictures are made from the demo house, so they always match
the app). The privacy statement in the Documentation is the App's own, from
``knowledge/languages/en.yaml``, so the two never say something different (E-527).

Usage:
    python tools/app_docs.py --shots   # new pictures from the demo house, then the page
    python tools/app_docs.py           # only the page, from the pictures already there

``--shots`` needs the frontend built (``cd frontend && npm run build``) and Playwright.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "app" / "DOCS.md"
IMAGES = ROOT / "docs" / "app" / "images"
TARGET = ROOT / "ha_app_stable" / "DOCS.md"
# The Info tab: what HouseMind does for you, for someone who has not installed it yet (E-527).
INFO_SOURCE = ROOT / "docs" / "app" / "README.md"
INFO_TARGET = ROOT / "ha_app_stable" / "README.md"
PAGES = ((SOURCE, TARGET), (INFO_SOURCE, INFO_TARGET))
PRIVACY = "<!-- The privacy statement: tools/app_docs.py puts the App's own here. -->"
DIST = ROOT / "frontend" / "dist"
TZ = ZoneInfo("Europe/Amsterdam")
PHONE_WIDTH, PHONE_HEIGHT = 390, 844
QUALITY = 62  # small enough to live inside the page
# Introduced, toured, and What's new of this version read: the pictures show the screens.
SEEN = {"onboarded": True, "tour": [1, 2, 3, 4]}
IMAGE = re.compile(r"!\[([^\]]*)\]\((images/[^)]+)\)")


def privacy() -> str:
    """The App's privacy statement in English, as the Documentation shows it (E-165, E-527).
    Where it speaks of her, it uses her default name: a house may give her another one."""
    raw = yaml.safe_load((ROOT / "knowledge" / "languages" / "en.yaml").read_text("utf-8"))
    voice = yaml.safe_load((ROOT / "knowledge" / "vesta" / "en.yaml").read_text("utf-8"))
    p = {k: str(v).replace("{name}", str(voice["name"])) for k, v in raw["privacy"].items()}
    parts = [p["intro"]]
    for key in ("stored", "out", "see", "rights"):
        parts.append(f"**{p[key + '_title']}**")
        lines = str(p[key]).strip().split("\n")  # the list, then a line after it on its own
        parts.append("\n".join(x for x in lines if x.startswith("- ")))
        parts += [x for x in lines if x and not x.startswith("- ")]
    parts.append(p["contact"])
    return "\n\n".join(parts)


def build_page(source: Path = SOURCE, target: Path = TARGET) -> int:
    """Write one tab with the pictures inside. Returns its size in bytes."""
    text = source.read_text(encoding="utf-8").replace(PRIVACY, privacy())

    def inline(match: re.Match[str]) -> str:
        path = source.parent / match.group(2)
        kind = "jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else path.suffix[1:].lower()
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"![{match.group(1)}](data:image/{kind};base64,{data})"

    page = (
        f"<!-- Made by tools/app_docs.py from docs/app/{source.name}; edit that file instead. -->\n"
        + IMAGE.sub(inline, text)
    )
    target.write_text(page, encoding="utf-8")
    return len(page.encode("utf-8"))


def _seen() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from housemind import __version__

    return json.dumps({**SEEN, "seen_version": __version__})


async def _house() -> tuple[Any, Any, Any]:
    """The family demo house after three days in the time machine, in English."""
    sys.path.insert(0, str(ROOT / "src"))
    from housemind.brain.proposals import Answer
    from housemind.core.clock import SimulatedClock
    from housemind.demo.houses import load_demo_house
    from housemind.demo.timemachine import standard_routine
    from housemind.runtime import HouseMind, Paths

    HouseMind.FRESH_INACTIVE = False  # an installed house, not the start screen
    monday = datetime(2026, 10, 19, 6, 0, tzinfo=TZ).astimezone(UTC)
    clock = SimulatedClock(monday)
    house = load_demo_house("family_home", clock, "en")
    data = ROOT / "dist" / "app-docs-house"
    if data.exists():
        for path in sorted(data.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], data), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    for user_id, level in (("demo-user-1", 3), ("user-1", 1), ("user-2", 2), ("user-4", 4)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, preferences, created) "
            "VALUES(?,?,?,?,?)",
            (user_id, "Resident", level, _seen(), monday.isoformat()),
        )
    await hm._refresh_people()
    routine = standard_routine(house, TZ)
    rng = random.Random(3)  # noqa: S311 - a simulation, not security
    for day in range(3):
        date = (monday.astimezone(TZ) + timedelta(days=day)).date()
        steps = sorted(routine(date, rng))
        end = datetime.combine(date + timedelta(days=1), datetime.min.time(), TZ).astimezone(UTC)
        if day == 2:  # the pictures: Wednesday early in the evening
            end = datetime.combine(date, datetime.min.time(), TZ).replace(hour=19, minute=10)
            end = end.astimezone(UTC)
        while clock.now() < end:
            target = clock.now() + timedelta(minutes=5)
            while steps and steps[0].at <= target:
                step = steps.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                if step.entity_id in house.ha.states:
                    await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            if target > clock.now():
                await clock.advance((target - clock.now()).total_seconds())
            await hm.tick()
            for proposal in await hm.open_proposals():
                await hm.answer(proposal.id, Answer.DO, "demo-user-1")
    return hm, clock, house


async def _extended() -> Any:
    """The extended demo house, with a group of lamps, for the devices picture."""
    from housemind.core.clock import SimulatedClock
    from housemind.demo.houses import load_demo_house
    from housemind.runtime import HouseMind, Paths

    clock = SimulatedClock(datetime(2026, 10, 21, 19, 0, tzinfo=TZ).astimezone(UTC))
    house = load_demo_house("extended", clock, "en")
    data = ROOT / "dist" / "app-docs-extended"
    if data.exists():
        for path in sorted(data.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], data), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        ("user-4", "Resident", 4, _seen(), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm


# Two drawings in the app's own style and letters, drawn in the app page itself so the fonts
# and colours are the app's. The numbers are the app's own (learning.py); the curve is an example.
FIGURE_STYLE = """
body { margin: 0; background: var(--ink); }
#fig { width: 358px; padding: 16px; display: flex; flex-direction: column; gap: 10px;
  font-family: var(--font-body); color: var(--text); background: var(--ink); }
#fig h2 { font: 600 17px/1.3 var(--font-display); margin: 0; }
#fig p { margin: 0; font-size: 14px; line-height: 1.45; color: var(--muted); }
.step { border: 1px solid var(--line); border-radius: 16px; background: var(--surface);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.pill { align-self: flex-start; border-radius: 999px; padding: 3px 12px; font-weight: 600;
  font-size: 13px; color: #0a0e19; }
.obs .pill { background: var(--step-observe); } .pro .pill { background: var(--step-propose); }
.aut .pill { background: var(--step-autonomous); }
.go { display: flex; align-items: center; gap: 10px; padding-left: 18px; }
.go svg { flex: none; width: 14px; height: 34px; }
.go span, .back span { font-size: 13px; line-height: 1.4; color: var(--text); }
.back { display: flex; gap: 10px; align-items: center; border: 1px dashed var(--important);
  border-radius: 12px; padding: 8px 12px; }
.chart { border: 1px solid var(--line); border-radius: 16px; background: var(--surface);
  padding: 12px; }
.chart svg { width: 100%; height: auto; display: block; }
.chart text { font-family: var(--font-body); font-size: 11px; fill: var(--muted); }
.chart .strong { fill: var(--text); font-weight: 600; }
.legend { margin-top: 8px !important; font-size: 12.5px !important; line-height: 1.7 !important; }
.dash { display: inline-block; width: 22px; margin-right: 8px; vertical-align: middle;
  border-top: 2px dashed; }
.dash.pro { color: var(--step-propose); } .dash.aut { color: var(--step-autonomous); }
"""
ARROW = (
    '<svg viewBox="0 0 14 34" aria-hidden="true"><path d="M7 2v28M2 24l5 6 5-6" fill="none" '
    'stroke="var(--accent)" stroke-width="2" stroke-linecap="round"/></svg>'
)


HERO_STYLE = """
body { margin: 0; background: var(--ink); }
#hero { width: 822px; padding: 22px 20px; display: flex; gap: 16px; background: var(--ink); }
#hero img { width: 250px; height: auto; border-radius: 22px; border: 1px solid var(--line);
  box-shadow: 0 12px 32px rgb(0 0 0 / 0.35); }
"""
HERO = ("home", "brain", "vesta")  # a calm Home, Ready for autonomy?, and Vesta


def _hero() -> str:
    """The Info tab's first picture: three of the screens just made, side by side."""
    images = "".join(
        f'<img alt="" src="data:image/jpeg;base64,'
        f'{base64.b64encode((IMAGES / f"{name}.jpg").read_bytes()).decode("ascii")}">'
        for name in HERO
    )
    return f'<div id="hero">{images}</div>'


def _numbers() -> Any:
    sys.path.insert(0, str(ROOT / "src"))
    from housemind.brain.learning import DEFAULT_THRESHOLDS, SETBACKS_FOR_STEP

    words = {2: "two", 3: "three"}
    return DEFAULT_THRESHOLDS, words.get(SETBACKS_FOR_STEP, str(SETBACKS_FOR_STEP))


def _steps() -> str:
    limits, setbacks = _numbers()
    propose = (
        f"Seen {limits.propose_situations:g} times, right {limits.propose_score * 100:g} times"
        f" out of 100, and at least {limits.propose_days:g} days"
    )
    autonomous = (
        f"Proven like that: one question, and one yes is enough. Or {limits.autonomous_answers:g}"
        f" answers, yes {limits.autonomous_score * 100:g} times out of 100, and at least"
        f" {limits.autonomous_days:g} days"
    )
    return f"""<div id="fig">
<h2>Per skill, per room: three steps</h2>
<div class="step obs"><span class="pill">Observe</span>
<p>HouseMind only watches and predicts what you would do. Nothing happens yet.</p></div>
<div class="go">{ARROW}<span>{propose}</span></div>
<div class="step pro"><span class="pill">Propose</span>
<p>It asks first: “Shall I turn on the light in the hall?” –
Do it, Not now, Myself from now on or Never like that.</p></div>
<div class="go">{ARROW}<span>{autonomous}</span></div>
<div class="step aut"><span class="pill">Autonomous</span>
<p>It does it itself, and tells you why. What you change yourself always wins.</p></div>
<div class="back"><span>↺ Put back {setbacks} times in a week? One step back,
and it asks first again.</span></div>
</div>"""


def _curve(points: Sequence[tuple[float, float]], x0: float, y0: float, w: float, h: float) -> str:
    """A polyline for (day 0–28, percent 50–100) in a box."""
    return " ".join(
        f"{x0 + d / 28 * w:.1f},{y0 + h - (pct - 50) / 50 * h:.1f}" for d, pct in points
    )


WEEKS = {0: "now", 6: "6 weeks"}  # the ends of the bars


def _learning() -> str:
    limits, _ = _numbers()
    x0, y0, w, h = 34.0, 10.0, 290.0, 120.0
    sure = [(0, 55), (2, 63), (4, 72), (6, 79), (8, 84), (10, 87), (14, 90), (21, 92), (28, 93)]
    propose, autonomous = limits.propose_score * 100, limits.autonomous_score * 100
    y_pro = y0 + h - (propose - 50) / 50 * h
    y_aut = y0 + h - (autonomous - 50) / 50 * h
    week = x0 + limits.propose_days / 28 * w
    ticks = "".join(
        f'<text x="{x0 + d / 28 * w:.0f}" y="{y0 + h + 16:.0f}" text-anchor="middle">{label}</text>'
        for d, label in ((0, "start"), (7, "1 week"), (14, "2 weeks"), (21, "3 weeks"))
    )
    ticks += f'<text x="{x0 + w:.0f}" y="{y0 + h + 16:.0f}" text-anchor="end">4 weeks</text>'
    fade = [(d, 100 * 0.5 ** (d / limits.half_life_days)) for d in range(0, 43, 7)]
    bars = "".join(
        f'<rect x="{34 + i * 42:.0f}" y="{24 + 100 - v:.0f}" width="26" height="{v:.0f}" rx="4" '
        f'fill="var(--accent)" opacity="{0.35 + v / 160:.2f}"/>'
        f'<text x="{47 + i * 42:.0f}" y="140" text-anchor="middle">'
        f"{WEEKS.get(d // 7, d // 7)}</text>"
        + (
            f'<text x="{47 + i * 42:.0f}" y="{18 + 100 - v:.0f}" text-anchor="middle" '
            f'class="strong">{v:.0f}%</text>'
            if d // 7 in (0, 2, 4)
            else ""
        )
        for i, (d, v) in enumerate(fade)
    )
    dash = 'stroke-dasharray="4 4"'
    return f"""<div id="fig">
<h2>It needs a little time</h2>
<p>An example: how often HouseMind's prediction is right, day by day.</p>
<div class="chart"><svg viewBox="0 0 340 150" role="img" aria-label="How sure HouseMind gets">
<rect x="{x0}" y="{y0}" width="{week - x0:.1f}" height="{h}" fill="var(--raised)"/>
<text x="{x0 + 6}" y="{y0 + h - 6}">watching</text>
<line x1="{x0}" y1="{y_pro:.1f}" x2="{x0 + w}" y2="{y_pro:.1f}"
  stroke="var(--step-propose)" {dash}/>
<line x1="{x0}" y1="{y_aut:.1f}" x2="{x0 + w}" y2="{y_aut:.1f}"
  stroke="var(--step-autonomous)" {dash}/>
<polyline points="{_curve(sure, x0, y0, w, h)}" fill="none" stroke="var(--text)"
  stroke-width="2.5" stroke-linejoin="round"/>
<text x="2" y="{y0 + 8}">100%</text><text x="8" y="{y0 + h}">50%</text>
{ticks}</svg>
<p class="legend"><span class="dash pro"></span>{propose:g} out of 100: it starts to propose
<br><span class="dash aut"></span>{autonomous:g} out of 100 yes: it may do it itself</p></div>
<p>Old habits count less and less: what you did two weeks ago counts for half. So HouseMind
follows you when you start living differently.</p>
<div class="chart"><p class="legend">How much a day counts, weeks later</p>
<svg viewBox="0 0 340 150" role="img" aria-label="Old habits fade">{bars}</svg></div>
</div>"""


async def take_shots() -> list[Path]:
    from aiohttp import web
    from playwright.async_api import async_playwright

    sys.path.insert(0, str(ROOT / "src"))
    from housemind.server.web import create_app

    hm, _clock, _demo = await _house()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/"
    IMAGES.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def page_for(user: str, height: int = PHONE_HEIGHT) -> Any:
            context = await browser.new_context(
                viewport={"width": PHONE_WIDTH, "height": height},
                device_scale_factor=2,
                color_scheme="dark",
                locale="en-US",
                extra_http_headers={"X-Remote-User-Id": user},
            )
            page = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=15000)
            await page.wait_for_timeout(1200)  # the Orb settles
            return page

        async def calm(page: Any) -> None:
            """Vesta's own window (a message she tells by herself) away, as a person would."""
            for _ in range(5):
                window = page.locator(".backdrop .subtitle")
                if not await window.count():
                    return
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)

        async def tap(page: Any, name: str, role: str = "button", exact: bool = False) -> None:
            await calm(page)
            await page.get_by_role(role, name=name, exact=exact).first.click()

        async def brain_tab(page: Any, name: str) -> None:
            """A tab of Brain; one an expert looks at now and then sits under More (E-503)."""
            await calm(page)
            tab = page.get_by_role("tab", name=name, exact=True)
            if await tab.count():
                await tab.first.click()
                return
            await page.locator("[role=tab][data-more]").first.click()
            await page.get_by_role("menuitem", name=name, exact=True).first.click()

        async def part(page: Any, selector: str, name: str, sharp: bool = False) -> None:
            """One part of a screen; a drawing at full sharpness."""
            path = IMAGES / f"{name}.jpg"
            element = page.locator(selector).first
            await element.scroll_into_view_if_needed()
            await element.screenshot(
                path=str(path),
                type="jpeg",
                quality=70 if sharp else QUALITY,
                scale="device" if sharp else "css",
            )
            made.append(path)

        async def shot(page: Any, name: str) -> None:
            path = IMAGES / f"{name}.jpg"
            await page.screenshot(path=str(path), type="jpeg", quality=QUALITY, scale="css")
            made.append(path)

        carefree = await page_for("user-1")
        await calm(carefree)
        await shot(carefree, "carefree")
        involved = await page_for("demo-user-1")
        await calm(involved)
        await shot(involved, "home")
        await tap(involved, "Logbook")
        await involved.wait_for_timeout(800)
        await shot(involved, "logbook")
        await tap(involved, "Brain")
        await involved.wait_for_timeout(800)
        await shot(involved, "brain")
        await tap(involved, "Home", exact=True)  # not a rule with "home" in its name
        await involved.wait_for_timeout(500)
        await calm(involved)
        await involved.locator(".dock .ask").first.click()
        await involved.wait_for_selector("[role=dialog]", timeout=5000)
        await involved.wait_for_timeout(1500)
        await shot(involved, "vesta")
        await involved.keyboard.press("Escape")
        await involved.wait_for_timeout(400)
        settings = await page_for("demo-user-1")
        await tap(settings, "House settings")
        await settings.wait_for_selector("[role=dialog]", timeout=5000)
        await settings.get_by_role("button", name="Learning and steps").first.click()
        await settings.wait_for_timeout(800)
        await part(settings, "section[data-section=max_step] ul.parts", "parts")
        expert = await page_for("user-4")
        for name, figure in (("steps", _steps()), ("learning", _learning())):
            await expert.evaluate(
                "([style, html]) => { const s = document.createElement('style');"
                " s.textContent = style; document.head.append(s);"
                " document.body.innerHTML = html; }",
                [FIGURE_STYLE, figure],
            )
            await expert.wait_for_timeout(300)
            await part(expert, "#fig", name, sharp=True)
        await expert.evaluate(
            "([style, html]) => { const s = document.createElement('style');"
            " s.textContent = style; document.head.append(s);"
            " document.body.innerHTML = html; }",
            [HERO_STYLE, _hero()],
        )
        await expert.wait_for_timeout(300)
        await part(expert, "#hero", "hero")
        await runner.cleanup()
        # The devices of a room, with a group of lamps and the lamps under it: a bigger house.
        other = await _extended()
        runner = web.AppRunner(create_app(other, DIST, ingress_only=False, demo=True))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{runner.addresses[0][1]}/"
        devices = await page_for("user-4", 1800)  # the whole group, clear of the bars
        await tap(devices, "Brain")
        await brain_tab(devices, "Devices")
        await devices.wait_for_timeout(800)
        await devices.evaluate(
            "() => { const g = [...document.querySelectorAll('details.grp')]"
            "   .find((d) => d.querySelector('.tag.radio'));"
            " const room = g.closest('details.group'); room.open = true; g.open = true;"
            " setTimeout(() => g.scrollIntoView({block: 'center'}), 100); }"
        )
        await devices.wait_for_timeout(500)
        await part(devices, "details.grp:has(.tag.radio)", "devices")
        await browser.close()
        await other.stop()
    await runner.cleanup()
    await hm.stop()
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shots", action="store_true", help="new pictures from the demo house")
    args = parser.parse_args()
    if args.shots:
        made = asyncio.run(take_shots())
        print(json.dumps({p.name: p.stat().st_size for p in made}))
    for source, target in PAGES:
        print(f"{target.relative_to(ROOT)}: {build_page(source, target)} bytes")


if __name__ == "__main__":
    main()
