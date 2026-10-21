"""Rule-based government-portal form filling: no LLM, no agent, no tokens.

Reads every visible field on the current page, works out which case value (if
any) each one asks for from its label / placeholder / name, and fills it. Unclear
fields are left for the user - a wrong guess in a legal complaint is worse than a
blank. Passwords, OTPs, CAPTCHAs, Aadhaar and PAN are never touched.

Layout:
  * `plan_fills(fields, values)` - pure matching/planning; unit-tested directly.
  * `scan_fields(page)` / `fill_page(page, values)` - thin Playwright driver.

It never clicks anything: no Submit, no Next, no links.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger("uvicorn.error")

# A page is treated as a complaint form only if this many distinct case values
# are asked for. Stops a lone "Email" newsletter box or a search field from being
# mistaken for the form (and from making a run look successful).
MIN_MATCHED_VALUES = 3
MIN_SCORE = 2
# Pause after each field so the live view can show it being filled.
FILL_PAUSE_SECONDS = 0.25
_MAX_LABEL_CHARS = 60

# ── Page scan ─────────────────────────────────────────────────────────────────
# Reads structure only. Field *values* are never returned - just whether a field
# already has one. Tags each element so it can be addressed again by index.

SCAN_JS = """
(() => {
  const clip = (s, n = 120) => String(s || '').replace(/\\s+/g, ' ').trim().slice(0, n);
  const visible = el => el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const labelText = label => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button').forEach(n => n.remove());
    return clip(copy.textContent);
  };
  const labelledBy = el => (el.getAttribute('aria-labelledby') || '').split(/\\s+/)
    .map(id => document.getElementById(id)).filter(Boolean)
    .map(n => clip(n.textContent)).join(' ');
  const short = text => text.length > 0 && text.length <= 80 ? text : '';
  const neighbour = el => {
    const cell = el.closest('td,th');
    if (cell && cell.previousElementSibling) {
      const t = short(clip(cell.previousElementSibling.textContent, 200));
      if (t) return t;
    }
    const prev = el.previousElementSibling;
    if (prev && !prev.matches('input,select,textarea,button')) {
      const t = short(clip(prev.textContent, 200));
      if (t) return t;
    }
    const parentPrev = el.parentElement && el.parentElement.previousElementSibling;
    if (parentPrev && parentPrev.matches('label,span,div,td,th,p,legend,b,strong,h3,h4')) {
      return short(clip(parentPrev.textContent, 200));
    }
    return '';
  };
  const out = [];
  Array.from(document.querySelectorAll('input, textarea, select')).forEach((el, i) => {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    if (type === 'hidden' || !visible(el)) return;
    el.setAttribute('data-nyaya-fill', String(i));
    const entry = {
      index: i, tag, type,
      label: Array.from(el.labels || []).map(labelText).join(' '),
      aria: clip(el.getAttribute('aria-label')) || labelledBy(el),
      placeholder: clip(el.getAttribute('placeholder')),
      name: clip(el.getAttribute('name'), 80),
      ident: clip(el.id, 80),
      neighbour: neighbour(el),
      disabled: Boolean(el.disabled),
      readonly: Boolean(el.readOnly),
      maxlength: el.maxLength > 0 ? el.maxLength : null,
    };
    if (tag === 'select') {
      entry.options = Array.from(el.options).slice(0, 400).map(o => clip(o.text, 80));
      const chosen = el.options[el.selectedIndex];
      entry.selected_text = chosen ? clip(chosen.text, 80) : '';
      entry.selected_value = chosen ? clip(chosen.value, 40) : '';
      entry.filled = false;
    } else {
      entry.options = [];
      entry.filled = type === 'checkbox' || type === 'radio' ? Boolean(el.checked) : el.value.length > 0;
    }
    out.push(entry);
  });
  return out;
})()
"""

# ── Field model ───────────────────────────────────────────────────────────────


@dataclass
class FieldInfo:
    frame: int
    index: int
    tag: str
    type: str
    label: str = ""
    section: str = ""
    aria: str = ""
    placeholder: str = ""
    name: str = ""
    ident: str = ""
    neighbour: str = ""
    options: list[str] = field(default_factory=list)
    selected_text: str = ""
    selected_value: str = ""
    filled: bool = False
    disabled: bool = False
    readonly: bool = False
    maxlength: int | None = None

    @property
    def display(self) -> str:
        text = self.label or self.aria or self.placeholder or self.neighbour or self.name or self.ident
        return _clip(text) or "Unlabelled field"


def field_from_scan(frame: int, raw: dict) -> FieldInfo:
    return FieldInfo(
        frame=frame, index=int(raw.get("index", 0)), tag=str(raw.get("tag", "input")),
        type=str(raw.get("type", "text")), label=str(raw.get("label") or ""),
        section=str(raw.get("section") or ""),
        aria=str(raw.get("aria") or ""), placeholder=str(raw.get("placeholder") or ""),
        name=str(raw.get("name") or ""), ident=str(raw.get("ident") or ""),
        neighbour=str(raw.get("neighbour") or ""), options=[str(o) for o in raw.get("options") or []],
        selected_text=str(raw.get("selected_text") or ""), selected_value=str(raw.get("selected_value") or ""),
        filled=bool(raw.get("filled")), disabled=bool(raw.get("disabled")),
        readonly=bool(raw.get("readonly")),
        maxlength=raw.get("maxlength") if isinstance(raw.get("maxlength"), int) else None,
    )


def _clip(text: str) -> str:
    return " ".join(str(text).split())[:_MAX_LABEL_CHARS]


def _norm(text: str) -> str:
    """Lowercase words only: splits camelCase, turns punctuation into spaces."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


