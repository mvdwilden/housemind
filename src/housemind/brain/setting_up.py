"""Setting HouseMind up (E-277 – E-282, B-125). After a new installation and after the factory
settings HouseMind stands still – emergencies keep working – until an administrator chooses
"Inrichten": the introduction, the link with Home Assistant, Vesta's name and voice with her
own Assist pipeline, the AI, and done. It goes on when that is done and the history is read.

The language of the house, and so of her pipeline, is the language Home Assistant shows the
administrator who sets up: Dutch, English, German or French, else English (E-281)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import start_over

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

_LOGGER = logging.getLogger(__name__)

STEPS = ("intro", "link", "vesta", "ai", "finish")
LANGUAGES = ("nl", "en", "de", "fr")
PENDING, DONE = "pending", "done"
READ_STALL = timedelta(
    minutes=3
)  # Klaar waits for the reading unless it stood still this long (E-396)


def house_language(code: str | None) -> str:
    """The language HA shows the administrator who sets up – one of the four, else English."""
    base = (code or "").split("-")[0].lower()
    return base if base in LANGUAGES else "en"


async def state(hm: HouseMind) -> str:
    """ "pending" (not set up), the step the administrator is at, or "done"."""
    value = await hm.db.get_setting("setup")
    return value if value == PENDING or value in STEPS else DONE


def reading(hm: HouseMind) -> bool:
    task = hm._history_task
    return hm.history_status.state == "reading" or (task is not None and not task.done())


async def begin(hm: HouseMind, user_id: str, ha_language: str | None) -> bool:
    """The administrator starts setting up: the house speaks their HA language (E-281), and
    their own introduction starts from the beginning."""
    if await state(hm) != PENDING:
        return False
    await hm.set_house_language(house_language(ha_language))
    await hm.db.set_setting("setup", STEPS[0])
    await start_over.intro_again(hm, user_id)
    await hm.db.set_setting("intro_again", None)  # the first introduction: nothing to show
    _LOGGER.info("Setting up HouseMind")
    return True


async def goto(hm: HouseMind, step: str) -> bool:
    """A step forward or back (E-278); kept, so a next time starts there."""
    if step not in STEPS or await state(hm) in (PENDING, DONE):
        return False
    await hm.db.set_setting("setup", step)
    await hm._notify()
    return True


async def read_wait(hm: HouseMind) -> int:
    """How many seconds Klaar still waits for the reading (E-396): until it is done, however
    long – it was at most three minutes (E-282). Only when the reading stood still for three
    minutes can Klaar, and it reads on in the background. Counts again whenever it goes on."""
    if not reading(hm):
        return 0
    status = hm.history_status
    if status.moved_at is None:
        status.moved_at = hm.clock.now()  # first seen reading
    return max(0, int((status.moved_at + READ_STALL - hm.clock.now()).total_seconds()))


def stalled(hm: HouseMind) -> bool:
    """The reading stands still: no step on for three minutes (E-396)."""
    status = hm.history_status
    moved = status.moved_at
    return reading(hm) and moved is not None and hm.clock.now() - moved >= READ_STALL


async def finish(hm: HouseMind) -> str:
    """Done – after the reading, or once it stood still for three minutes (E-396); the
    reading then goes on and its end turns HouseMind on, and Vesta tells what it found.
    Returns "done", "reading" or "not_now"."""
    if await state(hm) in (PENDING, DONE):
        return "not_now"
    if await read_wait(hm) > 0:
        return "reading"
    await hm.db.set_setting("setup", DONE)
    _LOGGER.info("HouseMind is set up")
    await start_over.introduced(hm)
    await hm._notify()
    return DONE


# -- Vesta's own Assist pipeline (E-280) ----------------------------------------------------


def agent(hm: HouseMind) -> str | None:
    """HouseMind's own Assist agent in HA, once the link is there."""
    return next(
        (
            e.entity_id
            for e in hm.model.entities.values()
            if e.domain == "conversation" and e.platform == "housemind"
        ),
        None,
    )


async def _own(hm: HouseMind) -> str | None:
    own = (await hm._integration_record()).get("pipeline")
    return own if isinstance(own, str) else None


def engine_place(engine: str | None, places: dict[str, list[str]]) -> str:
    """Where a speech engine works: 'local', 'cloud' or 'unknown' (knowledge/speech_engines)."""
    if not engine:
        return "unknown"
    name = engine.lower()
    if any(word in name for word in places.get("cloud", [])):
        return "cloud"
    if any(word in name for word in places.get("local", [])):
        return "local"
    return "unknown"


