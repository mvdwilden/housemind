# HouseMind – quality summary

*For the first public beta, HouseMind 0.4.0, 25 November 2026.*

This is a short, honest summary of how HouseMind is checked and where it stands. It claims
nothing that was not checked. HouseMind is a **beta**: it runs every day in a real house, but
it has not yet had two weeks in other people's houses without a serious fault – that is the bar
for the first stable version.

## How every version is checked

Before any version reaches a house, one script checks the exact commit on a clean copy:

- **Lint and types:** ruff, and mypy in strict mode, on all of the backend.
- **1,486 fast tests and the slow release tests** (Python), written from the user's side: a
  sensor that changes, a screen, a question and its answer, a restart. Each test says in plain
  words what a resident would notice.
- **226 tests of the user interface** (Vitest) and a type check of every screen.
- **Screens in a real browser** (Playwright with axe): phone and computer, light and dark,
  contrast, focus and 44 px touch targets.
- **Demo houses:** six invented houses, from a flat to a house with solar panels, and a large
  house of about 1,750 entities made from them – all running the same code as a real house.

Only a version that is green on all of this is installed – first in the maintainer's own house,
which has run HouseMind every day since October 2026.

## Reviews

Next to the tests, HouseMind had independent reviews that only read the code and the house:

| When | What | Outcome |
|---|---|---|
| October 2026 | Safety, privacy, honesty, use per level and accessibility, code health, documentation | All high findings fixed in the 0.1 and 0.2 versions. |
| November 2026 | Every screen and every setting: what can go, what is shown twice | Calmer screens in 0.3.26 – 0.3.28. |
| November 2026 | Code: work done twice, unused code, speed | One place per idea, quicker screens and rounds in 0.3.25 – 0.3.33. |
| November 2026 | A security review that tried to break in, with proof scripts | Ten findings, from high to low; all fixed in 0.3.24, each with a test that fails without the fix. |

Security problems are reported privately and answered within seven days – see
[SECURITY.md](../SECURITY.md).

## Safety in one gate

Every action – from a learned habit, an answer, a house rule or an emergency – passes one safety
gate with an allow-list per kind of device. Some limits can never be loosened, not by a setting,
a file or an AI: HouseMind never turns off an alarm and never opens a lock, a garage door, a gate
or a door, also when such a door sits behind a plain switch. AI only ever proposes; it never
decides while the house runs.

## Speed

Measured on a desktop computer; a Raspberry Pi is about four to five times slower.

| What | Demo house (151 entities) | Large house (1,756 entities) |
|---|---|---|
| From sensor to action, in HouseMind itself | 0.4 ms | 2.6 ms |
| A round of thinking (every 30 s) | – | about 6 ms |
| Three screens on Expert after a sensor | about 12 ms | about 42 ms |

In the maintainer's house (about 360 devices) the reaction from sensor to lamp is about 10 ms in
HouseMind, and a round about 0.2 s including Home Assistant.

## Privacy

Everything stays in the house. HouseMind works without internet and without AI; it has no
account, no cloud and no telemetry. When an administrator turns AI on, names of people,
addresses and camera images never go out, and every question to an AI is in the Logbook on
Expert, word for word.

## Known limits

- Tested on Home Assistant 2026.4 and newer, on amd64 and aarch64.
- Four languages: English, Dutch, German and French. A fifth is a file, but has not been tried
  by a native speaker yet.
- Knowledge updates and the opt-in count are off in the beta.
- It helps with safety, but does not replace certified smoke, CO or alarm systems.
