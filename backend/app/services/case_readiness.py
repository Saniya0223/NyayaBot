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
    SafetyAssessment,
    safety_intake_facts_for,
    safety_triage_resolved,
)


# A document is a legal act, and the turns right after a self-harm disclosure
# are the wrong moment to propose one. This is a cooling-off window rather than
# a permanent ban on purpose: a permanent block would recreate the very trap
# this cycle is removing, one layer down, where a single gentle false positive
# ("is traffic me mar jaunga") would disable document generation for that case
# forever. Bounded and deterministic is the right trade.
CRISIS_DOCUMENT_COOLDOWN_TURNS = 3


def crisis_document_block_active(key_facts: Any) -> bool:
    """True while a case is inside the post-crisis document cooling-off window."""
    key_facts = key_facts or {}
    if not key_facts.get("crisis_support_offered"):
        return False
    turns = int(key_facts.get("turns_since_crisis_support") or 0)
    return turns < CRISIS_DOCUMENT_COOLDOWN_TURNS


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
    # Which safety contract applies is case-scoped: a crisis-only disclosure has
    # no attacker, so the attacker-oriented facts are not merely unanswered,
    # they are unanswerable, and demanding them froze the case permanently.
    safety_facts = safety_intake_facts_for(profile.key_facts or {})

    # Safety questions come first and replace ordinary intake until answered.
    if safety.is_safety_case and not safety_triage_resolved(profile.key_facts or {}):
        return [fact for fact in safety_facts if not _has_value(profile, fact)]

    # The registry returns semantically ordered, issue-applicable facts and
    # treats explicit False as known rather than missing.
    required = [fact.key for fact in domain_registry.unresolved_facts(profile)]
    if safety.is_safety_case:
        # Keep safety facts in the intake set once triage is answered, so the
        # case is still understood on its own terms.
        for fact in safety_facts:
            if fact not in required:
                required.append(fact)

    # Domain facts above are already unresolved. Safety facts still use the
    # global safety contract, which intentionally remains outside domains.
    return [
        field for field in required
        if field not in safety_facts or not _has_value(profile, field)
    ]


def compute_blocking_missing_facts(
    profile: Any,
    safety: SafetyAssessment,
    intake_missing: List[str],
) -> List[str]:
    """Identify which missing intake facts actually block taking the next executable action.
    
    A fact is missing when it is not yet known. A fact is blocking only when the
    next practical action cannot be executed without it. If the user has stated
    they do not know a detail, or if that detail can instead be demanded from another
    institution/counterparty (e.g. receiving bank or UTR in an unauthorized loan),
    it remains missing/unknown but does NOT block progress.
    """
    if safety.is_safety_case and not safety_triage_resolved(profile.key_facts or {}):
        return [
            fact for fact in safety_intake_facts_for(profile.key_facts or {})
            if not _has_value(profile, fact)
        ]

    stated_unknown = set(getattr(profile, "key_facts", {}).get("stated_unknown_facts", []))
    metadata = getattr(profile, "fact_metadata", {}) or {}
    for key, meta in metadata.items():
        if isinstance(meta, dict) and meta.get("stated_unknown"):
            stated_unknown.add(key)

    blocking: List[str] = []
    category = getattr(profile, "category", "GENERAL")

    if category == "CYBER_FRAUD":
        # In cyber fraud / unauthorized loan:
        # If we know the lender/bank or opposite party, and amount/reference,
        # missing receiving bank or UTR is not blocking because it can be demanded from the lender.
        has_party = bool(getattr(profile, "opposite_party_name", None) or getattr(profile, "bank_name", None))
        has_amount_or_ref = bool(getattr(profile, "disputed_amount", 0) or getattr(profile, "transaction_id", None) or (profile.key_facts or {}).get("loan_reference"))
        for fact in intake_missing:
            if fact in stated_unknown:
                continue
            if fact in {"bank_name", "transaction_id"} and has_party and has_amount_or_ref:
                # Actionable against the known lender/institution
                continue
            if fact in {"incident_date", "user_state", "scam_method"}:
                # Contextual/jurisdiction fields that don't block initial action
                continue
            blocking.append(fact)
    elif category == "GENERAL":
        # No workflow exists, so nothing is action-scoped. Every unresolved fact
        # is still blocking, exactly as before.
        for fact in intake_missing:
            if fact in stated_unknown:
                continue
            blocking.append(fact)
    else:
        # H7. This function documented itself as "a fact is blocking only when
        # the next practical action cannot be executed without it" and then
        # appended every unresolved fact, which is why a case could never leave
        # UNDERSTANDING_CASE: for CONSUMER that meant 6-8 facts, all of them,
        # before anything at all could happen. `NextActionPlanner` already
        # computes the documented thing and exposes it as
        # `NextActionPlan.blocking_missing_facts`, scoped to the action's
        # `required_fact_keys`. The right computation existed; the ladder used
        # the wrong one.
        from app.services.action_planner import action_planner

        plan = action_planner.plan_next_action(profile)
        if not plan.required_facts:
            # An action that declares *no* required facts cannot certify that a
            # case is understood well enough to act on. Both POLICE_COMPLAINT
            # actions declare none (`action_planner.py`), so scoping to the
            # action would scope to the empty set: a police case would reach
            # READY_FOR_ACTION - and an offer to draft POLICE_COMPLAINT_BNSS -
            # with nothing whatsoever on the record. Verified on a W1-05 replay
            # (death threat in Lucknow), where the document appeared on turn 3.
            # An empty requirement set means "the planner has nothing to say
            # here", not "nothing is needed", so the domain's own
            # understanding facts stay in charge. Guidance still flows; only
            # the action rung and the paperwork behind it wait.
            return [fact for fact in intake_missing if fact not in stated_unknown]

        required = set(plan.blocking_missing_facts)
        for fact in intake_missing:
            if fact in stated_unknown or fact not in required:
                continue
            blocking.append(fact)

    return blocking


