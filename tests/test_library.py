"""Vesta knows the Help and the manual (E-246, E-247, E-238, B-94, B-95), from the user's
side: a clear hit is answered from the Help in her words with Lees meer; what the house does not
know goes – as asked, with the best pieces of the Help – to the AI; without one she is honest
and names the closest part. Each test fails without it; what it checks is in its docstring."""

from __future__ import annotations

import json
from pathlib import Path

from housemind.brain.library import STRONG, Library, gist, manual_for, manual_parts
from housemind.i18n.catalog import load_languages
from tests.test_ai import FakeAI, enable, start
from tests.test_knowing import _said
from tests.test_vesta import _house

ROOT = Path(__file__).resolve().parents[1]
MANUAL = """<article lang="nl">
<h2 id="vesta">Vesta, de stem van je huis</h2>
<p>Vesta vertelt wat er in huis gebeurt.</p>
<h3 id="vesta-typen-of-praten">Typen of praten</h3>
<p>Je kunt met Vesta praten of typen. De microfoon werkt alleen via een veilige verbinding,
dus met https. In de Home Assistant-app gebruik je dan het externe adres.</p>
</article>"""


def _library(tmp_path: Path, with_manual: bool = False) -> Library:
    folders = [ROOT / "knowledge"]
    if with_manual:
        extra = tmp_path / "extra"
        (extra / "manual").mkdir(parents=True)
        (extra / "manual" / "nl.html").write_text(MANUAL, encoding="utf-8")
        (extra / "manual" / "help-links.yaml").write_text(
            "l2:\n  q18: vesta-typen-of-praten\n", encoding="utf-8"
        )
        folders.append(extra)
    languages = load_languages([ROOT / "knowledge" / "languages"])
    return Library.build(languages, folders, "Vesta")


def test_the_help_in_four_languages_is_searched_on_its_words(tmp_path: Path) -> None:
    """Every Help question is a piece in every language; a question finds its own answer,
    also with other word forms ("microfoons")."""
    library = _library(tmp_path)
    for lang in ("nl", "en", "de", "fr"):
        assert len([p for p in library.pieces[lang] if p.source == "help"]) >= 40
    score, piece = library.search("Hoe praat ik terug met Vesta?", "nl")[0]
    assert piece.key == "help:l2:q18" and score >= STRONG
    found = library.search("mijn microfoons werken niet", "nl")[0][1]
    assert "microfoon" in f"{found.title} {found.text}".lower()
    assert gist("Een. Twee. Drie.") == "Een. Twee."


def test_the_manual_in_the_app_is_searched_and_lees_meer_goes_there(tmp_path: Path) -> None:
    """With the manual in the app, its parts are found too, and a Help question with a part
    that tells more leads there (Lees meer)."""
    library = _library(tmp_path, with_manual=True)
    assert "nl" in library.manuals and "en" in library.manuals  # in English too (H-15)
    keys = [p.key for p in library.pieces["nl"] if p.source == "manual"]
    assert keys == ["manual:vesta", "manual:vesta-typen-of-praten"]
    help_piece = next(p for p in library.pieces["nl"] if p.key == "help:l2:q18")
    assert help_piece.more == "vesta-typen-of-praten"
    found = library.search("Werkt de microfoon met https?", "nl")
    assert any(p.key == "manual:vesta-typen-of-praten" for _, p in found)


async def test_a_clear_help_question_is_answered_from_the_help(tmp_path: Path) -> None:
    """Without an AI, "Hoe praat ik terug met Vesta?" got "Dat weet ik niet". Now: the core of
    the Help answer in her words, with Lees meer to the part of the manual that tells more – the
    Help is the manual (E-506); via Assist she says where, by that part's title."""
    hm, _ha, _clock = await _house(tmp_path)
    reply = await hm.talk("Hoe praat ik terug met Vesta?", "nl", 2, "demo-user-1")
    piece = next(p for p in hm.library.pieces["nl"] if p.key == "help:l2:q18")
    assert reply["text"] in _said("know_manual", text=gist(piece.text))
    part = next(p for p in hm.library.pieces["nl"] if p.key == f"manual:{piece.more}")
    assert reply["more"] == {"kind": "manual", "id": piece.more, "title": part.title}
    spoken = await hm.talk("Hoe praat ik terug met Vesta?", "nl", 2, "demo-user-1", "", "assist")
    assert "more" not in spoken and part.title in spoken["text"]
    await hm.stop()


