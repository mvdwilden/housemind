"""Starting again (E-271 – E-275, B-121): the introduction again, learning again, the factory
settings, and "Nog niet actief" until an administrator finished the new introduction.

Learning again forgets only what HouseMind learned. The factory settings give everything back
first, take the link in Home Assistant away and then erase HouseMind's own memory; the App's
options in Home Assistant and the own files in /config/housemind stay. A fresh HouseMind
starts in the same process, as after a new installation. Emergencies always keep working."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from typing import TYPE_CHECKING, Any

from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.notify import Kind

if TYPE_CHECKING:
    from housemind.ha.adapter import HomeAssistant
    from housemind.runtime import HouseMind, Paths

_LOGGER = logging.getLogger(__name__)

WORD_KEY = "ui.restart.factory.word"  # WISSEN, ERASE, LÖSCHEN, EFFACER
DATABASE = ("housemind.db", "housemind.db-wal", "housemind.db-shm")
SECRETS = "secrets"  # the AI's keys (encrypted): HouseMind's own, so they go too
# What learning again forgets besides the skills' own tables (Learning.forget_all).
LEARNED_ELSEWHERE = ("history_evidence", "history_automations", "corrections")


# -- "Nog niet actief" (E-275) ------------------------------------------------------------


async def introduced(hm: HouseMind) -> None:
    """An administrator finished the introduction (or chose "Later")."""
    await hm.db.set_setting("intro_again", None)
    await maybe_activate(hm)


async def maybe_activate(hm: HouseMind) -> bool:
    """HouseMind goes on by itself once it is set up (E-277) – and, after a yes to learning,
    once the history is read. Everything starts on Observe."""
    if hm.safe_mode is not None:
        return False  # after a failed update only the emergency reactions work (E-301)
    if hm.activated or (await hm.db.get_setting("setup") or "done") != "done":
        return False
    task = hm._history_task
    busy = task is not None and not task.done() and task is not asyncio.current_task()
    if busy or (hm.history_status.state == "reading" and task is not asyncio.current_task()):
        return False  # the end of the reading activates
    hm.activated = True
    if hm.gate is not None:
        hm.gate.state.inactive = False
    await hm.db.set_setting("activated", True)
    if hm.learning_allowed and not await hm.db.get_setting("learning_since"):
        await hm.db.set_setting("learning_since", hm.clock.now().isoformat())
    _LOGGER.info("HouseMind is active: the introduction is done")
    if hm.world is not None:
        await hm._raise(
            Kind.INFO, "activated", "notify.activated", {}, hm.world.snapshot(hm.clock.now())
        )
    hm._refresh_orb()
    await hm._notify()
    return True


# -- the introduction again (E-272) --------------------------------------------------------


async def intro_again(hm: HouseMind, user_id: str) -> None:
    """The administrator's introduction once more, with the answers of now; nothing goes."""
    await hm.db.set_setting("intro_again", True)
    row = await hm.db.fetchone("SELECT preferences FROM people WHERE user_id = ?", (user_id,))
    prefs = json.loads(row["preferences"] or "{}") if row else {}
    prefs["onboarded"] = False
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = ?", (json.dumps(prefs), user_id)
    )
    await hm._refresh_people()
    await hm._notify()


# -- learning again (E-273) ----------------------------------------------------------------


async def _automations_back_on(hm: HouseMind, user_id: str | None, reason: str) -> list[str]:
    """Every automation HouseMind took over goes back on first; open questions are withdrawn.
    Returns what could not be switched on."""
    failed: list[str] = []
    for entity_id in hm.automations.turned_off():
        result = await hm.restore_automation(entity_id, user_id, reason=reason)
        if not result["ok"]:
            failed.append(entity_id)
    for entity_id, status in list(hm.automations.status.items()):
        if status is AutomationStatus.OFFERED:
            await hm.automations.set_status(
                entity_id, AutomationStatus.SEEN, hm.clock.now(), user_id
            )
            await hm._resolve(f"automation:{entity_id}")
    return failed


