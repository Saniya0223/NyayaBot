"""Shared deterministic fact backfill, wired into every conversation path.

Cycle-3 root cause (S2): `ConversationalLegalAgent._extract_entities_into_profile`
had exactly one call site, and that call site only runs when the provider is
*not* configured. On the healthy LLM path and on the 429 path nothing
deterministic ever looked at the user's words, so an amount, a city or a party
name stated during an outage was lost permanently.

This module holds the *structural* extractors - the ones that read a shape
("Rs 85000", "12 July 2025", a corporate suffix, a city name) rather than
guessing intent from a keyword - and is invoked from all three paths. The loose
category keyword heuristics stay in `conversation_agent`, on the offline demo
path only: promoting `"refund" -> seller_contacted = True` to the live path
would be a new false-positive source.

Two invariants hold everywhere in here:

* **Fill only what is empty.** The model and the user always win; the
  deterministic layer never overwrites a value somebody else supplied. For
  money, `0`/`0.0` counts as empty, matching `_apply_extraction`.
* **Never assert what was not said.** An unknown city leaves `user_state`
  `None`; a date with no stated year is stored without one.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

from app.domains import domain_registry
from app.domains.compatibility import PROFILE_FACT_FIELDS, read_profile_fact
from app.domains.contracts import FactValueType
from app.services.date_facts import (
    MONTH_NAMES,
    normalize_date_phrase,
    strip_unstated_year,
    years_in,
)
from app.services.jurisdiction import (
    PLACE_WORDS,
    canonical_state,
    resolve_jurisdiction,
    state_for_city,
)
from app.services.pending_interaction import valid_for_case


logger = logging.getLogger("uvicorn.error")


# Date-typed facts stored directly on the profile.
DATE_FACT_FIELDS = ("incident_date", "vacating_date")


# --------------------------------------------------------------------- amounts

_BARE_NUMBER_RE = re.compile(r"^\s*(\d[\d,]*(?:\.\d{1,2})?)\s*[.!]?\s*$")


def amount_from_text(text: str) -> Optional[float]:
    """Parse an Indian-format money figure. Verbatim from the cycle-1 parser."""
    lakh_match = re.search(
        r"(?:rs\.?|inr|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:lakh|lac|lakhs|lacs|लाख)\b",
        text,
        re.IGNORECASE,
    )
    if lakh_match:
        return float(lakh_match.group(1).replace(",", "")) * 100000.0

    crore_match = re.search(
        r"(?:rs\.?|inr|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:crore|cr|crores|करोड़)\b",
        text,
        re.IGNORECASE,
    )
    if crore_match:
        return float(crore_match.group(1).replace(",", "")) * 10000000.0

    match = re.search(
        r"(?:rs\.?|inr|₹)\s*(\d[\d,]*(?:\.\d{1,2})?)|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees|rs|inr|rupaye|रुपये|k\b)",
        text,
        re.IGNORECASE,
    )
    if match:
        raw = (match.group(1) or match.group(2)).replace(",", "")
        amount = float(raw)
        if match.group(0).lower().strip().endswith("k"):
            amount *= 1000
        return amount
    short = re.search(r"\b(\d+(?:\.\d+)?)\s*k\b", text, re.IGNORECASE)
    if short:
        return float(short.group(1)) * 1000
    large_number = re.search(r"\b(\d{4,8})\b", text)
    if large_number:
        value = float(large_number.group(1))
        if 1900 <= value <= 2100:
            return None
        return value
    return None


# ----------------------------------------------------------------- party names

_CORP_SUFFIX = (
    r"(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Ltd\.?|LLP|Inc\.?|Corporation|"
    r"Technologies|Solutions|Services|Enterprises|Industries|Travels|Tours|"
    r"Motors|Hospital|Infotech|Systems|Finance|Realty|Builders|Developers)"
)
_CORP_RE = re.compile(rf"(?<!\w)((?:[A-Z][\w&.\-]*\s+){{1,4}}{_CORP_SUFFIX})(?!\w)")

_PROPER_RUN = r"[A-Z][A-Za-z0-9&.\-]{2,}(?:\s+[A-Z][A-Za-z0-9&.\-]+){0,2}"

# role + apposition: "landlord Raj Verma", "employer ka naam Acme hai"
_ROLE_APPOSITION_RE = re.compile(
    r"(?i:landlord|employer|seller|operator|builder|company|makan malik|maalik)\s+"
    r"(?i:(?:ka\s+naam|name\s+is|named|is|called)\s+)?"
    rf"({_PROPER_RUN})(?!\w)"
)

# English transactional/adversarial frames
_ENGLISH_FRAME_RE = re.compile(
    r"(?i:ordered|order|bought|buying|purchased|booked|hired|billed|charged)\s+"
    r"(?i:(?:it|them|this|that)\s+)?(?i:from|with|on|through)\s+"
    rf"({_PROPER_RUN})(?!\w)"
)
_ADVERSARIAL_FRAME_RE = re.compile(
    r"(?i:against|versus|vs\.?|sued|scammed\s+by|defrauded\s+by|cheated\s+by)\s+"
    rf"({_PROPER_RUN})(?!\w)"
)
# Bare "from / by / through X". Broad, so the rejection list below carries the
# weight: a place, a month, a weekday, a role word or a bank is never a party.
_FROM_FRAME_RE = re.compile(rf"(?i:from|by|through)\s+({_PROPER_RUN})(?!\w)")

# Hinglish postposition frames on a capitalised proper-noun run. `ka/ki/ke` are
# deliberately NOT used: "3 mahine ka", "kaam ke liye" would make every quantity
# phrase a counterparty.
_HINGLISH_CAP_RE = re.compile(
    rf"(?<!\w)({_PROPER_RUN})\s+(?i:se|ne|ko|wale|walon|walo)(?!\w)"
)

# The same frame for an all-lowercase brand token ("flipkart se ... order ki
# thi"). Deliberately narrow: one token, >= 5 letters, not a common word, and
# the sentence must carry a transactional verb.
_HINGLISH_LOWER_RE = re.compile(r"(?<!\w)([a-z][a-z0-9]{4,19})\s+(?i:se|ne)(?!\w)")
_TRANSACTIONAL_RE = re.compile(
    r"(?i)(?<!\w)(?:order|ordered|kharid|kharida|kharidi|purchase|purchased|bought|"
    r"book|booked|mangwaya|manga|delivery|refund|return)(?!\w)"
)

# brand-shaped token adjacent to a domain noun ("Zerodha app", "Acme portal")
_APP_ADJACENT_RE = re.compile(
    r"(?<!\w)([A-Z][A-Za-z0-9]{2,})\s+(?i:app|portal|website|platform|marketplace)(?!\w)"
)

# The genuinely closed class: role words, generic nouns and function words that
# are never a party name. A list belongs here, not on the recognition side.
_ROLE_AND_GENERIC_WORDS = frozenset({
    "landlord", "landlords", "seller", "sellers", "employer", "employers",
    "company", "companies", "operator", "manager", "hr", "startup", "pg",
    "bank", "app", "agent", "builder", "owner", "malik", "maalik", "thekedar",
    "dukandar", "shopkeeper", "vendor", "merchant", "platform", "tenant",
    "boss", "contractor", "society", "police", "thana", "sho", "court",
    "lawyer", "advocate", "wakeel", "customer", "support", "service", "team",
    "sir", "madam", "bhai", "ji", "main", "mera", "meri", "mere", "unhone",
    "uska", "unka", "iska", "yeh", "woh", "wo", "they", "them", "he", "she",
    "the", "this", "that", "there", "and", "but", "kal", "aaj", "raat", "din",
    "mahine", "mahina", "saal", "hafte", "paisa", "paise", "rupees", "rupaye",
    "deposit", "rent", "salary", "refund", "order", "invoice", "receipt",
    "flat", "room", "ghar", "office", "shop", "dukan", "link", "sms", "otp",
    "upi", "account", "card", "number", "id", "utr", "rrn", "fir", "sp",
    "consumer", "notice", "complaint", "letter", "document", "pdf",
    # Hinglish/English function words. A capitalised run that *starts* with one
    # of these is a sentence, not a name: "Aur SP ko complaint kaise karun?"
    # (W1-05 t3) was being recorded as the opposite party "Aur SP".
    "aur", "ya", "par", "lekin", "kya", "kaise", "kab", "kahan", "kyun", "kyu",
    "phir", "toh", "to", "bhi", "nahi", "nahin", "abhi", "ab", "agar", "jab",
    "iske", "uske", "inka", "unke", "koi", "kuch", "sab", "hai", "hain", "tha",
    "thi", "the", "hum", "humne", "humara", "tum", "aap", "aapka", "aapke",
    "our", "your", "my", "his", "her", "its", "their", "also", "then", "when",
    "what", "which", "who", "how", "why", "can", "could", "should", "would",
    "so", "is", "are", "was", "were", "for", "with", "not", "no", "yes",
    "मालिक", "मकान", "कंपनी", "दुकानदार", "ठेकेदार", "बैंक", "एजेंट", "पुलिस", "थाना",
    "और", "या", "लेकिन", "क्या", "कैसे", "अभी", "अगर", "जब",
})

_MONTH_WORDS = frozenset(MONTH_NAMES)
_WEEKDAY_WORDS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})

# Banks / wallets. In CYBER_FRAUD these are the money *rail*, usually the user's
# own institution - recording one as the opposite party would be wrong (W1-04).
BANK_NAMES: tuple[str, ...] = (
    "State Bank of India", "SBI", "HDFC Bank", "HDFC", "ICICI Bank", "ICICI",
    "Axis Bank", "Kotak Mahindra", "Kotak", "Punjab National Bank", "PNB",
    "Bank of Baroda", "Canara Bank", "Union Bank", "IDFC First", "IDFC",
    "Yes Bank", "IndusInd", "Paytm", "PhonePe", "Google Pay", "GPay",
    "Amazon Pay", "BHIM", "Airtel Payments Bank",
)
_BANK_LOOKUP = {name.casefold(): name for name in BANK_NAMES}
_BANK_RE = re.compile(
    "(?<!\\w)(?:" + "|".join(re.escape(name) for name in sorted(BANK_NAMES, key=len, reverse=True)) + ")(?!\\w)",
    re.IGNORECASE,
)

# Counterparty *role*, a closed product vocabulary. Ordered: the more specific
# phrase wins ("co-living PG" before the word "landlord" in the same turn).
_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("PG operator", r"(?<!\w)(?:pg\s+operator|co-?living|paying\s+guest|pg\s+me|pg\s+owner|pg\s+operator)(?!\w)"),
    ("tour operator", r"(?<!\w)(?:tour\s+operator|travel\s+agent|travel\s+agency|tour\s+company)(?!\w)"),
    ("trading app", r"(?<!\w)(?:trading\s+app|trading\s+platform|investment\s+app)(?!\w)"),
    ("landlord", r"(?<!\w)(?:landlord|land\s+lord|makan\s+malik|makaan\s+malik|मकान\s*मालिक)(?!\w)"),
    ("employer", r"(?<!\w)(?:employer|employer's|startup|my\s+boss|hr\s+department)(?!\w)"),
    ("builder", r"(?<!\w)(?:builder|developer\s+company)(?!\w)"),
    ("seller", r"(?<!\w)(?:seller|shopkeeper|dukandar|merchant|vendor|दुकानदार)(?!\w)"),
)
_COMPILED_ROLES = tuple(
    (role, re.compile(pattern, re.IGNORECASE)) for role, pattern in _ROLE_PATTERNS
)


def _rejected_party(candidate: str, profile: Any) -> bool:
    value = candidate.strip().strip(".,;:").strip()
    if len(value) < 3:
        return True
    folded = value.casefold()
    if folded in PLACE_WORDS:
        return True
    tokens = [token for token in re.split(r"[\s.]+", folded) if token]
    if not tokens:
        return True
    # A proper-noun run never *begins* with a role word, a function word, a
    # weekday, a month or a place. Requiring every token to be rejectable let
    # "Aur SP" (W1-05 t3) and "Sunday. Our" (W1-07 t2) through, because their
    # second token was an ordinary word. The head of the run is what decides.
    if (
        tokens[0] in _ROLE_AND_GENERIC_WORDS or tokens[0] in _MONTH_WORDS
        or tokens[0] in _WEEKDAY_WORDS or tokens[0] in PLACE_WORDS
    ):
        return True
    if all(
        token in _ROLE_AND_GENERIC_WORDS or token in _MONTH_WORDS
        or token in _WEEKDAY_WORDS or token in PLACE_WORDS
        for token in tokens
    ):
        return True
    own_name = (getattr(profile, "user_name", None) or "").strip().casefold()
    if own_name and folded == own_name:
        return True
    return False


def _clean_party(candidate: str) -> str:
    # A proper-noun run must not cross a sentence boundary. `_PROPER_RUN` allows
    # "." inside a token so "Pvt. Ltd." survives, which also let "by Sunday. Our
    # agreement..." match as one candidate. Cut at the first full stop that is
    # followed by whitespace - that is a sentence break, not an abbreviation.
    candidate = re.split(r"\.\s", candidate, maxsplit=1)[0]
    return re.sub(r"\s+", " ", candidate).strip().strip(".,;:-").strip()


def _party_candidate(text: str, profile: Any) -> Optional[str]:
    """Structural counterparty guess, or None. Frames, never a brand list."""
    category = getattr(profile, "category", "GENERAL")

    match = _CORP_RE.search(text)
    if match:
        candidate = _clean_party(match.group(1))
        if not _rejected_party(candidate, profile):
            return candidate

    match = _ADVERSARIAL_FRAME_RE.search(text)
    if match:
        candidate = _clean_party(match.group(1))
        if not _rejected_party(candidate, profile):
            return candidate

    if category == "CYBER_FRAUD":
        # In fraud the counterparty is an unknown scammer, and the institutions
        # the user names are the money *rail*, usually their own (W1-04:
        # "PhonePe se gaya, SBI account hai"). So the Hinglish postposition and
        # lowercase-brand frames, which read the rail, are switched off here;
        # an explicitly named lender ("a loan ... from QuickCredit") still
        # counts.
        match = _FROM_FRAME_RE.search(text)
        if match:
            candidate = _clean_party(match.group(1))
            if candidate.casefold() not in _BANK_LOOKUP and not _rejected_party(candidate, profile):
                return candidate
        return None

    for pattern in (_ROLE_APPOSITION_RE, _ENGLISH_FRAME_RE, _HINGLISH_CAP_RE,
                    _APP_ADJACENT_RE, _FROM_FRAME_RE):
        match = pattern.search(text)
        if not match:
            continue
        candidate = _clean_party(match.group(1))
        if candidate.casefold() in _BANK_LOOKUP:
            continue
        if not _rejected_party(candidate, profile):
            return candidate

    if _TRANSACTIONAL_RE.search(text):
        for match in _HINGLISH_LOWER_RE.finditer(text):
            candidate = match.group(1)
            if candidate.casefold() in _ROLE_AND_GENERIC_WORDS:
                continue
            if candidate.casefold() in PLACE_WORDS or candidate.casefold() in _MONTH_WORDS:
                continue
            if candidate.casefold() in _BANK_LOOKUP:
                continue
            return candidate.title()
    return None


def _role_candidate(text: str) -> Optional[str]:
    for role, pattern in _COMPILED_ROLES:
        if pattern.search(text):
            return role
    return None


# ------------------------------------------------------------------- utilities


def _is_empty(value: Any) -> bool:
    if value is None or value == "" or value == []:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _current(profile: Any, field: str) -> Any:
    if field in PROFILE_FACT_FIELDS and hasattr(profile, field):
        return getattr(profile, field)
    return (getattr(profile, "key_facts", {}) or {}).get(field)


def _fill(
    profile: Any,
    field: str,
    value: Any,
    *,
    confidence: float = 0.9,
    needs_confirmation: bool = False,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Set `field` only when it is currently empty. Never overwrites."""
    if value is None or value == "":
        return False
    if not _is_empty(_current(profile, field)):
        return False
    if field in PROFILE_FACT_FIELDS and hasattr(profile, field):
        setattr(profile, field, value)
    else:
        profile.key_facts[field] = value
    metadata = {
        "value": value,
        "source": "deterministic_backfill",
        "confidence": confidence,
        "confirmed": False,
    }
    if needs_confirmation:
        metadata["needs_confirmation"] = True
    if extra_metadata:
        metadata.update(extra_metadata)
    profile.fact_metadata[field] = metadata
    return True