# ── What the form may never receive ───────────────────────────────────────────

_SENSITIVE_RE = re.compile(
    r"\b(password|passcode|otp|one time|captcha|verification code|security code|aadhaar|aadhar|uidai|"
    r"pan|cvv|cvc|card number|debit card|credit card|upi pin|atm pin|mpin|secret|security answer)\b"
)
_FILLABLE_INPUT_TYPES = frozenset({"text", "search", "email", "tel", "number", "textarea", "select", "date"})


def is_sensitive(info: FieldInfo) -> bool:
    if info.type == "password":
        return True
    return bool(_SENSITIVE_RE.search(_norm(" ".join((info.label, info.aria, info.placeholder, info.neighbour, info.name, info.ident)))))


# ── Matching rules ────────────────────────────────────────────────────────────

_OTHER_PARTY = (r"opposite|respondent|accused|company|firm|organi[sz]ation|authority|department|seller|employer|establishment|"
                r"management|regulated entity|landlord|bank|branch|police|station|office")


@dataclass(frozen=True)
class _Rule:
    key: str
    positive: tuple[str, ...]
    negative: str = ""
    types: frozenset[str] | None = None   # None: any text-like field
    selectable: bool = False              # may fill a <select> by option text


_TEXT = frozenset({"text", "search", "textarea", ""})
_RULES: tuple[_Rule, ...] = (
    _Rule("complainant_name", (
        r"\b(full name|complainant name|applicant name|your name|victim name|worker name|citizen name|"
        r"petitioner name|grievant name|name of (the )?(complainant|applicant|person|victim|worker|employee|citizen))\b",
        r"^name$",
    ), negative=r"\b(father|mother|husband|wife|spouse|guardian|user ?name|login|witness|nominee|reference|first|middle|last|sur|"
                + _OTHER_PARTY + r")\b",
        types=_TEXT),
    _Rule("complainant_email", (r"\be ?mail\b",),
          negative=r"\b(alternate|alt|confirm|retype|re ?enter|" + _OTHER_PARTY + r")\b",
          types=frozenset({"email", "text", "search"})),
    _Rule("complainant_phone", (r"\b(mobile|phone|contact (no|number)|cell|telephone|whatsapp)\b",),
          negative=r"\b(alternate|alt|landline|std|isd|country code|otp|verify|verification|" + _OTHER_PARTY + r")\b",
          types=frozenset({"tel", "number", "text", "search"})),
    _Rule("complainant_pin", (r"\b(pin ?code|pincode|postal code|post code|zip)\b",),
          negative=r"\b(" + _OTHER_PARTY + r"|property)\b", types=frozenset({"number", "text", "search", "tel"})),
    _Rule("complainant_state", (r"\b(state|union territory)\b",),
          negative=r"\b(" + _OTHER_PARTY + r"|incident|court|jurisdiction)\b", types=_TEXT, selectable=True),
    _Rule("complainant_city", (r"\b(city|town|village|district)\b",),
          negative=r"\b(" + _OTHER_PARTY + r"|incident|court)\b", types=_TEXT, selectable=True),
    _Rule("opposite_party_address", (
        r"\b(opposite party|respondent|accused|company|firm|organi[sz]ation|seller|employer|landlord|establishment)\b.*\b(address|location)\b",
        r"\baddress of (the )?(opposite party|respondent|accused|company|firm|organi[sz]ation|seller|employer|landlord|establishment)\b",
    ), types=_TEXT),
    _Rule("property_address", (
        r"\b(property|premises|rented|tenanted|flat)\b.*\baddress\b", r"\baddress of (the )?(property|premises)\b",
    ), types=_TEXT),
    _Rule("complainant_address", (
        r"\b(address|residential|correspondence|present address|permanent address)\b",
    ), negative=r"\b(e ?mail|ip|web|website|url|proof|line 2|address 2|landmark|" + _OTHER_PARTY + r"|property|premises)\b",
        types=_TEXT),
    _Rule("opposite_party_name", (
        r"\b(opposite party|respondent|accused|against whom|company name|firm name|establishment name|"
        r"organi[sz]ation name|employer name|seller name|landlord name|merchant name|trader name|party name|"
        r"name of (the )?(company|firm|organi[sz]ation|seller|employer|landlord|establishment|authority|department)|"
        r"public authority|service provider)\b",
    ), negative=r"\b(address|location|phone|mobile|e ?mail|pin|city|state)\b", types=_TEXT),
    _Rule("bank_name", (r"\b(bank name|name of (the )?bank|bank)\b",),
          negative=r"\b(account|ifsc|branch|number|no|id|address|city|state|ref|reference|transaction)\b", types=_TEXT),
    _Rule("transaction_id", (r"\b(transaction|txn|utr|rrn)\b",),
          negative=r"\b(date|time|amount|mode|type|status|bank|fee|charge)\b", types=_TEXT),
    _Rule("reference_number", (r"\bcnr\b", r"\bcase (number|no)\b"), types=_TEXT),
    _Rule("disputed_amount", (r"\b(amount|loss|claim|sum|rupees|rs|inr)\b",),
          negative=r"\b(transaction|reference|ref|id|number|no|date|mode|type|account|bank|currency|words|fee|charges?|tax)\b",
          types=frozenset({"number", "text", "search"})),
    # Employment: the worker's own pay and job title (never the employer's).
    _Rule("monthly_salary", (r"\b(salary|wages?|monthly pay|basic pay|pay per month)\b",),
          negative=r"\b(date|period|month of|months|deduction|overtime|minimum|arrears?|due|unpaid|total|"
                   r"gratuity|bonus|bank|account|mode|slip)\b",
          types=frozenset({"number", "text", "search"})),
    _Rule("employee_role", (r"\b(designation|job title|post held|your role|role)\b",),
          negative=r"\b(employer|officer|union|president|secretary|nominee|authority|" + _OTHER_PARTY + r")\b",
          types=_TEXT),
    _Rule("complainant_dob", (r"\b(date of birth|birth date|dob|d o b)\b",),
          negative=r"\b(father|mother|husband|wife|spouse|guardian|child|son|daughter|suspect|accused|witness|nominee)\b",
          types=frozenset({"text", "search", "date", ""})),
    _Rule("incident_narrative", (
        r"\b(grievance|complaint|incident|issue|problem|dispute|request|matter)\b.*\b(detail|details|description|describe|narrative|text|statement|summary|brief)\b",
        r"\b(detail|details|description|describe|narrative|statement|brief)\b.*\b(grievance|complaint|incident|issue|problem|dispute|request)\b",
        r"^(description|details|remarks|narrative|brief description|comments)$",
    ), negative=r"\b(attach|document|upload|file|date|time|place|id|number|no|category|type|amount|opposite|respondent)\b",
        types=_TEXT),
)
_RULE_BY_KEY = {rule.key: rule for rule in _RULES}
_SOURCE_WEIGHTS = (("label", 4), ("aria", 3), ("placeholder", 2), ("neighbour", 2), ("name", 1), ("ident", 1))
# Amounts are formatted alike: a plain number of rupees.
AMOUNT_KEYS = frozenset({"disputed_amount", "claim_amount", "monthly_salary"})
_NUMERIC_TYPE_KEYS = frozenset({"complainant_phone", "opposite_party_phone", "complainant_pin"}) | AMOUNT_KEYS


