# Name and logo check (E-166)

Done on 18 October 2026, before the first public release. This is a search, not legal advice.

## The name "HouseMind"

**Trade mark registers** (TMview, the EUIPO's search across the EU office and the national
offices, Benelux included):

| Search | Result |
|---|---|
| contains "housemind" | 4 marks. HOUSEMIND Więcej niż Dom (Poland, 2010, building and security services) – **ended**. The HouseMinders (New Zealand) – ended. HOUSEMINDERS WHILE YOU ARE AWAY (France) – expired. LighthouseMinds (Germany, 2025, software) – registered, a different name. |
| contains "house mind" | 174 marks, mostly other combinations. Close to ours and covering software (class 9): **HOUSE MINDER** (United States, registered 2011; home automation), **MIND HOUSE** (United Kingdom, registered 2017; classes 9, 25, 41, 42), **House your mind** (EU, registered 2024; classes 9, 35, 41). |

**Apps and projects:**
- No other Home Assistant App or integration is called HouseMind.
- No smart-home app is called HouseMind.
- Close names:
  - *Housemindr*, a home-maintenance planner on Android;
  - a household-management project called HouseMind, announced on LinkedIn;
  - *roommind*, a Home Assistant integration for room climate, a different name.

**Conclusion.**
- No registered mark "HouseMind" exists in the EU, the Benelux or the national registers that TMview covers.
- The nearest registered marks for software are HOUSE MINDER (United States) and MIND HOUSE (United Kingdom). They are similar but not the same. How close is too close is a legal question.
- Before publishing, the project can choose one of these:
  - publish under this name;
  - register "HouseMind" itself (Benelux or EU, classes 9 and 42);
  - ask a trade mark adviser.

## The logo

The design (H9, E-166) makes the Orb the logo: the Orb at rest, aqua living light with a faint network inside (the mind), on deep ink. This follows the design system "deep and calm".

- Sources:
  - `docs/brand/mark.svg`: the icon;
  - `docs/brand/logo.svg`: the mark with the name, set in Sora, the font the App ships.
- `tools/brand.py` makes the PNG files from the sources:
  - `ha_app_stable/icon.png` (256 × 256), the App store icon;
  - `ha_app_stable/logo.png` (500 × 200), the App page;
  - `docs/brand/mark-512.png`;
  - the favicon of the App and the website.
- The earlier build's logo (an amber orb under a roof) is not used: it does not fit the design system.