# ------------------------------------------------------------------ date facts


_EVENT_INDICATOR_RE = re.compile(
    r"(?i)(?<!\w)(?:happen|happened|hua|hui|gaya|gayi|kat|kata|kate|paid|pay|"
    r"order|ordered|bought|purchase|purchased|vacate|vacated|moved|left|"
    r"resign|resigned|threat|threatened|dhamki|complaint|fraud|scam|click|"
    r"clicked|transfer|transferred|debit|debited|sent|received|nikla|nikli|"
    r"diya|liya|mila|tha|thi|the)(?!\w)|धमकी|हुआ|हुई|गया|दिया|लिया|मिला"
)
_VACATING_TERMS = ("vacat", "moved out", "move out", "handover", "khali", "left the flat")


def record_stated_years(profile: Any, text: str) -> None:
    """Remember every four-digit year the user has literally written.

    The invariant below is a containment check against the user's own words,
    and the only text available at call time is the trimmed history window.
    A year stated on turn 1 would scroll out of that window and then look
    invented on turn 9, so the evidence is accumulated on the case instead of
    re-derived from a sliding window.
    """
    found = years_in(text)
    if not found:
        return
    known = set((profile.key_facts or {}).get("stated_years") or [])
    if found <= known:
        return
    profile.key_facts["stated_years"] = sorted(known | found)


