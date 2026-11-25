"""One rule for "is this word in that name", for every language (code QA 3, punt 4).

The same question was answered at eight places, with three rules for letters with an accent:
one saw "Garáge" as no garage, another did. Now a name and a word are both folded the same way
– lower case, without accents, ``_`` and ``.`` as spaces – and a word counts when it starts a
word of the name ("laadpaal" in "laadpaalstroom"). A short word, or every word where a place
asks for it, counts only as a whole word, so "car" is never "carbon" and "zon" never
"zonwering"; a digit after it is fine ("gate2"), the broadest rule, which is the safe one for
the words of a garage door or a gate.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache

LETTER = r"[^\W\d_]"  # a letter in any language


@lru_cache(maxsize=8192)  # names and words come back every round
def fold(text: str) -> str:
    """Lower case, no accents, ``_`` and ``.`` as spaces: "Garáge_Deur" → "garage deur"."""
    bare = unicodedata.normalize("NFKD", text)
    bare = "".join(c for c in bare if not unicodedata.combining(c))
    return bare.casefold().replace("_", " ").replace(".", " ")


def plain(text: str) -> str:
    """Folded, letters and digits only, one space between words: 'Média (TV)' → 'media tv'."""
    return " ".join(re.sub(r"[\W_]+", " ", fold(text)).split())


def has_word(text: str, words: Iterable[str], whole_up_to: int = 0, whole: bool = False) -> bool:
    """A word at the start of a word of ``text``. A word of at most ``whole_up_to`` letters –
    or every word, with ``whole`` – only as a whole word."""
    haystack = fold(text)
    for word in words:
        needle = fold(word).strip()
        if not needle:
            continue
        end = f"(?!{LETTER})" if whole or len(needle) <= whole_up_to else ""
        if re.search(f"(?<!{LETTER}){re.escape(needle)}{end}", haystack):
            return True
    return False