def _field_text(info: FieldInfo, source: str) -> str:
    return _norm(getattr(info, source))


def _score(rule: _Rule, info: FieldInfo) -> int:
    total = 0
    for source, weight in _SOURCE_WEIGHTS:
        text = _field_text(info, source)
        if not text:
            continue
        if rule.negative and re.search(rule.negative, text):
            continue
        if any(re.search(pattern, text) for pattern in rule.positive):
            total += weight
    if total and rule.key == "complainant_email" and info.type == "email":
        total += 5
    if total and rule.key == "complainant_phone" and info.type == "tel":
        total += 3
    if total and rule.key == "incident_narrative" and info.tag == "textarea":
        total += 2
    return total


def match_field(info: FieldInfo) -> tuple[str | None, int]:
    """Best case-value key for a field, or (None, 0) when none fits clearly."""
    if is_sensitive(info) or info.type not in _FILLABLE_INPUT_TYPES:
        return None, 0
    scored: list[tuple[int, str]] = []
    section = _norm(info.section)
    for rule in _RULES:
        # A generic name/contact rule must never cross the party boundary when
        # the page supplied an explicit section heading.
        if re.search(r"\b(opposite party|respondent)\b", section) and rule.key.startswith("complainant_"):
            continue
        if re.search(r"\bcomplainant\b", section) and rule.key.startswith("opposite_party_"):
            continue
        if info.tag == "select":
            if not rule.selectable:
                continue
        elif info.type == "email" and rule.key != "complainant_email":
            continue
        elif info.type == "tel" and rule.key != "complainant_phone":
            continue
        elif info.type == "number" and rule.key not in _NUMERIC_TYPE_KEYS:
            continue
        elif rule.types is not None and info.type not in rule.types and info.tag != "textarea":
            continue
        score = _score(rule, info)
        if score >= MIN_SCORE:
            scored.append((score, rule.key))
    if not scored:
        return None, 0
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, 0                   # two values fit equally well: do not guess
    return scored[0][1], scored[0][0]


