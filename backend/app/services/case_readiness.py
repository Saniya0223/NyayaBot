"""Case readiness: understand the problem before proposing an action.

The previous design computed document requirements on every turn, so a case that
was barely understood already had a recommended document and was asking for the
name and city that template needed. This module separates the two concerns:

    intake_missing_facts   - what we still need to understand the legal issue
    document_missing_fields - what a chosen template needs, computed only once
                              a document is genuinely the right next step

Readiness is a one-way ladder through PRE_INTAKE -> UNDERSTANDING_CASE ->
READY_FOR_LEGAL_GUIDANCE -> READY_FOR_ACTION -> READY_FOR_DOCUMENT. Document
routing is gated at the top of that ladder.
"""

from __future__ import annotations

from typing import Any, List

from app.domains import domain_registry

from app.services.safety_triage import (
    SAFETY_INTAKE_FACTS,
    SafetyAssessment,
    safety_triage_resolved,
)


PRE_INTAKE = "PRE_INTAKE"
UNDERSTANDING_CASE = "UNDERSTANDING_CASE"
READY_FOR_LEGAL_GUIDANCE = "READY_FOR_LEGAL_GUIDANCE"
READY_FOR_ACTION = "READY_FOR_ACTION"
READY_FOR_DOCUMENT = "READY_FOR_DOCUMENT"

READINESS_ORDER = [
    PRE_INTAKE,
    UNDERSTANDING_CASE,
    READY_FOR_LEGAL_GUIDANCE,
    READY_FOR_ACTION,
    READY_FOR_DOCUMENT,
]


def readiness_at_least(readiness: str, minimum: str) -> bool:
    try:
        return READINESS_ORDER.index(readiness) >= READINESS_ORDER.index(minimum)
    except ValueError:
        return False


# Phrases that carry no legal content. A case stays in PRE_INTAKE until the user
# has actually described a problem.
GREETING_TOKENS = {
    "hi", "hello", "hey", "namaste", "namaskar", "hii", "hlo", "good morning",
    "good evening", "good afternoon", "start", "help", "hi there", "hey there",
    "yo", "test", "testing",
}


def is_greeting_only(message: str) -> bool:
    value = (message or "").strip().lower().rstrip("!.?,")
    if not value:
        return True
    if value in GREETING_TOKENS:
        return True
    # "hi" / "hello there" style openers with no substance.
    return len(value.split()) <= 2 and all(
        token.strip("!.?,") in GREETING_TOKENS for token in value.split()
    )


def _has_value(profile: Any, field: str) -> bool:
    """A fact counts as known when set; False is a real answer, not a gap."""
    if hasattr(profile, field):
        value = getattr(profile, field)
        if value not in (None, "", [], 0, 0.0):
            return True
    facts = getattr(profile, "key_facts", {}) or {}
    if field in facts:
        return facts[field] is not None
    return False


def compute_intake_missing_facts(
    profile: Any,
    safety: SafetyAssessment,
) -> List[str]:
    """Facts still needed to understand the case - never document fields."""
    # Safety questions come first and replace ordinary intake until answered.
    if safety.is_safety_case and not safety_triage_resolved(profile.key_facts or {}):
        return [fact for fact in SAFETY_INTAKE_FACTS if not _has_value(profile, fact)]

    # The registry returns semantically ordered, issue-applicable facts and
    # treats explicit False as known rather than missing.
    required = [fact.key for fact in domain_registry.unresolved_facts(profile)]
    if safety.is_safety_case:
        # Keep safety facts in the intake set once triage is answered, so the
        # case is still understood on its own terms.
        for fact in SAFETY_INTAKE_FACTS:
            if fact not in required:
                required.append(fact)

    # Domain facts above are already unresolved. Safety facts still use the
    # global safety contract, which intentionally remains outside domains.
    return [
        field for field in required
        if field not in SAFETY_INTAKE_FACTS or not _has_value(profile, field)
    ]


def compute_readiness(
    profile: Any,
    safety: SafetyAssessment,
    intake_missing: List[str],
    message: str = "",
) -> str:
    """Place the case on the readiness ladder."""
    if profile.category == "GENERAL" and (is_greeting_only(message) or not profile.issue_type):
        return PRE_INTAKE

    # An unresolved safety case must not advance toward action or documents.
    if safety.is_safety_case and not safety_triage_resolved(profile.key_facts or {}):
        return UNDERSTANDING_CASE

    if intake_missing:
        return UNDERSTANDING_CASE

    if profile.risk_level == "RED":
        # Serious-risk cases get guidance and referral, not document automation.
        return READY_FOR_LEGAL_GUIDANCE

    if profile.category == "GENERAL":
        # Without a known category there is no workflow to act within.
        return READY_FOR_LEGAL_GUIDANCE

    if getattr(profile, "actions_completed", None) or profile.current_stage_key:
        return READY_FOR_ACTION

    return READY_FOR_LEGAL_GUIDANCE


def document_routing_allowed(
    profile: Any,
    safety: SafetyAssessment,
    readiness: str,
) -> bool:
    """The single gate every document recommendation must pass.

    A document is a legal act. It is offered only when the issue is identified,
    the workflow has reached a point where it is the right move, safety questions
    are settled, and the user has not been left with unanswered intake.
    """
    if readiness == PRE_INTAKE or readiness == UNDERSTANDING_CASE:
        return False
    if safety.blocks_document_routing and not safety_triage_resolved(profile.key_facts or {}):
        return False
    if safety.is_safety_case and not (profile.key_facts or {}).get("safety_triage_complete"):
        # Answering "safe right now" resolves the emergency question, but it
        # does not mean the threat has been understood well enough for a legal
        # document. The paced safety intake must finish first.
        return False
    if profile.category == "GENERAL":
        # No curated workflow exists, so no template is demonstrably appropriate.
        return False
    if profile.risk_level == "RED":
        return False
    return readiness_at_least(readiness, READY_FOR_ACTION)