async def test_what_the_house_does_not_know_goes_to_the_ai_as_asked(tmp_path: Path) -> None:
    """The question as asked, names as labels, with the best pieces of the Help (general text,
    nothing of the house); her answer says it came via the AI, with Lees meer when the AI used
    a piece of the Help."""
    hm, _, _ = await start(tmp_path)
    fake = FakeAI([{"answer": "Tik op Vesta en praat.", "about_house": True, "section": ""}])
    await enable(hm, fake)
    question = "Kan Emma in de Hobbyhoek van Emma met de microfoon praten tegen Vesta?"
    hits = hm.library.search(question, "nl")
    fake.replies[0]["section"] = hits[0][1].key
    reply = await hm.talk(question, "nl", 2, "demo-user-1")
    _system, sent, _model = fake.sent[-1]
    data = json.loads(sent.removeprefix("<data>\n").removesuffix("\n</data>"))
    assert "Emma" not in sent and "Hobbyhoek" not in sent
    assert data["question"].startswith("Kan ") and len(data["manual"]) <= 3
    assert reply["via_ai"] is True and "Tik op Vesta en praat." in reply["text"]
    assert reply["text"] in _said("know_via_ai", text="Tik op Vesta en praat.")
    assert reply["more"]["title"] == hits[0][1].title
    await hm.stop()


async def test_without_an_answer_she_is_honest(tmp_path: Path) -> None:
    """No AI and no fitting Help: she says she does not know, in her words; close to a Help
    question, she names it and offers Lees meer."""
    hm, _ha, _clock = await _house(tmp_path)
    nothing = await hm.talk("Hoeveel kost een brood bij de bakker?", "nl", 2, "demo-user-1")
    assert nothing["text"] in _said("know_unknown")
    close = await hm.talk("Kan ik een tweede wandtablet gebruiken?", "nl", 2, "demo-user-1")
    assert "more" in close
    await hm.stop()


async def test_a_question_about_pausing_never_pauses(tmp_path: Path) -> None:
    """Found while testing: "Hoe werkt de pauze?" paused HouseMind for an hour. A question
    about something is answered from the Help; "pauze" alone still pauses."""
    hm, _ha, _clock = await _house(tmp_path)
    for question, lang in (
        ("Hoe werkt de pauze?", "nl"),
        ("How does the pause work?", "en"),
        ("Wie funktioniert die Pause?", "de"),
        ("Comment fonctionne la pause ?", "fr"),
    ):
        await hm.talk(question, lang, 2, "demo-user-1")
        assert hm.paused_until() is None, question
    await hm.talk("pauze", "nl", 2, "demo-user-1")
    assert hm.paused_until() is not None
    await hm.stop()


def test_button_words_stay_button_words() -> None:
    """Found while building: the stop words for searching ended up on the Stop button."""
    languages = load_languages([ROOT / "knowledge" / "languages"])
    for lang in ("nl", "en", "de", "fr"):
        for key in ("ui.vesta.stop", "ui.vesta.done", "ui.vesta.more"):
            assert "," not in languages.text(lang, key), (lang, key)


def test_every_lees_meer_leads_to_a_part_of_the_manual() -> None:
    """help-links.yaml only names parts that exist in the manual, and only Help questions that
    exist; the manual in the app has no links to elsewhere and loads nothing from elsewhere
    (E-238). Its own small scripts may run since it is the formatted page (E-324)."""
    import re

    import yaml

    folder = ROOT / "knowledge" / "manual"
    manual = (folder / "nl.html").read_text(encoding="utf-8")
    ids = {part_id for part_id, _, _ in manual_parts(manual)}
    links = yaml.safe_load((folder / "help-links.yaml").read_text(encoding="utf-8"))
    languages = load_languages([ROOT / "knowledge" / "languages"])
    for level, questions in links.items():
        for question, section in questions.items():
            assert section in ids, (level, question, section)
            key = f"help.{level}.{question}"
            assert languages.text("nl", key) != key, key
    for forbidden in ("<link", "http://", "https://", "<script src", "@import", "<iframe"):
        assert forbidden not in manual.lower(), forbidden
    assert re.findall(r"<script[^>]*>", manual) == ["<script>"] * 2  # theme and menu