def enforce_date_year_invariant(profile: Any, spoken_text: str) -> list[str]:
    """Strip any four-digit year the user never actually wrote.

    Returns the fields that were changed. A date whose year was already vetted
    (`fact_metadata[...]["year_known"] is True`) is left alone, which is how a
    resolved relative date keeps its computed year.
    """
    changed: list[str] = []
    remembered = " ".join((profile.key_facts or {}).get("stated_years") or [])
    spoken_text = f"{spoken_text} {remembered}"
    for field in DATE_FACT_FIELDS:
        value = getattr(profile, field, None)
        if not isinstance(value, str) or not value.strip():
            continue
        metadata = dict((profile.fact_metadata or {}).get(field) or {})
        if metadata.get("year_known") is True:
            continue
        source = str(metadata.get("source") or "")
        if metadata.get("confirmed") or source.startswith("upload:") or source == "document_confirmation":
            # A year the user confirmed, or one a document supplied, was stated.
            metadata["year_known"] = True
            profile.fact_metadata[field] = metadata
            continue
        cleaned, year_known, was_changed = strip_unstated_year(value, spoken_text)
        if was_changed:
            logger.info(
                "case_id=%s field=%s event=unstated_year_stripped",
                getattr(profile, "case_id", None), field,
            )
            setattr(profile, field, cleaned or None)
            changed.append(field)
        metadata["year_known"] = year_known
        metadata.setdefault("value", cleaned)
        if was_changed:
            metadata["value"] = cleaned
        profile.fact_metadata[field] = metadata
    return changed


