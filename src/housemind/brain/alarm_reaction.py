"""What the house does when the alarm goes off, or at a fire (E-542 – E-547, design 97), beside
the lamps, the voice and the emergency message that were already there:

- music and television quiet, and the warning loud again every 30 seconds until it is over –
  at most five minutes (E-542);
- at a break-in a few lamps an administrator chose flash red: the lamp's own flash, or
  HouseMind calmly, at most three minutes; then they stay on, red (E-543);
- the wall tablet a red screen with what, where and when – at a break-in a camera's image and
  "Naar het alarm"; at a fire nothing that distracts (E-544);
- on the phone buttons to the camera, the alarm and the emergency number of the country, and
  the message once more after two minutes for whoever did not see it (E-545);
- at a fire the roller shutters and curtains an administrator chose go up, to get out; an air
  conditioner or heat pump that blows air goes off at smoke; the message says which outside
  doors are locked – HouseMind never opens a lock (E-546).

Never a siren (that is the alarm system's own), never a lock, door, gate or garage door open,
never the alarm off. Generic: what each device reports to Home Assistant – a lamp's colours
and flash, a media player playing, a climate device with a fan, a cover's class."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.levels import COLOUR_MODES
from housemind.safety.gate import ActionRequest, Layer, Outcome

if TYPE_CHECKING:
    from housemind.runtime import HouseMind
    from housemind.world.world import WorldState

_LOGGER = logging.getLogger(__name__)

REPEAT_EVERY = timedelta(seconds=30)  # the warning again (E-542)
REPEAT_FOR = timedelta(minutes=5)  # … at most this long
REMIND_AFTER = timedelta(minutes=2)  # the phone message once more for whoever did not see it
BLINK_FOR = timedelta(minutes=3)  # HouseMind's own flashing, at most (E-543)
BLINK_S = 1.0  # on a second, off a second: calm, never a strobe
FLASH = 8  # Home Assistant's LightEntityFeature.FLASH: the lamp flashes by itself
# What may go up at a fire, to get out (E-546): also a roller shutter – never a garage door,
# gate or door, never one in a garage or a group with one (as at the alarm, B-170).
FIRE_CLASSES = frozenset({"shutter", "blind", "shade", "curtain"})
SIGNAL_ROLES = frozenset({"hall", "living"})  # where signal lamps are suggested
# The emergency number for the button on the phone (E-545); 112 works in Europe and on most
# mobile networks elsewhere.
CALL = {"US": "911", "CA": "911", "MX": "911", "PR": "911", "AU": "000", "NZ": "111", "GB": "999"}
FIRE = ("smoke", "carbon_monoxide")
# A sensor that says where a break-in is: what went "on" in the minute before the alarm.
WHERE_CLASSES = frozenset(
    {"door", "window", "opening", "garage_door", "motion", "occupancy", "presence", "vibration"}
)
WHERE_WITHIN = timedelta(minutes=2)
AIR_MODES = frozenset({"cool", "dry", "fan_only", "heat_cool", "auto"})


@dataclass(slots=True)
class Going:
    """An alarm or a fire that is going on now: what is said again, and where."""

    kind: str  # "burglary", "smoke", "carbon_monoxide", "gas"
    topic: str  # its emergency message
    started: datetime
    where: str  # the room, when known
    said_at: datetime
    camera: str | None = None
    alarm: str | None = None
    reminded: bool = False
    repeat: bool = True  # gas: nothing that starts a device, so no voice again


def number(hm: HouseMind) -> str:
    country = (hm.config.country if hm.config else None) or ""
    return CALL.get(country.upper(), "112")


def links(
    hm: HouseMind, kind: str, camera: str | None = None, alarm: str | None = None
) -> dict[str, str]:
    """The buttons on the phone (E-545), as ``link:<n>`` = "text key|where it goes": at a
    break-in the camera and the alarm in Home Assistant; always the emergency number."""
    out: dict[str, str] = {}
    if kind == "burglary":
        if camera:
            out["link:1"] = f"notify.link.camera|entityId:{camera}"
        if alarm:
            out["link:2"] = f"notify.link.alarm|entityId:{alarm}"
    out["link:3"] = f"notify.link.call|tel:{number(hm)}"
    return out


# -- the administrator's choices ------------------------------------------------------------


def colour_lamps(hm: HouseMind) -> list[str]:
    """Lamps that can show red: a colour mode in Home Assistant, no group, nothing hidden."""
    out = []
    assert hm.world is not None
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.domain != "light" or entity.hidden or entity.disabled or entity.entity_category:
            continue
        if entity_id in hm.model.groups or entity.platform == "switch_as_x":
            continue
        modes = entity.capabilities.get("supported_color_modes")
        state = hm.world.states.get(entity_id)
        if not modes and state is not None:
            modes = state.attributes.get("supported_color_modes")
        if set(modes or ()) & COLOUR_MODES:
            out.append(entity_id)
    return out


def suggested_lamps(hm: HouseMind) -> list[str]:
    """Where signal lamps help most (E-543): in the hall and the living room – a suggestion
    only, nothing flashes until an administrator chooses."""
    return [
        e
        for e in colour_lamps(hm)
        if (a := hm.model.area_of(e)) is not None and hm.area_roles.get(a.id) in SIGNAL_ROLES
    ]


def detects_fire(hm: HouseMind) -> bool:
    """The house has a smoke or carbon monoxide detector that HouseMind knows."""
    found = hm.emergencies.emergency_for if hm.emergencies is not None else None
    return found is not None and any(
        (e := found(entity_id)) is not None and e.value in FIRE for entity_id in hm.model.entities
    )


def fire_coverings(hm: HouseMind) -> list[str]:
    """What may go up at a fire (E-546): curtains, blinds, shades and roller shutters – never a
    garage door, gate or door, nothing in a garage or in a group with one (the alarm's own
    rule, with the shutters added)."""
    from housemind.brain.security import coverings

    return coverings(hm, FIRE_CLASSES)


# -- what is done --------------------------------------------------------------------------


def signal_request(
    hm: HouseMind, lamp: str, reason: str, now: datetime, facts: dict[str, Any]
) -> ActionRequest:
    """A signal lamp red and full (E-543); with the lamp's own flash when it has one."""
    entity = hm.model.entities.get(lamp)
    data: dict[str, Any] = {"brightness_pct": 100, "color_name": "red"}
    if entity is not None and entity.supported_features & FLASH:
        data["flash"] = "long"
    return ActionRequest(
        Layer.EMERGENCY, lamp, "light.turn_on", data, reason, created_at=now, facts=facts
    )