async def relearn(hm: HouseMind, user_id: str | None) -> dict[str, Any]:
    """Forget what was learned and start watching again. Settings, house rules, rooms, people
    and the logbook stay. Nothing is forgotten while an automation could not go back on."""
    assert hm.learning is not None
    failed = await _automations_back_on(hm, user_id, "give_back")  # may be offered again later
    if failed:
        return {"ok": False, "failed": failed}
    task = hm._history_task
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    now = hm.clock.now()
    await hm.learning.forget_all(now)
    await hm.forget_all_proof()
    for table in LEARNED_ELSEWHERE:
        await hm.db.execute(f"DELETE FROM {table}")  # noqa: S608 – a fixed name
    await hm.db.execute("UPDATE proposals SET status = 'expired' WHERE status = 'open'")
    await hm._expire_proposal_notifications(now)
    for key in ("history_read_at", "history_days", "history_entities"):
        await hm.db.set_setting(key, None)
    await hm.db.set_setting("relearned_at", now.isoformat())
    if hm.learning_allowed:
        await hm.db.set_setting("learning_since", now.isoformat())
    hm.history_status.state, hm.history_status.read_at = "idle", None
    hm.history_status.progress = hm.history_status.days = 0.0
    from housemind.brain import history

    await history.load(hm)
    if hm.learning_allowed and await hm.setting("history_learning") is not False:
        hm.start_history()
    _LOGGER.info("Learning again: what HouseMind learned is forgotten")
    hm._refresh_orb()
    await hm._notify()
    return {"ok": True, "failed": []}


# -- the factory settings (E-274) ----------------------------------------------------------


def the_word(hm: HouseMind, lang: str) -> str:
    return hm.languages.text(lang, WORD_KEY)


async def remove_link(hm: HouseMind) -> bool:
    """Take HouseMind's integration out of Home Assistant: its entry (and with it the device
    Vesta and the Assist agent) and the files HouseMind placed itself. A demo house's memory
    leaves the App's link alone. True when nothing of it is left."""
    app_data = hm.paths.app_data
    if app_data is not None and hm.paths.data.resolve() != app_data.resolve():
        return True
    await hm.withdraw_announcements()  # no announcement with an old key stays (20.23)
    own = (await hm._integration_record()).get("pipeline")  # Vesta's own pipeline (E-280)
    ok = await hm.ha.unlink_housemind(own if isinstance(own, str) else None)
    target = hm._integration_target()
    if target is not None and target.exists() and await hm._placed_by_us(target):
        await asyncio.to_thread(shutil.rmtree, target, True)
    record = hm._integration_file()
    if record is not None:
        record.unlink(missing_ok=True)
    return ok


async def factory_reset(hm: HouseMind, word: str, lang: str, user_id: str | None) -> dict[str, Any]:
    """After the right word: give everything back, take the link away, and ask for a fresh
    start (``restart_requested``); the memory is erased by ``start_over``."""
    if not isinstance(word, str) or word.strip().casefold() != the_word(hm, lang).casefold():
        return {"ok": False, "reason": "word"}
    failed = await _automations_back_on(hm, user_id, "give_back")
    if failed:  # nothing is erased while HouseMind still holds an automation off
        return {"ok": False, "reason": "give_back", "failed": failed}
    given = await hm.give_back(True, user_id, unlink=False)
    if not given["ok"]:  # what Home Assistant netjes switched off must be back on first (B-243)
        return {"ok": False, "reason": "give_back", "failed": given["failed"]}
    linked = await remove_link(hm)
    if not linked:
        _LOGGER.warning("The HouseMind integration could not be removed from Home Assistant")
    _LOGGER.info("Back to the factory settings: HouseMind starts again")
    hm.reset_requested = True
    hm.restart_requested.set()
    return {"ok": True, "link": linked}


def wipe(paths: Paths) -> None:
    """HouseMind's own memory goes: the database, its copies from before an update, the daily
    snapshots of what it learned, and the AI's keys (QA 2, M-5: "Dan verdwijnt alles").
    Knowledge updates, the App's options and the own files in /config/housemind stay."""
    for name in DATABASE:
        (paths.data / name).unlink(missing_ok=True)
    for copy in [*paths.data.glob("housemind.v*.bak"), paths.data / "housemind.pre-migration"]:
        copy.unlink(missing_ok=True)
    shutil.rmtree(paths.data / SECRETS, ignore_errors=True)
    shutil.rmtree(paths.data / "snapshots", ignore_errors=True)


async def start_over(old: HouseMind, ha: HomeAssistant, autorun: bool = True) -> HouseMind:
    """Stop, erase and start a fresh HouseMind – as after a new installation (E-274)."""
    from housemind.runtime import HouseMind

    await old.stop()
    await asyncio.to_thread(wipe, old.paths)
    fresh = HouseMind(old.clock, ha, old.paths, old._default_pets)
    await fresh.start(autorun=autorun)
    return fresh


# -- a readable copy first (E-274) ---------------------------------------------------------
