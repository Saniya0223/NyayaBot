import asyncio

from app.agents.conversation_agent import ConversationalLegalAgent
from app.llm.contracts import (
    CaseExtraction,
    DocumentAnalysis,
    ExtractedCaseFacts,
    IssueClassification,
    LLMExtractionContext,
    LLMProvider,
    LLMProviderError,
    LLMResponseContext,
    ProviderStatus,
)
from app.schemas.chat import ChatMessage, ChatTurnRequest
from app.services.llm_conversation import GeminiConversationService


class FakeGeminiProvider(LLMProvider):
    def __init__(self, extraction: CaseExtraction, fail: bool = False):
        self.extraction = extraction
        self.fail = fail
        self.extraction_context = None
        self.response_context = None

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider="gemini",
            model="gemini-test",
            configured=True,
            mode="gemini",
            message="Test provider",
        )

    async def extract_case_updates(self, context: LLMExtractionContext) -> CaseExtraction:
        self.extraction_context = context
        if self.fail:
            raise LLMProviderError("test failure")
        return self.extraction

    async def classify_issue(self, context: LLMExtractionContext) -> IssueClassification:
        return self.extraction.classification

    async def chat(self, context: LLMResponseContext) -> str:
        self.response_context = context
        return "Theek hai — maine aapke validated facts aur current legal step ko update kar diya hai."

    async def analyze_document(self, text: str, document_type_hint: str) -> DocumentAnalysis:
        raise NotImplementedError


class SequencedFakeProvider(FakeGeminiProvider):
    def __init__(self, extractions: list[CaseExtraction]):
        super().__init__(extractions[0])
        self.extractions = list(extractions)
        self.response_contexts: list[LLMResponseContext] = []

    async def extract_case_updates(self, context: LLMExtractionContext) -> CaseExtraction:
        self.extraction_context = context
        return self.extractions.pop(0)

    async def chat(self, context: LLMResponseContext) -> str:
        self.response_contexts.append(context)
        return "I have noted the facts and can explain the current workflow step."


def test_eligible_document_request_uses_prepare_action_without_model_draft():
    provider = FakeGeminiProvider(
        CaseExtraction(
            user_intent="Prepare the consumer notice",
            classification=IssueClassification(
                category="CONSUMER", issue_type="DEFECTIVE_PRODUCT", confidence=0.98,
            ),
        )
    )
    agent = ConversationalLegalAgent()
    service = GeminiConversationService(provider=provider, workflow_agent=agent)
    profile = agent._init_case_profile("The seller supplied a defective product", category_override="CONSUMER")
    profile.opposite_party_name = "Example Seller"
    profile.key_facts["product_name"] = "Air conditioner"
    profile.incident_date = "10 September 2026"
    profile.key_facts["seller_contacted"] = False
    profile.key_facts["invoice_available"] = True
    profile.user_state = "Maharashtra"
    service._refresh_workflow(profile)
    assert profile.recommended_next_action is not None
    assert profile.recommended_next_action["type"] == "PREPARE_DOC"
    assert profile.missing_document_fields

    response = asyncio.run(service.process_turn(ChatTurnRequest(message="prepare the notice"), profile, []))

    assert provider.extraction_context is not None
    assert provider.response_context is None
    assert response.suggested_action == response.case_profile.recommended_next_action
    assert response.suggested_action["type"] == "PREPARE_DOC"
    assert not response.case_profile.key_facts.get("document_intake_active")
    assert len(response.reply_text) < 250
    assert "PDF" in response.reply_text
    assert "LEGAL NOTICE" not in response.reply_text

    provider.response_context = None
    ordinary = asyncio.run(service.process_turn(ChatTurnRequest(message="What can I do next?"), response.case_profile, []))
    assert provider.response_context is not None
    assert ordinary.suggested_action == ordinary.case_profile.recommended_next_action


def test_document_words_without_prepare_action_remain_ordinary_chat():
    provider = FakeGeminiProvider(
        CaseExtraction(
            user_intent="Asks about a notice",
            classification=IssueClassification(category="GENERAL", issue_type="GENERAL", confidence=0.9),
        )
    )
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())

    response = asyncio.run(service.process_turn(ChatTurnRequest(message="prepare the notice"), None, []))

    assert response.suggested_action is None
    assert provider.response_context is not None


