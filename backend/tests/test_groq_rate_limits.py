"""Bounded retries and terminal local fallback, with no external HTTP calls."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from groq import APIConnectionError, APIStatusError, APITimeoutError, AsyncGroq, AuthenticationError, RateLimitError

from app.agents.conversation_agent import ConversationalLegalAgent
from app.config import settings
from app.llm.contracts import LLMExtractionContext, LLMProviderError, LLMRateLimitedError
from app.llm.groq_provider import GroqProvider
from app.schemas.chat import ChatTurnRequest, PendingInteraction
from app.services.language_style import LanguageScript
from app.services.llm_conversation import GeminiConversationService


RESULT = {
    "user_intent": "Report unpaid salary",
    "classification": {"category": "EMPLOYMENT", "issue_type": "UNPAID_DELAYED_SALARY", "confidence": 0.95},
    "facts": {"disputed_amount": 12000, "hr_contacted": False},
}


def completion():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(RESULT)))],
        usage=SimpleNamespace(
            prompt_tokens=5000, prompt_tokens_details=SimpleNamespace(cached_tokens=3000),
            completion_tokens=400, total_tokens=5400,
        ),
    )


def status_error(error_type=RateLimitError, status=429, headers=None):
    response = httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "https://unit.test/completions"),
    )
    return error_type("private-error-body", response=response, body={"private": "private-case-data"})


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in rate-limit tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS", 1.0)
    result = GroqProvider(api_key="unit-test-placeholder", model="openai/gpt-oss-120b")
    result._client.chat.completions.create = AsyncMock(return_value=completion())
    return result


@pytest.fixture
def sleep(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr("app.llm.groq_provider.asyncio.sleep", fake)
    return fake


def extract(provider):
    return asyncio.run(provider.extract_case_updates(LLMExtractionContext(user_message="private-user-message")))


def test_sdk_retries_are_disabled(provider):
    assert provider._client.max_retries == 0


@pytest.mark.parametrize("seconds", [5, 40])
def test_long_rate_limit_is_one_attempt_and_no_sleep(provider, sleep, caplog, seconds):
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": str(seconds)})
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()
    assert f"retry_after_seconds={float(seconds)}" in caplog.text
    assert "decision=no_retry" in caplog.text
    assert "private-" not in caplog.text


def test_short_rate_limit_retries_once_and_preserves_cache_usage(provider, sleep, caplog):
    provider._client.chat.completions.create.side_effect = [
        status_error(headers={"retry-after": "0.2"}), completion(),
    ]
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        assert extract(provider).facts.disputed_amount == 12000
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.2)
    assert "decision=retry_once" in caplog.text
    assert "event=llm_usage" in caplog.text
    assert "cached_tokens=3000 fresh_prompt_tokens=2000" in caplog.text
    assert "cache_hit_percent=60.0" in caplog.text


def test_second_rate_limit_is_terminal_never_a_third_attempt(provider, sleep, caplog):
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": "0.2"})
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.2)
    decisions = [record.getMessage() for record in caplog.records if "event=groq_rate_limit" in record.getMessage()]
    assert len(decisions) == 2
    assert decisions[0].endswith("decision=retry_once")
    assert decisions[1].endswith("decision=no_retry")


@pytest.mark.parametrize("value", [None, "", "not-a-time", "nan", "inf", "-0.2"])
def test_missing_or_invalid_timing_never_triggers_a_blind_retry(provider, sleep, value):
    headers = {"retry-after": value} if value is not None else {}
    provider._client.chat.completions.create.side_effect = status_error(headers=headers)
    with pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


def test_retry_threshold_is_configurable(provider, sleep, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS", 0.1)
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": "0.2"})
    with pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


def test_exhausted_request_quota_prevents_early_retry_even_with_short_retry_after(provider, sleep):
    provider._client.chat.completions.create.side_effect = status_error(headers={
        "retry-after": "0.2", "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "2m59.56s",
    })
    with pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


def test_exhausted_token_reset_can_supply_a_short_retry_when_retry_after_is_missing(provider, sleep):
    provider._client.chat.completions.create.side_effect = [status_error(headers={
        "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "200ms",
    }), completion()]
    extract(provider)
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.2)


def test_rate_limit_metadata_is_numeric_and_never_logs_response_content(provider, sleep, caplog):
    provider._client.chat.completions.create.side_effect = status_error(headers={
        "retry-after": "40", "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "7.66s", "x-ratelimit-remaining-requests": "12",
        "x-ratelimit-reset-requests": "2m59.56s", "x-private-header": "private-header",
    })
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert "http_status=429 retry_after_seconds=40.0 remaining_tokens=0" in caplog.text
    assert "token_reset_seconds=7.66 remaining_requests=12 request_reset_seconds=179.56" in caplog.text
    assert "private-" not in caplog.text
    assert "unit-test-placeholder" not in caplog.text


@pytest.mark.parametrize("value, expected", [("0.2s", 0.2), ("200ms", 0.2), ("2m59.56s", 179.56), ("1h2m3s", 3723), ("private-data", None)])
def test_reset_header_duration_parsing(value, expected):
    assert GroqProvider._reset_seconds(value) == expected


def test_retry_after_http_date_is_supported_without_changing_any_prompt(monkeypatch):
    monkeypatch.setattr("app.llm.groq_provider.time.time", lambda: 0)
    assert GroqProvider._retry_after_seconds("Thu, 01 Jan 1970 00:00:40 GMT") == 40


def test_authentication_failure_is_not_rate_limit_and_is_not_retried(provider, sleep, caplog):
    provider._client.chat.completions.create.side_effect = status_error(AuthenticationError, 401)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(LLMProviderError) as raised:
        extract(provider)
    assert not isinstance(raised.value, LLMRateLimitedError)
    assert isinstance(raised.value.__cause__, AuthenticationError)
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()
    assert "event=groq_rate_limit" not in caplog.text


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), APITimeoutError(httpx.Request("POST", "https://unit.test"))])
def test_timeouts_remain_distinct_and_bounded(provider, sleep, caplog, error):
    provider._client.chat.completions.create.side_effect = error
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(LLMProviderError, match="timed out") as raised:
        extract(provider)
    assert not isinstance(raised.value, LLMRateLimitedError)
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.4)
    assert "event=groq_rate_limit" not in caplog.text


@pytest.mark.parametrize("error", [
    APIConnectionError(request=httpx.Request("POST", "https://unit.test")),
    status_error(APIStatusError, 408), status_error(APIStatusError, 409), status_error(APIStatusError, 503),
])
def test_transient_errors_retain_one_retry(provider, sleep, error):
    provider._client.chat.completions.create.side_effect = [error, completion()]
    assert extract(provider).facts.disputed_amount == 12000
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.4)


def test_mixed_errors_cannot_reset_the_attempt_budget(provider, sleep):
    provider._client.chat.completions.create.side_effect = [
        APIConnectionError(request=httpx.Request("POST", "https://unit.test")),
        status_error(headers={"retry-after": "0.2"}),
    ]
    with pytest.raises(LLMRateLimitedError):
        extract(provider)
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.4)


def test_normal_success_is_one_attempt(provider, sleep):
    assert extract(provider).facts.hr_contacted is False
    assert provider._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("retry_after, succeeds, expected_attempts", [("40", False, 1), ("0.2", True, 2), ("0.2", False, 2)])
def test_real_sdk_has_no_hidden_retries_using_mock_http_transport(sleep, retry_after, succeeds, expected_attempts):
    requests = []

    def handler(request):
        requests.append(request)
        if succeeds and len(requests) == 2:
            return httpx.Response(200, json={
                "id": "local-test", "object": "chat.completion", "created": 0,
                "model": "openai/gpt-oss-120b",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(RESULT)}}],
            })
        return httpx.Response(429, headers={"retry-after": retry_after}, json={"error": {"message": "Local simulated limit"}})

    async def run():
        async with AsyncGroq(
            api_key="unit-test-placeholder", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            provider = GroqProvider(api_key="", model="openai/gpt-oss-120b")
            provider._client = client
            if succeeds:
                await provider.extract_case_updates(LLMExtractionContext(user_message="Local test"))
            else:
                with pytest.raises(LLMRateLimitedError):
                    await provider.extract_case_updates(LLMExtractionContext(user_message="Local test"))

    asyncio.run(run())
    assert len(requests) == expected_attempts


@pytest.mark.parametrize("retry_after, expected_attempts", [("40", 1), ("0.2", 2)])
def test_final_extraction_rate_limit_stops_the_turn_and_preserves_known_facts(provider, sleep, retry_after, expected_attempts):
    """A rate-limited turn must not corrupt the case - and must not discard it.

    Cycle 3 (H6) changed one thing here deliberately. The original test asserted
    the profile came back byte-identical, which pinned two different things
    together: (a) a *failed LLM extraction* must never apply facts, and (b) the
    user's own message must not be read at all on this path. (a) is the real
    safety property and is asserted harder below than it was before. (b) was
    over-broad, and it was a defect: `_rate_limit_fallback` performed no
    extraction of any kind, while the extraction prompt only ever reads the
    newest turn, so a fact stated during an outage was lost *permanently* - five
    of wave 1's 31 turns went that way, including W1-01's "Flipkart" and
    "Jaipur" and W1-04's "mere paas transaction ID ya UTR nahi hai". Reading the
    user's own words with a deterministic parser is not the same act as trusting
    the output of a call that failed.

    So: the deterministic backfill and a readiness refresh now run here, and the
    guarantee is restated as "nothing already known is changed" - which is the
    stronger and more useful form, and is what the new assertions check.
    """
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("Existing case", category_override="EMPLOYMENT")
    profile.disputed_amount = 10000
    profile.key_facts["pending_conflict"] = {"field": "disputed_amount", "candidate": 20000}
    profile.pending_interaction = PendingInteraction(
        type="FACT_CONFIRMATION", target_keys=["hr_contacted"], expected_answer_type="boolean", case_id=profile.case_id,
    )
    before = profile.model_dump(mode="json")
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": retry_after})
    provider.chat = AsyncMock(side_effect=AssertionError("No final chat after rate-limited extraction"))
    provider.classify_issue = AsyncMock(side_effect=AssertionError("No second provider operation"))
    provider.analyze_document = AsyncMock(side_effect=AssertionError("No evidence provider operation"))
    # Unchanged and still enforced: the offline composer does not run a whole
    # turn here. Only the structural backfill does.
    agent.process_turn = MagicMock(side_effect=AssertionError("No offline agent turn in rate-limit fallback"))
    service = GeminiConversationService(provider=provider, workflow_agent=agent)
    service._apply_extraction = MagicMock(side_effect=AssertionError("Failed extraction must not apply facts"))
    service._verified_sources = MagicMock(side_effect=AssertionError("Turn must stop before final response setup"))

    response = asyncio.run(service.process_turn(
        ChatTurnRequest(message="Use 20000", case_id=profile.case_id), profile, [],
        evidence_context={"text": "private-evidence"}, user_context={"profile": "private-profile"},
    ))

    assert response.llm_mode == "limited_demo"
    assert response.quick_replies == ["Try Groq again"]
    # Nothing already known changed. The message says "Use 20000" and the case
    # already holds 10000: the deterministic layer fills only what is empty, so
    # the stored amount, the unresolved conflict and the open question all
    # survive the outage exactly as they were.
    after = response.case_profile.model_dump(mode="json")
    assert after["disputed_amount"] == before["disputed_amount"] == 10000
    assert after["key_facts"]["pending_conflict"] == before["key_facts"]["pending_conflict"]
    assert after["pending_interaction"] == before["pending_interaction"]
    assert after["case_id"] == before["case_id"]
    assert after["category"] == before["category"]
    assert response.case_profile is profile
    assert provider._client.chat.completions.create.await_count == expected_attempts
    provider.chat.assert_not_awaited()
    provider.classify_issue.assert_not_awaited()
    provider.analyze_document.assert_not_awaited()


def test_rate_limited_turn_still_records_facts_the_user_stated(provider, sleep):
    """H6 / W1-01 turn 3: the outage must not swallow what the user just said."""
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": "40"})
    agent = ConversationalLegalAgent()
    service = GeminiConversationService(provider=provider, workflow_agent=agent)
    profile = agent._init_case_profile("Order not delivered", category_override="CONSUMER")

    response = asyncio.run(service.process_turn(ChatTurnRequest(
        message="flipkart se maine ek washing machine order ki thi, main Jaipur me hun",
        case_id=profile.case_id,
    ), profile, []))

    assert response.llm_mode == "limited_demo"
    assert response.case_profile.opposite_party_name == "Flipkart"
    assert response.case_profile.user_city == "Jaipur"
    assert response.case_profile.user_state == "Rajasthan"
    # One provider attempt, the one that raised. No live call was made.
    assert provider._client.chat.completions.create.await_count == 1


def test_new_case_rate_limit_does_not_invent_or_apply_facts(provider, sleep):
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": "40"})
    agent = ConversationalLegalAgent()
    agent.process_turn = MagicMock(side_effect=AssertionError("No secondary fact extraction"))
    service = GeminiConversationService(provider=provider, workflow_agent=agent)
    response = asyncio.run(service.process_turn(
        ChatTurnRequest(message="My employer owes Rs 30000 salary and my name is Alex", case_id="new"), None, [],
    ))
    assert response.llm_mode == "limited_demo"
    assert response.case_profile.category == "GENERAL"
    assert response.case_profile.user_name is None
    assert response.case_profile.disputed_amount == 0
    assert response.case_profile.key_facts == {}
    assert response.suggested_action is None
    assert provider._client.chat.completions.create.await_count == 1


def test_fallback_itself_makes_zero_groq_calls(provider):
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    profile = service.workflow_agent._init_case_profile("Existing case", category_override="EMPLOYMENT")
    response = service._rate_limit_fallback(
        ChatTurnRequest(message="Try Groq again"), profile, LanguageScript("english", "roman"),
    )
    assert response.llm_mode == "limited_demo"
    provider._client.chat.completions.create.assert_not_awaited()


def test_final_chat_rate_limit_keeps_successfully_extracted_facts(provider, sleep):
    provider._client.chat.completions.create.side_effect = [completion(), status_error(headers={"retry-after": "40"})]
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    service._verified_sources = MagicMock(return_value=[])
    response = asyncio.run(service.process_turn(ChatTurnRequest(message="My salary is unpaid"), None, []))
    assert response.llm_mode == "limited_demo"
    assert response.case_profile.disputed_amount == 12000
    assert response.case_profile.key_facts["hr_contacted"] is False
    assert provider._client.chat.completions.create.await_count == 2


def test_manual_try_again_starts_one_new_turn_without_recursive_retry(provider, sleep):
    provider._client.chat.completions.create.side_effect = status_error(headers={"retry-after": "40"})
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    profile = service.workflow_agent._init_case_profile("Existing case", category_override="EMPLOYMENT")
    first = asyncio.run(service.process_turn(ChatTurnRequest(message="What next?"), profile, []))
    assert provider._client.chat.completions.create.await_count == 1
    second = asyncio.run(service.process_turn(ChatTurnRequest(message="Try Groq again"), first.case_profile, []))
    assert second.llm_mode == "limited_demo"
    assert provider._client.chat.completions.create.await_count == 2
    sleep.assert_not_awaited()
