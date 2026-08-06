"""Text normalisation.

This is the module that defeats SECOP's accent problem. Every comparison in the
radar runs over normalised text, never over the raw strings, so 'Capacitación',
'CAPACITACION' and 'capacitacion' are the same token to us. We never delegate a
search to SECOP's own index, which is where the accent tricks live.
"""

import re
import unicodedata
from functools import lru_cache

_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def strip_accents(text: str) -> str:
    """Decompose to NFD and drop combining marks: 'formación' -> 'formacion'."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text) -> str:
    """Lowercase, de-accent, strip punctuation and collapse whitespace.

    Returns an empty string for None so callers never have to null-check.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = strip_accents(text).lower()
    # 'ñ' survives NFD as 'n' + combining tilde, so it is already folded to 'n'.
    text = _NON_ALNUM.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def tokens(text) -> set:
    """Normalised token set, used for cheap fuzzy overlap checks."""
    return set(normalize(text).split())


def contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-phrase containment over normalised text.

    Padded with spaces so 'ingles' does not match inside 'inglesa', while
    multi-word phrases such as 'competencias laborales' still match.
    """
    return phrase_in_padded(pad(normalize(haystack)), phrase)


def pad(normalized_text: str) -> str:
    """Wrap already-normalised text in spaces, ready for phrase lookups."""
    return f" {normalized_text} "


@lru_cache(maxsize=2048)
def _normalized_phrase(phrase: str) -> str:
    """Normalised, space-padded needle.

    Cached because the keyword lists are fixed while records are not: the same
    few dozen phrases get looked up once per record, for tens of thousands of
    records.
    """
    return f" {normalize(phrase)} "


def phrase_in_padded(padded_haystack: str, phrase: str) -> bool:
    """Phrase lookup against a haystack that is already normalised and padded.

    Scoring checks every keyword against the same record, so normalising the
    haystack inside the lookup would redo that work once per keyword — on a
    full sweep that is the dominant cost.
    """
    return _normalized_phrase(phrase) in padded_haystack


def flatten_record(record: dict) -> str:
    """Concatenate every value of a Socrata record into one searchable blob.

    We match against the whole record rather than named columns on purpose: the
    open-data schema shifts between refreshes, and entities are inconsistent
    about which field carries the real description of the object. Searching the
    blob costs nothing and cannot miss a match because of a renamed column.
    """
    parts = []
    for value in record.values():
        if isinstance(value, dict):
            parts.append(flatten_record(value))
        elif isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif value is not None:
            parts.append(str(value))
    return normalize(" ".join(parts))
