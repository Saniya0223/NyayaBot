"""
portal_selector.py
------------------
Maps NyayaBot case categories / sub-categories to the correct Indian government
portal URL and a plain-English description of the task the browser agent should
perform.  Kept intentionally simple: the LLM inside browser-use reads the live
page and works out which fields to fill — we only need to tell it *where* to go
and *what* to accomplish.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class PortalTarget:
    url: str
    label: str                      # Human-readable portal name
    task_template: str              # Template filled with case data before being sent to the agent
    # Fields the agent must NOT fill — user fills these themselves in the browser
    sensitive_fields: list[str] = field(default_factory=list)
    notes: str = ""                 # Extra hints for the LLM agent
    # Regexes (on lower-cased, punctuation-free link text) for the click that leads
    # from the landing page toward the form. Only add one that was seen on the real
    # portal; the generic navigator rules cover common wording.
    entry_hints: tuple[str, ...] = ()
    # Regexes (same text form) for links that must never be taken on this portal,
    # matched against the link text plus the card it sits in.
    entry_avoid: tuple[str, ...] = ()


# ── Registry ────────────────────────────────────────────────────────────────

_PORTAL_MAP: dict[str, PortalTarget] = {
    # ── Consumer grievances ──────────────────────────────────────────────────
    "consumer": PortalTarget(
        url="https://consumerhelpline.gov.in/",
        label="National Consumer Helpline",
        task_template=(
            "Register a consumer complaint on the National Consumer Helpline portal. "
            "Complainant name: {complainant_name}. "
            "Address: {complainant_address}, {complainant_city}, {complainant_state}. "
            "Phone: {complainant_phone}. "
            "Opposite party / company: {opposite_party_name}. "
            "Nature of complaint: {incident_narrative}. "
            "Amount involved: Rs {disputed_amount}. "
            "Do NOT submit the form — pause and take a screenshot for review first."
        ),
        sensitive_fields=[],
        notes="The portal may require OTP on mobile. Pause and let the user enter it.",
    ),

    # ── Cybercrime ───────────────────────────────────────────────────────────
    "cybercrime": PortalTarget(
        url="https://cybercrime.gov.in/",
        label="National Cyber Crime Reporting Portal",
        # From the portal's own page text: the home page has a "Cyber Crime Related
        # Complaint" card and a separate "Women & Children Related Crime" card, both
        # with a "Register a Complaint" button, plus a "Report Cyber Crime" menu.
        entry_hints=(r"^register a complaint$",),
        entry_avoid=(r"\bwomen\b", r"\bchild(ren)?\b", r"\banonymous(ly)?\b", r"\bsocial media\b"),
        task_template=(
            "File a cyber crime complaint on cybercrime.gov.in. "
            "Victim name: {complainant_name}. "
            "Address: {complainant_address}, {complainant_city}, {complainant_state}. "
            "Phone: {complainant_phone}. "
            "Incident summary: {incident_narrative}. "
            "Financial loss: Rs {disputed_amount}. "
            "Bank / payment method involved: {bank_name}. "
            "Transaction ID if known: {transaction_id}. "
            "Do NOT submit — pause after filling all visible fields and take a screenshot."
        ),
        sensitive_fields=[],
        notes="Portal uses Captcha. If a captcha appears, pause and ask the user to solve it.",
    ),

    # ── RTI ─────────────────────────────────────────────────────────────────
    "rti": PortalTarget(
        url="https://rtionline.gov.in/",
        label="RTI Online Portal",
        task_template=(
            "File an RTI application on rtionline.gov.in. "
            "Applicant name: {complainant_name}. "
            "Address: {complainant_address}, {complainant_city}, {complainant_state}. "
            "Phone: {complainant_phone}. "
            "Public authority: {opposite_party_name}. "
            "RTI request text: {incident_narrative}. "
            "Do NOT pay or submit — pause and take a screenshot for user review."
        ),
        sensitive_fields=[],
        notes="Payment of Rs 10 is required. Pause before the payment step.",
    ),

    # ── Police / FIR status ──────────────────────────────────────────────────
    "police": PortalTarget(
        url="https://services.ecourts.gov.in/ecourtindia_v6/",
        label="eCourts Services Portal",
        task_template=(
            "Check case status on the eCourts Services portal (ecourts.gov.in). "
            "Search using: complainant name '{complainant_name}', "
            "state '{complainant_state}', district '{complainant_city}'. "
            "If a CNR number is known use it directly: {reference_number}. "
            "Do NOT submit anything — just retrieve the status and take a screenshot."
        ),
        sensitive_fields=[],
        notes="No login required for case status lookup.",
    ),

    # ── Tenant / rent disputes ───────────────────────────────────────────────
    "tenant": PortalTarget(
        url="https://consumerhelpline.gov.in/",
        label="National Consumer Helpline (Tenant Dispute)",
        task_template=(
            "Register a housing / tenant dispute complaint on the National Consumer Helpline portal. "
            "Complainant: {complainant_name}, {complainant_address}, {complainant_city}, {complainant_state}. "
            "Phone: {complainant_phone}. "
            "Opposite party (landlord): {opposite_party_name}. "
            "Property address: {property_address}. "
            "Dispute summary: {incident_narrative}. "
            "Amount disputed: Rs {disputed_amount}. "
            "Do NOT submit — pause and screenshot for user approval."
        ),
        sensitive_fields=[],
    ),

    # ── Employment disputes ──────────────────────────────────────────────────
    "employment": PortalTarget(
        url="https://samadhan.labour.gov.in/",
        label="Samadhan — Ministry of Labour",
        # Seen on the live landing page: a pop-up whose button reads
        # "Click here to continue with this new portal".
        entry_hints=(r"continue with this new portal",),
        task_template=(
            "File a labour grievance on samadhan.labour.gov.in. "
            "Worker name: {complainant_name}. "
            "Address: {complainant_address}, {complainant_city}, {complainant_state}. "
            "Phone: {complainant_phone}. "
            "Employer / company: {opposite_party_name}. "
            "Grievance details: {incident_narrative}. "
            "Do NOT submit — pause before submission and take a screenshot."
        ),
        sensitive_fields=[],
    ),
}

# Fallback when we cannot map the category
_FALLBACK = PortalTarget(
    url="https://pgportal.gov.in/",
    label="CPGRAMS — Central Public Grievance Portal",
    task_template=(
        "File a public grievance on CPGRAMS (pgportal.gov.in). "
        "Complainant: {complainant_name}, {complainant_address}, {complainant_city}, {complainant_state}. "
        "Phone: {complainant_phone}. "
        "Grievance: {incident_narrative}. "
        "Do NOT submit — pause and screenshot for review."
    ),
    sensitive_fields=[],
    notes="Generic grievance portal used as a fallback.",
)


# ── Public API ───────────────────────────────────────────────────────────────

# NyayaBot case categories (domain ids, e.g. "CYBER_FRAUD") -> _PORTAL_MAP key.
# _PORTAL_MAP was keyed by portal names ("cybercrime", "tenant"), which no case
# category ever matched, so cyber-fraud and tenant cases silently went to the
# generic CPGRAMS fallback. A category absent from this table (and from
# _PORTAL_MAP) deliberately falls back; see POLICE_COMPLAINT below.
_CATEGORY_TO_PORTAL_KEY: dict[str, str] = {
    "consumer": "consumer",
    "employment": "employment",
    "cyber_fraud": "cybercrime",
    "housing_tenant": "tenant",
    # POLICE_COMPLAINT is intentionally NOT mapped. The "police" entry is an eCourts
    # case-status lookup (search by name / CNR), but the POLICE_COMPLAINT domain is
    # help with a police refusal to register an FIR. Sending that user to a case
    # status search would be the wrong task, so it keeps the general fallback until
    # a suitable portal is chosen.
}


def _normalise_category(category: str | None) -> str:
    return "_".join((category or "").lower().replace("-", " ").split())


def select_portal(category: str | None) -> PortalTarget:
    """Return the best portal target for the given NyayaBot case category.

    Accepts a case category ("CYBER_FRAUD") or a portal key ("cybercrime").
    """
    key = _normalise_category(category)
    key = _CATEGORY_TO_PORTAL_KEY.get(key, key)
    return _PORTAL_MAP.get(key, _FALLBACK)


MOCK_PORTAL_PATH = "/api/v1/browser/mock-portal"


def mock_portal_enabled() -> bool:
    """Development switch: route every Auto-Fill session to the local mock portal."""
    return os.getenv("AUTOFILL_MOCK_PORTAL", "").strip().lower() in {"1", "true", "yes", "on"}


def mock_portal(base_url: str) -> PortalTarget:
    """Local test-only portal served by this backend; filled by Playwright without an LLM."""
    return PortalTarget(
        url=base_url.rstrip("/") + MOCK_PORTAL_PATH,
        label="Local Mock Portal (test only)",
        task_template="Fill the local mock grievance form for {complainant_name}. Do NOT submit.",
        notes="Test-only page. Nothing is sent anywhere.",
    )


def build_task(portal: PortalTarget, case_data: dict) -> str:
    """
    Interpolate case_data into the portal's task_template.
    Missing keys are explicitly marked as unavailable; never invent a value.
    """
    from collections import defaultdict
    missing = "[not provided; leave this field blank for the user]"
    safe = defaultdict(lambda: missing, {k: (v or missing) for k, v in case_data.items()})
    return (
        portal.task_template.format_map(safe)
        + "\nOnly use supplied values. Never invent names, dates, amounts, IDs or contact details. "
        "Leave missing fields blank for manual completion. Never bypass login, CAPTCHA or OTP."
    )
