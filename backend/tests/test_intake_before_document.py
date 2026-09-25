"""Regression tests: understand the case before proposing a document.

Guards the original defect, where a barely-understood case already carried a
recommended document ("Prepare a factual written record") and asked for the
full name and city that template required - before establishing whether the
user was in danger, what happened, or when.
"""

import pytest

from app.agents.conversation_agent import conversational_agent
from app.config import settings
from app.schemas.chat import ChatTurnRequest
from app.schemas.fact_graph import FactGraphSchema, FinancialBreakdown, PartyInfo
from app.services.case_readiness import (
    PRE_INTAKE,
    READY_FOR_ACTION,
    READY_FOR_DOCUMENT,
    UNDERSTANDING_CASE,
)
from app.services.llm_conversation import GeminiConversationService
from app.services.doc_generator import doc_generator
from app.services.safety_triage import assess_safety


@pytest.fixture
def service() -> GeminiConversationService:
    return GeminiConversationService()


def build(service, message: str, profile=None):
    """Run the deterministic turn plus readiness/gating, without any provider."""
    safety = assess_safety(message)
    if profile is None:
        profile = conversational_agent.process_turn(
            ChatTurnRequest(message=message), None
        ).case_profile
    service._refresh_workflow(profile, safety, message)
    return profile, safety


def assert_no_document(profile):
    assert profile.recommended_doc_type is None, profile.recommended_doc_type
    assert profile.recommended_doc_label is None, profile.recommended_doc_label
    assert profile.recommended_next_action is None
    assert profile.missing_document_fields == []
    assert profile.is_ready_for_document is False


def assert_no_identity_questions(profile):
    """Name and city are document fields, never intake questions."""
    for field in ("user_name", "user_city", "full_name", "address"):
        assert field not in profile.intake_missing_facts, (
            f"{field} asked during case understanding: {profile.intake_missing_facts}"
        )


# ----------------------------------------------------------------- A: greeting

def test_A_greeting_is_pre_intake_with_no_document(service):
    profile, _ = build(service, "hi")
    assert profile.category == "GENERAL"
    assert profile.readiness == PRE_INTAKE
    assert_no_document(profile)


# ------------------------------------------------------------ B: threat triage

def test_B_threat_asks_safety_first_not_name_or_city(service):
    profile, safety = build(service, "my neighbour has threatened me")

    assert safety.is_safety_case is True
    assert safety.severity == "ELEVATED"
    assert "immediate danger" in (safety.triage_question or "").lower()

    assert profile.readiness == UNDERSTANDING_CASE
    assert profile.intake_missing_facts[0] == "immediate_danger"
    assert_no_document(profile)
    assert_no_identity_questions(profile)
    # A threat is not a consumer dispute; it must not inherit those questions.
    assert "product_name" not in profile.intake_missing_facts
    assert "invoice_available" not in profile.intake_missing_facts


# --------------------------------------------------------- C: immediate danger

def test_C_immediate_danger_takes_priority(service):
    profile, safety = build(
        service, "my neighbour threatened to kill me and is outside my house"
    )
    assert safety.severity == "IMMEDIATE"
    assert "112" in (safety.guidance or "")
    assert profile.readiness == UNDERSTANDING_CASE
    assert_no_document(profile)
    assert_no_identity_questions(profile)


def test_C_safety_reply_works_without_any_provider(service):
    """The safety path must not depend on the model being reachable."""
    safety = assess_safety("he said he will kill me")
    reply = service._safety_first_reply(safety)
    assert "112" in reply
    assert safety.severity == "IMMEDIATE"
    assert service._safety_quick_replies(safety)


# --------------------------------------------------------- D: evidence of threat

def test_D_threatening_messages_asks_relevant_facts(service):
    profile, safety = build(
        service, "my neighbour sent threatening WhatsApp messages yesterday"
    )
    assert safety.is_safety_case is True
    missing = profile.intake_missing_facts
    # Danger, evidence and police contact are the legally useful questions here.
    assert "immediate_danger" in missing
    assert "evidence_available" in missing
    assert "police_contacted" in missing
    assert_no_document(profile)
    assert_no_identity_questions(profile)


# ------------------------------------------- E/F: document only once earned

