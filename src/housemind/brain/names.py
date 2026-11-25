"""Plain names for devices (E-248, B-93): how Vesta names a device, the way a person does.

"Anna slaapkamer lamp 1" in the room "Slaapkamer" becomes "de lamp in de slaapkamer";
"Camera Dak 1234 AB" next to "Camera Tuin 1234 AB" becomes "de camera op het dak". What it is
comes from the words in its name or else from its kind in HA; where it is from its room in HA.
Names of people, numbers, postcodes, brands and type numbers are left out. A distinction is only
added when the same room has more of the same. Ordinary words per language live in
``knowledge/names/<language>.yaml``; nothing here knows one house.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from housemind.core.model import WHOLE_HOUSE, Entity, HouseModel

# Main parts of a device first: the thing a person calls it by.
_MAIN = (
    "light",
    "cover",
    "climate",
    "lock",
    "camera",
    "media_player",
    "vacuum",
    "lawn_mower",
    "fan",
    "valve",
    "water_heater",
    "humidifier",
    "siren",
    "alarm_control_panel",
    "switch",
    "binary_sensor",
    "sensor",
)
_POSTCODES = (
    re.compile(r"\b\d{4}\s?[A-Za-z]{2}\b"),  # 1234 AB
    re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}\b"),  # SW1A 1AA
)
_TOKEN = re.compile(r"[^\s_/,:;()\[\]|+&]+")
_DE_ARTICLES = {
    "nom": {"m": "der", "f": "die", "n": "das", "pl": "die"},
    "acc": {"m": "den", "f": "die", "n": "das", "pl": "die"},
    "dat": {"m": "dem", "f": "der", "n": "dem", "pl": "den"},
}
_TIDY = {
    "de": (
        (r"\b([Ii])n dem\b", r"\1m"),
        (r"\b([Aa])n dem\b", r"\1m"),
        (r"\b([Bb])ei dem\b", r"\1eim"),
        (r"\b([Vv])on dem\b", r"\1om"),
        (r"\b([Zz])u dem\b", r"\1um"),
        (r"\b([Zz])u der\b", r"\1ur"),
    ),
    "fr": (
        (r"\b([Dd])e le\b", lambda m: "Du" if m.group(1) == "D" else "du"),
        (r"\b([Dd])e les\b", lambda m: "Des" if m.group(1) == "D" else "des"),
        (r"\b([Àà]) le\b", lambda m: "Au" if m.group(1) == "À" else "au"),
        (r"\b([Àà]) les\b", lambda m: "Aux" if m.group(1) == "À" else "aux"),
    ),
}


def tidy(language: str, text: str) -> str:
    """Contractions a language makes: "in dem" → "im", "de le" → "du"."""
    for pattern, replacement in _TIDY.get(language.split("-")[0], ()):
        text = re.sub(pattern, replacement, text)  # type: ignore[call-overload]
    return text


def sentence_case(text: str) -> str:
    """A capital at the start of each sentence ("de wasmachine is klaar" → "De …")."""
    return re.sub(r"(^|[.!?]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), text)


@dataclass(frozen=True, slots=True)
class Lexicon:
    grammar: str
    joiner: str
    one_of: str
    numbers: frozenset[str]
    fillers: frozenset[str]
    nouns: dict[str, tuple[str, str, str]]  # lower word -> (shown word, gender, plural)
    plurals: dict[str, str]  # lower plural -> lower word ("gordijnen" -> "gordijn")
    defaults: dict[str, str]
    places: dict[str, tuple[str, str, str]]  # lower word -> (shown word, gender, preposition)
    alone: frozenset[str]
    sides: frozenset[str]
    of: str = ""  # "slaapkamer van Anna" when two rooms would sound the same
    states: dict[str, list[str]] | None = None  # "aan", "dicht"; French agrees (m, f, m pl, f pl)

    @classmethod
    def from_raw(cls, raw: dict[str, object]) -> Lexicon:
        def words(key: str) -> frozenset[str]:
            value = raw.get(key) or []
            return (
                frozenset(str(w).lower() for w in value) if isinstance(value, list) else frozenset()
            )

        def table(key: str) -> dict[str, tuple[str, str, str]]:
            value = raw.get(key) or {}
            out: dict[str, tuple[str, str, str]] = {}
            if isinstance(value, dict):
                for word, (gender, extra) in value.items():
                    out[str(word).lower()] = (str(word), str(gender), str(extra))
            return out

        defaults = raw.get("defaults") or {}
        nouns = table("nouns")
        return cls(
            grammar=str(raw.get("grammar", "en")),
            joiner=str(raw.get("and", "and")),
            one_of=str(raw.get("one_of", "")),
            numbers=words("numbers"),
            fillers=words("fillers"),
            nouns=nouns,
            plurals={
                plural.lower(): word
                for word, (_s, _g, plural) in nouns.items()
                if plural.lower() != word and plural.lower() not in nouns
            },
            defaults={str(k): str(v) for k, v in defaults.items()}
            if isinstance(defaults, dict)
            else {},
            places=table("places"),
            alone=words("alone"),
            sides=words("sides"),
            of=str(raw.get("of", "")),
            states=_states(raw.get("states")),
        )

    # -- grammar -------------------------------------------------------------------------

    def article(self, word: str, gender: str, plural: bool, case: str = "nom") -> str:
        if self.grammar == "nl":
            return "de" if plural or gender != "n" else "het"
        if self.grammar == "de":
            return _DE_ARTICLES.get(case, _DE_ARTICLES["nom"])["pl" if plural else gender]
        if self.grammar == "fr":
            if plural:
                return "les"
            if word[:1].lower() in "aeiouhàâéèêëîïôûù":
                return "l'"
            return "le" if gender == "m" else "la"
        return "the"

    def state(self, key: str, gender: str = "m", plural: bool = False) -> str:
        """How a state is said: "aan", "dicht"; in French it agrees ("fermée", "allumés")."""
        forms = (self.states or {}).get(key) or [key]
        if len(forms) < 4:
            return forms[0]
        index = (2 if plural else 0) + (1 if gender == "f" else 0)
        return forms[index]

    def phrase(self, word: str, gender: str, plural: bool = False, case: str = "nom") -> str:
        """A noun with its article: "de lamp", "het raam", "die Lampe", "l'entrée"."""
        article = self.article(word, gender, plural, case)
        return f"{article}{word}" if article.endswith("'") else f"{article} {word}"

    def noun(self, word: str) -> tuple[str, str, str, str] | None:
        """The known noun in a word: (shown word, gender, plural, head). Compounds count in
        Dutch and German ("bureaulamp" → lamp), whole words elsewhere. A plural in a name is
        said in the singular ("gordijnen" → het gordijn), so "… is klaar" stays right; a Dutch
        diminutive is a het-word ("nachtlampje")."""
        low = word.lower()
        if low in self.plurals and low not in self.nouns:
            single = self.plurals[low]
            return self.noun(self.nouns[single][0] if self.grammar == "de" else single)
        if self.grammar == "nl" and len(low) > 4:
            for suffix in ("etje", "tje", "pje", "je"):
                if low.endswith(suffix) and low[: -len(suffix)] not in ("", "t"):
                    found = self.noun(low[: -len(suffix)])
                    if found is not None:
                        return (low, "n", low + "s", found[3])
        if low in self.nouns:
            shown, gender, plural = self.nouns[low]
            if self.grammar == "de":
                shown = word[:1].upper() + word[1:]
            return (shown, gender, plural, low)
        if self.grammar in ("nl", "de"):
            for plural in sorted(self.plurals, key=len, reverse=True):
                if len(low) > len(plural) + 2 and low.endswith(plural):
                    return self.noun(word[: len(word) - len(plural)] + self.plurals[plural])
            for head in sorted(self.nouns, key=len, reverse=True):
                if len(low) > len(head) + 2 and low.endswith(head):
                    _shown, gender, plural = self.nouns[head]
                    stem = word[: len(word) - len(head)]
                    if self.grammar == "de":
                        compound = (stem + head)[:1].upper() + (stem + head)[1:]
                        many = (stem + plural.lower())[:1].upper() + (stem + plural.lower())[1:]
                        return (compound, gender, many, head)
                    return ((stem + head).lower(), gender, (stem + plural).lower(), head)
        return None

    def place(self, word: str) -> tuple[str, str, str] | None:
        """A known place in a word: (shown word, gender, preposition)."""
        low = word.lower()
        if low in self.places:
            return self.places[low]
        if self.grammar in ("nl", "de"):
            for head in sorted(self.places, key=len, reverse=True):
                if len(low) > len(head) + 2 and low.endswith(head):
                    _shown, gender, prep = self.places[head]
                    return (word, gender, prep)
        return None


def _states(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(x) for x in v] for k, v in raw.items() if isinstance(v, list)}


def load_lexicons(directories: Iterable[Path]) -> dict[str, Lexicon]:
    out: dict[str, Lexicon] = {}
    for directory in directories:
        folder = directory / "names"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            out[path.stem] = Lexicon.from_raw(raw)  # a later folder replaces a language
    return out


@dataclass(frozen=True, slots=True)
class Thing:
    """What one device is and where, in one language."""

    noun: str
    gender: str
    plural: str
    place: str  # "in de slaapkamer", "op het dak", "buiten" – or ""
    side: str  # "links" – or ""
    unique: bool
    head: str = ""  # the core noun: "lamp" for "bureaulamp", "camera" for "garden camera"


class Names:
    """Plain names in every language for one house; rebuilt when the house changes."""

    def __init__(
        self,
        model: HouseModel,
        lexicons: dict[str, Lexicon],
        people: Iterable[str] = (),
        brands: dict[str, set[str]] | None = None,
    ) -> None:
        self.model = model
        self.lexicons = lexicons
        self.people = _people(people)
        self.brands = brands or {}
        self._counts: dict[str, dict[tuple[str, str], dict[str, str]]] = {}
        self._by_name: dict[str, str] | None = None
        self._index: dict[str, list[Entity]] | None = None
        self.generation = 0  # one more at every reset: what others keep of these names is old

    def reset(self, people: Iterable[str] = (), brands: dict[str, set[str]] | None = None) -> None:
        """The house changed (or who lives there): start over, keeping the word lists."""
        self.people = _people(people)
        self.brands = brands or {}
        self._counts = {}
        self._by_name = None
        self._index = None
        self.generation += 1

    def room(self, area_id: str | None, language: str) -> str:
        """A room's own words without article, for texts that bring their own ("in de {area}")."""
        lexicon = self._lexicon(language)
        area = self.model.areas.get(area_id or "")
        if area is None:
            return ""
        if lexicon is None:
            return area.name
        words = self._room_words(area.id, lexicon)
        return self._shown(words, lexicon) if words else area.name

    def where(self, area_id: str | None, language: str) -> str:
        """How to say "in that room": "in de keuken", "im Flur", "buiten"."""
        lexicon = self._lexicon(language)
        if lexicon is None or not area_id or area_id == WHOLE_HOUSE:
            return ""
        return self._room(area_id, lexicon)[2]

    def area_of(self, entity_id: str) -> str | None:
        """The room of a device (its own, else its device's)."""
        entity = self.model.entities.get(entity_id)
        if entity is None:
            return None
        main = self._main(entity)
        device = self.model.devices.get(main.device_id or "")
        return main.area_id or (device.area_id if device else None)

    def _lexicon(self, language: str) -> Lexicon | None:
        return self.lexicons.get(language) or self.lexicons.get(language.split("-")[0])

    # -- the words of a name -------------------------------------------------------------

    def _ordinary(self, word: str) -> bool:
        """A word some language knows as a thing, a place or a small word – never a name."""
        low = word.lower()
        for lexicon in self.lexicons.values():
            if low in lexicon.fillers or low in lexicon.alone or low in lexicon.sides:
                return True
            if lexicon.noun(word) or lexicon.place(word):
                return True
        return False

    def _person(self, word: str) -> bool:
        low = word.lower().strip("'’")
        known = low in self.people or any(
            low.endswith(suffix) and low[: -len(suffix)] in self.people
            for suffix in ("'s", "’s", "s")
        )
        return known and not self._ordinary(word)

    def tokens(
        self, name: str, lexicon: Lexicon, device_id: str | None = None, people: bool = False
    ) -> list[str]:
        """The words of a name without people, numbers, postcodes, brands or type numbers."""
        text = name
        for pattern in _POSTCODES:
            text = pattern.sub(" ", text)
        brands = self.brands.get(device_id or "", set())
        out: list[str] = []
        for raw in _TOKEN.findall(text):
            word = raw.strip(".-'’\"")
            if not word:
                continue
            if any(c.isdigit() for c in word):
                out.extend(p for p in word.split("-") if p and not any(c.isdigit() for c in p))
                continue
            low = word.lower()
            if low in lexicon.numbers or (not people and self._person(word)):
                continue
            if low in brands and not (lexicon.noun(word) or lexicon.place(word)):
                continue
            out.append(word)
        # "Lamp van Anna": the "van" that belonged to the name goes too.
        while out and out[-1].lower() in lexicon.fillers:
            out.pop()
        return out

    def _parts(self) -> dict[str, list[Entity]]:
        if self._index is None:
            index: dict[str, list[Entity]] = {}
            for entity in self.model.entities.values():
                if entity.device_id:
                    index.setdefault(entity.device_id, []).append(entity)
            self._index = index
        return self._index

    def _main(self, entity: Entity) -> Entity:
        """The part of a device a person names it by (its lamp, not its battery)."""
        if not entity.device_id:
            return entity
        siblings = self._parts().get(entity.device_id, [entity])
        for domain in _MAIN:
            for sibling in siblings:
                if sibling.domain == domain and not sibling.entity_category:
                    return sibling
        return entity

    def _default(self, entity: Entity, lexicon: Lexicon) -> str:
        key = f"{entity.domain}.{entity.device_class}" if entity.device_class else entity.domain
        return lexicon.defaults.get(key) or lexicon.defaults.get(entity.domain) or ""

    def _shown(self, words: list[str], lexicon: Lexicon) -> str:
        """Ordinary words in lower case (German keeps its capitals); a name stays a name."""
        if lexicon.grammar == "de":
            return " ".join(words)
        return " ".join(w.lower() if self._ordinary(w) or w.isupper() else w for w in words)

    def _room_words(self, area_id: str | None, lexicon: Lexicon) -> list[str]:
        """A room's words: without a person's name ("Slaapkamer Anna" → slaapkamer), unless
        another room would then sound the same – then "slaapkamer van Anna"."""
        area = self.model.areas.get(area_id or "")
        if area is None:
            return []
        every = self.tokens(area.name, lexicon, people=True)
        own = [w for w in every if not self._person(w)]
        names = [w for w in every if self._person(w)]
        if not own:
            return every
        if names:
            key = self._shown(own, lexicon).lower()
            for other in self.model.areas.values():
                if other.id == area.id:
                    continue
                theirs = [
                    w for w in self.tokens(other.name, lexicon, people=True) if not self._person(w)
                ]
                if self._shown(theirs, lexicon).lower() == key:
                    return [*own, lexicon.of, *names] if lexicon.of else every
        return own

    def _room(self, area_id: str | None, lexicon: Lexicon) -> tuple[list[str], str, str]:
        """A room's words, its gender and how to say "in that room" ("in de keuken")."""
        words = self._room_words(area_id, lexicon)
        if not words:
            return [], _GUESS[lexicon.grammar], ""
        shown = self._shown(words, lexicon)
        if shown.lower() in lexicon.alone:
            return words, "", shown
        heads = words if lexicon.grammar == "fr" else words[::-1]
        found = next((p for p in (lexicon.place(w) for w in heads) if p), None)
        gender, prep = (
            (found[1], found[2]) if found else (_GUESS[lexicon.grammar], _IN[lexicon.grammar])
        )
        return (
            words,
            gender,
            tidy(lexicon.grammar, f"{prep} {lexicon.phrase(shown, gender, case='dat')}"),
        )

    def area(self, area_id: str | None, language: str, case: str = "nom") -> str:
        """A room as a noun with its article: "de keuken", "das Wohnzimmer"."""
        lexicon = self._lexicon(language)
        area = self.model.areas.get(area_id or "")
        if lexicon is None or area is None:
            return area.name if area else ""
        words, gender, _place = self._room(area_id, lexicon)
        if not words:
            return area.name
        shown = self._shown(words, lexicon)
        if not gender:
            return shown  # "buiten"
        return lexicon.phrase(shown, gender, case=case)

    def _what(
        self, entity: Entity, lexicon: Lexicon
    ) -> tuple[Thing, str, list[str], set[str]] | None:
        """What a thing is and where, before knowing whether it needs a distinction:
        (thing, area id, other words of its name, words of its room)."""
        main = self._main(entity)
        device = self.model.devices.get(main.device_id or "")
        name = main.name or ""
        if device and device.name and device.name.lower() not in name.lower():
            name = f"{device.name} {name}"
        words = self.tokens(name, lexicon, main.device_id)
        area_id = main.area_id or (device.area_id if device else None) or ""
        room, _gender, place = self._room(area_id, lexicon)
        in_room = {w.lower() for w in room}
        nouns = [(i, n) for i, n in ((i, lexicon.noun(w)) for i, w in enumerate(words)) if n]
        found = nouns[0] if nouns else None
        if len(nouns) > 1:
            # Two things in one name ("TV - Lamp 2"): the one this device is wins – a light
            # is a lamp, the TV says where.
            default = self._default(main, lexicon)
            first = default.split(" ")[-1 if lexicon.grammar == "en" else 0] if default else ""
            known = lexicon.noun(first) if first else None
            head = known[3] if known else default.lower()
            # Else the thing comes last ("TV lamp", "bank lamp"), in French first.
            usual = nouns[0] if lexicon.grammar == "fr" else nouns[-1]
            found = next((x for x in nouns if x[1][3] == head), usual)
        if found is not None:
            index, (shown, gender, plural, head) = found
            rest = [w for i, w in enumerate(words) if i != index and w.lower() not in in_room]
            if (
                lexicon.grammar == "fr"
                and index + 2 < len(words)
                and words[index + 1].lower() in ("de", "d")
            ):
                extra = words[index + 2].lower()  # "détecteur de fumée" stays one noun
                shown, plural = f"{shown} de {extra}", f"{plural} de {extra}"
                rest = [w for w in rest if w.lower() not in ("de", "d", extra)]
            elif lexicon.grammar == "en" and index > 0:
                before = words[index - 1]
                low = before.lower()
                if low not in in_room and low not in lexicon.fillers and low not in lexicon.sides:
                    # "smoke detector", "desk lamp", "roof camera": English puts it in front.
                    shown, plural = f"{low} {shown}", f"{low} {plural}"
                    rest = [w for w in rest if w != before]
        else:
            default = self._default(main, lexicon)
            if not default:
                return None
            first = default.split(" ")[-1 if lexicon.grammar == "en" else 0]
            known = lexicon.noun(first)
            head = known[3] if known else default.lower()
            gender = known[1] if known else _GUESS[lexicon.grammar]
            plural = default.replace(first, known[2], 1) if known else default
            shown = default
            rest = [w for w in words if w.lower() not in in_room]
        return Thing(shown, gender, plural, place, "", True, head), area_id, rest, in_room

    def thing(self, entity_id: str, language: str) -> Thing | None:
        lexicon = self._lexicon(language)
        entity = self.model.entities.get(entity_id)
        if lexicon is None or entity is None:
            return None
        found = self._what(entity, lexicon)
        if found is None:
            return None
        thing, area_id, rest, in_room = found
        others = self._count(language).get((area_id, thing.head), {})
        same = sum(1 for shown in others.values() if shown == thing.noun.lower())
        if len(others) <= 1 or (same <= 1 and thing.noun.lower() != thing.head):
            return thing  # alone, or its own word already tells it apart ("de bureaulamp")
        # The same room has more of these: the shortest word that tells them apart.
        for word in rest:
            spot = lexicon.place(word)
            if spot is not None and word.lower() not in in_room:
                shown = word if lexicon.grammar == "de" else word.lower()
                where = tidy(
                    lexicon.grammar, f"{spot[2]} {lexicon.phrase(shown, spot[1], case='dat')}"
                )
                return Thing(thing.noun, thing.gender, thing.plural, where, "", True)
            if word.lower() in lexicon.sides:
                return Thing(
                    thing.noun, thing.gender, thing.plural, thing.place, word.lower(), True
                )
        return Thing(thing.noun, thing.gender, thing.plural, thing.place, "", False)

    def _count(self, language: str) -> dict[tuple[str, str], dict[str, str]]:
        """How many things of each kind each room has (to know when a distinction is needed)."""
        if language in self._counts:
            return self._counts[language]
        counts: dict[tuple[str, str], dict[str, str]] = {}
        lexicon = self._lexicon(language)
        if lexicon is not None:
            for entity in self.model.entities.values():
                if entity.entity_category or entity.hidden or entity.disabled:
                    continue
                if self._main(entity).entity_id != entity.entity_id:
                    continue  # one count per device
                found = self._what(entity, lexicon)
                if found is not None:
                    thing, area_id, _rest, _room = found
                    key = (area_id, thing.head)
                    thing_id = entity.device_id or entity.entity_id
                    counts.setdefault(key, {})[thing_id] = thing.noun.lower()
        self._counts[language] = counts
        return counts

    # -- what Vesta says -----------------------------------------------------------------

    def device(self, entity_id: str, language: str, case: str = "nom") -> str:
        """A device as Vesta says it: "de lamp in de slaapkamer"; never its HA name."""
        lexicon = self._lexicon(language)
        thing = self.thing(entity_id, language)
        if lexicon is None or thing is None:
            entity = self.model.entities.get(entity_id)
            return self._cleaned(entity.name if entity else entity_id, language)
        if not thing.unique and lexicon.one_of:
            many = lexicon.phrase(thing.plural, thing.gender, plural=True, case=case)
            text = lexicon.one_of.format(np=f"{many} {thing.place}".strip())
            return tidy(lexicon.grammar, text)
        parts = [lexicon.phrase(thing.noun, thing.gender, case=case), thing.side, thing.place]
        return tidy(lexicon.grammar, " ".join(p for p in parts if p))

    def forms(self, entity_id: str, language: str) -> dict[str, str]:
        """The facts for Vesta's words: the device as subject, as object, after "von"."""
        return {
            "device": self.device(entity_id, language),
            "device_acc": self.device(entity_id, language, "acc"),
            "device_dat": self.device(entity_id, language, "dat"),
        }

    def group(self, entity_ids: Iterable[str], language: str) -> str:
        """More at once, per kind and room: "de lampen in de keuken en de woonkamer"."""
        lexicon = self._lexicon(language)
        ids = list(dict.fromkeys(entity_ids))
        if lexicon is None:
            return ", ".join(self._cleaned(i, language) for i in ids)
        kinds: dict[str, list[Thing]] = {}
        loose: list[str] = []
        for entity_id in ids:
            thing = self.thing(entity_id, language)
            if thing is None:
                loose.append(self.device(entity_id, language))
            else:
                kinds.setdefault(thing.noun, []).append(thing)
        parts: list[str] = []
        for things in kinds.values():
            first = things[0]
            places = list(dict.fromkeys(t.place for t in things if t.place))
            if len(things) == 1:
                parts.append(
                    tidy(
                        lexicon.grammar,
                        " ".join(
                            p for p in (lexicon.phrase(first.noun, first.gender), first.place) if p
                        ),
                    )
                )
                continue
            many = lexicon.phrase(first.plural, first.gender, plural=True)
            parts.append(tidy(lexicon.grammar, f"{many} {self._join(places, lexicon)}".strip()))
        return self._join(parts + loose, lexicon)

    def _join(self, items: list[str], lexicon: Lexicon) -> str:
        if len(items) <= 1:
            return "".join(items)
        # "in de keuken en in de woonkamer" → "in de keuken en de woonkamer"
        preps = {i.split(" ", 1)[0] for i in items}
        if len(preps) == 1 and all(" " in i for i in items):
            prep = preps.pop()
            rest = [i.split(" ", 1)[1] for i in items]
            if prep in _PREPOSITIONS:
                return f"{prep} {', '.join(rest[:-1])} {lexicon.joiner} {rest[-1]}"
        return f"{', '.join(items[:-1])} {lexicon.joiner} {items[-1]}"

    def by_name(self, name: str, language: str) -> dict[str, str]:
        """A name from an old message: back to its device or room, then plain."""
        if self._by_name is None:
            index: dict[str, str] = {}
            for entity in self.model.entities.values():
                if entity.name:
                    index.setdefault(entity.name.lower(), entity.entity_id)
            for device in self.model.devices.values():
                parts = self._parts().get(device.id)
                if parts and device.name:
                    index.setdefault(device.name.lower(), self._main(parts[0]).entity_id)
            for area in self.model.areas.values():
                index.setdefault(area.name.lower(), f"area:{area.id}")
            self._by_name = index
        found = self._by_name.get(name.strip().lower())
        if found is None:
            cleaned = self._cleaned(name, language)
            return {"device": cleaned, "device_acc": cleaned, "device_dat": cleaned}
        if found.startswith("area:"):
            area_id = found.removeprefix("area:")
            room = self.area(area_id, language)
            return {
                "device": room,
                "device_acc": self.area(area_id, language, "acc"),
                "device_dat": self.area(area_id, language, "dat"),
            }
        return self.forms(found, language)

    def area_forms(self, name: str, language: str) -> dict[str, str]:
        """A room named in an old message, plain: "de tuin", "dem Garten" after "bei", and its
        own words for texts that bring their article ("in de {area}")."""
        wanted = name.strip().lower()
        area = next((a for a in self.model.areas.values() if a.name.lower() == wanted), None)
        if area is None:
            cleaned = self._cleaned(name, language)
            return {"area": cleaned, "area_dat": cleaned, "room": cleaned}
        return {
            "area": self.area(area.id, language),
            "area_dat": self.area(area.id, language, "dat"),
            "room": self.room(area.id, language),
        }

    def _cleaned(self, name: str, language: str) -> str:
        lexicon = self._lexicon(language)
        if lexicon is None:
            return name
        words = self.tokens(name, lexicon)
        return " ".join(words) if words else name


