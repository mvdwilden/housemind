# Release check (E-144, design H12.3)

Nothing goes to Beta or Stable unless items 1–5 pass; item 6 is two weeks on the Beta channel.
Items 1, 2 and 5 run on every change (`.github/workflows/ci.yml`); items 3 and 4, the year in
the time machine and the screens run weekly and before every release tag
(`.github/workflows/full-test.yml`).

| # | Check | How | Command |
|---|---|---|---|
| 1 | All automatic tests green | ruff, mypy, pytest, knowledge files, svelte-check, vitest | `uv run pytest` · `cd frontend && npm run check && npm test` |
| 2 | S-01 – S-04 in every demo house | release tests R1–R4 in the time machine | part of `uv run pytest` |
| 3 | Speed norms (E-157) | sensor → command, restart, in a Pi-like container; tap, change of screen and first screen in a browser | `tools/pi_like.sh tests/test_speed.py` · `uv run pytest -m ui -s tests/test_ui.py` |
| 4 | Tested Home Assistant versions, also the beta (E-142) | the real Home Assistant container: start, recognition, self-check, own vs. a person's change, an emergency, a restart | `uv run python tools/real_ha.py stable` (also `beta`, `2026.4`) |
| 5 | Help and "what's new" match the app (E-119, E-150) | every help topic linked to what it explains, with the app's own words in every language; CHANGELOG updated | `tests/test_help_map.py` (in `uv run pytest`) |

Also in the full test: a year in the time machine (`uv run pytest -m slow -s tests/test_year.py`),
accessibility of every screen with axe (WCAG 2.2 AA, in `tests/test_ui.py`), and the security
scans: `pip-audit` (on `requirements.lock`) and `npm audit` run in CI on every change; a
secrets scan is not there yet.

When a check passes on a new Home Assistant version, add its year.month to
`knowledge/tested_ha.yaml` (or ship it in a knowledge update), so houses on that version leave
their careful day behind (E-155).

## Phase 4 run – 17 October 2026

| Check | Result |
|---|---|
| 1 | 287 backend tests, 13 frontend tests, ruff, mypy (strict), svelte-check: all green |
| 2 | R1–R4 release tests green in all three demo houses |
| 3 | Reflex p95 1.5 ms, emergency p95 70 ms (0.25 CPU, 1 GB); ready after a restart 1.0 s. Tap ≤ 50 ms, change of screen ≤ 135 ms, first screen on a 4× slower phone 0.18 s |
| 4 | Home Assistant 2026.10.2 (stable and beta) and 2026.4.4: passed; back after a real restart in 4.2 s |
| 5 | 51 help topics linked; labels match in en, nl, de, fr |
| Year | Database flat at 1.41 MB (family home) and 1.54 MB (energy home); nothing older than its keeping time; at most 9 open messages |
| Accessibility | 178 screens (4 levels × phone, tablet, computer × light, dark): 0 findings |
| Security | No known vulnerabilities (Python, npm); no secrets in the code or its history; App rights as designed (H7.6) |

Found and fixed in this run:
- No reconnect after a restart or update of Home Assistant (H7.4).
- No self-check (E-152).
- No list of tested versions (E-142, E-155).
- Registry changes were not seen while running.
- The status line asked HA for all states on every screen.
- Weekly tips and "learned" messages piled up.
- Settled proposals, notifications and corrections were kept forever.
- Garden valves were not recognised (now watch-only).
- Four kinds of accessibility problems, among them button contrast in dark mode.
- Vulnerable test runner (vitest 3 → 4).

Not covered yet: item 6 (two weeks on the Beta channel), the Home Assistant beta of the next
month (the beta channel still carried 2026.10.2), and timings on a real Raspberry Pi.
