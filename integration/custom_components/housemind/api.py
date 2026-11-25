"""Talks to the HouseMind App over the Supervisor's internal network, with the App's secret."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp


class HouseMindError(Exception):
    """The App could not be reached or refused."""


class HouseMindClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, secret: str) -> None:
        self._session = session
        self._base = f"http://{host}:{port}/integration/"
        self._headers = {"Authorization": f"Bearer {secret}"}

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            async with self._session.request(
                method,
                self._base + path,
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                if response.status != 200:
                    raise HouseMindError(f"HouseMind answered {response.status}")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HouseMindError(str(err)) from err

    async def state(self, fallback_done: str | None = None) -> dict[str, Any]:
        """The state, telling HouseMind when the watchdog gave everything back (B-204)."""
        path = f"state?fallback_done={quote(fallback_done)}" if fallback_done else "state"
        result = await self._request("GET", path)
        return result if isinstance(result, dict) else {}

    async def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", "action", payload)
        return result if isinstance(result, dict) else {}

    async def converse(
        self, text: str, language: str, conversation_id: str | None, user_id: str | None
    ) -> dict[str, Any]:
        result = await self._request(
            "POST",
            "converse",
            {
                "text": text,
                "language": language,
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )
        return result if isinstance(result, dict) else {}