def _people(names: Iterable[str]) -> set[str]:
    """Each part of each person's name: "Anna" of "Anna van der Berg" too."""
    return {
        part.lower()
        for name in names
        for part in re.findall(r"[^\W\d_][\w'’-]+", str(name))
        if len(part) >= 2 and part.lower() not in _NOT_NAMES
    }


# Small words inside people's names that are never only a name ("van der Berg").
_NOT_NAMES = {"van", "der", "den", "de", "het", "von", "zu", "du", "le", "la", "of", "the"}

_GUESS = {"nl": "m", "en": "n", "de": "n", "fr": "m"}  # the most usual article when unknown
_IN = {"nl": "in", "en": "in", "de": "in", "fr": "dans"}
_PREPOSITIONS = {"in", "op", "bij", "aan", "on", "at", "by", "dans", "sur", "im", "auf", "am"}


class PlainNames:
    """Plain names for labels that come back from an AI, made only when asked for (E-248)."""

    def __init__(self, names: Names, language: str) -> None:
        self.names = names
        self.language = language

    def get(self, local_id: str, default: str | None = None) -> str | None:
        if local_id.startswith("area:"):
            return self.names.area(local_id.removeprefix("area:"), self.language) or default
        if local_id in self.names.model.entities:
            return self.names.device(local_id, self.language)
        return default
