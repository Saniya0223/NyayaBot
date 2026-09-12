"""Regression tests for RAG query construction.

The bug these guard against: retrieval was driven by the newest user message
alone, so a terse confirmation turn ("Yes, both") stripped the legal context out
of the query and collapsed the retrieved sources.
"""

import pytest

from app.agents.rag_node import RagQueryContext, statutory_rag
from app.schemas.chat import EvidenceStatusItem, StructuredCaseProfile
from app.services.llm_conversation import GeminiConversationService


def build_profile(**overrides) -> StructuredCaseProfile:
    base = {
        "case_id": "test-case",
        "case_number": "NYA-2026-TEST0001",
        "title": "Test case",
        "category": "HOUSING_TENANT",
        "category_display_name": "Housing & Tenancy Dispute",
        "issue_type": "Security Deposit Withholding",
        "current_stage_key": "FORMAL_DEMAND_SENT",
        "current_stage_label": "Formal Written Notice Issued",
    }
    base.update(overrides)
    return StructuredCaseProfile(**base)


@pytest.fixture
def service() -> GeminiConversationService:
    # Provider is never called: only query construction and retrieval are tested.
    return GeminiConversationService()


# --------------------------------------------------------------- low context

@pytest.mark.parametrize("message", [
    "Yes", "Yes, both.", "No", "Okay", "done", "I did", "Yesterday",
    "July and August.", "10", "", "   ",
])
def test_terse_turns_are_detected_as_low_context(service, message):
    assert service._is_low_context_message(message) is True


@pytest.mark.parametrize("message", [
    "My landlord is refusing to return the security deposit after I vacated.",
    "The seller rejected my refund request for the defective laptop in writing.",
])
def test_substantive_turns_are_not_low_context(service, message):
    assert service._is_low_context_message(message) is False


def test_low_context_message_is_excluded_from_query_text():
    query = RagQueryContext(
        category="HOUSING_TENANT",
        issue_type="Security Deposit Withholding",
        state="Uttar Pradesh",
        workflow_stage="FORMAL_DEMAND_SENT",
        facts=("rental_agreement", "deposit_payment_proof"),
        latest_message="Yes, both",
        low_context=True,
    )
    text = query.to_query_text()
    assert "Yes, both" not in text
    assert "Security Deposit Withholding" in text
    assert "Uttar Pradesh" in text


# ------------------------------------------------------------------ PII rules

def test_rag_query_excludes_pii(service):
    profile = build_profile(
        user_name="Saniya Sharma",
        user_city="Noida",
        user_state="Uttar Pradesh",
        opposite_party_name="Raj Verma",
        opposite_party_address="B-1204, Sector 62, Noida",
        property_address="Flat 9, Green Residency, Noida",
        disputed_amount=50000,
        transaction_id="UTR123456789",
        bank_name="HDFC Bank",
    )
    text = service._build_rag_query(profile, "Yes, both").to_query_text()

    for secret in ["Saniya", "Sharma", "Raj Verma", "B-1204", "Green Residency",
                   "UTR123456789", "HDFC", "50000"]:
        assert secret not in text, f"PII leaked into RAG query: {secret}"
    # Jurisdiction, which is legally necessary, is retained.
    assert "Uttar Pradesh" in text


def test_city_dropped_when_state_present_and_kept_when_not(service):
    with_state = service._build_rag_query(
        build_profile(user_city="Noida", user_state="Uttar Pradesh"), "Yes")
    without_state = service._build_rag_query(build_profile(user_city="Noida"), "Yes")
    assert "Noida" not in with_state.to_query_text()
    assert "Noida" in without_state.to_query_text()


def test_only_allowlisted_facts_reach_the_query(service):
    profile = build_profile(evidence_checklist=[
        EvidenceStatusItem(id="rental_agreement", name="Rental agreement", why_needed="x", is_available=True),
        EvidenceStatusItem(id="secret_internal_note", name="Not allowlisted", why_needed="x", is_available=True),
    ])
    query = service._build_rag_query(profile, "Yes")
    assert "rental_agreement" in query.facts
    assert "secret_internal_note" not in query.facts


# ------------------------------------------- required scenarios A through E

