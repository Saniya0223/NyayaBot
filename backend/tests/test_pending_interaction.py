"""Local-only regression checks for case-scoped short follow-up replies."""

import asyncio

from app.agents.conversation_agent import ConversationalLegalAgent
from app.domains import domain_registry
from app.llm.contracts import (
    CaseExtraction, ExtractedCaseFacts, IssueClassification, LLMProvider,
    ProviderStatus,
)
from app.schemas.chat import ChatTurnRequest, PendingInteraction, StructuredCaseProfile
from app.services.llm_conversation import GeminiConversationService
from app.services.pending_interaction import (
    document_offer, fact_candidate, resolve_reply, valid_for_case,
)


class StubProvider(LLMProvider):
    def __init__(self, extraction: CaseExtraction, reply: str = "I noted that."):
        self.extraction = extraction
        self.reply = reply
        self.extraction_context = None
        self.response_context = None
        self.extraction_calls = 0
        self.chat_calls = 0

    @property
    def status(self):
        return ProviderStatus(provider="groq", model="local-stub", configured=True, mode="groq", message="local")

    async def extract_case_updates(self, context):
        self.extraction_calls += 1
        self.extraction_context = context
        return self.extraction.model_copy(deep=True)

    async def classify_issue(self, context):
        return self.extraction.classification

    async def chat(self, context):
        self.chat_calls += 1
        self.response_context = context
        return self.reply

    async def analyze_document(self, text, document_type_hint):
        raise AssertionError("No document analysis expected")


def profile_for(category: str = "HOUSING_TENANT") -> StructuredCaseProfile:
    return ConversationalLegalAgent()._init_case_profile("Existing case", category_override=category)


def provider_for(category: str = "HOUSING_TENANT", facts=None, reply="I noted that.") -> StubProvider:
    return StubProvider(CaseExtraction(
        user_intent="Continue case",
        classification=IssueClassification(category=category, issue_type="GENERAL", confidence=0.95),
        facts=facts or ExtractedCaseFacts(),
    ), reply)


def pending(profile, key, kind="EVIDENCE_CONFIRMATION"):
    return PendingInteraction(
        type=kind, target_keys=[key], expected_answer_type="boolean",
        case_id=profile.case_id,
    )


def turn(service, message, profile):
    return asyncio.run(service.process_turn(ChatTurnRequest(message=message, case_id=profile.case_id), profile, []))


def test_boolean_evidence_yes_and_no_are_known_and_not_reasked():
    for answer, expected in (("yes i have", True), ("no", False)):
        profile = profile_for()
        profile.pending_interaction = pending(profile, "deposit_payment_proof_available")
        provider = provider_for()
        result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), answer, profile)
        assert provider.extraction_context.pending_interaction.target_keys == ["deposit_payment_proof_available"]
        assert result.case_profile.key_facts["deposit_payment_proof_available"] is expected
        assert result.case_profile.pending_interaction is None
        assert "deposit_payment_proof_available" not in {
            item["key"] for item in domain_registry.compact_context(result.case_profile)["next_fact_candidates"]
        }
        assert provider.extraction_calls == provider.chat_calls == 1
        assert GeminiConversationService._is_low_context_message(answer)


def test_action_boolean_and_hinglish_replies():
    for answer, expected in (("haan kiya hai", True), ("abhi nahi", False)):
        profile = profile_for("CONSUMER")
        profile.pending_interaction = pending(profile, "seller_contacted", "ACTION_CONFIRMATION")
        result = turn(GeminiConversationService(provider_for("CONSUMER"), ConversationalLegalAgent()), answer, profile)
        assert result.case_profile.key_facts["seller_contacted"] is expected
        assert "seller_contacted" not in {
            item["key"] for item in domain_registry.compact_context(result.case_profile)["next_fact_candidates"]
        }


