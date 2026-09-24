import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from app.llm.contracts import (
    IssueClassification,
    LLMExtractionContext,
    LLMNotConfiguredError,
    LLMProviderError,
    LLMResponseContext,
)
from app.llm.gemini_provider import CHAT_SYSTEM_PROMPT as GEMINI_CHAT_SYSTEM_PROMPT
from app.llm.gemini_provider import EXTRACTION_SYSTEM_PROMPT as GEMINI_EXTRACTION_SYSTEM_PROMPT
from app.llm.groq_provider import CHAT_SYSTEM_PROMPT as GROQ_CHAT_SYSTEM_PROMPT, GroqProvider
from app.llm.groq_provider import EXTRACTION_SYSTEM_PROMPT as GROQ_EXTRACTION_SYSTEM_PROMPT


def test_chat_prompt_has_consistent_professional_emoji_rules():
    expected_rule = """Use a clean, professional, natural conversational tone in every supported language.
Do not use emojis by default or add decorative emojis to ordinary responses.
If the user is actively using emojis, you may mirror them very lightly only when natural.
Never use emojis as substitutes for headings, bullets, warnings, evidence status, workflow state, or legal seriousness.
In urgent or safety situations, use clear plain language rather than decorative warning emojis."""

    assert GROQ_CHAT_SYSTEM_PROMPT == GEMINI_CHAT_SYSTEM_PROMPT
    assert expected_rule in GROQ_CHAT_SYSTEM_PROMPT


def test_both_provider_prompts_use_domain_candidates_for_conversational_questions():
    assert GROQ_CHAT_SYSTEM_PROMPT == GEMINI_CHAT_SYSTEM_PROMPT
    assert GROQ_EXTRACTION_SYSTEM_PROMPT == GEMINI_EXTRACTION_SYSTEM_PROMPT
    assert "domain_context.next_fact_candidates is the only ranked source" in GROQ_CHAT_SYSTEM_PROMPT
    assert "Do not select ordinary questions from missing_information" in GROQ_CHAT_SYSTEM_PROMPT
    assert 'from "missing_information"' not in GROQ_CHAT_SYSTEM_PROMPT
    assert "offer useful preliminary guidance even if readiness is UNDERSTANDING_CASE" in GROQ_CHAT_SYSTEM_PROMPT
    assert "seller_response_received=false" in GROQ_EXTRACTION_SYSTEM_PROMPT
    assert "unavailable_facts" in GROQ_EXTRACTION_SYSTEM_PROMPT


def test_groq_status_unconfigured():
    provider = GroqProvider(api_key="", model="llama-3.3-70b-versatile")
    assert provider.status.configured is False
    assert provider.status.mode == "limited_demo"
    assert provider.status.provider == "groq"


def test_groq_status_configured():
    provider = GroqProvider(api_key="gsk-testkey", model="llama-3.3-70b-versatile")
    assert provider.status.configured is True
    assert provider.status.mode == "groq"
    assert provider.status.provider == "groq"


def test_groq_chat():
    provider = GroqProvider(api_key="gsk-testkey", model="llama-3.3-70b-versatile")
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a legal guidance reply from Groq."
    mock_response = MagicMock(choices=[mock_choice])

    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

    reply = asyncio.run(
        provider.chat(
            LLMResponseContext(
                user_message="Landlord kept my deposit.",
                case_summary={},
                workflow={},
            )
        )
    )
    assert reply == "This is a legal guidance reply from Groq."


def test_groq_classify_issue():
    provider = GroqProvider(api_key="gsk-testkey", model="llama-3.3-70b-versatile")
    payload = {
        "category": "HOUSING_TENANT",
        "issue_type": "Security Deposit",
        "confidence": 0.95,
    }
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(payload)
    mock_response = MagicMock(choices=[mock_choice])

    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = asyncio.run(
        provider.classify_issue(LLMExtractionContext(user_message="Landlord kept my deposit."))
    )
    assert isinstance(result, IssueClassification)
    assert result.category == "HOUSING_TENANT"
    assert result.issue_type == "Security Deposit"


def test_groq_unconfigured_raises():
    provider = GroqProvider(api_key="", model="llama-3.3-70b-versatile")
    try:
        asyncio.run(
            provider.chat(
                LLMResponseContext(
                    user_message="Test",
                    case_summary={},
                    workflow={},
                )
            )
        )
    except LLMNotConfiguredError:
        pass
    else:
        raise AssertionError("expected LLMNotConfiguredError")
