import asyncio
import re

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.llm.contracts import (
    DocumentAnalysis,
    IssueClassification,
    LLMExtractionContext,
    LLMProvider,
    LLMResponseContext,
    ProviderStatus,
)
from app.schemas.chat import ChatMessage, ChatTurnRequest
from app.services.llm_conversation import GeminiConversationService
from app.services.safety_triage import (
    AMBER,
    CHILD_SAFETY,
    DOMESTIC_OR_PARTNER,
    RED,
    WEAPON,
    assess_safety,
)


DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")


class NoCallProvider(LLMProvider):
    """Configured provider that records any attempted model call."""

    def __init__(self, configured: bool = True):
        self.configured = configured
        self.calls: list[str] = []

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider="groq",
            model="test-model",
            configured=self.configured,
            mode="groq" if self.configured else "limited_demo",
            message="test provider",
        )

    async def extract_case_updates(self, context: LLMExtractionContext):
        self.calls.append("extract")
        raise AssertionError("Safety triage must return before provider extraction")

    async def classify_issue(self, context: LLMExtractionContext) -> IssueClassification:
        self.calls.append("classify")
        raise AssertionError("Safety triage must return before provider classification")

    async def chat(self, context: LLMResponseContext) -> str:
        self.calls.append("chat")
        raise AssertionError("Safety triage must return before provider response composition")

    async def analyze_document(self, text: str, document_type_hint: str) -> DocumentAnalysis:
        self.calls.append("document")
        raise AssertionError("Safety triage must not analyze a document")


def run_safety_turn(message: str, profile=None, history=None):
    provider = NoCallProvider(configured=True)
    service = GeminiConversationService(
        provider=provider,
        workflow_agent=ConversationalLegalAgent(),
    )
    response = asyncio.run(
        service.process_turn(
            ChatTurnRequest(message=message, case_id=profile.case_id if profile else None),
            profile,
            history or [],
        )
    )
    return response, provider


def assert_no_document(response) -> None:
    profile = response.case_profile
    assert response.suggested_action is None
    assert profile.recommended_next_action is None
    assert profile.recommended_doc_type is None
    assert profile.recommended_doc_label is None
    assert profile.missing_document_fields == []
    assert profile.is_ready_for_document is False
    assert not any(term in response.reply_text.casefold() for term in ("document", "draft", "legal notice"))


def test_roman_hinglish_partner_death_threat_routes_before_provider():
    message = "mere pati ne mujhe jaan se marne ki dhamki di"
    assessment = assess_safety(message)

    assert assessment.is_safety_case is True
    assert assessment.safety_level == AMBER
    assert assessment.safety_context == DOMESTIC_OR_PARTNER
    assert assessment.immediate_danger is None
    assert assessment.language_style == "hinglish"
    assert assessment.script_style == "roman"

    response, provider = run_safety_turn(message)
    assert provider.calls == []
    assert response.reply_text.startswith("Kya aap abhi turant khatre mein hain")
    assert "112" in response.reply_text
    assert not DEVANAGARI_RE.search(response.reply_text)
    assert response.case_profile.safety_status["safety_context"] == DOMESTIC_OR_PARTNER
    assert_no_document(response)


def test_devanagari_partner_threat_uses_devanagari_safety_reply():
    message = "मेरे पति ने मुझे जान से मारने की धमकी दी"
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.safety_context == DOMESTIC_OR_PARTNER
    assert assessment.immediate_danger is None
    assert assessment.language_style == "hindi"
    assert assessment.script_style == "devanagari"
    assert provider.calls == []
    assert response.reply_text.startswith("क्या आप अभी तुरंत खतरे में हैं")
    assert DEVANAGARI_RE.search(response.reply_text)
    assert_no_document(response)


def test_english_partner_threat_uses_english_safety_reply():
    message = "my husband threatened to kill me"
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.safety_level == AMBER
    assert assessment.safety_context == DOMESTIC_OR_PARTNER
    assert assessment.immediate_danger is None
    assert assessment.language_style == "english"
    assert provider.calls == []
    assert response.reply_text.startswith("Are you in immediate danger right now")
    assert not DEVANAGARI_RE.search(response.reply_text)
    assert_no_document(response)


@pytest.mark.parametrize(
    "message",
    [
        "mera husband mujhe threaten kar raha hai",
        "woh mujhe maarne ki dhamki de raha hai",
    ],
)
def test_common_roman_hinglish_threats_are_detected(message):
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.is_safety_case is True
    assert assessment.language_style == "hinglish"
    assert assessment.script_style == "roman"
    assert provider.calls == []
    assert not DEVANAGARI_RE.search(response.reply_text)
    assert "khatre" in response.reply_text.casefold()
    assert_no_document(response)


def test_roman_hinglish_current_danger_is_red_and_guidance_first():
    message = "woh bahar khada hai aur bol raha hai mujhe maar dega"
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.safety_level == RED
    assert assessment.immediate_danger is True
    assert provider.calls == []
    assert response.reply_text.startswith("Pehle kisi safe jagah")
    assert "112" in response.reply_text
    assert_no_document(response)