def flashes_itself(hm: HouseMind, lamp: str) -> bool:
    entity = hm.model.entities.get(lamp)
    return entity is not None and bool(entity.supported_features & FLASH)


def media_requests(
    hm: HouseMind, reason: str, now: datetime, facts: dict[str, Any], area_id: str | None = None
) -> list[ActionRequest]:
    """Music and television quiet (E-542): every media player that is playing – or only the
    ones of one room, for Test het huis."""
    assert hm.world is not None
    out = []
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.domain != "media_player" or entity.hidden or entity.disabled:
            continue
        if entity_id in hm.model.groups:
            continue
        state = hm.world.states.get(entity_id)
        if state is None or state.state != "playing":
            continue
        if area_id is not None and ((a := hm.model.area_of(entity_id)) is None or a.id != area_id):
            continue
        out.append(
            ActionRequest(
                Layer.EMERGENCY,
                entity_id,
                "media_player.media_pause",
                {},
                reason,
                created_at=now,
                facts=facts,
            )
        )
    return out


def fire_requests(hm: HouseMind, smoke: bool, reason: str, now: datetime) -> list[ActionRequest]:
    """At a fire (E-546): the chosen coverings up to get out; at smoke an air conditioner or
    heat pump that blows air off – it would blow the smoke around."""
    assert hm.world is not None
    out = [
        ActionRequest(Layer.EMERGENCY, c, "cover.open_cover", {}, reason, created_at=now)
        for c in fire_coverings(hm)
        if c in hm.security.fire_covers
        and (s := hm.world.states.get(c)) is not None
        and s.state not in ("open", "opening")
    ]
    if not smoke:
        return out
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.domain != "climate" or entity_id in hm.model.groups or entity.disabled:
            continue
        state = hm.world.states.get(entity_id)
        if state is None or state.state in ("off", "unavailable", "unknown"):
            continue
        modes = set(state.attributes.get("hvac_modes") or ())
        blows = bool(state.attributes.get("fan_modes")) or "fan_only" in modes
        if (
            blows
            and "off" in modes
            and (state.state in AIR_MODES or state.attributes.get("fan_modes"))
        ):
            out.append(
                ActionRequest(
                    Layer.EMERGENCY,
                    entity_id,
                    "climate.set_hvac_mode",
                    {"hvac_mode": "off"},
                    reason,
                    created_at=now,
                )
            )
    return out