def test_E_document_appears_once_facts_are_confirmed(service):
    profile, _ = build(service, "my neighbour has threatened me")
    assert_no_document(profile)

    # Safety triage answered, then the issue facts established.
    profile.key_facts["immediate_danger"] = False
    profile.key_facts["threat_details"] = "verbal threat at the gate"
    profile.key_facts["repeated_incidents"] = True
    profile.key_facts["physical_violence_or_weapon"] = False
    profile.key_facts["evidence_available"] = True
    profile.key_facts["police_contacted"] = False
    profile.incident_date = "2026-09-01"
    profile.user_state = "Rajasthan"
    profile, _ = build(service, "yes that is right", profile)

    assert profile.intake_missing_facts == []
    assert profile.readiness in (READY_FOR_ACTION, READY_FOR_DOCUMENT)
    assert profile.recommended_doc_type is not None
    assert profile.recommended_next_action is not None


def test_F_document_fields_requested_only_after_document_is_selected(service):
    profile, _ = build(service, "my neighbour has threatened me")
    # Nothing is asked about identity while the case is being understood.
    assert profile.missing_document_fields == []

    profile.key_facts.update({
        "immediate_danger": False, "threat_details": "verbal threat",
        "repeated_incidents": True, "physical_violence_or_weapon": False,
        "evidence_available": True, "police_contacted": False,
    })
    profile.incident_date = "2026-09-01"
    profile.user_state = "Rajasthan"
    profile, _ = build(service, "correct", profile)

    # Only now do template fields appear, and only because a document applies.
    assert profile.recommended_doc_type is not None
    assert "user_name" in profile.missing_document_fields


def test_ready_for_document_still_offers_action_and_generates_pdf(service, tmp_path, monkeypatch):
    profile, _ = build(service, "my employer hasn't paid salary for two months")
    assert_no_document(profile)

    profile.user_name = "Example Employee"
    profile.user_city = "Jaipur"
    profile.user_state = "Rajasthan"
    profile.opposite_party_name = "Example Employer"
    profile.unpaid_months = ["July", "August"]
    profile.key_facts["monthly_salary"] = 100000
    profile.key_facts["hr_contacted"] = True
    profile.key_facts["employment_proof_available"] = True
    profile.disputed_amount = 200000
    profile, _ = build(service, "Those details are correct", profile)

    assert profile.readiness == READY_FOR_DOCUMENT
    assert profile.is_ready_for_document is True
    assert profile.missing_document_fields == []
    assert profile.recommended_doc_type == "SALARY_DEMAND_NOTICE"
    assert profile.recommended_next_action["type"] == "PREPARE_DOC"
    assert profile.recommended_next_action["doc_type"] == "SALARY_DEMAND_NOTICE"

    (tmp_path / "documents").mkdir()
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    facts = FactGraphSchema(
        complainant=PartyInfo(name=profile.user_name, city=profile.user_city),
        opposite_party=PartyInfo(name=profile.opposite_party_name),
        incident_narrative="Two months of salary remain unpaid.",
        financials=FinancialBreakdown(amount_paid=profile.disputed_amount),
    )
    document = doc_generator.generate_document(
        case_id=profile.case_id,
        doc_type=profile.recommended_next_action["doc_type"],
        fact_graph=facts,
    )
    assert document.pdf_download_url is not None
    pdf_path = tmp_path / "documents" / document.pdf_download_url.rsplit("/", 1)[-1]
    assert pdf_path.read_bytes().startswith(b"%PDF")


# ------------------------------------------------- G/H: non-safety categories

def test_G_tenancy_asks_issue_facts_not_document_fields(service):
    profile, _ = build(service, "my landlord isn't returning my deposit")
    assert profile.category == "HOUSING_TENANT"
    assert profile.readiness == UNDERSTANDING_CASE
    assert "vacating_date" in profile.intake_missing_facts
    assert_no_document(profile)
    assert_no_identity_questions(profile)


def test_H_employment_asks_issue_facts_and_no_default_record(service):
    profile, _ = build(service, "my employer hasn't paid salary for two months")
    assert profile.category == "EMPLOYMENT"
    assert "unpaid_months" in profile.intake_missing_facts
    assert_no_document(profile)
    assert_no_identity_questions(profile)


# ------------------------------------------------------- the original defect

def test_the_factual_written_record_default_is_gone(service):
    """The hardcoded fallback label must never be produced again."""
    for message in ["hi", "my neighbour has threatened me", "something happened to me"]:
        profile, _ = build(service, message)
        assert profile.recommended_doc_label != "Prepare a factual written record"
        assert profile.recommended_doc_label is None


def test_intake_and_document_lists_are_disjoint_concepts(service):
    """Intake facts describe the issue; document fields identify the parties."""
    profile, _ = build(service, "my landlord isn't returning my deposit")
    assert profile.intake_missing_facts
    assert profile.missing_document_fields == []
    assert "user_name" not in profile.intake_missing_facts