# ── Value formatting ──────────────────────────────────────────────────────────


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text))


def parse_date(raw: str):
    """A date from ISO (2026-03-02) or Indian day-first text (02/03/2026). None if unclear."""
    text = str(raw or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_DATE_HINTS = (
    (re.compile(r"\byyyy[-/.]mm[-/.]dd\b"), "%Y-%m-%d"),
    (re.compile(r"\bmm/dd/yyyy\b"), "%m/%d/%Y"),
    (re.compile(r"\bdd/mm/yyyy\b"), "%d/%m/%Y"),
    (re.compile(r"\bdd-mm-yyyy\b"), "%d-%m-%Y"),
    (re.compile(r"\bdd\.mm\.yyyy\b"), "%d.%m.%Y"),
)


def format_date_for_field(raw: str, info: FieldInfo) -> str | None:
    """The date as this field wants it: ISO for a date input, else the format its
    placeholder or label shows (e.g. DD/MM/YYYY). None when the format is unknown,
    because guessing day-first vs month-first could swap day and month."""
    parsed = parse_date(raw)
    if parsed is None:
        return None
    if info.type == "date":
        return parsed.isoformat()
    hint = " ".join((info.placeholder, info.label, info.aria, info.neighbour)).lower()
    for pattern, fmt in _DATE_HINTS:
        if pattern.search(hint):
            return parsed.strftime(fmt)
    return None


def display_date(raw: str) -> str:
    parsed = parse_date(raw)
    return parsed.strftime("%d/%m/%Y") if parsed else str(raw or "")


def format_value(key: str, raw: str, info: FieldInfo) -> str | None:
    """The text to type for `key` in this field, or None when it would not fit."""
    value = " ".join(str(raw or "").split()) if key != "incident_narrative" else str(raw or "").strip()
    if not value:
        return None
    if key in {"complainant_phone", "opposite_party_phone"}:
        digits = _digits(value)
        if len(digits) > 10 and digits.startswith("91"):
            digits = digits[-10:]
        value = digits if len(digits) == 10 else None
    elif key == "complainant_pin":
        digits = _digits(value)
        value = digits if len(digits) == 6 else None
    elif key in AMOUNT_KEYS:
        try:
            number = float(re.sub(r"[,\s]|rs\.?|inr|₹", "", value.lower()))
        except ValueError:
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        value = str(int(number)) if number == int(number) else f"{number:.2f}"
    elif key in {"complainant_email", "opposite_party_email"} and "@" not in value:
        return None
    elif key == "complainant_dob":
        value = format_date_for_field(value, info)
    if value is None:
        return None
    if info.maxlength and len(value) > info.maxlength:
        if key == "incident_narrative":
            value = value[: info.maxlength].rstrip()
        else:
            return None                  # never silently chop a name or address
    return value


_PLACEHOLDER_OPTION_RE = re.compile(r"^\W*(select|choose|please|none|all)\b|^\W*$", re.IGNORECASE)


def pick_option(options: list[str], wanted: str) -> str | None:
    """One option clearly meaning `wanted`: exact (normalised), else a unique containment."""
    target = _norm(wanted)
    if not target:
        return None
    real = [o for o in options if not _PLACEHOLDER_OPTION_RE.match(o)]
    exact = [o for o in real if _norm(o) == target]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    loose = [o for o in real if len(_norm(o)) >= 3 and (target in _norm(o) or _norm(o) in target)]
    return loose[0] if len(loose) == 1 else None


def select_is_filled(info: FieldInfo) -> bool:
    if not info.selected_text and not info.selected_value:
        return False
    if info.selected_value in {"", "0", "-1"}:
        return False
    return not _PLACEHOLDER_OPTION_RE.match(info.selected_text)


# ── Planning (pure) ───────────────────────────────────────────────────────────


@dataclass
class FillAction:
    info: FieldInfo
    key: str
    value: str          # text to type, or the option label to select


@dataclass
class FillPlan:
    actions: list[FillAction] = field(default_factory=list)
    matched_keys: set[str] = field(default_factory=set)
    already_filled: list[FieldInfo] = field(default_factory=list)
    sensitive: list[FieldInfo] = field(default_factory=list)
    missing_value: list[tuple[FieldInfo, str]] = field(default_factory=list)
    unfit_value: list[tuple[FieldInfo, str]] = field(default_factory=list)
    duplicates: list[tuple[FieldInfo, str]] = field(default_factory=list)
    unmatched: list[FieldInfo] = field(default_factory=list)
    reason: str = ""


def plan_fills(fields: list[FieldInfo], values: dict[str, str]) -> FillPlan:
    plan = FillPlan()
    candidates: dict[str, list[tuple[int, int, FieldInfo]]] = {}
    for order, info in enumerate(fields):
        if info.type in {"checkbox", "radio", "file", "submit", "button", "image", "reset"}:
            continue
        if is_sensitive(info):
            plan.sensitive.append(info)
            continue
        if info.disabled or info.readonly:
            continue
        key, score = match_field(info)
        if key is None:
            if not (info.filled or (info.tag == "select" and select_is_filled(info))):
                plan.unmatched.append(info)
            continue
        candidates.setdefault(key, []).append((score, order, info))
    plan.matched_keys = set(candidates)
    if len(plan.matched_keys) < MIN_MATCHED_VALUES:
        plan.reason = "too_few_matches"
        return plan

    for key, found in candidates.items():
        found.sort(key=lambda item: (-item[0], item[1]))
        best = found[0][2]
        plan.duplicates.extend((extra, key) for _, _, extra in found[1:])
        if best.filled or (best.tag == "select" and select_is_filled(best)):
            plan.already_filled.append(best)
            continue
        raw = values.get(key) or ""
        if not str(raw).strip():
            plan.missing_value.append((best, key))
            continue
        if best.tag == "select":
            option = pick_option(best.options, str(raw))
            if option is None:
                plan.unfit_value.append((best, key))
                continue
            plan.actions.append(FillAction(best, key, option))
            continue
        text = format_value(key, raw, best)
        if text is None:
            plan.unfit_value.append((best, key))
            continue
        plan.actions.append(FillAction(best, key, text))
    plan.actions.sort(key=lambda action: (action.info.frame, action.info.index))
    if not plan.actions:
        plan.reason = "nothing_to_fill"
    return plan


# ── Page driver ───────────────────────────────────────────────────────────────


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)          # field labels, never values
    failed: list[str] = field(default_factory=list)
    already_filled: list[str] = field(default_factory=list)
    left_for_user: list[str] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    matched_values: int = 0
    reason: str = ""

    @property
    def filled_count(self) -> int:
        return len(self.filled)