def locked_doors(hm: HouseMind) -> list[str]:
    """Locks that are locked now (E-546): the message says so – HouseMind never opens one."""
    assert hm.world is not None
    out = []
    for entity_id, entity in sorted(hm.model.entities.items()):
        if entity.domain != "lock" or entity.hidden or entity.disabled or entity.entity_category:
            continue
        state = hm.world.states.get(entity_id)
        if state is not None and state.state == "locked":
            out.append(entity.name or entity_id)
    return out


def where_it_began(hm: HouseMind, now: datetime) -> str:
    """The room of the door, window or motion that went "on" in the two minutes before the
    alarm went off – generic, from Home Assistant's own classes; empty when nothing did."""
    assert hm.world is not None
    best: tuple[datetime, str] | None = None
    for entity_id, state in hm.world.states.items():
        if not entity_id.startswith("binary_sensor.") or state.state != "on":
            continue
        entity = hm.model.entities.get(entity_id)
        if entity is None or entity.device_class not in WHERE_CLASSES:
            continue
        at = state.last_changed
        if at is None or now - at > WHERE_WITHIN or at > now:
            continue
        area = hm.model.area_of(entity_id)
        if area is not None and (best is None or at > best[0]):
            best = (at, area.name)
    return best[1] if best else ""


async def submit_all(hm: HouseMind, requests: list[ActionRequest], keep: bool = True) -> list[Any]:
    assert hm.gate is not None
    done = []
    for request in requests:
        decision = await hm.gate.submit(request)
        if keep:
            await hm._record(decision)
        done.append(decision)
    return done


# -- going on --------------------------------------------------------------------------------


async def begin(
    hm: HouseMind,
    kind: str,
    topic: str,
    reason: str,
    snapshot: WorldState,
    where: str = "",
    camera: str | None = None,
    alarm: str | None = None,
    blink: list[str] | None = None,
    facts: dict[str, Any] | None = None,
) -> None:
    """It began (E-542 – E-544): media quiet (not at gas), the warning again later, the
    tablet's screen, and the signal lamps flashing."""
    now = snapshot.now
    gas = kind == "gas"
    if not gas:
        await submit_all(hm, media_requests(hm, reason, now, dict(facts or {})))
    hm.security.going[topic] = Going(kind, topic, now, where, now, camera, alarm, repeat=not gas)
    for lamp in blink or ():
        if lamp not in hm.security.blinking:
            hm.security.blinking.add(lamp)
            hm._beside(_blink(hm, lamp, topic, now + BLINK_FOR), "alarm-blink")


