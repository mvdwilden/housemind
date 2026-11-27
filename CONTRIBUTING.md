# Contributing to HouseMind

HouseMind is made and kept by one maker. The code is open to read, so everyone can see what it
does in their house, but it is not open to changes from others: pull requests are not taken in,
and the licence (PolyForm Strict 1.0.0, see `LICENSE`) does not allow changed copies.

What helps a lot, and is very welcome:

- **A problem.** Open an issue with what you did, what you expected and what happened. Please
  attach the diagnostics package (House settings → "Download the diagnostics package"): versions,
  counts and the last warnings, with every device, room and name replaced by a neutral label.
- **A device HouseMind does not know**, or recognises wrongly: open the issue "A device kind
  HouseMind does not know" and say what Home Assistant calls it.
- **A language**, or better words in one: open the issue "A language".
- **An idea or a question:** in [Discussions](https://github.com/mvdwilden/housemind/discussions).
- **A security problem:** privately, see [SECURITY.md](SECURITY.md).

## For the maker

Code changes: run `uv run pytest`, `uv run ruff check .`, `uv run mypy` and, in `frontend/`,
`npm run check && npm test`. Everything must be green.

Comments say why, in the present tense, with the requirement number when there is one (E-…,
B-…). How something was found or what it used to do belongs in the commit message and the
changelog, not in the code. A test's docstring says what it checks.

A language is one file, `knowledge/languages/<code>.yaml`, with every `{placeholder}` kept, in
plain, simple language (B1), the user's informal "you", and no brand names;
`uv run python -m housemind.knowledge.validate` lists missing texts. Device kinds live in
`knowledge/catalog/*.yaml`, in Home Assistant's own terms; a kind may only ever be stricter than
the general one for its domain, and hard limits are in code.

`docs/` is the website (GitHub Pages). Change the texts in `docs/site-src/content.yaml` and run
`python tools/build_site.py`; level names come from the app's language files.
