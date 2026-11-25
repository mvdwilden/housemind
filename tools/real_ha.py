"""Test HouseMind against a real Home Assistant in a container (E-142, H12.1 "Echt HA").

    uv run python tools/real_ha.py stable      # also: beta, or a version such as 2026.4

It starts the official Home Assistant container with a small test configuration (HA's own demo
devices plus one automation), finishes the onboarding with a throwaway test user, waits until the
devices are there and runs ``tests/test_real_ha.py`` against it – including a restart of Home
Assistant. Everything happens on this computer; the container is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PORT = 18123
CONFIGURATION = """\
homeassistant:
  name: HouseMind test
  latitude: 52.37
  longitude: 4.89
  elevation: 0
  unit_system: metric
  time_zone: Europe/Amsterdam
  country: NL
  currency: EUR
http:
api:
websocket_api:
config:
person:
demo:
automation:
  - id: "housemind_test_1"
    alias: "Kitchen light at motion"
    triggers:
      - trigger: state
        entity_id: binary_sensor.movement_backyard
        to: "on"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.kitchen_lights
"""


def _request(
    url: str, data: dict[str, object] | None = None, token: str | None = None, form: bool = False
) -> object:
    body = None
    headers = {}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)  # noqa: S310 - local test HA
    with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw else None


def _wait(what: str, check: object, limit_s: float = 300) -> None:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        try:
            if check():  # type: ignore[operator]
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(2)
    raise SystemExit(f"Home Assistant did not get ready: {what}")


def _onboard(base: str) -> str:
    """Create the owner (a throwaway test user) and finish onboarding; returns a token."""
    client_id = f"{base}/"
    created = _request(
        f"{base}/api/onboarding/users",
        {
            "client_id": client_id,
            "name": "Tester",
            "username": "tester",
            "password": secrets.token_urlsafe(24),
            "language": "en",
        },
    )
    assert isinstance(created, dict)
    tokens = _request(
        f"{base}/auth/token",
        {"grant_type": "authorization_code", "code": created["auth_code"], "client_id": client_id},
        form=True,
    )
    assert isinstance(tokens, dict)
    token = str(tokens["access_token"])
    for step, data in (
        ("core_config", {}),
        ("analytics", {}),
        ("integration", {"client_id": client_id, "redirect_uri": f"{base}/?auth_callback=1"}),
    ):
        try:
            _request(f"{base}/api/onboarding/{step}", data, token)
        except urllib.error.HTTPError as err:
            if err.code not in (400, 403):  # already done in this version
                raise
    return token


async def _second_user(base: str, owner_token: str) -> str:
    """A second throwaway user, for "a person does something" (HouseMind has its own user)."""
    username, password = "person", secrets.token_urlsafe(24)
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(f"{base.replace('http', 'ws')}/api/websocket")
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": owner_token})
        await ws.receive_json()
        await ws.send_json(
            {
                "id": 1,
                "type": "config/auth/create",
                "name": "Person",
                "group_ids": ["system-admin"],
            }
        )
        user = (await ws.receive_json())["result"]["user"]
        await ws.send_json(
            {
                "id": 2,
                "type": "config/auth_provider/homeassistant/create",
                "user_id": user["id"],
                "username": username,
                "password": password,
            }
        )
        await ws.receive_json()
        await ws.close()
    client_id = f"{base}/"
    flow = _request(
        f"{base}/auth/login_flow",
        {"client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id},
    )
    assert isinstance(flow, dict)
    done = _request(
        f"{base}/auth/login_flow/{flow['flow_id']}",
        {"client_id": client_id, "username": username, "password": password},
    )
    assert isinstance(done, dict)
    tokens = _request(
        f"{base}/auth/token",
        {"grant_type": "authorization_code", "code": done["result"], "client_id": client_id},
        form=True,
    )
    assert isinstance(tokens, dict)
    return str(tokens["access_token"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", nargs="?", default="stable")
    args = parser.parse_args()
    image = f"ghcr.io/home-assistant/home-assistant:{args.tag}"
    name = f"housemind-real-ha-{args.tag.replace('.', '-')}"
    base = f"http://127.0.0.1:{PORT}"
    with tempfile.TemporaryDirectory(prefix="housemind-real-ha-") as config:
        Path(config, "configuration.yaml").write_text(CONFIGURATION)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(
            [
                *("docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{PORT}:8123"),
                *("-v", f"{config}:/config", "-e", "TZ=Europe/Amsterdam", image),
            ],
            check=True,
            capture_output=True,
        )
        try:
            _wait("onboarding", lambda: _request(f"{base}/api/onboarding") is not None)
            token = _onboard(base)
            person = asyncio.run(_second_user(base, token))

            def demo_ready() -> bool:
                states = _request(f"{base}/api/states", token=token)
                ids = {s["entity_id"] for s in states} if isinstance(states, list) else set()
                return {"light.bed_light", "cover.kitchen_window", "climate.hvac"} <= ids

            _wait("demo devices", demo_ready)
            version = subprocess.run(
                [
                    *("docker", "exec", name, "python", "-c"),
                    "from homeassistant.const import __version__; print(__version__)",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            print(f"Home Assistant {version} is ready ({image})", flush=True)
            env = {
                **os.environ,
                "HOUSEMIND_REAL_HA": base,
                "HOUSEMIND_REAL_HA_TOKEN": token,
                "HOUSEMIND_REAL_HA_PERSON_TOKEN": person,
                "HOUSEMIND_REAL_HA_VERSION": version,
                "HOUSEMIND_REAL_HA_CONTAINER": name,
                "HOUSEMIND_REAL_HA_CONFIG": config,
            }
            result = subprocess.run(
                [
                    *("uv", "run", "pytest", "-m", "realha", "-s", "-p", "no:cacheprovider"),
                    "tests/test_real_ha.py",
                ],
                cwd=ROOT,
                env=env,
                check=False,
            )
            return result.returncode
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


if __name__ == "__main__":
    sys.exit(main())