def _backfill_dates(text: str, profile: Any, today: Optional[date]) -> list[str]:
    fact = normalize_date_phrase(text, today)
    if fact is None:
        return []
    if fact.precision == "relative" and not _EVENT_INDICATOR_RE.search(text):
        # A bare "today"/"kal" in a question is not a statement about when the
        # incident happened.
        return []
    lowered = text.casefold()
    if getattr(profile, "category", None) == "HOUSING_TENANT" and any(
        term in lowered for term in _VACATING_TERMS
    ):
        field = "vacating_date"
    else:
        field = "incident_date"
    filled = _fill(
        profile, field, fact.value, confidence=0.9,
        extra_metadata={"year_known": fact.year_known, "precision": fact.precision},
    )
    return [field] if filled else []


# --------------------------------------------------------------- bare numerics


def _pending_numeric_target(profile: Any) -> Optional[tuple[str, FactValueType]]:
    pending = getattr(profile, "pending_interaction", None)
    if not pending or len(pending.target_keys) != 1:
        return None
    if not valid_for_case(pending, profile):
        return None
    key = pending.target_keys[0]
    definition = next(
        (item for item in domain_registry.resolve(getattr(profile, "category", None)).facts
         if item.key == key),
        None,
    )
    if definition is None or definition.value_type not in {FactValueType.MONEY, FactValueType.IDENTIFIER}:
        return None
    return key, definition.value_type


