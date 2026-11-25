"""The few Supervisor calls HouseMind makes as an App (E-86: nothing else is reachable)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from housemind.ha.adapter import Installation

_LOGGER = logging.getLogger(__name__)
SUPERVISOR = "http://supervisor"


async def announce(
    token: str, config: dict[str, Any], earlier: list[str] | None = None
) -> str | None:
    """Tell Home Assistant that the HouseMind integration can be set up (discovery). HA then
    offers it under Settings → Devices & services. The earlier announcements go first, so HA
    never offers one with an old key. Gives the new announcement's id, or None."""
    for uuid in earlier or []:
        await withdraw(token, uuid)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{SUPERVISOR}/discovery",
                json={"service": "housemind", "config": config},
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response,
        ):
            if response.status != 200:
                return None
            body = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Could not announce the HouseMind integration: %s", err)
        return None
    data = body.get("data") if isinstance(body, dict) else None
    found = data.get("uuid") if isinstance(data, dict) else None
    return str(found) if found else ""


async def withdraw(token: str, uuid: str) -> bool:
    """Take back one of HouseMind's own announcements (only its own: the Supervisor checks)."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.delete(
                f"{SUPERVISOR}/discovery/{uuid}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response,
        ):
            return response.status in (200, 400, 404)  # gone, also when it already was
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Could not take back an announcement: %s", err)
        return False


async def installation(token: str, base: str = SUPERVISOR) -> Installation | None:
    """How Home Assistant is installed (E-390, B-293): ``/info`` says whether it is Home
    Assistant OS (``hassos`` has its version) or Supervised, ``/supervisor/info`` whether the
    Supervisor calls it supported and healthy. Every App may read ``/info``; ``/supervisor/info``
    is open to the role ``default``. None when either could not be read."""
    info = await _data(token, f"{base}/info")
    supervisor = await _data(token, f"{base}/supervisor/info")
    if info is None or supervisor is None:
        return None
    supported, healthy = supervisor.get("supported"), supervisor.get("healthy")
    if not isinstance(supported, bool) or not isinstance(healthy, bool):
        return None
    return Installation(
        "os" if info.get("hassos") else "supervised",
        str(info.get("operating_system") or ""),
        supported,
        healthy,
    )


async def _data(token: str, url: str) -> dict[str, Any] | None:
    """The ``data`` of one Supervisor answer, or None."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response,
        ):
            if response.status != 200:
                return None
            body = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Could not ask the Supervisor %s: %s", url, err)
        return None
    if not isinstance(body, dict) or body.get("result") != "ok":
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else None