def test_small_multi_boolean_requires_explicit_two_targets():
    profile = profile_for()
    two = PendingInteraction(
        type="EVIDENCE_CONFIRMATION",
        target_keys=["rental_agreement_available", "deposit_payment_proof_available"],
        expected_answer_type="boolean", case_id=profile.case_id,
    )
    assert valid_for_case(two, profile)
    assert resolve_reply(two, "both").values == {
        "rental_agreement_available": True, "deposit_payment_proof_available": True,
    }
    assert resolve_reply(pending(profile, "rental_agreement_available"), "both").status == "AMBIGUOUS"
    profile.pending_interaction = two
    result = turn(GeminiConversationService(provider_for(), ConversationalLegalAgent()), "both", profile)
    assert result.case_profile.key_facts["rental_agreement_available"] is True
    assert result.case_profile.key_facts["deposit_payment_proof_available"] is True


def test_choice_needs_a_specific_option_and_clarifies_yes():
    profile = profile_for("CONSUMER")
    choice = PendingInteraction(
        type="CHOICE", target_keys=["desired_outcome"], expected_answer_type="choice",
        allowed_choices=["delivery", "refund"], case_id=profile.case_id,
    )
    profile.pending_interaction = choice
    provider = provider_for("CONSUMER", ExtractedCaseFacts(desired_outcome="delivery"), "Would you prefer delivery or refund?")
    result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), "yes", profile)
    assert "desired_outcome" not in result.case_profile.key_facts
    assert result.case_profile.pending_interaction == choice
    assert provider.response_context.pending_resolution["status"] == "AMBIGUOUS"

    provider = provider_for("CONSUMER")
    result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), "refund", result.case_profile)
    assert result.case_profile.key_facts["desired_outcome"] == "refund"
    assert result.case_profile.pending_interaction is None


def test_document_confirmation_uses_only_supported_case_document():
    profile = profile_for()
    profile.pending_interaction = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["TENANT_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=profile.case_id,
    )
    provider = provider_for()
    result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), "ok make", profile)
    assert result.case_profile.document_request["intent"] == "USER_REQUESTED"
    assert result.case_profile.document_request["document_type"] == "TENANT_DEMAND_NOTICE"
    assert result.suggested_action["type"] == "PREPARE_DOC"
    assert result.case_profile.pending_interaction is None
    assert provider.extraction_calls == 1 and provider.chat_calls == 0

    employment = profile_for("EMPLOYMENT")
    employment.pending_interaction = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["SALARY_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=employment.case_id,
    )
    salary = turn(GeminiConversationService(provider_for("EMPLOYMENT"), ConversationalLegalAgent()), "yes prepare it", employment)
    assert salary.case_profile.document_request["document_type"] == "SALARY_DEMAND_NOTICE"

    declined = profile_for()
    declined.pending_interaction = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["TENANT_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=declined.case_id,
    )
    result = turn(GeminiConversationService(provider_for(), ConversationalLegalAgent()), "no", declined)
    assert result.case_profile.document_request is None
    assert result.case_profile.pending_interaction is None


def test_no_pending_bare_confirmation_cannot_invent_a_fact_or_document():
    for message in ("make it", "yes"):
        profile = profile_for()
        provider = provider_for(facts=ExtractedCaseFacts(rental_agreement_available=True))
        provider.extraction.document_request = "TENANT_DEMAND_NOTICE"
        result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), message, profile)
        assert "rental_agreement_available" not in result.case_profile.key_facts
        assert result.case_profile.document_request is None


def test_case_switch_and_topic_switch_do_not_force_old_referent():
    case_a = profile_for()
    case_b = profile_for()
    case_b.pending_interaction = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["TENANT_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=case_a.case_id,
    )
    result = turn(GeminiConversationService(provider_for(), ConversationalLegalAgent()), "yes make it", case_b)
    assert result.case_profile.document_request is None
    assert result.case_profile.pending_interaction is None

    # The pending contract round-trips through the existing profile JSON store,
    # yet still refuses to bind when restored under another case.
    case_b.pending_interaction = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["TENANT_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=case_a.case_id,
    )
    restored = StructuredCaseProfile.model_validate(case_b.model_dump(mode="json"))
    assert not valid_for_case(restored.pending_interaction, restored)

    profile = profile_for("CYBER_FRAUD")
    profile.pending_interaction = pending(profile, "bank_reported", "ACTION_CONFIRMATION")
    provider = provider_for("CYBER_FRAUD", ExtractedCaseFacts(account_freeze_complication=True))
    result = turn(GeminiConversationService(provider, ConversationalLegalAgent()), "My bank froze my account today", profile)
    assert result.case_profile.key_facts["account_freeze_complication"] is True
    assert "bank_reported" not in result.case_profile.key_facts
    assert result.case_profile.pending_interaction is None


