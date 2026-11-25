"""Less motion follows only the device (E-319, wish from the real house, 21-10-2026): "Beweging"
in Mijn voorkeuren › Dit apparaat was taken for how fast Vesta moves; it was the reduced-motion
choice, and "Zelfde als mijn apparaat" already did what the device says. The choice is gone;
HouseMind follows the device's own setting, everywhere (E-46). Each test fails without it;
what it checks is its docstring."""

from __future__ import annotations

import re
from pathlib import Path

from housemind.i18n.catalog import load_languages
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


def test_there_is_no_motion_choice_any_more() -> None:
    """No "Beweging" in Mijn voorkeuren: the server neither keeps nor sends it, and no
    language names it in the choices of this device."""
    assert "reduced_motion" not in views.PREF_CHOICES
    languages = load_languages([ROOT / "knowledge" / "languages"]).languages
    for code in ("nl", "en", "de", "fr"):
        texts = languages[code]
        assert not [k for k in texts if k.startswith("ui.prefs.motion")], code
        hint = texts["ui.prefs.group.device_hint"].lower()
        assert not re.search(r"beweging|motion|bewegung|mouvement", hint), code
    source = "\n".join(p.read_text() for p in FRONTEND.rglob("*.svelte"))
    assert "ui.prefs.motion" not in source and "reducedMotion" not in source


def test_less_motion_on_the_device_stills_the_whole_app() -> None:
    """The device's own "less motion" stills every animation and transition in the app – not
    only Vesta and a few bars – and nothing depends on an app choice any more."""
    css = (FRONTEND / "app.css").read_text()
    block = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    assert "animation: none !important" in block.split("}\n}")[0]
    assert "transition: none !important" in block.split("}\n}")[0]
    everything = "\n".join(
        p.read_text() for p in FRONTEND.rglob("*") if p.suffix in (".svelte", ".ts", ".css")
    )
    assert "reduce-motion" not in everything
