"""Deterministic language and script detection for conversation mirroring.

Language and script are separate decisions.  In particular, Hindi vocabulary
written with Latin characters is Hinglish/Roman, not Devanagari Hindi.  The
detector intentionally favours the prior turn for short neutral replies so a
conversation does not change voice merely because the user answered "yes".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
LATIN_WORD_RE = re.compile(r"[a-z]+")

# Common Roman-Hindi words.  A few of these can occur in English names, so the
# detector normally requires two matches unless a highly distinctive term is
# present.
ROMAN_HINDI_TERMS = frozenset(
    {
        "aap", "aapka", "aapke", "aapki", "abhi", "agar", "aur", "bahar",
        "bachcha", "bachche", "bachchon", "bandook", "bhi", "chaku", "dhamki",
        "di", "dijiye", "dega", "degi", "de", "ghar", "hai", "hain", "ho",
        "hua", "hui", "hun", "jaan", "karega", "kar", "khatra", "ki", "ko",
        "lekin", "main", "maar", "maarne", "marne", "mera", "mere", "meri",
        "mujhe", "nahi", "ne", "paas", "pati", "patni", "peet", "peecha",
        "raha", "rahi", "safe", "se", "tha", "thi", "turant", "wapas", "woh",
    }
)

DISTINCTIVE_ROMAN_HINDI_TERMS = frozenset(
    {
        "aapka", "aapke", "aapki", "bachche", "bachchon", "bandook", "chaku",
        "dhamki", "dijiye", "khatra", "maarne", "marne", "mujhe", "pati",
        "patni", "peecha", "turant", "wapas",
    }
)

STYLE_NEUTRAL_REPLIES = frozenset(
    {
        "yes", "no", "ok", "okay", "safe", "correct", "right", "continue",
        "not yet", "yes please", "today", "yesterday", "haan", "han", "ji",
        "nahi", "nahin", "nhi",
    }
)


@dataclass(frozen=True)
class LanguageScript:
    language: str = "english"  # english | hindi | hinglish | other
    script: str = "roman"  # roman | devanagari

    def to_dict(self) -> dict[str, str]:
        return {"language": self.language, "script": self.script}


def detect_language_script(
    text: str,
    prior_language: Optional[str] = None,
    prior_script: Optional[str] = None,
) -> LanguageScript:
    """Detect the current turn's language/script, preserving neutral replies."""
    value = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    if DEVANAGARI_RE.search(value):
        return LanguageScript(language="hindi", script="devanagari")

    words = LATIN_WORD_RE.findall(value)
    normalized = " ".join(words)
    if prior_language and normalized in STYLE_NEUTRAL_REPLIES:
        return LanguageScript(
            language=prior_language,
            script=prior_script or ("devanagari" if prior_language == "hindi" else "roman"),
        )

    matches = [word for word in words if word in ROMAN_HINDI_TERMS]
    distinctive = any(word in DISTINCTIVE_ROMAN_HINDI_TERMS for word in words)
    if distinctive or len(matches) >= 2:
        return LanguageScript(language="hinglish", script="roman")

    if words:
        return LanguageScript(language="english", script="roman")

    if prior_language:
        return LanguageScript(
            language=prior_language,
            script=prior_script or ("devanagari" if prior_language == "hindi" else "roman"),
        )
    return LanguageScript()
