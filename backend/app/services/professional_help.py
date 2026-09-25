"""Deterministic, advisory assessment of when legal advice may be useful."""

import re

from app.domains import domain_registry
from app.domains.compatibility import read_profile_fact
from app.schemas.chat import StructuredCaseProfile
from app.schemas.professional_help import (
    ProfessionalHelpAssessment,
    ProfessionalHelpLevel as Level,
    ProfessionalHelpReason as Reason,
    ProfessionalHelpRule,
    ProfessionalType,
    ReassessTrigger as Trigger,
)


# Product guidance signals, not statements that representation is legally required.
GLOBAL_RULES = (
    ProfessionalHelpRule(source="fact_true", key="formal_proceeding_started", reason=Reason.FORMAL_PROCEEDING_STARTED, level=Level.LEGAL_HELP_RECOMMENDED),
    ProfessionalHelpRule(source="fact_true", key="court_notice_received", reason=Reason.COURT_OR_TRIBUNAL_NOTICE_RECEIVED, level=Level.LEGAL_HELP_RECOMMENDED),
    ProfessionalHelpRule(source="fact_true", key="formal_legal_notice_received", reason=Reason.FORMAL_LEGAL_NOTICE_RECEIVED, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="criminal_allegation_against_user", reason=Reason.CRIMINAL_ALLEGATION, level=Level.LEGAL_HELP_RECOMMENDED),
    ProfessionalHelpRule(source="fact_true", key="arrest_or_police_risk", reason=Reason.ARREST_OR_POLICE_RISK, level=Level.URGENT_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="facts_materially_disputed", reason=Reason.COMPLEX_FACTUAL_DISPUTE, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="multiple_significant_parties", reason=Reason.MULTIPLE_PARTIES, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="cross_jurisdiction_complexity", reason=Reason.CROSS_JURISDICTION_COMPLEXITY, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="counterparty_represented", reason=Reason.COUNTERPARTY_REPRESENTED, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="fact_true", key="repeated_escalation_failed", reason=Reason.REPEATED_ESCALATION_FAILED, level=Level.CONSIDER_LEGAL_HELP),
    ProfessionalHelpRule(source="action_completed", key="response_rejected", reason=Reason.GRIEVANCE_FAILED, level=Level.CONSIDER_LEGAL_HELP),
)

SIGNAL_FACT_KEYS = frozenset(rule.key for rule in GLOBAL_RULES if rule.source == "fact_true") | frozenset(
    rule.key for domain in domain_registry.all() for rule in domain.professional_help.rules if rule.source == "fact_true"
)

DEFAULT_TRIGGERS = (
    Trigger.FORMAL_NOTICE_RECEIVED,
    Trigger.FORMAL_PROCEEDING_STARTED,
    Trigger.COUNTERPARTY_LAWYER_INVOLVED,
    Trigger.VERIFIED_DEADLINE_IDENTIFIED,
)

TRIGGER_SIGNALS = {
    Trigger.FORMAL_NOTICE_RECEIVED: "formal_legal_notice_received",
    Trigger.FORMAL_PROCEEDING_STARTED: "formal_proceeding_started",
    Trigger.GRIEVANCE_FAILED: "repeated_escalation_failed",
    Trigger.BANK_REJECTED_CLAIM: "bank_claim_rejected",
    Trigger.EVICTION_PROCEEDING_STARTED: "eviction_proceeding_started",
    Trigger.TERMINATION_OCCURRED: "termination_occurred",
    Trigger.COUNTERPARTY_LAWYER_INVOLVED: "counterparty_represented",
    Trigger.CASE_FACTS_BECAME_DISPUTED: "facts_materially_disputed",
}

LEVEL_ORDER = {
    Level.SELF_HELP_REASONABLE: 0,
    Level.CONSIDER_LEGAL_HELP: 1,
    Level.LEGAL_HELP_RECOMMENDED: 2,
    Level.URGENT_LEGAL_HELP: 3,
}


def asks_about_legal_help(message: str) -> bool:
    """Detect direct advice questions; never decide the level from wording."""
    return bool(re.search(
        r"\b(lawyer|advocate|legal help|legal aid|legal advice|vakil|vakeel|handle (?:this|it) myself|do (?:this|it) myself)\b|वकील|कानूनी (?:मदद|सलाह)|खुद (?:कर|संभाल)",
        message,
        re.IGNORECASE,
    ))


def evaluate_professional_help(profile: StructuredCaseProfile) -> ProfessionalHelpAssessment:
    """Recompute from known, positive signals. Missing and explicit false stay inert."""
    domain = domain_registry.get(profile.category)
    policy = domain.professional_help if domain else None
    rules = (*GLOBAL_RULES, *(policy.rules if policy else ()))
    actions = {action.get("type") for action in profile.actions_completed}
    fired = []
    known = []
    unknown = []
    for rule in rules:
        if rule.source == "action_completed":
            active = rule.key in actions
        else:
            present, value = read_profile_fact(profile, rule.key)
            active = present and value is True
            if not present:
                unknown.append(rule.key)
        if active:
            fired.append(rule)
            known.append(rule.key)

    reasons = list(dict.fromkeys(rule.reason for rule in fired))
    strongest = max((LEVEL_ORDER[rule.level] for rule in fired), default=0)
    # Several independent complexity indicators warrant case-specific advice.
    if strongest == 1 and len(reasons) >= 2:
        strongest = 2
    level = next(level for level, rank in LEVEL_ORDER.items() if rank == strongest)
    if not reasons:
        reasons = [Reason.CURRENT_INFORMATION_LIMITED] if profile.category == "GENERAL" else [Reason.EARLY_STAGE]
        if profile.category != "GENERAL" and profile.current_stage_key != "RESOLVED":
            reasons.append(Reason.STANDARD_SELF_HELP_AVAILABLE)

    triggers = [
        trigger for trigger in dict.fromkeys((*DEFAULT_TRIGGERS, *(policy.reassess_on if policy else ())))
        if TRIGGER_SIGNALS.get(trigger) not in known
    ]
    return ProfessionalHelpAssessment(
        level=level,
        reason_codes=reasons,
        professional_types=list(policy.professional_types) if policy and strongest else (
            [ProfessionalType.ADVOCATE, ProfessionalType.LEGAL_AID] if strongest else []
        ),
        urgency="PROMPT" if level == Level.URGENT_LEGAL_HELP else "ROUTINE",
        reassess_on=triggers,
        relevant_known_signals=known,
        unknown_relevant_signals=list(dict.fromkeys(unknown)),
    )
