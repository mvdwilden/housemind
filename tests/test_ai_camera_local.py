"""Images only in the house (E-424, design 50.5): with an AI task of Home Assistant that works in
the house, and only after an administrator's own yes, the AI describes what a camera's image
shows ('een pakketje bij de deur') and the camera message says it. Never with an AI in the
cloud; off by default. Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ai.service import AIConfig
from housemind.core.model import Entity, State
from housemind.server import views
from housemind.server.web import create_app
from tests.test_cameras import blink, camera_pushes, everyone_away, start
from tests.test_start_over import _hello, _next

LOCAL = "ai_task.ollama_ai_task"
CLOUD = "ai_task.openai_ai_task"


async def _house(tmp_path: Path) -> Any:
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    for entity_id, platform in ((LOCAL, "ollama"), (CLOUD, "openai_conversation")):
        ha.model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", entity_id, platform=platform
        )
        ha.states[entity_id] = State(entity_id, "unknown", {}, clock.now())
    await hm._refresh_model()

    def answer(entity_id: str, task: str, instructions: str) -> Any:
        if "camera" in instructions:
            return {"text": "Eén persoon met een pakketje bij de oprit."}
        return {"answer": "Ja."}

    ha.ai_task_reply = answer
    return hm, clock, ha


async def _settings(hm: Any, tmp_path: Path, config: dict[str, Any]) -> None:
    """An administrator saves the AI settings in the app."""
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _next(ws, "snapshot")
        await ws.send_json({"type": "ai_config", "config": config})
        for _ in range(100):  # until the house has it
            await asyncio.sleep(0.02)
            if hm.ai.config.base_url == config["baseUrl"]:
                break
        await ws.close()
    await hm.ai.give_consent("demo-user-1")


def _images(ha: Any) -> list[Any]:
    return [t for t in ha.ai_tasks if t[3]]


async def test_an_ai_in_the_house_says_what_the_camera_sees(tmp_path: Path) -> None:
    """With the AI task in the house and an administrator's yes: someone on the driveway while
    everyone is away – the message also says what the image shows. The image goes as Home
    Assistant's media source, never as a picture through HouseMind, and the AI log keeps no
    image."""
    hm, clock, ha = await _house(tmp_path)
    ai = {"enabled": True, "provider": "home_assistant", "baseUrl": LOCAL, "images": True}
    await _settings(hm, tmp_path, ai)
    assert hm.ai.config.images and hm.ai.config.images_by == "demo-user-1"
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    await hm.ai_jobs.idle()
    [asked] = _images(ha)
    assert asked[0] == LOCAL
    assert asked[3] == [
        {
            "media_content_id": "media-source://camera/camera.driveway",
            "media_content_type": "image/jpeg",
        }
    ]
    pushes = camera_pushes(ha)
    assert "het beeld: Eén persoon met een pakketje bij de oprit." in pushes[-1][2]
    log = await hm.ai.log(5)
    image = [r for r in log if r["purpose"] == "image"]
    assert image and image[0]["sent"].startswith("[") and "media-source" not in image[0]["sent"]
    settings = await views.house_settings(hm, "nl")
    assert settings["ai"]["images"] is True
    await hm.stop()


async def test_never_with_an_ai_in_the_cloud_and_off_by_default(tmp_path: Path) -> None:
    """Off by default: no image is described. With an AI task in the cloud it cannot be turned
    on, and even if the setting says so, HouseMind refuses: no image goes to the cloud."""
    hm, clock, ha = await _house(tmp_path)
    ai = {"enabled": True, "provider": "home_assistant", "baseUrl": LOCAL}
    await _settings(hm, tmp_path, ai)
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    await hm.ai_jobs.idle()
    assert _images(ha) == []  # off by default

    await _settings(hm, tmp_path, {**ai, "baseUrl": CLOUD, "images": True})
    assert hm.ai.config.images is False  # cannot be turned on for the cloud
    await hm.ai.configure(
        AIConfig(enabled=True, provider="home_assistant", base_url=CLOUD, images=True)
    )
    await hm.ai.give_consent("demo-user-1")
    await clock.advance(3600)
    await blink(ha, "binary_sensor.driveway_person")
    await hm.ai_jobs.idle()
    assert _images(ha) == []  # and refused anyway
    assert json.dumps([t[2] for t in ha.ai_tasks]).count("still image") == 0
    await hm.stop()