async def over(hm: HouseMind, topic: str) -> None:
    """It is over: no more warning, the tablet's screen goes, the flashing stops."""
    hm.security.going.pop(topic, None)


def _words(hm: HouseMind, going: Going, lang: str) -> str:
    t = hm.languages.text
    kind = "alarm" if going.kind == "burglary" else going.kind
    if going.where:
        return t(lang, f"say.{kind}_again", where=going.where, area=going.where)
    return t(lang, f"say.{kind}_again_house")


async def tick(hm: HouseMind, snapshot: WorldState) -> None:
    """Every tick: the warning again every half minute (at most five minutes), and after two
    minutes the phone message once more for whoever did not see it (E-542, E-545)."""
    now = snapshot.now
    lang = hm._language()
    for going in list(hm.security.going.values()):
        if (
            going.repeat
            and now - going.started < REPEAT_FOR
            and now - going.said_at >= REPEAT_EVERY
        ):
            going.said_at = now
            text = _words(hm, going, lang)
            for area_id in list(hm.speech.devices):
                await hm.say(snapshot, area_id, text, "emergency")
        if not going.reminded and now - going.started >= REMIND_AFTER:
            going.reminded = True
            assert hm.router is not None
            await hm.router.remind(going.topic, hm._people, now, snapshot.local)


async def _blink(hm: HouseMind, lamp: str, topic: str, until: datetime) -> None:
    """HouseMind flashes a signal lamp itself (E-543): a second off, a second red, until the
    alarm is over or three minutes passed – then it stays on, red. Each step through the gate;
    only the first and last are kept, not a line per flash."""
    assert hm.gate is not None
    try:
        on = True
        while topic in hm.security.going and hm.clock.now() < until:
            await hm.clock.sleep(BLINK_S)
            if topic not in hm.security.going:
                break
            on = not on
            now = hm.clock.now()
            request = (
                signal_request(hm, lamp, topic, now, {"alarm": topic.removeprefix("alarm:")})
                if on
                else ActionRequest(
                    Layer.EMERGENCY,
                    lamp,
                    "light.turn_off",
                    {},
                    topic,
                    created_at=now,
                    facts={"alarm": topic.removeprefix("alarm:")},
                )
            )
            decision = await hm.gate.submit(request)
            if decision.outcome is not Outcome.EXECUTED:
                break
        if topic in hm.security.going and not on:
            now = hm.clock.now()
            await hm.gate.submit(
                signal_request(hm, lamp, topic, now, {"alarm": topic.removeprefix("alarm:")})
            )
    finally:
        hm.security.blinking.discard(lamp)


# -- the wall tablet -----------------------------------------------------------------------


def screen(hm: HouseMind, lang: str) -> dict[str, Any] | None:
    """The wall tablet while it goes on (E-544): the worst first – fire, then a break-in, then
    gas. A break-in shows a camera's live image straight from Home Assistant and the button to
    the alarm; a fire shows nothing that distracts."""
    order = {"smoke": 0, "carbon_monoxide": 1, "burglary": 2, "gas": 3}
    going = sorted(hm.security.going.values(), key=lambda g: order.get(g.kind, 9))
    if not going or hm.world is None:
        return None
    g = going[0]
    t = hm.languages.text
    tz = hm.world.tz
    out: dict[str, Any] = {
        "kind": g.kind,
        "title": t(lang, f"ui.tablet.alarm.{g.kind}"),
        "where": g.where,
        "at": g.started.astimezone(tz).strftime("%H:%M"),
        "advice": t(lang, f"ui.tablet.alarm.{g.kind}_advice"),
    }
    if g.kind == "burglary":
        state = hm.world.states.get(g.camera) if g.camera else None
        token = state.attributes.get("access_token") if state is not None else None
        out["url"] = (
            f"/api/camera_proxy_stream/{g.camera}?token={token}" if g.camera and token else None
        )
        out["alarm"] = g.alarm
    return out