async def scan_fields(page) -> list[FieldInfo]:
    """All visible fillable fields across the page's frames. Never raises."""
    frames = list(getattr(page, "frames", None) or [page])
    found: list[FieldInfo] = []
    for number, frame in enumerate(frames):
        try:
            raw_fields = await frame.evaluate(SCAN_JS)
        except Exception as exc:  # noqa: BLE001 - a frame we cannot read is skipped
            logger.info("event=rule_fill_frame_unreadable frame=%s type=%s", number, type(exc).__name__)
            continue
        found.extend(field_from_scan(number, raw) for raw in raw_fields or [])
    return found


async def _apply(frames: list, action: FillAction, pause: float) -> bool:
    frame = frames[action.info.frame] if action.info.frame < len(frames) else frames[0]
    locator = frame.locator(f'[data-nyaya-fill="{action.info.index}"]').first
    if action.info.tag == "select":
        await locator.select_option(label=action.value, timeout=2000)
        return True
    await locator.fill(action.value, timeout=5000)
    try:
        shown = await locator.input_value()
    except Exception:  # noqa: BLE001 - some inputs cannot report a value; trust the fill
        shown = action.value
    try:
        await locator.dispatch_event("change")
    except Exception:  # noqa: BLE001
        pass
    if pause:
        await asyncio.sleep(pause)       # lets the live view show each field being filled
    return bool(str(shown).strip())