def _handle_bare_number(text: str, profile: Any) -> bool:
    """True when the whole turn was a number, so nothing else should be read."""
    match = _BARE_NUMBER_RE.match(text or "")
    if not match:
        return False
    raw = match.group(1).replace(",", "")
    target = _pending_numeric_target(profile)
    if target is not None:
        key, value_type = target
        value: Any = float(raw) if value_type == FactValueType.MONEY else raw
        _fill(profile, key, value, confidence=0.95)
        profile.key_facts.pop("unbound_numeric", None)
        return True

    # No referent. Silently dropping the number (today) and silently calling it
    # an order number (today's reply) are both worse than recording it as
    # unbound so the next turn can ask what it was.
    already_bound = any(
        str(_current(profile, field) or "").replace(".0", "") == raw
        for field in ("disputed_amount", "transaction_id", "order_reference_id",
                      "payment_transaction_id", "shipment_tracking_id", "monthly_salary")
    )
    if not already_bound:
        profile.key_facts["unbound_numeric"] = {"value": raw, "turn_text": (text or "").strip()}
    return True


# ------------------------------------------------------------------- stated unknown

_STATED_UNKNOWN_RE = re.compile(
    r"(?:don't have|do not have|dont have|don't know|do not know|genuinely don't know|"
    r"cannot provide|can't provide|nahi pata|nahi hai|nahin hai|pata nahi)",
    re.IGNORECASE,
)

