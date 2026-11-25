"""Start HouseMind: ``python -m housemind``.

As a Home Assistant App the options come from ``/data/options.json`` and the Supervisor token
from the environment. Locally, ``--demo <house>`` starts a demo house without any real HA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from housemind import __version__
from housemind.brain.start_over import start_over
from housemind.core.clock import Clock, DemoClock, SystemClock
from housemind.demo.houses import DEMO_HOUSES, DemoHouse, load_demo_house
from housemind.demo.life import energy_sim, live, simulate_energy
from housemind.demo.play import Play, load_clock
from housemind.ha.adapter import HomeAssistant
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

_LOGGER = logging.getLogger("housemind")

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
CONTAINER_ENV = Path("/run/s6/container_environment")


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def supervisor_token(
    environ: dict[str, str] | None = None, container_env: Path = CONTAINER_ENV
) -> str | None:
    """The Supervisor token. The base image starts us with a cleaned environment, so the
    token may only be in the container environment that s6-overlay keeps as files."""
    env = os.environ if environ is None else environ
    token = env.get("SUPERVISOR_TOKEN")
    if token:
        return token
    try:
        return (container_env / "SUPERVISOR_TOKEN").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _demo_choice(options: dict[str, Any]) -> str:
    """The demo house chosen in the App options: simple, average or extended (20-10-2026). An
    older App kept "demo_house" with a test house; that one still works."""
    chosen = options.get("demo_home") or options.get("demo_house")
    return chosen if chosen in DEMO_HOUSES else "average"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="housemind")
    parser.add_argument("--demo", choices=DEMO_HOUSES, help="run a demo house instead of real HA")
    parser.add_argument("--language", default="en")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--data", type=Path, default=None)
    args = parser.parse_args(argv)

    options_file = Path("/data/options.json")
    options = json.loads(options_file.read_text()) if options_file.exists() else {}
    logging.basicConfig(level=options.get("log_level", "INFO").upper())

    token = supervisor_token()
    demo = args.demo or (_demo_choice(options) if options.get("demo_mode") else None)
    # Running as an App: Home Assistant gave us options and a token (also in demo mode).
    in_app = (token is not None or options_file.exists()) and not args.demo
    _LOGGER.info(
        "Starting HouseMind %s (%s%s)",
        __version__,
        "App" if in_app else "local",
        f", demo house {demo}" if demo else "",
    )

    data_dir = args.data or (Path("/data") if in_app else REPO_ROOT / ".data")
    if demo:
        # Never touches the real house's memory (E-48); each demo house keeps its own.
        data_dir = data_dir / "demo" / demo
    knowledge = [
        _first_existing(Path("/app/knowledge"), REPO_ROOT / "knowledge"),
        data_dir / "knowledge-update",
        Path("/config/housemind"),
    ]
    frontend = _first_existing(Path("/app/frontend"), REPO_ROOT / "frontend" / "dist")

    # A demo house has its own time: it plays or stands still, slower or faster, and jumps ahead
    # (E-321, B-188); after a restart it is as it was left. The real house has the real clock.
    clock: Clock = load_clock(data_dir / "play.json") if demo else SystemClock()
    ha: HomeAssistant
    pets: set[str] = set()
    house = None
    if demo:
        house = load_demo_house(demo, clock, args.language)
        ha, pets = house.ha, set(house.pet_areas)
    elif token:
        ha = WebSocketHomeAssistant(clock, token)
    else:
        parser.error("no Home Assistant: run as an App, or use --demo")

    def connect() -> HomeAssistant:
        """A fresh connection after the factory settings (E-274); a demo house stays itself."""
        if house is not None:
            return house.ha
        assert token is not None
        return WebSocketHomeAssistant(clock, token)

    asyncio.run(
        _serve(
            HouseMind(
                clock,
                ha,
                Paths(
                    knowledge,
                    data_dir,
                    ha_config=Path("/config") if in_app and not demo else None,
                    # Also in demo mode: the token only tells HA where the App is (the
                    # integration shows the demo house then); it never touches the house.
                    supervisor_token=token,
                    app_data=Path("/data") if in_app else None,
                ),
                pets,
            ),
            frontend,
            args.port,
            in_app,
            house,
            connect,
            data_dir / "play.json",
        )
    )


async def _serve(
    hm: HouseMind,
    frontend: Path,
    port: int,
    ingress_only: bool,
    house: DemoHouse | None = None,
    connect: Callable[[], HomeAssistant] | None = None,
    play_store: Path | None = None,
) -> None:
    await hm.start()
    tasks: list[asyncio.Task[None]] = []
    play = None
    if house is not None:  # the demo residents follow their daily rhythm (E-48)
        tz = hm.config.time_zone if hm.config else "UTC"
        sim = energy_sim(house, tz)
        if isinstance(hm.clock, DemoClock) and play_store is not None:
            # 'Speel het huis' (E-321): only here, for a demo house.
            play = Play(house, hm.clock, hm, tz, play_store, sim)
            await play.settle()  # emergencies left open from before the restart close
            tasks.append(asyncio.create_task(play.keep(), name="demo-time"))
        held = (lambda: play.held) if play is not None else None
        tasks.append(asyncio.create_task(live(house, hm.clock, tz, held=held), name="demo-life"))
        tasks.append(
            asyncio.create_task(simulate_energy(house, hm.clock, tz, sim), name="demo-energy")
        )
    try:
        while True:
            app = create_app(hm, frontend, ingress_only, demo=house is not None, play=play)
            runner = web.AppRunner(app)
            await runner.setup()
            host = "0.0.0.0" if ingress_only else "127.0.0.1"  # noqa: S104
            await web.TCPSite(runner, host, port).start()
            _LOGGER.info("HouseMind is running on port %s", port)
            await hm.restart_requested.wait()
            # Back to the factory settings (E-274): the screens reconnect to a fresh HouseMind.
            await runner.cleanup()
            hm = await start_over(hm, connect() if connect is not None else hm.ha)
            if play is not None:
                play.hm = hm
    finally:
        for task in tasks:
            task.cancel()
        if play is not None:
            play.save()
        await runner.cleanup()
        await hm.stop()


if __name__ == "__main__":
    main()