def compute_readiness(
    profile: Any,
    safety: SafetyAssessment,
    blocking_missing: List[str],
    message: str = "",
) -> str:
    """Place the case on the readiness ladder based on blocking facts."""
    if profile.category == "GENERAL" and (is_greeting_only(message) or not profile.issue_type):
        return PRE_INTAKE

    # An unresolved safety case must not advance toward action or documents.
    if safety.is_safety_case and not safety_triage_resolved(profile.key_facts or {}):
        return UNDERSTANDING_CASE

    # "Do we understand the issue?" is what this rung is named after, and
    # `issue_understood` (each domain's `minimum_context_any_of`) is the test
    # that already exists for it. "Can the next action be executed?" is the
    # READY_FOR_ACTION test, below.
    if profile.category != "GENERAL" and not domain_registry.issue_understood(profile):
        return UNDERSTANDING_CASE

    if blocking_missing:
        # A classified case whose issue is understood but whose next action is
        # still blocked earns guidance, not action. Documents stay shut:
        # `document_routing_allowed` floors at READY_FOR_ACTION. GENERAL keeps
        # today's behaviour exactly, because it has no workflow to be ready for.
        return UNDERSTANDING_CASE if profile.category == "GENERAL" else READY_FOR_LEGAL_GUIDANCE

    if profile.risk_level == "RED":
        # Serious-risk cases get guidance and referral, not document automation.
        return READY_FOR_LEGAL_GUIDANCE

    if profile.category == "GENERAL":
        # Without a known category there is no workflow to act within.
        return READY_FOR_LEGAL_GUIDANCE

    return READY_FOR_ACTION


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
    if crisis_document_block_active(profile.key_facts or {}):
        # A crisis disclosure resolves safety triage (there is no attacker to
        # ask about), so the unresolved-triage brake below no longer holds it.
        # The cooling-off window is what keeps paperwork away from the turns
        # immediately after a self-harm disclosure.
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
    if profile.risk_level == "RED" or safety.safety_level == "RED":
        # `profile.risk_level` is recomputed from the latest message text on
        # every turn, so it silently drops back to GREEN once a case in active
        # danger is released to the legal flow. The triage assessment is the
        # durable statement of danger, so the RED brake reads that too.
        return False
    return readiness_at_least(readiness, READY_FOR_ACTION)