async def test_why_about_something_else_is_not_her_last_action(tmp_path: Path) -> None:
    """Found while testing: "Waarom werkt mijn microfoon niet?" got "Ik heb vandaag nog niets
    gedaan" – the "why" about her last action. Now only a short "Waarom?" means that."""
    hm, _ha, _clock = await _house(tmp_path)
    from housemind.brain.talk import intent

    assert intent("Waarom?", "nl", hm.languages) == "why"
    assert intent("waarom deed je dat", "nl", hm.languages) == "why"
    assert intent("Waarom werkt mijn microfoon niet?", "nl", hm.languages) is None
    reply = await hm.talk("Waarom werkt mijn microfoon niet?", "nl", 2, "demo-user-1")
    assert "microfoon" in reply["text"].lower() and "more" in reply
    await hm.stop()


def test_the_manual_is_also_in_english_with_the_same_parts() -> None:
    """QA 2, H-15: the manual was only in Dutch. Now it is in English too, with the same
    parts in the same order, so Lees meer and the contents work in both – also while Dutch is
    the formatted page and English still the plain text (E-324)."""
    import re

    ids = {
        lang: [
            part_id
            for part_id, _, _ in manual_parts(
                (ROOT / "knowledge" / "manual" / f"{lang}.html").read_text()
            )
        ]
        for lang in ("nl", "en")
    }
    assert ids["en"] == ids["nl"] and len(ids["nl"]) > 50
    # What a reader sees, not the class names of the page (those are Dutch in both).
    english = " ".join(
        f"{title} {text}"
        for _, title, text in manual_parts((ROOT / "knowledge" / "manual" / "en.html").read_text())
    )
    assert not re.search(r"\b(?:Huisinstellingen|Klaar voor autonomie|ruimte)\b", english)


def test_vesta_reads_the_formatted_manual_by_its_parts(tmp_path: Path) -> None:
    """The formatted manual (E-324, B-194) has a section per chapter and a div per part, with
    a menu, pictures and scripts around them. Every part comes back once, with its title and
    only its own text – the menu script ended up in the last part – and Vesta finds the part
    about waking up, with Lees meer to it."""
    manual = (ROOT / "knowledge" / "manual" / "nl.html").read_text(encoding="utf-8")
    parts = manual_parts(manual)
    ids = [part_id for part_id, _, _ in parts]
    assert len(ids) == len(set(ids)) > 100
    assert all(title for _, title, _ in parts)
    for part_id, _, text in parts:
        for code in ("function", "addEventListener", "var(--", "stroke-width"):
            assert code not in text, (part_id, code)
    library = _library(tmp_path)
    found = library.search("Hoe word ik 's ochtends rustig wakker?", "nl")
    assert any(p.key == "manual:wekken" or p.more == "wekken" for _, p in found), found


def test_her_name_is_in_the_text_and_never_in_an_id_or_a_script() -> None:
    """The name the administrator gave Vesta (E-239) goes in the manual's text and labels;
    an id stays as it is (Lees meer), and so does a script (its hash is in the CSP)."""
    source = (
        '<!doctype html><html><head><script>var vesta = "Vesta";</script></head><body>'
        '<section id="vesta" aria-label="Over Vesta"><h2>Vesta</h2><p>Vesta praat.</p>'
        "</section></body></html>"
    )
    named = manual_for(source, "Juno")
    assert '<script>var vesta = "Vesta";</script>' in named
    assert 'id="vesta" aria-label="Over Juno"><h2>Juno</h2><p>Juno praat.</p>' in named
    assert manual_for(source, "Vesta") == source
