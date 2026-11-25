"""On Supervised HouseMind simply goes on (E-390, B-293 – B-295). Home Assistant no longer
supports Supervised since 2025.12, but such a house still works, and so do Apps. HouseMind asks
the Supervisor how Home Assistant is installed and whether it calls it supported and healthy;
on Supervised, or when it is not supported or not healthy, the administrators get one calm
message that HouseMind goes on – in their own language. Never a guess, never in a demo house.
Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web
from aiohttp.test_utils import TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha import supervisor
from housemind.ha.adapter import Installation
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.logbook import logbook

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
EVENING = datetime(2026, 10, 27, 20, 0, tzinfo=TZ)
ADMIN, RESIDENT = "demo-user-1", "demo-user-2"  # in the demo house user 1 is an administrator

SUPERVISED = Installation("supervised", "Debian GNU/Linux 12 (bookworm)", False, True)
HAOS = Installation("os", "Home Assistant OS 16.2", True, True)

# The Supervisor's own answers, as it gives them (shortened).
INFO_SUPERVISED = {
    "supervisor": "2026.10.2",
    "homeassistant": "2026.10.4",
    "hassos": None,
    "hostname": "kelder-server",
    "operating_system": "Debian GNU/Linux 12 (bookworm)",
    "machine": "qemux86-64",
    "machine_id": "0f1e2d3c4b5a",
    "arch": "amd64",
    "state": "running",
    "supported": False,
    "channel": "stable",
}
INFO_HAOS = {**INFO_SUPERVISED, "hassos": "16.2", "operating_system": "Home Assistant OS 16.2"}
SUPERVISOR_INFO = {
    "version": "2026.10.2",
    "channel": "stable",
    "arch": "amd64",
    "supported": False,
    "healthy": True,
    "ip_address": "172.30.32.2",
    "addons": [],
}


async def start(
    tmp_path: Path, install: Installation | None
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    house.ha.install = install
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    for user, level in ((ADMIN, 3), (RESIDENT, 3)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (user, user, level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def shown(hm: HouseMind, user_id: str, lang: str = "nl") -> list[dict[str, Any]]:
    """The message about the installation as this person sees it in the app. Other parts have
    messages of their own, such as Home Assistant netjes' first line for the administrators."""
    viewer = await views.load_viewer(hm, user_id, None, trusted_local=False)
    ours = {n["id"] for n in await install_messages(hm)}
    seen = await views.notifications(hm, lang, viewer.is_admin, user_id)
    return [m for m in seen if m["id"] in ours]


async def install_messages(hm: HouseMind) -> list[dict[str, Any]]:
    return [n for n in await hm.notifications() if n["topic"] == "ha:install"]


async def a_day_later(hm: HouseMind, clock: SimulatedClock) -> None:
    await clock.advance(timedelta(hours=24, minutes=10).total_seconds())
    for _ in range(32):  # the next quarter-hour round
        await clock.advance(30)
        await hm.tick()


async def test_on_supervised_the_administrators_hear_calmly_that_housemind_goes_on(
    tmp_path: Path,
) -> None:
    """E-390: on Supervised an administrator sees one calm message (Info): Home Assistant no
    longer supports it since 2025.12, HouseMind simply goes on, and a backup moves it to Home
    Assistant OS. It is in Logboek too. A resident does not see it."""
    hm, _, _ = await start(tmp_path, SUPERVISED)
    mine = await shown(hm, ADMIN)
    assert len(mine) == 1
    message = mine[0]
    assert message["kind"] == "info"
    assert message["text"] == (
        "Deze Home Assistant is geïnstalleerd als Supervised. Home Assistant ondersteunt dat "
        "sinds versie 2025.12 niet meer. Ik werk gewoon door. Wil je weer "
        "ondersteuning? Zet dan een back-up over naar Home Assistant OS."
    )
    viewer = await views.load_viewer(hm, ADMIN, None, trusted_local=False)
    log = await logbook(hm, viewer, ADMIN, "nl")
    assert any(i["type"] == "notice" and i["id"] == message["id"] for i in log["items"])
    assert await shown(hm, RESIDENT) == []
    await hm.stop()


async def test_the_message_is_in_each_administrators_own_language(tmp_path: Path) -> None:
    """E-390: the same message in Dutch, English, German and French – each with 2025.12, that
    HouseMind goes on, and Home Assistant OS; never a code."""
    hm, _, _ = await start(tmp_path, SUPERVISED)
    goes_on = {
        "nl": "Ik werk gewoon door.",
        "en": "I simply keep working.",
        "de": "Ich arbeite einfach weiter.",
        "fr": "Je continue de fonctionner normalement.",
    }
    texts = set()
    for lang, words in goes_on.items():
        [message] = await shown(hm, ADMIN, lang)
        text = message["text"]
        assert words in text and "2025.12" in text and "Home Assistant OS" in text, lang
        assert "install_" not in text and "notify." not in text
        texts.add(text)
    assert len(texts) == 4
    await hm.stop()


async def test_home_assistant_os_supported_and_healthy_says_nothing(tmp_path: Path) -> None:
    """E-390: on Home Assistant OS that the Supervisor calls supported and healthy, there is no
    message – as in the real house."""
    hm, clock, _ = await start(tmp_path, HAOS)
    await a_day_later(hm, clock)
    assert await install_messages(hm) == []
    assert await shown(hm, ADMIN) == []
    await hm.stop()


