import asyncio
import json

from app.llm.contracts import (
    IssueClassification,
    LLMExtractionContext,
    LLMProviderError,
    LLMResponseContext,
)
from app.llm.gemini_provider import CHAT_MAX_OUTPUT_TOKENS, GeminiProvider


class _InvalidArgumentError(Exception):
    """Mirrors the SDK ClientError raised when a model rejects a config field."""

    code = 400

    def __init__(self) -> None:
        super().__init__("400 INVALID_ARGUMENT. Request contains an invalid argument.")


class _StubResponse:
    def __init__(self, payload: dict):
        self.parsed = None
        self.text = json.dumps(payload)


class _StubModels:
    def __init__(self, outcomes):
        self.configs = []
        self._outcomes = list(outcomes)

    async def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StubClient:
    def __init__(self, models):
        self.aio = type("_Aio", (), {"models": models})()


CLASSIFICATION = {"category": "HOUSING_TENANT", "issue_type": "Security Deposit", "confidence": 0.9}


def _provider(outcomes):
    provider = GeminiProvider(api_key="test-key", model="gemini-test")
    models = _StubModels(outcomes)
    provider._client = _StubClient(models)
    return provider, models


def _classify(provider):
    return asyncio.run(
        provider.classify_issue(LLMExtractionContext(user_message="Landlord kept my deposit."))
    )


def test_structured_call_never_sends_zero_thinking_budget():
    # Gemini 3.x rejects thinking_budget=0 with HTTP 400, which silently pushed
    # every turn into limited demo mode.
    provider, models = _provider([_StubResponse(CLASSIFICATION)])

    result = _classify(provider)

    assert isinstance(result, IssueClassification)
    thinking = models.configs[0].thinking_config
    assert thinking is not None
    assert thinking.thinking_budget != 0
    # The SDK normalizes the value to a ThinkingLevel enum.
    assert str(thinking.thinking_level.value).lower() == "low"


def test_invalid_argument_retries_once_without_thinking_config():
    provider, models = _provider([_InvalidArgumentError(), _StubResponse(CLASSIFICATION)])

    result = _classify(provider)

    assert result.category == "HOUSING_TENANT"
    assert len(models.configs) == 2
    assert models.configs[0].thinking_config is not None
    assert models.configs[1].thinking_config is None
    # The retry must not drop the structured-output contract.
    assert models.configs[1].response_json_schema is not None


def test_repeated_invalid_argument_surfaces_provider_error():
    provider, models = _provider([_InvalidArgumentError(), _InvalidArgumentError()])

    try:
        _classify(provider)
    except LLMProviderError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("expected LLMProviderError")

    assert len(models.configs) == 2


class _Candidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _ChatResponse:
    def __init__(self, text, finish_reason="STOP"):
        self.text = text
        self.parsed = None
        self.candidates = [_Candidate(finish_reason)]


def _chat(provider):
    return asyncio.run(
        provider.chat(
            LLMResponseContext(
                user_message="Landlord kept my deposit.",
                case_summary={},
                workflow={},
            )
        )
    )


def test_chat_budget_leaves_room_for_thinking_tokens():
    # thinking_level="low" spends ~500+ tokens that are billed against
    # max_output_tokens, which truncated replies mid-sentence at 900.
    provider, models = _provider([_ChatResponse("A complete reply.")])

    assert _chat(provider) == "A complete reply."
    assert models.configs[0].max_output_tokens == CHAT_MAX_OUTPUT_TOKENS
    assert CHAT_MAX_OUTPUT_TOKENS >= 2048


def test_truncated_reply_is_rejected_rather_than_shown():
    provider, _ = _provider([_ChatResponse("I understand you are trying to", "MAX_TOKENS")])

    try:
        _chat(provider)
    except LLMProviderError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("expected LLMProviderError for a truncated reply")