def test_english_weapon_and_current_threat_is_red():
    message = "my neighbour is outside with a knife and says he will kill me"
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.safety_level == RED
    assert assessment.immediate_danger is True
    assert assessment.safety_context == WEAPON
    assert provider.calls == []
    assert response.reply_text.startswith("Move to safety first")
    assert "112" in response.reply_text
    assert_no_document(response)


def test_explicitly_safe_partner_threat_continues_paced_safety_intake():
    message = "mere pati ne dhamki di lekin abhi main safe hun"
    assessment = assess_safety(message)
    response, provider = run_safety_turn(message)

    assert assessment.immediate_danger is False
    assert assessment.safety_level == AMBER
    assert provider.calls == []
    assert response.reply_text.startswith("Theek hai")
    assert "kab hua" in response.reply_text
    assert response.reply_text.count("?") <= 1
    assert_no_document(response)


def test_child_relevance_is_recorded_and_carried_in_safety_conversation():
    child_only = assess_safety("mere bachche bhi ghar mein hain")
    assert child_only.dependants_present is True
    assert CHILD_SAFETY in child_only.contexts
    assert child_only.is_safety_case is False

    offline_provider = NoCallProvider(configured=False)
    offline_service = GeminiConversationService(
        provider=offline_provider,
        workflow_agent=ConversationalLegalAgent(),
    )
    child_response = asyncio.run(
        offline_service.process_turn(
            ChatTurnRequest(message="mere bachche bhi ghar mein hain"),
            None,
            [],
        )
    )
    assert child_response.case_profile.safety_status["dependants_present"] is True
    assert CHILD_SAFETY in child_response.case_profile.safety_status["contexts"]

    first, _ = run_safety_turn("mere pati ne mujhe dhamki di")
    history = [
        ChatMessage(sender="user", text="mere pati ne mujhe dhamki di"),
        ChatMessage(sender="bot", text=first.reply_text),
    ]
    second, provider = run_safety_turn(
        "mere bachche bhi ghar mein hain",
        first.case_profile,
        history,
    )
    assert provider.calls == []
    assert second.case_profile.key_facts["dependants_present"] is True
    assert CHILD_SAFETY in second.case_profile.safety_status["contexts"]
    assert second.case_profile.language_style == "hinglish"
    assert second.case_profile.script_style == "roman"
    assert_no_document(second)


def test_roman_hinglish_style_is_preserved_after_safe_follow_up():
    first, _ = run_safety_turn("mere pati ne mujhe dhamki di")
    history = [
        ChatMessage(sender="user", text="mere pati ne mujhe dhamki di"),
        ChatMessage(sender="bot", text=first.reply_text),
    ]
    second, provider = run_safety_turn("abhi main safe hun", first.case_profile, history)

    assert provider.calls == []
    assert second.case_profile.key_facts["immediate_danger"] is False
    assert second.case_profile.language_style == "hinglish"
    assert second.case_profile.script_style == "roman"
    assert not DEVANAGARI_RE.search(second.reply_text)
    assert "kab hua" in second.reply_text
    assert_no_document(second)


def test_short_no_answer_resolves_the_immediate_danger_question_in_context():
    first, _ = run_safety_turn("mere pati ne mujhe dhamki di")
    second, provider = run_safety_turn("nahi", first.case_profile)

    assert provider.calls == []
    assert second.case_profile.key_facts["immediate_danger"] is False
    assert second.case_profile.language_style == "hinglish"
    assert second.case_profile.script_style == "roman"
    assert "kab hua" in second.reply_text
    assert_no_document(second)


@pytest.mark.parametrize(
    ("message", "language", "script", "contains_devanagari"),
    [
        ("mere landlord ne deposit wapas nahi diya", "hinglish", "roman", False),
        ("मेरे मकान मालिक ने डिपॉजिट वापस नहीं दिया", "hindi", "devanagari", True),
        ("my landlord is not returning my deposit", "english", "roman", False),
    ],
)
def test_non_safety_tenancy_mirrors_language_and_script(
    message,
    language,
    script,
    contains_devanagari,
):
    provider = NoCallProvider(configured=False)
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    response = asyncio.run(service.process_turn(ChatTurnRequest(message=message), None, []))

    assert provider.calls == []
    assert response.case_profile.safety_status is None
    assert response.case_profile.language_style == language
    assert response.case_profile.script_style == script
    assert bool(DEVANAGARI_RE.search(response.reply_text)) is contains_devanagari


def test_greeting_is_normal_pre_intake_without_safety_false_positive():
    provider = NoCallProvider(configured=False)
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    response = asyncio.run(service.process_turn(ChatTurnRequest(message="hi"), None, []))

    assert response.case_profile.safety_status is None
    assert response.case_profile.readiness == "PRE_INTAKE"
    assert response.case_profile.recommended_doc_type is None
    assert response.suggested_action is None
    assert "describe" in response.reply_text.casefold()
