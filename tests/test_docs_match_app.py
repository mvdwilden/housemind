"""The manual and the App's documentation say what the app does (E-119, QA 2 H-12): the
Help test checked only the Help, and the manual said three corrections while the app uses
two. Now the numbers in the manual, the App documentation and the Help are the app's own."""

from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

from housemind.brain.learning import DEFAULT_THRESHOLDS, SETBACKS_FOR_STEP

ROOT = Path(__file__).resolve().parents[1]
WORDS = {"twee": 2, "drie": 3, "two": 2, "three": 3, "zwei": 2, "drei": 3, "deux": 2, "trois": 3}
CORRECTIONS = (
    r"(\d+|twee|drie|two|three|zwei|drei|deux|trois) (?:correcties|corrections|Korrekturen)"
)
PROPOSE = r"(\d+) ?% (?:om voor te stellen|to propose|zum Vorschlagen|pour proposer)"
AUTONOMOUS = (
    r"(\d+) ?% (?:om het zelf te doen|to do it (?:itself|yourself)|um es selbst zu tun"
    r"|pour le faire)"
)


def _texts() -> dict[str, str]:
    out = {}
    manual = (ROOT / "knowledge" / "manual" / "nl.html").read_text(encoding="utf-8")
    out["manual nl"] = html.unescape(re.sub(r"<[^>]+>", " ", manual))
    out["App docs"] = (ROOT / "docs" / "app" / "DOCS.md").read_text(encoding="utf-8")
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        out[f"help {lang}"] = " ".join(
            str(a) for level in texts["help"].values() for a in level.values()
        )
    return out


def _number(found: str) -> int:
    return int(found) if found.isdigit() else WORDS[found.lower()]


# How the manual and the Help say it in sentences: (pattern, what it must be).
SAID = [
    (r"(\d+|twee|drie) keer in een week", SETBACKS_FOR_STEP),
    (r"zegt iemand (\d+|twee|drie) keer nee", SETBACKS_FOR_STEP),
    (r"[Bb]ij (\d+) (?:een|doe ik hier een) stap terug", SETBACKS_FOR_STEP),
    (r"(\d+|two|three) times in a week", SETBACKS_FOR_STEP),
    (r"drempels van (\d+) ?%", round(DEFAULT_THRESHOLDS.propose_score * 100)),
    (r"(\d+) situaties en \d+ dagen gelden", round(DEFAULT_THRESHOLDS.propose_situations)),
    (r"(\d+) antwoorden, \d+ van de 100", round(DEFAULT_THRESHOLDS.autonomous_answers)),
    (r"\d+ antwoorden, (\d+) van de 100 keer ja", round(DEFAULT_THRESHOLDS.autonomous_score * 100)),
]


def test_the_documents_use_the_apps_own_numbers() -> None:
    wrong = []
    seen = 0
    for where, text in _texts().items():
        text = re.sub(r"\s+", " ", text)
        for pattern, value in SAID:
            for found in re.findall(pattern, text):
                seen += where == "manual nl"
                if _number(found) != value:
                    wrong.append(f"{where}: {found!r} in {pattern!r}, the app uses {value}")
        for found in re.findall(CORRECTIONS, text, flags=re.IGNORECASE):
            if _number(found) != SETBACKS_FOR_STEP:
                wrong.append(f"{where}: {found} corrections, the app uses {SETBACKS_FOR_STEP}")
        for found in re.findall(PROPOSE, text):
            if int(found) != round(DEFAULT_THRESHOLDS.propose_score * 100):
                wrong.append(f"{where}: {found}% to propose")
        for found in re.findall(AUTONOMOUS, text):
            if int(found) != round(DEFAULT_THRESHOLDS.autonomous_score * 100):
                wrong.append(f"{where}: {found}% to do it itself")
    assert not wrong, "\n".join(wrong)
    assert seen >= 4  # the manual really says these numbers


def test_the_app_docs_link_with_home_assistant_as_the_app_does() -> None:
    """The App documentation described an old way of linking (a token to paste). Now the
    integration is placed from the App and confirmed in Devices & services."""
    docs = (ROOT / "docs" / "app" / "DOCS.md").read_text(encoding="utf-8")
    assert "Place the integration" in docs and "Devices & services" in docs
    assert "long-lived" not in docs.lower() and "access token" not in docs.lower()
