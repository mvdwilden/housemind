"""Help is the manual (E-506, B-432), from the user's side. The administrator's wish of
12-11-2026: "Als je op het help-icoon klikt, ga je al meteen naar de uitgebreide handleiding
pop-up, en ik wil dat je een nieuw icoontje maakt met rondleiding. Daarmee vervalt de oude versie
van de help pop-up." The short questions per level are not shown any more, so every place that
pointed at one now points at its part of the manual: Vesta's Lees meer, and what she says in
Assist. Each test fails without it; what it checks is in its docstring."""

from __future__ import annotations

from pathlib import Path

import pytest

from housemind.brain.library import Library, manual_parts
from housemind.i18n.catalog import load_languages
from tests.test_vesta import _house

ROOT = Path(__file__).resolve().parents[1]


def test_every_help_question_has_its_part_of_the_manual() -> None:
    """Five Help questions had no part of the manual (Netwerk twice, the curtains in the
    bedroom, a device added, the stands of a lamp): Lees meer went to the Help question, which
    is no longer shown. Now each one has its part – a part with text, in every language."""
    languages = load_languages([ROOT / "knowledge" / "languages"])
    library = Library.build(languages, [ROOT / "knowledge"], "Vesta")
    missing = []
    for lang in ("nl", "en", "de", "fr"):
        ids = {part_id for part_id, _, _ in manual_parts(library.manuals[lang])}
        for piece in library.pieces[lang]:
            if piece.source != "help":
                continue
            place = library.place(piece)
            if place is None or place[0] not in ids:
                missing.append(f"{lang} {piece.key}: {piece.title}")
    assert not missing, missing


@pytest.mark.parametrize(
    ("question", "part"),
    [
        ("Wat zie ik bij Netwerk?", "niveaus-een-ander-beeld-van-het-huis"),
        ("Hoe stel ik de standen van een lamp in?", "standen"),
    ],
)
async def test_lees_meer_goes_to_the_manual_never_to_a_help_question(
    tmp_path: Path, question: str, part: str
) -> None:
    """On Expert, "Wat zie ik bij Netwerk?" gave Lees meer to the Help question itself – a list
    that is gone now. Now it goes to the part of the manual, with that part's own title; in
    Assist she names that title too, not the Help question."""
    hm, _ha, _clock = await _house(tmp_path)
    reply = await hm.talk(question, "nl", 4, "demo-user-1")
    title = next(p.title for p in hm.library.pieces["nl"] if p.key == f"manual:{part}")
    assert reply.get("more") == {"kind": "manual", "id": part, "title": title}, reply
    spoken = await hm.talk(question, "nl", 4, "demo-user-1", "", "assist")
    assert "more" not in spoken and f"‘{title}’" in spoken["text"], spoken
    await hm.stop()
