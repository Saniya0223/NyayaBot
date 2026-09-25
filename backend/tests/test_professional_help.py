"""Local-only tests: professional help is advisory and based on known signals."""

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.schemas.professional_help import ProfessionalHelpLevel as Level, ProfessionalHelpReason as Reason, ReassessTrigger as Trigger
from app.services.professional_help import evaluate_professional_help
from app.services.professional_help import asks_about_legal_help


def case(category="CONSUMER"):
    return ConversationalLegalAgent()._init_case_profile(
        "A case for assessment", category_override=category,
    )


@pytest.mark.parametrize("category", ["CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD", "GENERAL"])
def test_unknown_and_early_cases_do_not_escalate(category):
    profile = case(category)
    result = evaluate_professional_help(profile)
    assert result.level == Level.SELF_HELP_REASONABLE
    assert "court_notice_received" in result.unknown_relevant_signals
    assert result.professional_types == []
    assert Trigger.FORMAL_PROCEEDING_STARTED in result.reassess_on


@pytest.mark.parametrize("category", ["CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD"])
def test_explicit_false_and_amount_never_escalate(category):
    profile = case(category)
    profile.disputed_amount = 10000000
    profile.key_facts.update(court_notice_received=False, formal_proceeding_started=None)
    result = evaluate_professional_help(profile)
    assert result.level == Level.SELF_HELP_REASONABLE
    assert Reason.COURT_OR_TRIBUNAL_NOTICE_RECEIVED not in result.reason_codes


def test_global_precedence_and_reassessment_are_explainable():
    profile = case()
    profile.key_facts.update(formal_legal_notice_received=True, facts_materially_disputed=True)
    result = evaluate_professional_help(profile)
    assert result.level == Level.LEGAL_HELP_RECOMMENDED
    assert {Reason.FORMAL_LEGAL_NOTICE_RECEIVED, Reason.COMPLEX_FACTUAL_DISPUTE} <= set(result.reason_codes)
    assert Trigger.CASE_FACTS_BECAME_DISPUTED not in result.reassess_on
    assert Trigger.FORMAL_PROCEEDING_STARTED in result.reassess_on
    profile.key_facts["arrest_or_police_risk"] = True
    assert evaluate_professional_help(profile).level == Level.URGENT_LEGAL_HELP


def test_completed_rejected_action_counts_but_planned_action_does_not():
    profile = case()
    profile.recommended_next_action = {"type": "response_rejected"}
    assert evaluate_professional_help(profile).level == Level.SELF_HELP_REASONABLE
    profile.actions_completed.append({"type": "response_rejected"})
    result = evaluate_professional_help(profile)
    assert result.level == Level.CONSIDER_LEGAL_HELP
    assert Reason.GRIEVANCE_FAILED in result.reason_codes


@pytest.mark.parametrize("category,signal,reason,level", [
    ("CONSUMER", "seller_disputes_transaction", Reason.SELLER_DISPUTES_TRANSACTION, Level.CONSIDER_LEGAL_HELP),
    ("HOUSING_TENANT", "eviction_proceeding_started", Reason.EVICTION_PROCEEDING_STARTED, Level.LEGAL_HELP_RECOMMENDED),
    ("EMPLOYMENT", "termination_occurred", Reason.TERMINATION_OCCURRED, Level.CONSIDER_LEGAL_HELP),
    ("CYBER_FRAUD", "police_case_complication", Reason.POLICE_CASE_COMPLICATION, Level.LEGAL_HELP_RECOMMENDED),
])
def test_domain_rules(category, signal, reason, level):
    profile = case(category)
    profile.key_facts[signal] = True
    result = evaluate_professional_help(profile)
    assert result.level == level
    assert reason in result.reason_codes


def test_cyber_reporting_actions_unchanged_by_assessment():
    profile = case("CYBER_FRAUD")
    before = profile.model_dump(exclude={"professional_help"})
    assert evaluate_professional_help(profile).level == Level.SELF_HELP_REASONABLE
    assert profile.model_dump(exclude={"professional_help"}) == before


def test_reassessment_uses_new_case_facts_not_old_assessment():
    profile = case("HOUSING_TENANT")
    profile.professional_help = evaluate_professional_help(profile)
    profile.key_facts["court_notice_received"] = True
    result = evaluate_professional_help(profile)
    assert result.level == Level.LEGAL_HELP_RECOMMENDED
    assert Reason.COURT_OR_TRIBUNAL_NOTICE_RECEIVED in result.reason_codes


def test_unknown_domain_still_uses_global_rules():
    profile = case()
    profile.category = "UNKNOWN_DOMAIN"
    profile.key_facts["formal_proceeding_started"] = True
    assert evaluate_professional_help(profile).level == Level.LEGAL_HELP_RECOMMENDED


def test_safety_state_does_not_become_a_lawyer_signal():
    profile = case("HOUSING_TENANT")
    profile.safety_status = {"is_safety_case": True, "immediate_danger": True, "severity": "RED"}
    profile.risk_level = "RED"
    assert evaluate_professional_help(profile).level == Level.SELF_HELP_REASONABLE


@pytest.mark.parametrize("message", ["Do I need a lawyer?", "Can I handle this myself?", "Kya mujhe vakil chahiye?", "क्या मुझे वकील चाहिए?"])
def test_direct_question_detection(message):
    assert asks_about_legal_help(message)