def test_devanagari_and_structured_candidate_and_offer():
    profile = profile_for()
    boolean = pending(profile, "rental_agreement_available")
    assert resolve_reply(boolean, "हाँ").values == {"rental_agreement_available": True}
    assert resolve_reply(boolean, "अभी नहीं").values == {"rental_agreement_available": False}
    assert resolve_reply(boolean, "mere paas hai").values == {"rental_agreement_available": True}
    candidate = fact_candidate(profile, {"key": "rental_agreement_available", "purpose": "evidence_context"})
    assert candidate.type == "EVIDENCE_CONFIRMATION"
    assert candidate.case_id == profile.case_id
    profile.recommended_next_action = {"type": "PREPARE_DOC", "doc_type": "TENANT_DEMAND_NOTICE"}
    assert document_offer(profile).target_keys == ["TENANT_DEMAND_NOTICE"]
    profile.recommended_next_action["doc_type"] = "NOT_SUPPORTED"
    assert document_offer(profile) is None
    unsupported = PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=["SALARY_DEMAND_NOTICE"],
        expected_answer_type="document", case_id=profile.case_id,
    )
    assert not valid_for_case(unsupported, profile)

    stale = pending(profile, "rental_agreement_available")
    profile.key_facts["rental_agreement_available"] = False
    assert not valid_for_case(stale, profile)


def test_live_flow_records_a_ranked_question_then_resolves_the_next_yes():
    profile = profile_for()
    profile.opposite_party_name = "Landlord"
    profile.vacating_date = "1 September 2026"
    profile.key_facts.update({
        "landlord_reason": "No reason given", "landlord_contacted": True,
        "rental_agreement_available": True,
    })
    provider = provider_for(reply="Do you have proof that you paid the deposit?")
    service = GeminiConversationService(provider, ConversationalLegalAgent())
    first = turn(service, "What else would help?", profile)
    assert provider.response_context.domain_context["next_fact_candidates"][0]["key"] == "deposit_payment_proof_available"
    assert first.case_profile.pending_interaction.target_keys == ["deposit_payment_proof_available"]
    provider.reply = "Thanks, I have noted that proof."
    second = turn(service, "yes", first.case_profile)
    assert second.case_profile.key_facts["deposit_payment_proof_available"] is True
    assert "deposit_payment_proof_available" not in {
        item["key"] for item in provider.response_context.domain_context["next_fact_candidates"]
    }
    assert second.case_profile.pending_interaction is None


def test_live_flow_records_only_an_eligible_document_offer():
    profile = profile_for()
    profile.opposite_party_name = "Landlord"
    profile.vacating_date = "1 September 2026"
    profile.user_state = "Rajasthan"
    profile.key_facts.update({
        "landlord_reason": "No reason given", "landlord_contacted": True,
        "rental_agreement_available": True, "deposit_payment_proof_available": True,
    })
    service = GeminiConversationService(provider_for(), ConversationalLegalAgent())
    service._refresh_workflow(profile)
    assert profile.recommended_next_action["type"] == "PREPARE_DOC"
    provider = provider_for(reply=f"I can prepare the {profile.recommended_doc_label}.")
    service = GeminiConversationService(provider, ConversationalLegalAgent())
    offered = turn(service, "What can you do next?", profile)
    assert offered.case_profile.pending_interaction.target_keys == ["TENANT_DEMAND_NOTICE"]
    confirmed = turn(service, "ok make", offered.case_profile)
    assert confirmed.case_profile.document_request["document_type"] == "TENANT_DEMAND_NOTICE"
    assert confirmed.suggested_action["intent"] == "USER_REQUESTED"