def tenancy_extraction(amount: float = 50000) -> CaseExtraction:
    return CaseExtraction(
        user_intent="Recover rental security deposit",
        language_style="hinglish",
        classification=IssueClassification(
            category="HOUSING_TENANT",
            issue_type="Security Deposit Withholding",
            confidence=0.98,
        ),
        facts=ExtractedCaseFacts(
            user_city="Noida",
            user_state="Uttar Pradesh",
            disputed_amount=amount,
            vacating_date="10 August 2026",
            rental_agreement_available=True,
            deposit_payment_proof_available=False,
            landlord_contacted=True,
            landlord_reason="Landlord says it will be returned later",
        ),
        confidence_by_field=[
            {"field": "user_city", "confidence": 0.98},
            {"field": "user_state", "confidence": 0.98},
            {"field": "disputed_amount", "confidence": 0.99},
            {"field": "vacating_date", "confidence": 0.96},
            {"field": "rental_agreement_available", "confidence": 0.95},
            {"field": "deposit_payment_proof_available", "confidence": 0.95},
            {"field": "landlord_contacted", "confidence": 0.92},
            {"field": "landlord_reason", "confidence": 0.9},
        ],
        evidence_detected=["rental_agreement"],
    )


def test_consumer_follow_up_context_supports_direct_guidance_without_reasking_known_facts():
    def extraction(intent: str, **facts) -> CaseExtraction:
        return CaseExtraction(
            user_intent=intent,
            classification=IssueClassification(
                category="CONSUMER", issue_type="NON_DELIVERY", confidence=0.95,
            ),
            facts=ExtractedCaseFacts(**facts),
        )

    provider = SequencedFakeProvider([
        extraction("Delayed order"),
        extraction("Instagram seller", seller_platform="Instagram"),
        extraction("Named seller and product", opposite_party_name="Mahima Gift Gallery", product_name="resin items"),
        extraction(
            "Advance payment, unsuccessful contact, and delivery requested",
            purchase_timing="about a month ago",
            advance_payment_made=True,
            seller_contacted=True,
            seller_response_received=False,
            seller_response="No replies; calls unanswered",
            desired_outcome="delivery",
        ),
        extraction("Asks for next steps"),
    ])
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    profile = None
    for message in (
        "my order got delayed a week",
        "seller was a business on insta",
        "Mahima Gift Gallery, I purchased resin items",
        "I paid advance about a month ago, contacted her, and she is not replying or answering calls. I want delivery.",
        "what should i do now",
    ):
        response = asyncio.run(service.process_turn(ChatTurnRequest(message=message), profile, []))
        profile = response.case_profile

    context = provider.response_contexts[-1]
    facts = context.case_summary["facts"]
    assert context.user_message == "what should i do now"
    assert facts["opposite_party_name"] == "Mahima Gift Gallery"
    assert facts["seller_platform"] == "Instagram"
    assert facts["product_name"] == "resin items"
    assert facts["purchase_timing"] == "about a month ago"
    assert facts["advance_payment_made"] is True
    assert facts["seller_contacted"] is True
    assert facts["seller_response_received"] is False
    assert facts["seller_response"] == "No replies; calls unanswered"
    assert facts["desired_outcome"] == "delivery"
    assert context.domain_context["issue_understood"] is True
    assert context.domain_context["guidance_possible"] is True
    assert context.readiness == "UNDERSTANDING_CASE"
    assert context.missing_information == []
    assert response.case_profile.recommended_doc_type is None
    candidate_keys = {item["key"] for item in context.domain_context["next_fact_candidates"]}
    assert not candidate_keys & {
        "seller_contacted", "seller_response_received", "seller_response",
        "desired_outcome", "incident_date", "order_reference_id",
        "payment_transaction_id", "shipment_tracking_id", "user_name",
    }


def test_explicitly_unavailable_fact_is_not_reasked_and_can_later_be_filled():
    provider = SequencedFakeProvider([
        CaseExtraction(
            user_intent="Report online scam",
            classification=IssueClassification(category="CYBER_FRAUD", issue_type="ONLINE_SCAM", confidence=0.95),
            facts=ExtractedCaseFacts(scam_method="online scam"),
            unavailable_facts=["bank_name", "disputed_amount"],
        ),
        CaseExtraction(
            user_intent="Provides bank name",
            classification=IssueClassification(category="CYBER_FRAUD", issue_type="ONLINE_SCAM", confidence=0.95),
            facts=ExtractedCaseFacts(bank_name="Example Bank", disputed_amount=1000),
        ),
    ])
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    first = asyncio.run(service.process_turn(
        ChatTurnRequest(message="I was scammed online and I don't know the bank or amount"), None, [],
    ))
    assert first.case_profile.bank_name is None
    assert first.case_profile.key_facts["unavailable_fact_keys"] == ["bank_name", "disputed_amount"]
    assert "bank_name" in first.case_profile.intake_missing_facts
    assert not {"bank_name", "disputed_amount"} & {
        item["key"] for item in provider.response_contexts[-1].domain_context["next_fact_candidates"]
    }

    second = asyncio.run(service.process_turn(
        ChatTurnRequest(message="I found it: Example Bank, and the amount was 1000"), first.case_profile, [],
    ))
    assert second.case_profile.bank_name == "Example Bank"
    assert second.case_profile.disputed_amount == 1000
    assert "unavailable_fact_keys" not in second.case_profile.key_facts
    assert "bank_name" not in second.case_profile.intake_missing_facts


