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
from app.llm.groq_provider import GroqProvider


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
