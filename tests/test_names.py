"""Plain names for devices (E-248, B-93), from the user's side: Vesta says "de lamp in de
slaapkamer", never "Anna slaapkamer lamp 1". Each test fails without the plain names."""

from __future__ import annotations

from pathlib import Path

from housemind.brain.names import Names, load_lexicons, sentence_case, tidy
from housemind.core.model import Area, Device, Entity, HouseModel

ROOT = Path(__file__).resolve().parents[1]
LEXICONS = load_lexicons([ROOT / "knowledge"])


def _house(*things: tuple[str, str, str, str | None, str | None]) -> HouseModel:
    """(entity_id, name, area name, device id, device class) – areas made from their names."""
    model = HouseModel()
    for entity_id, name, area, device_id, device_class in things:
        area_id = area.lower().replace(" ", "_")
        model.areas.setdefault(area_id, Area(area_id, area))
        if device_id:
            model.devices.setdefault(device_id, Device(device_id, name, area_id))
        model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", name, device_id, area_id, device_class
        )
    return model


def test_a_lamp_with_a_person_and_a_number_is_the_lamp_in_the_room() -> None:
    """The administrator's example: "Anna slaapkamer lamp één" is "de lamp in de slaapkamer"."""
    model = _house(("light.ml1", "Anna slaapkamer lamp één", "Slaapkamer", "d1", None))
    names = Names(model, LEXICONS, people=["Anna van der Berg"])
    assert names.device("light.ml1", "nl") == "de lamp in de slaapkamer"


def test_two_cameras_with_a_postcode_are_told_apart_by_their_place() -> None:
    """The administrator's example: no postcode, just "op het dak" or "in de tuin"."""
    model = _house(
        ("camera.a", "Camera Dak 1234 AB", "Buiten", "c1", None),
        ("camera.b", "Camera Tuin 1234 AB", "Buiten", "c2", None),
    )
    names = Names(model, LEXICONS)
    assert names.device("camera.a", "nl") == "de camera op het dak"
    assert names.device("camera.b", "nl") == "de camera in de tuin"
    german = _house(
        ("camera.a", "Kamera Dach", "Draußen", "c1", None),
        ("camera.b", "Kamera Garten", "Draußen", "c2", None),
    )
    names = Names(german, LEXICONS)
    assert names.device("camera.a", "de") == "die Kamera auf dem Dach"
    assert names.device("camera.b", "de", "dat") == "der Kamera im Garten"
    english = _house(
        ("camera.a", "Camera Roof", "Outside", "c1", None),
        ("camera.b", "Garden Camera 4 SW1A 1AA", "Outside", "c2", None),
    )
    names = Names(english, LEXICONS)
    assert names.device("camera.a", "en") == "the camera on the roof"
    assert names.device("camera.b", "en") == "the garden camera outside"


def test_one_camera_needs_no_distinction_and_a_brand_goes() -> None:
    model = _house(
        ("camera.a", "Camera Dak 1234 AB", "Buiten", "c1", None),
        ("light.desk", "Philips Hue Bureaulamp", "Werkkamer", "d3", None),
    )
    names = Names(model, LEXICONS, brands={"d3": {"philips", "hue", "white"}})
    assert names.device("camera.a", "nl") == "de camera buiten"
    assert names.device("light.desk", "nl") == "de bureaulamp in de werkkamer"


def test_more_of_the_same_without_a_word_to_tell_them_apart() -> None:
    model = _house(
        ("light.l1", "Lamp 1", "Woonkamer", "d1", None),
        ("light.l2", "Lamp 2", "Woonkamer", "d2", None),
        ("light.left", "Lamp links", "Slaapkamer", "d3", None),
        ("light.right", "Lamp rechts", "Slaapkamer", "d4", None),
    )
    names = Names(model, LEXICONS)
    assert names.device("light.l1", "nl") == "een van de lampen in de woonkamer"
    assert names.device("light.left", "nl") == "de lamp links in de slaapkamer"


