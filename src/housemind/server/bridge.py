"""The bridge for the small HouseMind integration in Home Assistant (B-06, E-96, E-97, E-167).

Only Home Assistant itself can reach it: from the Supervisor's internal network, with the
secret the App put next to the integration. No port opens to the outside (E-86). Every action
goes through exactly the same checks as in the app (E-110).
"""

from __future__ import annotations

import hmac
from ipaddress import ip_address, ip_network
from typing import Any

from aiohttp import web

from housemind.runtime import HouseMind

HASSIO_NETWORK = ip_network("172.30.32.0/23")
MODES = ("home", "away", "sleep", "vacation", "guests")
PREFIX = "/integration/"


def from_home_assistant(request: web.Request) -> bool:
    try:
        return ip_address(request.remote or "") in HASSIO_NETWORK
    except ValueError:
        return False


async def _authorised(request: web.Request, hm: HouseMind) -> bool:
    header = request.headers.get("Authorization", "")
    secret = await hm.integration_secret()
    return header.startswith("Bearer ") and hmac.compare_digest(header[7:], secret)


def _text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


def routes(app: web.Application, key: web.AppKey[HouseMind]) -> None:
    async def guard(request: web.Request) -> HouseMind:
        hm = request.app[key]
        ok = await _authorised(request, hm)
        await hm.bridge_contact(ok)  # so the link step knows HA really reaches it (20.23)
        if not ok:
            raise web.HTTPUnauthorized(text="wrong or missing secret")
        return hm

    async def state(request: web.Request) -> web.Response:
        hm = await guard(request)
        if request.query.get("fallback_done"):
            hm.watchdog_done()  # B-204: what HouseMind had set is no longer its own
        return web.json_response(await hm.bridge_state())

    async def action(request: web.Request) -> web.Response:
        hm = await guard(request)
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        name = data.get("action")
        result: dict[str, Any] = {"ok": True}
        if name == "pause":
            minutes = data.get("minutes")
            if not (minutes is None or (isinstance(minutes, int) and 0 <= minutes <= 7 * 24 * 60)):
                raise web.HTTPBadRequest()
            # Longer than an hour only for an administrator (QA 3, gat 3): a person HA names
            # and knows as one; an automation, Assist without a person or anyone else: an hour.
            who = _text(data.get("user_id"), 64)
            held = await hm.pause(0 if minutes is None else minutes, who, await hm.admin_user(who))
            result["minutes"] = held
        elif name == "resume":
            await hm.pause(None, _text(data.get("user_id"), 64))
        elif name == "house_mode" and data.get("mode") in MODES:
            result["ok"] = await hm.set_house_mode(str(data["mode"]))
        elif name == "feedback" and isinstance(data.get("good"), bool):
            decision = data.get("decision")
            result = await hm.feedback(
                bool(data["good"]),
                decision if isinstance(decision, int) else None,
                _text(data.get("user_id"), 64),
            )
        elif name == "house_rule" and (text := _text(data.get("text"), 300)):
            lang = hm.languages.resolve(_text(data.get("language"), 16) or hm._language())
            # Only someone HouseMind knows, from level Involved (E-87, E-292): a speaker or
            # an automation without a user cannot make a house rule.
            user_id = _text(data.get("user_id"), 64)
            if user_id is None or await hm.person_level(user_id) < 3:
                key = "talk.rule_where" if user_id is None else "talk.rule_level"
                return web.json_response({"ok": False, "message": hm.languages.text(lang, key)})
            understood = await hm.understand_rule_text(text, lang)
            if not understood["ok"]:
                return web.json_response({"ok": False, "message": understood["message"]})
            result = {"ok": True, "read_back": understood["read_back"], "saved": False}
            if data.get("confirm") is True:
                await hm.save_rule(understood["rule"], user_id, understood["conflicts"])
                result["saved"] = True
        elif name == "ask" and (question := _text(data.get("question"), 500)):
            reply = await hm.assist(
                question,
                _text(data.get("language"), 16) or hm._language(),
                None,
                _text(data.get("user_id"), 64),
            )
            result = {"ok": True, "answer": reply["text"]}
        else:
            raise web.HTTPBadRequest()
        return web.json_response(result)

    async def converse(request: web.Request) -> web.Response:
        hm = await guard(request)
        data = await request.json()
        text = _text(data.get("text") if isinstance(data, dict) else None, 500)
        if text is None:
            raise web.HTTPBadRequest()
        reply = await hm.assist(
            text,
            _text(data.get("language"), 16) or hm._language(),
            _text(data.get("conversation_id"), 64),
            _text(data.get("user_id"), 64),
        )
        return web.json_response(reply)

    app.router.add_get(f"{PREFIX}state", state)
    app.router.add_post(f"{PREFIX}action", action)
    app.router.add_post(f"{PREFIX}converse", converse)
