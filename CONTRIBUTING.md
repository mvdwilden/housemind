# Contributing to HouseMind

Thank you for helping! The easiest contributions are **knowledge files**:

- a new language: add `knowledge/languages/<code>.yaml` (copy `en.yaml`, translate the texts);
- a new device kind: add an entry to a file in `knowledge/catalog/` (terms from the Home
  Assistant documentation, no brand names). New kinds may only be *stricter* than existing ones.

Code changes: run `uv run pytest`, `uv run ruff check .`, `uv run mypy` and, in `frontend/`,
`npm run check && npm test`. Everything must be green.

Comments say why, in the present tense, with the requirement number when there is one (E-…,
B-…). How something was found or what it used to do belongs in the commit message and the
changelog, not in the code. A test's docstring says what it checks.

Note for macOS with Python 3.13: if `python -m housemind` says the module is missing, the
editable-install `.pth` file got the "hidden" flag. Run with `PYTHONPATH=src` or
`chflags nohidden .venv/lib/python3.13/site-packages/*.pth`.

## Languages

A language is one file, `knowledge/languages/<code>.yaml`. Copy `en.yaml`, translate every text
and keep each `{placeholder}`. Write plain, simple language (B1), use the user's informal
"you", and no brand names. `uv run python -m housemind.knowledge.validate` lists missing texts.
English and Dutch are checked by a person; other languages are marked "not yet reviewed" in the
app until a native speaker checked them (B-38).

## Device kinds

Device kinds live in `knowledge/catalog/*.yaml`, in Home Assistant's own terms: domain,
`device_class`, `supported_features`. Say what HouseMind may read, what it may do, the highest
step and the safety class. A kind may only ever be stricter than the general one for its
domain; hard limits (never an alarm off, never a lock, garage door, gate or door open) are in
code and cannot be loosened by a file.

## Reporting a problem

Please attach the diagnostics package (House settings → "Download the diagnostics package"):
versions, counts and the last warnings, with every device, room and name replaced by a neutral
label.

## The website

`docs/` is the website (GitHub Pages). Change the texts in `docs/site-src/content.yaml` and
run `python tools/build_site.py`; level names come from the app's language files.