def test_articles_and_places_per_language() -> None:
    model = _house(
        ("light.a", "Kantoor", "Kantoor", "d1", None),
        ("cover.c", "Woonkamer", "Woonkamer", "d2", "curtain"),
        ("light.b", "Office Desk Lamp", "Office", "d3", None),
        ("light.c", "Schlafzimmer Lampe", "Schlafzimmer", "d4", None),
        ("light.d", "Lampe chambre", "Chambre", "d5", None),
        ("binary_sensor.s", "Détecteur de fumée entrée", "Entrée", "d6", "smoke"),
    )
    names = Names(model, LEXICONS)
    assert names.device("light.a", "nl") == "de lamp in het kantoor"
    assert names.device("cover.c", "nl") == "het gordijn in de woonkamer"
    assert names.device("light.b", "en") == "the desk lamp in the office"
    assert names.device("light.c", "de") == "die Lampe im Schlafzimmer"
    assert names.device("light.c", "de", "dat") == "der Lampe im Schlafzimmer"
    assert names.device("light.c", "de", "acc") == "die Lampe im Schlafzimmer"
    assert names.device("light.d", "fr") == "la lampe dans la chambre"
    assert names.device("binary_sensor.s", "fr") == "le détecteur de fumée dans l'entrée"
    assert tidy("fr", "La pile de le détecteur") == "La pile du détecteur"
    assert tidy("de", "Draußen bei dem Garten") == "Draußen beim Garten"
    assert sentence_case("de wasmachine is klaar. de droger ook.") == (
        "De wasmachine is klaar. De droger ook."
    )


def test_several_at_once_per_kind_and_room() -> None:
    model = _house(
        ("light.k", "Keuken lamp", "Keuken", "d1", None),
        ("light.w", "Woonkamer lamp", "Woonkamer", "d2", None),
        ("cover.w", "Rolluik", "Woonkamer", "d3", "shutter"),
    )
    names = Names(model, LEXICONS)
    said = names.group(["light.k", "light.w", "cover.w"], "nl")
    assert said == "de lampen in de keuken en de woonkamer en het rolluik in de woonkamer"


def test_a_name_from_an_old_message_comes_back_plain() -> None:
    """Messages keep the HA name (for lists); Vesta finds the device again and says it plain."""
    model = _house(("binary_sensor.rm", "Rookmelder gang", "Gang", "d1", "smoke"))
    names = Names(model, LEXICONS)
    assert names.by_name("Rookmelder gang", "nl")["device"] == "de rookmelder in de gang"
    assert names.area_forms("Gang", "de")["area_dat"] == "dem Gang"


# -- from the user's side: what Vesta says, and what the screens keep -------------------


async def test_vesta_says_a_device_plainly_and_the_list_keeps_its_name(tmp_path: Path) -> None:
    """Vesta said the exact name from HA ("Gang deur"); now she says "de deur in de gang", in
    German "die Tür im Gang". The list of messages keeps the HA name, to find it there."""
    from housemind.brain.notify import Kind
    from housemind.server import views
    from tests.test_vesta import _house as _vesta_house
    from tests.test_vesta import _raise

    hm, _ha, _clock = await _vesta_house(tmp_path)
    await _raise(hm, Kind.IMPORTANT, "open:door", "notify.open_when_away", {"device": "Gang deur"})
    line = (await hm.vesta_line("demo-user-1", 2, True, "nl")).text
    assert "de deur in de gang" in line.lower() and "Gang deur" not in line
    german = (await hm.vesta_line("demo-user-1", 2, True, "de")).text
    assert "Tür im Gang" in german and "Gang deur" not in german
    listed = await views.notifications(hm, "nl", admin=True)
    assert any("Gang deur" in n["text"] for n in listed)
    await hm.stop()


async def test_her_why_uses_plain_names_and_screens_keep_ha_names(tmp_path: Path) -> None:
    """ "Waarom?" to Vesta: "Ik heb de plafondlamp in de woonkamer aangedaan"; the brain
    screen keeps "Woonkamer plafondlamp", the name in HA."""
    from housemind.brain.explain import Explainer
    from tests.test_explain import T0, _decision, _explainer

    explainer = await _explainer(tmp_path)
    vesta = Explainer(
        explainer.languages, explainer.model, explainer.learning, Names(explainer.model, LEXICONS)
    )
    said = vesta.explain(_decision(), 2, "nl", T0)[0]
    assert said.startswith("Ik heb de plafondlamp in de woonkamer aangedaan"), said
    shown = explainer.explain(_decision(), 2, "nl", T0)[0]
    assert shown.startswith("Ik heb Woonkamer plafondlamp aangedaan"), shown