_TRANSACTION_RE = re.compile(
    r"(?:transaction id|transaction ref|utr|rrn|reference number|ref no|"
    r"disbursement reference|loan reference|disbursement ref|loan ref|reference)"
    r"\s*(?:is|:|-)?\s*([A-Z0-9\-/]{6,40})",
    re.IGNORECASE,
)

_NAME_RE = re.compile(
    r"(?:my name is|i am|mera naam|मेरा नाम)\s+([A-Z][a-zA-Z\s]{2,35}?)(?:[\.,]|\band\b|$)",
    re.IGNORECASE,
)

_STATION_RE = re.compile(
    r"(?:police station|thana|sho at)\s+(?:is|:|-)?\s*([A-Z][a-zA-Z\s]{2,40}?)(?:[\.,]|$)",
    re.IGNORECASE,
)

_ADDRESS_RE = re.compile(
    r"(?:property address is|rented property at|premises at)\s+(.{5,100}?)(?:[\.;]|$)",
    re.IGNORECASE,
)

# The user's *own* accounts, which must not be read as a counterparty.
_OWN_ACCOUNT_TERMS = ("accounts don't end", "accounts do not end", "my hdfc and sbi")


def _record_stated_unknown(text: str, profile: Any) -> None:
    lowered = text.casefold()
    if not _STATED_UNKNOWN_RE.search(lowered):
        return
    stated = set((profile.key_facts or {}).get("stated_unknown_facts", []))
    added = False
    if any(term in lowered for term in ("utr", "transaction", "payment id", "payment")):
        stated.add("transaction_id")
        added = True
    if any(term in lowered for term in ("receiving bank", "bank details", "bank name", "bank account")):
        stated.add("bank_name")
        added = True
    if not added:
        return
    profile.key_facts["stated_unknown_facts"] = sorted(stated)
    for key in stated:
        metadata = profile.fact_metadata.get(key)
        if isinstance(metadata, dict):
            metadata["stated_unknown"] = True
        else:
            profile.fact_metadata[key] = {"stated_unknown": True, "source": "user_stated_unknown"}


# ------------------------------------------------------------------ entry point


