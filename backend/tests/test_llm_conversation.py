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
    assert provider.response_context.case_summary["facts"]["disputed_amount"] == 50000
    assert provider.response_context.workflow["current_stage_key"] == "INFORMAL_REQUEST"
    assert provider.response_context.language_style == "hinglish"
    assert provider.response_context.legal_sources
    assert "document:user_name" not in provider.response_context.missing_information
    assert "user_name" in response.case_profile.missing_document_fields

    asyncio.run(
        service.process_turn(
            ChatTurnRequest(
                message="I want to prepare the demand letter. Please ask for missing details.",
                case_id=response.case_profile.case_id,
            ),
            response.case_profile,
            history,
        )
    )
    assert "document:user_name" in provider.response_context.missing_information
    assert "document:property_address" in provider.response_context.missing_information


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