def test_vesta_never_says_ids_numbers_or_technical_words() -> None:
    """E-250: in every demo house and language, a plain name has no HA id and no number, and
    her own words have no technical words such as "entiteit" or "integratie"."""
    import re

    from housemind.brain.voice import Phrases
    from housemind.core.clock import SimulatedClock
    from housemind.demo.houses import DEMO_HOUSES, load_demo_house
    from tests.test_explain import T0

    for house in DEMO_HOUSES:
        model = load_demo_house(house, SimulatedClock(T0), "nl").ha.model
        names = Names(model, LEXICONS)
        for entity_id in model.entities:
            for lang in ("nl", "en", "de", "fr"):
                said = names.device(entity_id, lang)
                assert not re.search(r"\b[a-z_]+\.[a-z0-9_]+\b", said), (entity_id, lang, said)
                assert not re.search(r"\d", said), (entity_id, lang, said)
    technical = re.compile(
        r"entit|integrati|intégration|domein|domain|domäne|entity_id|binary_sensor", re.I
    )
    phrases = Phrases.load([ROOT / "knowledge"])
    for lang, bank in phrases.banks.items():
        for topic, forms in bank.items():
            if not isinstance(forms, dict):
                continue
            for lines in forms.values():
                for line in lines:
                    assert not technical.search(line), (lang, topic, line)


def test_rooms_named_after_people_like_in_the_real_house() -> None:
    """In the real house: "Slaapkamer Anna", "Slaapkamer Bram" and "Slaapkamer Lotte";
    an account "Slaapkamer tablet" made "slaapkamer" a name, and Vesta said "in de lotte".
    A room word is never a name; a name stays out unless rooms would then sound the same."""
    model = _house(
        ("light.m", "Anna slaapkamer lamp één", "Slaapkamer Anna", "d1", None),
        ("light.s", "Lamp", "Slaapkamer Bram", "d2", None),
        ("light.t", "Plafondlamp", "Slaapkamer Lotte", "d3", None),
        ("light.k", "Lamp", "Kantoor", "d4", None),
        ("light.h", "Lamp", "Hal boven", "d5", None),
    )
    people = ["Anna van der Berg", "Bram", "Slaapkamer tablet"]
    names = Names(model, LEXICONS, people=people)
    assert names.device("light.m", "nl") == "de lamp in de slaapkamer van Anna"
    assert names.device("light.s", "nl") == "de lamp in de slaapkamer van Bram"
    assert names.device("light.t", "nl") == "de plafondlamp in de slaapkamer Lotte"
    assert names.device("light.k", "nl") == "de lamp in het kantoor"
    assert names.device("light.h", "nl") == "de lamp in de hal boven"
    alone = Names(
        _house(("light.m", "Anna lamp", "Slaapkamer Anna", "d1", None)),
        LEXICONS,
        people=people,
    )
    assert alone.device("light.m", "nl") == "de lamp in de slaapkamer"  # only one bedroom


def test_a_lamp_named_after_the_tv_is_a_lamp() -> None:
    """Found in the real house: "TV - Lamp 2" became "one of the TVs". Two things in one name:
    the one the device is wins (a light is a lamp); the other says which."""
    model = _house(
        ("light.tv_lamp_1", "TV - Lamp 1", "Woonkamer", "d1", None),
        ("light.tv_lamp_2", "TV - Lamp 2", "Woonkamer", "d2", None),
        ("media_player.tv", "TV", "Woonkamer", "d3", None),
    )
    names = Names(model, LEXICONS)
    assert names.device("light.tv_lamp_2", "nl") == "een van de lampen in de woonkamer"
    assert names.device("light.tv_lamp_2", "en") == "one of the tv lamps in the woonkamer"
    assert names.device("media_player.tv", "nl") == "de tv in de woonkamer"