def test_gemini_turn_uses_recent_history_and_updated_workflow_context():
    provider = FakeGeminiProvider(tenancy_extraction())
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    history = [
        ChatMessage(id=str(index), sender="user" if index % 2 == 0 else "bot", text=f"turn {index}")
        for index in range(12)
    ]

    response = asyncio.run(
        service.process_turn(
            ChatTurnRequest(message="Mera landlord deposit wapas nahi de raha.", case_id="new"),
            None,
            history,
        )
    )

    assert response.llm_mode == "gemini"
    assert response.llm_model == "gemini-test"
    assert response.case_profile.category == "HOUSING_TENANT"
    assert response.case_profile.disputed_amount == 50000
    assert response.case_profile.key_facts["deposit_payment_proof_available"] is False
    assert "deposit_payment_proof_available" not in response.case_profile.missing_required_fields
    assert len(provider.extraction_context.recent_messages) == 8
    assert provider.extraction_context.recent_messages[0]["content"] == "turn 4"
    assert provider.extraction_context.language_style == "hinglish"
    assert provider.extraction_context.script_style == "roman"
    assert any(item["domain_id"] == "HOUSING_TENANT" for item in provider.extraction_context.domain_catalog)
    assert provider.response_context.case_summary["facts"]["disputed_amount"] == 50000
    assert provider.response_context.workflow["current_stage_key"] == "INFORMAL_REQUEST"
    assert provider.response_context.language_style == "hinglish"
    assert provider.response_context.script_style == "roman"
    assert provider.response_context.domain_context["domain_id"] == "HOUSING_TENANT"
    assert provider.response_context.legal_sources
    assert "document:user_name" not in provider.response_context.missing_information
    assert "user_name" in response.case_profile.missing_document_fields

    provider.response_context = None
    handoff = asyncio.run(
        service.process_turn(
            ChatTurnRequest(
                message="I want to prepare the demand letter. Please ask for missing details.",
                case_id=response.case_profile.case_id,
            ),
            response.case_profile,
            history,
        )
    )
    assert provider.response_context is None
    assert handoff.suggested_action["type"] == "PREPARE_DOC"
    assert "user_name" in handoff.case_profile.missing_document_fields
    assert "property_address" in handoff.case_profile.missing_document_fields
    assert "form" in handoff.reply_text.casefold()


def test_gemini_candidate_conflict_does_not_overwrite_existing_fact():
    workflow_agent = ConversationalLegalAgent()
    profile = workflow_agent._init_case_profile(
        "tenant deposit dispute", "case-conflict", category_override="HOUSING_TENANT"
    )
    profile.disputed_amount = 50000
    profile.fact_metadata["disputed_amount"] = {
        "value": 50000,
        "source": "chat",
        "confidence": 0.95,
        "confirmed": False,
    }
    provider = FakeGeminiProvider(tenancy_extraction(amount=45000))
    service = GeminiConversationService(provider=provider, workflow_agent=workflow_agent)

    response = asyncio.run(
        service.process_turn(
            ChatTurnRequest(message="Actually the amount may be 45,000", case_id=profile.case_id),
            profile,
            [],
        )
    )

    assert response.case_profile.disputed_amount == 50000
    assert response.case_profile.key_facts["pending_conflict"]["candidate"] == 45000
    assert response.quick_replies == ["Keep 50000", "Use 45000.0"]
    assert provider.response_context.conflict["field"] == "disputed_amount"


def test_provider_failure_returns_limited_mode_without_freezing():
    provider = FakeGeminiProvider(tenancy_extraction(), fail=True)
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())

    response = asyncio.run(
        service.process_turn(
            ChatTurnRequest(message="My landlord kept my deposit", case_id="new"),
            None,
            [],
        )
    )

    assert response.llm_mode == "limited_demo"
    assert response.case_profile.case_id
    assert response.reply_text.startswith("Gemini is temporarily unavailable")