async def speech_places(hm: HouseMind) -> dict[str, Any]:
    """Which engines hear and speak for Vesta, and whether her voice leaves the house
    (20.19): from her own pipeline when she has one, else from what she would choose."""
    own, pipelines = await _own(hm), await hm.ha.assist_pipelines()
    mine = next((p for p in pipelines if p.get("id") == own), None) if own else None
    me = agent(hm)
    if mine is not None:
        hears, speaks = mine.get("stt_engine"), mine.get("tts_engine")
    elif me is not None:
        chosen = await _settings(hm, me)
        hears, speaks = chosen.get("stt_engine"), chosen.get("tts_engine")
    else:
        voice = await hm.vesta_voice()
        hears, speaks = None, voice.engine if voice else None
    places = hm.speech_engines
    found = {"hears": hears, "speaks": speaks}
    return {
        **found,
        "cloud": sorted(str(e) for e in found.values() if engine_place(e, places) == "cloud"),
        # In the house only when there is an engine and every one is known to work there.
        "local": any(found.values())
        and all(engine_place(e, places) == "local" for e in found.values() if e),
    }


async def pipeline_status(hm: HouseMind) -> dict[str, Any]:
    """Hers ("ours"), someone's with HouseMind's agent ("other", may be taken over), none
    yet ("none"), or no agent yet ("no_agent": the link comes first)."""
    own, pipelines, me = await _own(hm), await hm.ha.assist_pipelines(), agent(hm)
    mine = next((p for p in pipelines if p.get("id") == own), None) if own else None
    if mine is not None:
        return {
            "state": "ours",
            "name": mine.get("name"),
            "language": mine.get("language"),
            "voice": mine.get("tts_voice"),
        }
    if me is None:
        return {"state": "no_agent"}
    other = next((p for p in pipelines if p.get("conversation_engine") == me), None)
    return {"state": "other", "name": other.get("name")} if other else {"state": "none"}


async def _settings(hm: HouseMind, me: str) -> dict[str, Any]:
    from housemind.parts.vesta import voice_locale  # the runtime imports this module

    language = hm._language()
    voice = await hm.voice_for(language, await hm.vesta_voice())
    country = hm.config.country if hm.config else None
    stt = await hm.ha.stt_engines(language)
    hears = next(((e, voice_locale(language, codes, country)) for e, codes in stt), (None, None))
    wake = await _wake_word(hm, hm.vesta_name(language))
    return {
        "name": hm.vesta_name(language),
        "language": language,
        "wake_word_entity": wake[0],
        "wake_word_id": wake[1],
        "conversation_engine": me,
        "stt_engine": hears[0],
        "stt_language": hears[1],
        "tts_engine": voice.engine if voice else None,
        "tts_language": voice.language if voice else None,
        "tts_voice": voice.voice if voice else None,
    }


async def _wake_word(hm: HouseMind, name: str) -> tuple[str | None, str | None]:
    """ "Hey Vesta" (E-232): a wake word HA already offers with her name in it – HouseMind
    makes none and never picks another; without one the pipeline has none of its own."""
    wanted = name.strip().lower()
    states = hm.world.states if hm.world else {}
    engines = {e for e in [*hm.model.entities, *states] if e.startswith("wake_word.")}
    for entity in sorted(engines):
        for word_id, word in await hm.ha.wake_words(entity):
            if wanted and (wanted in word.lower() or wanted in word_id.lower()):
                return entity, word_id
    return None, None


async def make_pipeline(hm: HouseMind, adopt: bool = False) -> bool:
    """Make her pipeline, or bring it up to date – after the administrator's yes. ``adopt``:
    take over a pipeline that already has HouseMind's agent; else one of her own."""
    me = agent(hm)
    if me is None:
        return False
    own, pipelines = await _own(hm), await hm.ha.assist_pipelines()
    if own is not None and not any(p.get("id") == own for p in pipelines):
        own = None  # someone removed it in HA: a new one
    if own is None and adopt:
        other = next((p for p in pipelines if p.get("conversation_engine") == me), None)
        own = str(other["id"]) if other else None
    made = await hm.ha.vesta_pipeline(own, await _settings(hm, me))
    if made is None:
        return False
    record = await hm._integration_record()
    record["pipeline"] = made
    await hm._save_integration_record(record)
    hm.speech.voice = await hm.voice_for(None, await hm.vesta_voice())
    await hm._notify()
    return True


async def refresh_pipeline(hm: HouseMind) -> None:
    """Her name, the language or her voice changed: her own pipeline follows (E-280)."""
    if await _own(hm) is not None and agent(hm) is not None:
        await make_pipeline(hm)