def sections_for(service, profile, message):
    query = service._build_rag_query(profile, message)
    return [c.section for c in statutory_rag.retrieve_for_context(query)]


def test_A_tenant_deposit_survives_terse_turn(service):
    """The reported bug: 'Yes, both.' must not strip tenancy law from retrieval."""
    profile = build_profile(user_state="Uttar Pradesh", evidence_checklist=[
        EvidenceStatusItem(id="rental_agreement", name="Rental agreement", why_needed="x", is_available=True),
        EvidenceStatusItem(id="deposit_payment_proof", name="Deposit proof", why_needed="x", is_available=True),
    ])
    sections = sections_for(service, profile, "Yes, both.")
    assert sections, "terse turn returned no tenancy sources"
    # Section 11 is the security-deposit refund obligation: the on-point provision.
    assert "Section 11" in sections


def test_B_employment_terse_turn(service):
    profile = build_profile(category="EMPLOYMENT", issue_type="Unpaid Salary / Delayed Wages",
                            current_stage_key="INFORMAL_REQUEST")
    query = service._build_rag_query(profile, "July and August.")
    # No curated employment corpus exists, so the retriever must stay silent
    # rather than substitute unrelated law; grounding comes from OFFICIAL_SOURCES.
    assert statutory_rag.retrieve_for_context(query) == []
    assert query.low_context is True
    assert "Unpaid Salary" in query.to_query_text()


def test_C_consumer_refund_terse_turn(service):
    profile = build_profile(category="CONSUMER", issue_type="Defective Product / Refund Refusal",
                            current_stage_key="FORMAL_DEMAND_SENT")
    sections = sections_for(service, profile, "No.")
    assert sections, "terse turn returned no consumer sources"
    assert any("2(" in section for section in sections), sections


def test_D_cyber_fraud_terse_turn(service):
    profile = build_profile(category="CYBER_FRAUD", issue_type="UPI / Banking Scam",
                            current_stage_key="BANK_REPORTED")
    query = service._build_rag_query(profile, "Yesterday.")
    assert statutory_rag.retrieve_for_context(query) == []
    assert query.low_context is True
    assert "UPI" in query.to_query_text()


def test_E_long_message_contributes_to_retrieval(service):
    profile = build_profile(category="CONSUMER", issue_type="Defective Product / Refund Refusal")
    message = ("The seller refused my refund and I want to know the limitation "
               "period for filing a consumer complaint before the commission.")
    query = service._build_rag_query(profile, message)
    assert query.low_context is False
    assert message.strip() in query.to_query_text()
    sections = [c.section for c in statutory_rag.retrieve_for_context(query)]
    assert "Section 69" in sections, f"limitation provision not surfaced: {sections}"


# ------------------------------------------------- stability across turns

def test_retrieval_does_not_collapse_between_turn_2_and_turn_3(service):
    """Turn 3 being terse must not shrink retrieval below turn 2."""
    profile = build_profile(user_city="Noida", user_state="Uttar Pradesh")

    turn2 = sections_for(service, profile, "Noida, Uttar Pradesh. I moved out on 10 August 2026.")

    profile.evidence_checklist = [
        EvidenceStatusItem(id="rental_agreement", name="Rental agreement", why_needed="x", is_available=True),
        EvidenceStatusItem(id="deposit_payment_proof", name="Deposit proof", why_needed="x", is_available=True),
    ]
    turn3 = sections_for(service, profile, "Yes, both")

    assert len(turn3) >= len(turn2), f"retrieval collapsed: turn2={turn2} turn3={turn3}"
    assert "Section 11" in turn3


def test_query_text_is_stable_for_same_state(service):
    """Same case state plus different terse turns yields the same query."""
    profile = build_profile(user_state="Uttar Pradesh")
    a = service._build_rag_query(profile, "Yes").to_query_text()
    b = service._build_rag_query(profile, "No").to_query_text()
    assert a == b


def test_extraction_still_receives_no_legal_sources():
    """The extraction contract must remain free of statutory context."""
    from app.llm.contracts import LLMExtractionContext
    assert "legal_sources" not in LLMExtractionContext.model_fields
