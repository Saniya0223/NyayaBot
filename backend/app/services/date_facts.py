"""Deterministic date normalisation, with an honest year-precision flag.

H8: a user said "I resigned in July" and the reply told them their claim had
been time-barred "in July 2026", because the model supplied "July 2023" and
nothing in the backend could tell an asserted year from a stated one. There is
no date normalizer anywhere in the backend today; `incident_date` and
`vacating_date` are free text end to end.

The rule enforced here is a string-containment invariant, not a vocabulary:

    A four-digit year may appear in a stored DATE-typed fact only if that year
    literally appears in the user's own text for this case, or the resolver
    derived it from a relative phrase against a known `today`.

Everything else keeps the month and records `year_known = False`, so downstream
consumers (documents now, limitation arithmetic in a later cycle) can tell the
difference instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


# Precision of a stored date fact.
EXACT = "exact"              # day, month and year all stated
MONTH_YEAR = "month_year"    # month and year, no day
DAY_MONTH = "day_month"      # day and month, year unknown
MONTH_ONLY = "month_only"    # month named, no year
YEAR_ONLY = "year_only"      # bare year
RELATIVE = "relative"        # resolved against `today`
UNKNOWN = "unknown"

MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    # Devanagari
    "जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6,
    "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10,
    "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}

MONTH_LABELS = (
    "", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)

_MONTH_ALTERNATION = "|".join(
    re.escape(name) for name in sorted(MONTH_NAMES, key=len, reverse=True)
)

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

_NUMERIC_RE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?!\d)")
_DAY_MONTH_YEAR_RE = re.compile(
    rf"(?<!\w)(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALTERNATION})"
    rf"(?:,?\s+((?:19|20)\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"(?<!\w)({_MONTH_ALTERNATION})\s+(\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s+((?:19|20)\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(
    rf"(?<!\w)({_MONTH_ALTERNATION})\.?,?\s+((?:19|20)\d{{2}})(?!\w)", re.IGNORECASE
)
_MONTH_RE = re.compile(rf"(?<!\w)({_MONTH_ALTERNATION})(?!\w)", re.IGNORECASE)

# Relative forms that carry real information and can be resolved honestly.
_UNIT_DAYS = {"day": 1, "days": 1, "din": 1, "दिन": 1,
              "week": 7, "weeks": 7, "hafte": 7, "hafta": 7, "हफ्ते": 7, "सप्ताह": 7}
_UNIT_MONTHS = {"month": 1, "months": 1, "mahine": 1, "mahina": 1, "maheene": 1,
                "महीने": 1, "माह": 1}
_UNIT_YEARS = {"year": 1, "years": 1, "saal": 1, "sal": 1, "varsh": 1,
               "साल": 1, "वर्ष": 1}

_AGO_RE = re.compile(
    r"(?<!\w)(\d{1,3}|ek|do|teen|char|paanch|panch|one|two|three|four|five)\s+"
    r"(day|days|din|दिन|week|weeks|hafte|hafta|हफ्ते|सप्ताह|month|months|mahine|mahina|maheene|महीने|माह|year|years|saal|sal|varsh|साल|वर्ष)"
    r"\s*(?:pehle|pahle|back|ago|purane|पहले)(?!\w)",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3,
    "char": 4, "four": 4, "paanch": 5, "panch": 5, "five": 5,
}

_YESTERDAY_RE = re.compile(r"(?<!\w)(yesterday|kal raat|kal|कल)(?!\w)", re.IGNORECASE)
_TODAY_RE = re.compile(r"(?<!\w)(today|aaj|आज)(?!\w)", re.IGNORECASE)
_LAST_MONTH_RE = re.compile(
    r"(?<!\w)(last month|pichhle mahine|pichle mahine|pichhle mahina|पिछले महीने)(?!\w)",
    re.IGNORECASE,
)
_LAST_WEEK_RE = re.compile(
    r"(?<!\w)(last week|pichhle hafte|pichle hafte|पिछले हफ्ते)(?!\w)", re.IGNORECASE
)


@dataclass(frozen=True)
class DateFact:
    """A stored date plus what is actually known about it."""

    value: str
    precision: str
    year_known: bool
    resolved_date: Optional[str] = None
    raw: str = ""


def _shift_months(anchor: date, months: int) -> date:
    month_index = anchor.month - 1 - months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _quantity(token: str) -> int:
    token = token.strip().casefold()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token, 1)


def normalize_date_phrase(text: str, today: Optional[date] = None) -> Optional[DateFact]:
    """Normalise the first date-like phrase in `text`, or return None.

    Relative phrases ARE resolved: "3 mahine pehle" is real information the
    product currently discards. Absolute phrases are stored as written, with the
    year flag reflecting whether a year was actually present.
    """
    value = (text or "").strip()
    if not value:
        return None
    anchor = today or date.today()

    # --- resolvable relative forms -------------------------------------------
    match = _AGO_RE.search(value)
    if match:
        count = _quantity(match.group(1))
        unit = match.group(2).casefold()
        if unit in _UNIT_DAYS:
            resolved = anchor - timedelta(days=count * _UNIT_DAYS[unit])
        elif unit in _UNIT_MONTHS:
            resolved = _shift_months(anchor, count)
        else:
            resolved = _shift_months(anchor, count * 12)
        return DateFact(resolved.isoformat(), RELATIVE, True, resolved.isoformat(), match.group(0))

    if _LAST_MONTH_RE.search(value):
        resolved = _shift_months(anchor, 1)
        return DateFact(resolved.isoformat(), RELATIVE, True, resolved.isoformat(), _LAST_MONTH_RE.search(value).group(0))
    if _LAST_WEEK_RE.search(value):
        resolved = anchor - timedelta(days=7)
        return DateFact(resolved.isoformat(), RELATIVE, True, resolved.isoformat(), _LAST_WEEK_RE.search(value).group(0))
    if _YESTERDAY_RE.search(value):
        resolved = anchor - timedelta(days=1)
        return DateFact(resolved.isoformat(), RELATIVE, True, resolved.isoformat(), _YESTERDAY_RE.search(value).group(0))
    if _TODAY_RE.search(value):
        return DateFact(anchor.isoformat(), RELATIVE, True, anchor.isoformat(), _TODAY_RE.search(value).group(0))

    # --- absolute numeric ----------------------------------------------------
    match = _NUMERIC_RE.search(value)
    if match:
        day, month, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                resolved = date(year, month, day)
            except ValueError:
                resolved = None
            return DateFact(
                match.group(0), EXACT, True,
                resolved.isoformat() if resolved else None, match.group(0),
            )

    # --- "12 July 2023" / "12 July" ------------------------------------------
    for pattern, order in ((_DAY_MONTH_YEAR_RE, "dmy"), (_MONTH_DAY_YEAR_RE, "mdy")):
        match = pattern.search(value)
        if not match:
            continue
        if order == "dmy":
            day_text, month_text, year_text = match.group(1), match.group(2), match.group(3)
        else:
            month_text, day_text, year_text = match.group(1), match.group(2), match.group(3)
        month = MONTH_NAMES[month_text.casefold()]
        label = f"{int(day_text)} {MONTH_LABELS[month]}"
        if year_text:
            resolved = None
            try:
                resolved = date(int(year_text), month, int(day_text))
            except ValueError:
                pass
            return DateFact(
                f"{label} {year_text}", EXACT, True,
                resolved.isoformat() if resolved else None, match.group(0),
            )
        return DateFact(label, DAY_MONTH, False, None, match.group(0))

    # --- "July 2023" ---------------------------------------------------------
    match = _MONTH_YEAR_RE.search(value)
    if match:
        month = MONTH_NAMES[match.group(1).casefold()]
        return DateFact(
            f"{MONTH_LABELS[month]} {match.group(2)}", MONTH_YEAR, True,
            f"{match.group(2)}-{month:02d}-01", match.group(0),
        )

    # --- bare "July" ---------------------------------------------------------
    match = _MONTH_RE.search(value)
    if match:
        month = MONTH_NAMES[match.group(1).casefold()]
        return DateFact(MONTH_LABELS[month], MONTH_ONLY, False, None, match.group(0))

    # --- bare year -----------------------------------------------------------
    match = _YEAR_RE.search(value)
    if match:
        return DateFact(match.group(1), YEAR_ONLY, True, None, match.group(0))

    return None


def years_in(text: str) -> set[str]:
    """Four-digit years literally present in `text`."""
    return set(_YEAR_RE.findall(text or ""))


def strip_unstated_year(value: str, spoken_text: str) -> tuple[str, bool, bool]:
    """Apply the year invariant to one stored date string.

    Returns `(value, year_known, changed)`. A year the user never wrote is
    removed rather than trusted; the month-level remainder is kept because it is
    genuine information.
    """
    if not isinstance(value, str) or not value.strip():
        return value, False, False
    stored_years = _YEAR_RE.findall(value)
    if not stored_years:
        return value, False, False
    spoken = years_in(spoken_text)
    unstated = [year for year in stored_years if year not in spoken]
    if not unstated:
        return value, True, False
    cleaned = value
    for year in unstated:
        cleaned = re.sub(rf"(?<!\d){re.escape(year)}(?!\d)", " ", cleaned)
    # Tidy the separators the removed year left behind ("July , " -> "July").
    cleaned = re.sub(r"\s*[,/\-]\s*(?=$|[,/\-])", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,/-.")
    if not cleaned:
        # The value was nothing but the invented year; there is no honest
        # remainder to keep.
        return "", False, True
    return cleaned, False, True


def same_date_ignoring_year(stored: str, candidate: str) -> bool:
    """True when two date strings differ only by a four-digit year.

    Needed because the year invariant is applied every turn while the model
    re-asserts the same invented year on each one. Without this, the stored
    "10 August" and the model's "10 August 2026" would look like a factual
    conflict and hijack the turn.
    """
    if not isinstance(stored, str) or not isinstance(candidate, str):
        return False

    def core(value: str) -> str:
        value = _YEAR_RE.sub(" ", value)
        return re.sub(r"[\s,/\-.]+", " ", value).strip().casefold()

    stored_core = core(stored)
    return bool(stored_core) and stored_core == core(candidate)