def backfill_jurisdiction(text: str, profile: Any) -> list[str]:
    """Just the city/state half of the backfill, for the safety path.

    A safety turn returns before ordinary extraction, but users state where they
    live in the same sentence as the threat. Jurisdiction is inert - it never
    unlocks an action or a document by itself - so it is the one fact worth
    reading there.
    """
    if profile is None:
        return []
    filled: list[str] = []
    value = (text or "").strip()
    if value:
        city, state = resolve_jurisdiction(value)
        if city and _fill(profile, "user_city", city, confidence=0.94):
            filled.append("user_city")
        explicit_state = canonical_state(state) if state else None
        if explicit_state and _fill(profile, "user_state", explicit_state, confidence=0.94):
            filled.append("user_state")
    if getattr(profile, "user_city", None) and not getattr(profile, "user_state", None):
        derived = state_for_city(profile.user_city)
        if derived and _fill(profile, "user_state", derived, confidence=0.85):
            filled.append("user_state")
    return filled


def backfill_facts(
    text: str,
    profile: Any,
    *,
    prior_text: str = "",
    today: Optional[date] = None,
) -> list[str]:
    """Fill empty structural facts from `text`. Never overwrites, never guesses.

    `prior_text` is the user's earlier turns in this case; it is used only by the
    year invariant, which needs to know every year the user has actually written.
    """
    if profile is None:
        return []
    value = (text or "").strip()
    filled: list[str] = []
    record_stated_years(profile, value)

    if value and _handle_bare_number(value, profile):
        enforce_date_year_invariant(profile, f"{prior_text} {value}")
        return ["unbound_numeric"] if profile.key_facts.get("unbound_numeric") else []

    if value:
        amount = amount_from_text(value)
        if amount and _fill(profile, "disputed_amount", amount, confidence=0.94):
            filled.append("disputed_amount")

        city, state = resolve_jurisdiction(value)
        if city and _fill(profile, "user_city", city, confidence=0.94):
            filled.append("user_city")
        explicit_state = canonical_state(state) if state else None
        if explicit_state and _fill(profile, "user_state", explicit_state, confidence=0.94):
            filled.append("user_state")

        name_match = _NAME_RE.search(value)
        if name_match and _fill(profile, "user_name", name_match.group(1).strip(), confidence=0.92):
            filled.append("user_name")

        party = _party_candidate(value, profile)
        if party and _fill(
            profile, "opposite_party_name", party, confidence=0.6, needs_confirmation=True,
        ):
            filled.append("opposite_party_name")

        role = _role_candidate(value)
        if role and _fill(profile, "opposite_party_role", role, confidence=0.7):
            filled.append("opposite_party_role")

        filled.extend(_backfill_dates(value, profile, today))

        transaction = _TRANSACTION_RE.search(value)
        if transaction and _fill(profile, "transaction_id", transaction.group(1), confidence=0.96):
            filled.append("transaction_id")
            profile.key_facts.setdefault("loan_reference", transaction.group(1))

        if not any(term in value.casefold() for term in _OWN_ACCOUNT_TERMS):
            bank_match = _BANK_RE.search(value)
            if bank_match:
                canonical = _BANK_LOOKUP.get(bank_match.group(0).casefold(), bank_match.group(0))
                if _fill(profile, "bank_name", canonical, confidence=0.9):
                    filled.append("bank_name")

        station = _STATION_RE.search(value)
        if station and _fill(profile, "police_station_name", station.group(1).strip(), confidence=0.9):
            filled.append("police_station_name")

        address = _ADDRESS_RE.search(value)
        if address and _fill(profile, "property_address", address.group(1).strip(), confidence=0.88):
            filled.append("property_address")

        _record_stated_unknown(value, profile)

    # Derive over the *profile*, not only this message: the city usually arrived
    # on an earlier turn, from the model. This is what actually clears
    # `user_state is null` on every case in wave 1.
    if getattr(profile, "user_city", None) and not getattr(profile, "user_state", None):
        derived = state_for_city(profile.user_city)
        if derived and _fill(profile, "user_state", derived, confidence=0.85):
            filled.append("user_state")

    enforce_date_year_invariant(profile, f"{prior_text} {value}")
    return filled
