"""A tiny Home Assistant websocket server for testing the real client."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import WSMsgType, web

TOKEN = "test-token"
USER_ID = "supervisor-user"


class FakeWsServer:
    def __init__(self) -> None:
        self.user_list_calls = 0
        self.states: dict[str, dict[str, Any]] = {
            "light.kitchen": {
                "entity_id": "light.kitchen",
                "state": "off",
                "attributes": {"supported_color_modes": ["brightness"], "friendly_name": "Kitchen"},
                "last_changed": "2026-10-17T18:00:00+00:00",
                "context": {"id": "c0", "user_id": None, "parent_id": None},
            },
            "automation.kitchen_light": {
                "entity_id": "automation.kitchen_light",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen light at motion", "id": "1700000000"},
                "last_changed": "2026-10-17T18:00:00+00:00",
                "context": {"id": "c1", "user_id": None, "parent_id": None},
            },
        }
        self.automation_configs: dict[str, dict[str, Any]] = {
            "automation.kitchen_light": {
                "id": "1700000000",
                "alias": "Kitchen light at motion",
                "triggers": [
                    {"trigger": "state", "entity_id": "binary_sensor.kitchen_motion", "to": "on"}
                ],
                "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
            }
        }
        self.subscriptions: dict[str, list[int]] = {}
        self.sockets: list[web.WebSocketResponse] = []
        self.calls: list[dict[str, Any]] = []
        self._ctx = 0
        self.version = "2026.10.1"
        self.starting = 0  # how many more times get_config says HA is still starting
        self.accepting = True  # False: HA is away (restarting)
        self.speaking_s = 0.0  # how long an announcement takes to be said

    async def drop(self) -> None:
        """Home Assistant restarts: every connection closes and new ones are refused."""
        self.accepting = False
        self.subscriptions.clear()
        for ws in list(self.sockets):
            await ws.close()
        self.sockets.clear()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/websocket", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if not self.accepting:
            raise web.HTTPBadGateway()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_json({"type": "auth_required", "ha_version": self.version})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "bad"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": self.version})
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                continue
            await self._handle(ws, msg.json())
        return ws

    def _still_starting(self) -> bool:
        if self.starting > 0:
            self.starting -= 1
            return True
        return False

    async def _result(self, ws: web.WebSocketResponse, msg_id: int, result: Any) -> None:
        await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": result})

    async def _handle(self, ws: web.WebSocketResponse, msg: dict[str, Any]) -> None:
        msg_id = msg["id"]
        kind = msg["type"]
        if kind == "auth/current_user":
            await self._result(ws, msg_id, {"id": USER_ID, "name": "Supervisor"})
        elif kind == "get_config":
            await self._result(
                ws,
                msg_id,
                {
                    "version": self.version,
                    "state": "NOT_RUNNING" if self._still_starting() else "RUNNING",
                    "language": "nl",
                    "country": "NL",
                    "time_zone": "Europe/Amsterdam",
                },
            )
        elif kind == "subscribe_events":
            self.subscriptions.setdefault(msg["event_type"], []).append(msg_id)
            await self._result(ws, msg_id, None)
        elif kind == "get_states":
            await self._result(ws, msg_id, list(self.states.values()))
        elif kind == "config/floor_registry/list":
            await self._result(ws, msg_id, [{"floor_id": "ground", "name": "Ground", "level": 0}])
        elif kind == "config/area_registry/list":
            await self._result(
                ws, msg_id, [{"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"}]
            )
        elif kind == "config/device_registry/list":
            await self._result(
                ws, msg_id, [{"id": "dev1", "name": "Kitchen lamp", "area_id": "kitchen"}]
            )
        elif kind == "config/entity_registry/list":
            await self._result(
                ws,
                msg_id,
                [
                    {
                        "entity_id": "light.kitchen",
                        "id": "reg-1",
                        "device_id": "dev1",
                        "platform": "demo",
                    }
                ],
            )
        elif kind == "automation/config":
            config = self.automation_configs.get(msg["entity_id"])
            if config is None:
                await ws.send_json(
                    {
                        "id": msg_id,
                        "type": "result",
                        "success": False,
                        "error": {"code": "not_found", "message": "Entity not found"},
                    }
                )
            else:
                await self._result(ws, msg_id, {"config": config})
        elif kind == "config/auth/list":
            self.user_list_calls += 1
            await self._result(
                ws,
                msg_id,
                [{"id": "u1", "name": "Admin", "group_ids": ["system-admin"], "is_active": True}],
            )
        elif kind == "get_services":
            await self._result(ws, msg_id, {"light": {"turn_on": {}, "turn_off": {}}})
        elif kind == "call_service" and msg["domain"] == "assist_satellite":
            # An announcement answers once it has been said; one that cannot be said at once.
            self.calls.append(msg)
            if msg["target"]["entity_id"] not in self.states:
                await ws.send_json(
                    {
                        "id": msg_id,
                        "type": "result",
                        "success": False,
                        "error": {"code": "not_found", "message": "no such satellite"},
                    }
                )
                return
            await asyncio.sleep(self.speaking_s)
            await self._result(ws, msg_id, {})
        elif kind == "call_service":
            self.calls.append(msg)
            self._ctx += 1
            context = {"id": f"srv-{self._ctx}", "user_id": USER_ID, "parent_id": None}
            entity_id = msg["target"]["entity_id"]
            old = self.states[entity_id]
            new = {
                **old,
                "state": "on" if msg["service"] == "turn_on" else "off",
                "context": context,
            }
            self.states[entity_id] = new
            # Send the event *before* the result, like a busy HA can do.
            await self.push_state_changed(entity_id, old, new)
            await self._result(ws, msg_id, {"context": context})
        else:
            await ws.send_json(
                {
                    "id": msg_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown", "message": kind},
                }
            )

    async def push_event(
        self, event_type: str, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> None:
        """Any other event Home Assistant fires, such as ``automation_triggered`` (E-448)."""
        for ws in self.sockets:
            for sub_id in self.subscriptions.get(event_type, []):
                event = {"event_type": event_type, "data": data, "context": context or {}}
                await ws.send_json({"id": sub_id, "type": "event", "event": event})

    async def push_state_changed(
        self, entity_id: str, old: dict[str, Any], new: dict[str, Any]
    ) -> None:
        for ws in self.sockets:
            for sub_id in self.subscriptions.get("state_changed", []):
                await ws.send_json(
                    {
                        "id": sub_id,
                        "type": "event",
                        "event": {
                            "event_type": "state_changed",
                            "data": {"entity_id": entity_id, "old_state": old, "new_state": new},
                        },
                    }
                )