async def test_not_healthy_points_to_repairs_and_goes_away_by_itself(tmp_path: Path) -> None:
    """E-390, K1: Home Assistant OS that the Supervisor calls not healthy – the message says so
    and where to look in Home Assistant. A day later it is healthy again: HouseMind reads it
    again and the message goes away by itself."""
    hm, clock, ha = await start(tmp_path, Installation("os", "Home Assistant OS 16.2", True, False))
    [message] = await shown(hm, ADMIN)
    assert message["text"] == (
        "Home Assistant noemt deze installatie niet gezond. Ik werk gewoon door. Wat er "
        "mis is, zie je in Home Assistant bij Instellingen → Systeem → Reparaties."
    )
    ha.install = HAOS
    await a_day_later(hm, clock)
    assert await install_messages(hm) == []
    await hm.stop()


async def test_not_supported_says_where_to_see_why(tmp_path: Path) -> None:
    """E-390: Home Assistant OS that the Supervisor calls not supported – the message says so,
    and where to see why."""
    hm, _, _ = await start(tmp_path, Installation("os", "Home Assistant OS 16.2", False, True))
    [message] = await shown(hm, ADMIN, "en")
    assert message["text"] == (
        "Home Assistant calls this installation unsupported. I simply keep working. "
        "You can see why in Home Assistant under Settings → System → Repairs."
    )
    await hm.stop()


async def test_more_at_once_is_one_message_supervised_first(tmp_path: Path) -> None:
    """E-390, K2: Supervised, not supported and not healthy at once – one message, the one about
    Supervised. Still one after a day."""
    install = Installation("supervised", "Debian GNU/Linux 12 (bookworm)", False, False)
    hm, clock, _ = await start(tmp_path, install)
    await a_day_later(hm, clock)
    [message] = await shown(hm, ADMIN)
    assert "Supervised" in message["text"] and "Reparaties" not in message["text"]
    assert len(await install_messages(hm)) == 1
    await hm.stop()


async def test_without_an_answer_from_the_supervisor_nothing_is_said(tmp_path: Path) -> None:
    """E-390: no Supervisor to ask (a demo house), or no answer – HouseMind does not guess and
    says nothing; it goes on as always. A message already there stays until a clear answer."""
    hm, clock, ha = await start(tmp_path, None)
    assert await install_messages(hm) == []
    assert hm.gate is not None and hm.activated
    await hm.stop()
    hm, clock, ha = await start(tmp_path / "again", SUPERVISED)
    assert len(await install_messages(hm)) == 1
    ha.install = None  # the Supervisor does not answer this time
    await a_day_later(hm, clock)
    assert len(await install_messages(hm)) == 1
    await hm.stop()


async def test_the_diagnostics_package_names_the_kind_but_not_the_host(tmp_path: Path) -> None:
    """E-390 with E-15: the diagnostics package says Supervised, not supported, healthy – and
    nothing about the operating system or the host."""
    hm, _, _ = await start(tmp_path, SUPERVISED)
    package = await hm.diagnostics()
    assert package["self_check"]["installation"] == {
        "kind": "supervised",
        "supported": False,
        "healthy": True,
    }
    assert "Debian" not in json.dumps(package)
    await hm.stop()


async def _supervisor(answers: dict[str, Any]) -> tuple[TestServer, list[str]]:
    """A Supervisor that answers as the real one; it remembers the tokens it was given."""
    tokens: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        tokens.append(request.headers.get("Authorization", ""))
        answer = answers.get(request.path)
        if answer is None:
            return web.json_response({"result": "error", "message": "forbidden"}, status=403)
        return web.json_response(answer)

    app = web.Application()
    app.router.add_get("/info", handle)
    app.router.add_get("/supervisor/info", handle)
    server = TestServer(app)
    await server.start_server()
    return server, tokens


async def test_the_real_client_asks_the_supervisor_with_the_apps_token() -> None:
    """B-293: /info and /supervisor/info with the App's own token – no IP address, no key. No
    version of Home Assistant OS means Supervised."""
    server, tokens = await _supervisor(
        {
            "/info": {"result": "ok", "data": INFO_SUPERVISED},
            "/supervisor/info": {"result": "ok", "data": SUPERVISOR_INFO},
        }
    )
    try:
        found = await supervisor.installation("app-token", str(server.make_url("")).rstrip("/"))
    finally:
        await server.close()
    assert found == Installation("supervised", "Debian GNU/Linux 12 (bookworm)", False, True)
    assert tokens == ["Bearer app-token", "Bearer app-token"]


async def test_the_real_client_knows_home_assistant_os() -> None:
    """B-293: with a version of Home Assistant OS in /info it is Home Assistant OS."""
    server, _ = await _supervisor(
        {
            "/info": {"result": "ok", "data": INFO_HAOS},
            "/supervisor/info": {
                "result": "ok",
                "data": {**SUPERVISOR_INFO, "supported": True},
            },
        }
    )
    try:
        found = await supervisor.installation("app-token", str(server.make_url("")).rstrip("/"))
    finally:
        await server.close()
    assert found == Installation("os", "Home Assistant OS 16.2", True, True)


async def test_the_real_client_never_guesses() -> None:
    """B-293: a refusal, an error answer or no answer at all gives nothing, not a guess."""
    server, _ = await _supervisor({"/info": {"result": "ok", "data": INFO_HAOS}})
    try:
        base = str(server.make_url("")).rstrip("/")
        assert await supervisor.installation("app-token", base) is None  # 403 on /supervisor
    finally:
        await server.close()
    server, _ = await _supervisor(
        {
            "/info": {"result": "error", "message": "x"},
            "/supervisor/info": {"result": "ok", "data": SUPERVISOR_INFO},
        }
    )
    try:
        found = await supervisor.installation("app-token", str(server.make_url("")).rstrip("/"))
        assert found is None  # an error answer
    finally:
        await server.close()
    assert await supervisor.installation("app-token", base) is None  # nobody answers now
