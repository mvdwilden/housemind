# Glossary

One word for one thing, everywhere: in the app, in the Help, in what Vesta says and in the
manual (E-118). `tests/test_glossary.py` checks the texts in the language files against it.
Words people *say* (the recognition lists in `talk.words`, `talk.know` and `daily_words`) may
keep other words, so that Vesta still understands them.

| Thing | In the code | Nederlands | English | Deutsch | Français |
|---|---|---|---|---|---|
| A room of the house (an area in Home Assistant) | `area` | ruimte | room | Raum | pièce |
| A part of the house, like Lighting | `domain` | onderdeel | part | Bereich | domaine |
| A part that is still on trial | `experimental` | proefonderdeel | trial part | Testbereich | domaine à l'essai |
| Something HouseMind learns, like light on entry | `skill` | vaardigheid | skill | Fähigkeit | compétence |
| Observe, Propose or Autonomous | `step` | stap | step | Schritt | étape |
| A rule people set, like "never before 7:00" | `house rule` | huisregel | house rule | Hausregel | règle de la maison |
| A saved way a room looks | `scene` | moment | moment | Moment | moment |
| What a room is for, like bedroom | `role` | rol | role | Rolle | rôle |
| Trying HouseMind out in the house, without it counting for learning | `test` | test | test | Test | test |

"Step" means only the learning step. The parts of the introduction and the tour are counted
without a word: "2 van 4", "2 of 4", "2 von 4", "2 sur 4".
