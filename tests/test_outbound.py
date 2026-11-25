"""Everything that leaves the house is in one list, and the privacy statement names it
(E-14, E-15, 20.19). A new way out without a flow, or a flow the statement does not name,
fails here."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from housemind.support.outbound import FLOWS, INSIDE

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
# Calls that leave the house: an HTTP session of our own, and the Home Assistant actions that
# reach a phone, a speech service or another conversation agent.
OUTWARD = {"notify", "speak", "tts_url", "speech_to_text", "converse"}


def _ways_out() -> list[str]:
    """Where in the code something goes out: module.Class.function (or the module)."""
    found: list[str] = []
    for path in sorted((SRC / "housemind").rglob("*.py")):
        module = ".".join(path.relative_to(SRC).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, scope: list[str], module: str = module) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    visit(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    via_ha = (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Attribute | ast.Name)
                        and (
                            getattr(func.value, "attr", "") == "ha"
                            or getattr(func.value, "id", "") == "ha"
                        )
                    )
                    if name == "ClientSession" or (name in OUTWARD and via_ha):
                        found.append(".".join([module, *scope]))
                visit(child, scope)

        visit(tree, [])
    return sorted(set(found))


def _covered(where: str) -> bool:
    places = [code for flow in FLOWS for code in flow.code] + list(INSIDE)
    return any(where == p or where.startswith(p + ".") for p in places)


def test_every_way_out_belongs_to_a_flow() -> None:
    ways = _ways_out()
    assert ways, "the scan should find the ways out"
    assert [w for w in ways if not _covered(w)] == []


def test_every_flow_is_named_in_the_privacy_statement_of_every_language() -> None:
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        out = texts["privacy"]["out"].lower()
        missing = [f.key for f in FLOWS if f.privacy[lang].lower() not in out]
        assert missing == [], (lang, missing)


def test_the_six_flows_of_e14() -> None:
    assert [f.key for f in FLOWS] == [
        "ai",
        "updates",
        "push",
        "speech",
        "forward",
        "diagnostics",
    ]
    assert all(f.what and f.when and f.off for f in FLOWS)


def test_speech_engines_are_known_as_in_the_house_or_in_the_cloud() -> None:
    from housemind.brain.setting_up import engine_place
    from housemind.runtime import _speech_engines

    places = _speech_engines([ROOT / "knowledge"])
    assert engine_place("stt.home_assistant_cloud", places) == "cloud"
    assert engine_place("tts.google_translate_nl_com", places) == "cloud"
    assert engine_place("tts.piper", places) == "local"
    assert engine_place("stt.faster_whisper", places) == "local"
    assert engine_place("tts.something_new", places) == "unknown"
    assert engine_place(None, places) == "unknown"


async def test_the_privacy_statement_says_where_her_voice_goes(tmp_path: Path) -> None:
    from housemind.server import views
    from tests.test_house_modes import _start

    hm, _ = await _start(tmp_path)
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()
    assert hm.speech_out is not None
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    speech = (await views.snapshot(hm, viewer))["speech"]
    assert speech is not None and set(speech) == {"cloud", "local"}
    await hm.stop()
