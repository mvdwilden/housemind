"""Saving the settings as a file and reading them back in (E-296, E-274, 20.18). Each test
fails without the build."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import settings_file
from housemind.brain.rules import parse_rule
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.server.web import create_app
from tests.test_house_modes import _start

ADMIN = {"X-Remote-User-Id": "demo-user-1"}


async def _house(tmp_path: Path) -> HouseMind:
    hm, _ = await _start(tmp_path)
    rule = parse_rule("nooit de gordijnen in de woonkamer open", "nl", hm.languages, hm.model)
    assert rule is not None
    await hm.save_rule(rule, "demo-user-1")
    await hm.set_house_setting("excluded", ["light.garden"])
    await hm.set_house_setting("admin_max_step", {"cover": "propose"})
    await hm.db.set_setting("vesta_voice", {"engine": "tts.piper", "api_token": "abc123"})
    await hm.db.execute(
        "INSERT INTO conversations(at, user_id, question, answer) VALUES(?,?,?,?)",
        (hm.clock.now().isoformat(), "demo-user-1", "Mijn geheime vraag", "Goed."),
    )
    return hm


async def test_the_file_has_the_settings_and_nothing_personal_or_secret(tmp_path: Path) -> None:
    hm = await _house(tmp_path)
    made = await settings_file.make(hm)
    assert made["format"] == "housemind-settings" and made["version"] == 1
    assert made["settings"]["excluded"] == ["light.garden"]
    assert made["settings"]["admin_max_step"] == {"cover": "propose"}
    assert len(made["house_rules"]) == 1
    text = json.dumps(made)
    assert "abc123" not in text and "api_token" not in text  # not even nested
    assert "geheime" not in text and "demo-user-1" not in text  # no conversations, no people
    assert settings_file.name(hm).startswith("housemind-instellingen-")
    await hm.stop()


async def test_reading_back_shows_first_what_changes_and_skips_what_is_not_here(
    tmp_path: Path,
) -> None:
    hm = await _house(tmp_path)
    made = await settings_file.make(hm)
    same = await settings_file.preview(hm, made)
    assert same["ok"] and same["changes"]["settings"] == [] and same["skipped"] == []
    other = json.loads(json.dumps(made))
    other["settings"]["excluded"] = ["light.garden", "light.sauna"]
    other["settings"]["admin_max_step"] = {"cover": "observe", "light.pool": "observe"}
    other["house_rules"].append({**made["house_rules"][0], "scope_id": "attic"})
    shown = await settings_file.preview(hm, other)
    assert shown["ok"] and set(shown["changes"]["settings"]) == {"admin_max_step"}
    assert shown["skipped"] == ["attic", "light.pool", "light.sauna"]
    assert await hm.db.get_setting("admin_max_step") == {"cover": "propose"}  # nothing yet
    await hm.stop()


async def test_after_the_yes_those_parts_are_replaced_and_the_rest_stays(tmp_path: Path) -> None:
    hm = await _house(tmp_path)
    made = await settings_file.make(hm)
    await hm.set_house_setting("excluded", [])
    await hm.set_house_setting("admin_max_step", {})
    await hm.rules.remove(hm.db, hm.rules.rules[0].id or 0)
    await hm.set_house_setting("pet_areas", ["living"])
    made["settings"].pop("pet_areas", None)
    done = await settings_file.apply(hm, made)
    assert done["ok"]
    assert hm.gate is not None and hm.gate.state.excluded == {"light.garden"}
    assert hm.gate.state.admin_max_step == {"cover": Step.PROPOSE}
    assert len(hm.rules.rules) == 1
    assert sorted(hm.pet_areas) == ["living"]  # not in the file: it stays
    await hm.stop()


async def test_another_format_or_a_newer_version_is_refused(tmp_path: Path) -> None:
    hm = await _house(tmp_path)
    assert (await settings_file.preview(hm, {"format": "something"}))["reason"] == "format"
    newer = {**(await settings_file.make(hm)), "version": 99}
    assert (await settings_file.apply(hm, newer))["reason"] == "newer"
    assert (await settings_file.preview(hm, ["not", "a", "file"]))["reason"] == "format"
    await hm.stop()


async def test_only_an_administrator_on_expert_reads_a_file_in(tmp_path: Path) -> None:
    hm = await _house(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        saved = await client.get("/api/export", headers=ADMIN)
        assert saved.status == 200
        assert "housemind-instellingen-" in saved.headers["Content-Disposition"]
        made = json.loads(await saved.text())
        body = {"apply": False, "file": made}
        refused = await client.post("/api/settings-file", json=body, headers=ADMIN)
        assert refused.status == 403  # level 3
        await hm.db.execute("UPDATE people SET level = 4 WHERE user_id = 'demo-user-1'")
        shown = await client.post("/api/settings-file", json=body, headers=ADMIN)
        assert shown.status == 200 and (await shown.json())["ok"]
        other = {"X-Remote-User-Id": "demo-user-2"}
        assert (await client.post("/api/settings-file", json=body, headers=other)).status == 403
    await hm.stop()