async def fill_page(page, values: dict[str, str], *, pause: float | None = None, session_id: str = "-") -> FillReport:
    """Fill what is clearly askable, leave the rest, and report by field label only."""
    pause = FILL_PAUSE_SECONDS if pause is None else pause
    fields = await scan_fields(page)
    plan = plan_fills(fields, values)
    report = FillReport(
        matched_values=len(plan.matched_keys),
        already_filled=[f.display for f in plan.already_filled],
        sensitive=[f.display for f in plan.sensitive],
        reason=plan.reason,
    )
    frames = list(getattr(page, "frames", None) or [page])
    for action in plan.actions:
        label = action.info.display
        try:
            if await _apply(frames, action, pause):
                report.filled.append(label)
                logger.info("session=%s event=rule_fill_field label=%r key=%s outcome=filled",
                            session_id, label, action.key)
            else:
                report.failed.append(label)
                logger.info("session=%s event=rule_fill_field label=%r key=%s outcome=empty_after_fill",
                            session_id, label, action.key)
        except Exception as exc:  # noqa: BLE001 - one stubborn field must not stop the rest
            report.failed.append(label)
            logger.info("session=%s event=rule_fill_field label=%r key=%s outcome=error type=%s",
                        session_id, label, action.key, type(exc).__name__)
    report.left_for_user = (
        [f.display for f, _ in plan.missing_value]
        + [f.display for f, _ in plan.unfit_value]
        + [f.display for f in plan.unmatched]
        + report.failed
    )
    logger.info(
        "session=%s event=rule_fill_report fields_seen=%s matched_values=%s filled=%s failed=%s "
        "already_filled=%s missing_value=%s unfit_value=%s unmatched=%s sensitive=%s duplicates=%s reason=%s",
        session_id, len(fields), len(plan.matched_keys), len(report.filled), len(report.failed),
        len(plan.already_filled), len(plan.missing_value), len(plan.unfit_value), len(plan.unmatched),
        len(plan.sensitive), len(plan.duplicates), plan.reason or "-",
    )
    for info in plan.unmatched[:15]:
        logger.info("session=%s event=rule_fill_unmatched label=%r type=%s", session_id, info.display, info.type)
    return report
