"""One rule for a word in a name, for every language (code QA 3, punt 4) – what someone with a
French, German or Spanish name in the house notices: an accent no longer decides whether
HouseMind recognises a garage door or a room. The word lists of the languages are split once.
Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from housemind.brain.rhythm import daily_words
from housemind.core.model import Device, Entity, HouseModel
from housemind.i18n.catalog import Languages
from housemind.knowledge.words import has_word, plain
from housemind.safety.hard_limits import find_openers
from housemind.world.zones import plain as zone_plain


def test_a_garage_switch_with_an_accent_is_an_opener() -> None:
    """A relay called "Garáge deur" – a Spanish keyboard, a French name – is as protected as
    "Garagedeur": HouseMind never opens it. Before, the accent hid the word."""
    model = HouseModel()
    model.devices["relay"] = Device("relay", "Relais", None)
    model.entities["switch.relais_1"] = Entity("switch.relais_1", "r-1", "Garáge deur", "relay")
    model.entities["switch.relais_2"] = Entity("switch.relais_2", "r-2", "Pörtchen", "relay")
    found = find_openers(model, (), ("garage",), ("garage deur", "portchen"), {})
    assert found == {"switch.relais_1", "switch.relais_2"}


def test_a_short_word_is_a_whole_word_and_a_number_after_it_is_fine() -> None:
    """ "car" is never "carbon monoxide", "zon" never "zonwering" – and "gate 2", "gate2" and
    "Gâte" are all a gate: the broadest rule, the safe one for what opens."""
    assert not has_word("Carbon monoxide", ["car"], whole_up_to=4)
    assert not has_word("Zonwering woonkamer", ["zon"], whole_up_to=4)
    assert has_word("gate2", ["gate"], whole_up_to=4)
    assert has_word("Gâte_tuin", ["gate"], whole_up_to=4)
    assert has_word("Laadpaalstroom", ["laadpaal"], whole_up_to=4)


def test_rooms_and_labels_fold_names_the_same_way() -> None:
    """A room "Salle à manger" and a label "Média (TV)" read the same as they are written
    without accents; a letter outside the Latin alphabet is kept, not cut off."""
    assert zone_plain("Salle à manger") == plain("Salle a manger") == "salle a manger"
    assert plain("Média (TV)") == "media tv"
    assert zone_plain("Børneværelse") == "børneværelse"


def test_a_word_list_is_split_once_until_a_language_changes() -> None:
    """The words of every language for one key are split once; a new language file is taken
    along at once."""
    languages = Languages()
    languages.add("nl", {"daily_words.opener": "garagedeur, poort"})
    assert daily_words(languages, "opener") == ["garagedeur", "poort"]
    assert languages.daily["opener"] == ("garagedeur", "poort")
    languages.add("de", {"daily_words.opener": "Garagentor"})
    assert daily_words(languages, "opener") == ["garagedeur", "poort", "garagentor"]
