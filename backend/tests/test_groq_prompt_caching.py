"""Groq request-shape and usage tests; all completions are local fakes."""

import asyncio
import json
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from groq.types.completion_usage import CompletionUsage

from app.domains import domain_registry
from app.llm.contracts import CaseExtraction, LLMExtractionContext, LLMResponseContext
from app.llm.groq_provider import (
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    GroqProvider,
)
from app.schemas.chat import PendingInteraction
from app.services.document_registry import DOCUMENT_DEFINITIONS


EXTRACTION_RESULT = {
    "user_intent": "report unpaid salary",
    "language_style": "english",
    "classification": {
        "category": "EMPLOYMENT", "issue_type": "Unpaid Salary", "confidence": 0.9,
    },
    "facts": {"hr_contacted": False, "disputed_amount": 12000},
    "unavailable_facts": ["opposite_party_address"],
    "document_request": None,
}


def completion(content, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage,
    )


@pytest.fixture
def provider(monkeypatch):
    def block_http(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in prompt-caching tests")

    monkeypatch.setattr(httpx.Client, "send", block_http)
    monkeypatch.setattr(httpx.AsyncClient, "send", block_http)
    create = AsyncMock(return_value=completion(json.dumps(EXTRACTION_RESULT)))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("app.llm.groq_provider.AsyncGroq", lambda **kwargs: client)
    return GroqProvider(api_key="unit-test-placeholder", model="openai/gpt-oss-120b")


def extraction_context(marker):
    return LLMExtractionContext(
        user_message=f"synthetic-newest-{marker}",
        recent_messages=[{"sender": "user", "text": f"synthetic-history-{marker}"}],
        case_summary={
            "case_id": f"synthetic-case-{marker}",
            "facts": {"user_name": f"synthetic-name-{marker}"},
            "updated_at": f"synthetic-time-{marker}",
            "document_request": {"status": f"synthetic-document-state-{marker}"},
        },
        pending_interaction=PendingInteraction(
            type="FACT_CONFIRMATION", target_keys=["hr_contacted"],
            expected_answer_type="boolean", case_id=f"synthetic-case-{marker}",
            source=f"synthetic-pending-{marker}",
        ),
        language_style="english" if marker == "a" else "hinglish",
        script_style="roman" if marker == "a" else "devanagari",
        domain_catalog=domain_registry.extraction_catalog(),
        document_catalog=[{"id": item.id, "name": item.name} for item in DOCUMENT_DEFINITIONS.values()],
    )


def input_data(request):
    return json.loads(request["messages"][1]["content"].split("INPUT_DATA:\n", 1)[1])


def extraction_prefix(request):
    static_data, dynamic_data = request["messages"][1]["content"].split('"existing_case_summary":', 1)
    return (request["messages"][0]["content"], static_data), dynamic_data


def test_extraction_prefix_is_identical_across_users_and_cases(provider):
    first, second = extraction_context("a"), extraction_context("b")
    second.domain_catalog.reverse()
    second.document_catalog = [dict(reversed(list(item.items()))) for item in reversed(second.document_catalog)]
    original_first, original_second = first.model_dump(mode="json"), second.model_dump(mode="json")

    first_result = asyncio.run(provider.extract_case_updates(first))
    second_result = asyncio.run(provider.extract_case_updates(second))
    requests = [call.kwargs for call in provider._client.chat.completions.create.await_args_list]
    first_prefix, first_dynamic = extraction_prefix(requests[0])
    second_prefix, second_dynamic = extraction_prefix(requests[1])

    assert first_prefix == second_prefix
    assert first_dynamic != second_dynamic
    assert first_prefix[0].startswith(EXTRACTION_SYSTEM_PROMPT + "\n\n")
    for marker in ("case", "name", "time", "history", "pending", "document-state", "newest"):
        assert f"synthetic-{marker}-" not in repr(first_prefix)
        assert f"synthetic-{marker}-a" in first_dynamic
        assert f"synthetic-{marker}-b" in second_dynamic
    for request, context in zip(requests, (first, second)):
        payload = input_data(request)
        assert list(payload) == [
            "domain_catalog", "document_catalog", "existing_case_summary", "pending_interaction",
            "language_style", "script_style", "recent_messages", "newest_user_message",
        ]
        assert payload["existing_case_summary"] == context.case_summary
        assert payload["pending_interaction"] == context.pending_interaction.model_dump(mode="json")
        assert payload["recent_messages"] == context.recent_messages
        assert payload["newest_user_message"] == context.user_message
        assert payload["language_style"] == context.language_style
        assert payload["script_style"] == context.script_style
        assert request["response_format"] == {"type": "json_object"}
        assert request["temperature"] == 0
        assert request["max_tokens"] == CHAT_MAX_OUTPUT_TOKENS == 2048
        assert request["model"] == "openai/gpt-oss-120b"
    assert first_result == second_result == CaseExtraction.model_validate(EXTRACTION_RESULT)
    assert first.model_dump(mode="json") == original_first
    assert second.model_dump(mode="json") == original_second


def test_static_catalog_serialization_is_deterministic_and_lossless():
    original = [
        {"domain_id": "TENANCY", "issue_type_ids": ["DEPOSIT", "EVICTION"], "metadata": {"z": 1, "a": 2}},
        {"issue_type_ids": ["SALARY"], "domain_id": "EMPLOYMENT"},
    ]
    snapshot = deepcopy(original)
    reordered = [dict(reversed(list(item.items()))) for item in reversed(original)]
    reordered[1]["metadata"] = {"a": 2, "z": 1}
    first = GroqProvider._stable_catalog(original)
    second = GroqProvider._stable_catalog(reordered)

    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert original == snapshot
    assert sorted(json.dumps(item, sort_keys=True) for item in first) == sorted(
        json.dumps(item, sort_keys=True) for item in original
    )
    assert next(item for item in first if item["domain_id"] == "TENANCY")["issue_type_ids"] == ["DEPOSIT", "EVICTION"]


def test_full_extraction_schema_is_canonical_without_contract_changes(provider, monkeypatch):
    original_schema = CaseExtraction.model_json_schema()
    asyncio.run(provider.extract_case_updates(extraction_context("a")))
    first_system = provider._client.chat.completions.create.await_args.kwargs["messages"][0]["content"]

    def reverse_dicts(value):
        if isinstance(value, dict):
            return {key: reverse_dicts(item) for key, item in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse_dicts(item) for item in value]
        return value

    monkeypatch.setattr(CaseExtraction, "model_json_schema", classmethod(lambda cls: reverse_dicts(original_schema)))
    asyncio.run(provider.extract_case_updates(extraction_context("b")))
    second_system = provider._client.chat.completions.create.await_args.kwargs["messages"][0]["content"]

    assert first_system == second_system
    supplied_schema = json.loads(first_system.split("You MUST output valid JSON matching this schema:\n", 1)[1])
    assert supplied_schema == original_schema
    assert set(supplied_schema["$defs"]["ExtractedCaseFacts"]["properties"]) == set(
        CaseExtraction.model_fields["facts"].annotation.model_fields
    )


@pytest.mark.parametrize("response_mode", ["CHAT", "CASE_SUMMARY"])
def test_final_chat_preserves_static_instructions_and_all_dynamic_context(provider, response_mode):
    provider._client.chat.completions.create.return_value = completion("Local fake reply")
    contexts = []
    for marker in ("a", "b"):
        contexts.append(LLMResponseContext(
            user_message=f"synthetic-newest-{marker}", response_mode=response_mode,
            recent_messages=[{"sender": "user", "text": f"synthetic-history-{marker}"}],
            case_summary={"case_id": f"synthetic-case-{marker}", "facts": {"known": False}},
            workflow={"stage": f"synthetic-workflow-{marker}", "document_state": "CONFIRMING"},
            missing_information=[f"synthetic-missing-{marker}"],
            legal_sources=[{"text": f"synthetic-rag-{marker}", "section": "supplied-section"}],
            language_style="english" if marker == "a" else "hindi",
            script_style="roman" if marker == "a" else "devanagari",
            conflict={"value": f"synthetic-conflict-{marker}"},
            safety={"context": f"synthetic-safety-{marker}"},
            domain_context={"next_fact_candidates": [{"key": "first"}, {"key": "second"}]},
            professional_help={"level": "CONSIDER_LEGAL_HELP", "relevant_known_signals": [f"synthetic-help-{marker}"]},
            professional_help_should_surface=True, professional_help_question=True,
            user_context={
                "profile_context": {"user_id": f"synthetic-user-{marker}"},
                "saved_memory_context": {"text": f"synthetic-memory-{marker}"},
                "previous_case_context": {"cases": [f"synthetic-prior-{marker}"]},
            },
            pending_resolution={"source": f"synthetic-pending-{marker}"},
            evidence={"text": f"synthetic-evidence-{marker}"},
        ))
        assert asyncio.run(provider.chat(contexts[-1])) == "Local fake reply"

    requests = [call.kwargs for call in provider._client.chat.completions.create.await_args_list]
    for request, context in zip(requests, contexts):
        assert request["messages"][0] == {"role": "system", "content": CHAT_SYSTEM_PROMPT}
        assert "synthetic-" not in request["messages"][0]["content"]
        payload = input_data(request)
        # Cycle 4 / D1: providers now serialise through model_payload(), which strips the
        # internal profile field name from next_fact_candidates[*]["key"]. The model echoed
        # that key verbatim in W1-02 t2 ("... ka naam (opposite_party_name)"). Every other
        # dynamic field must still reach the provider untouched, and the in-memory context
        # must keep "key" so pending-interaction binding still works.
        dumped = context.model_dump(mode="json")
        assert payload == context.model_payload()
        assert {k: v for k, v in payload.items() if k != "domain_context"} == {
            k: v for k, v in dumped.items() if k != "domain_context"
        }
        candidates = payload["domain_context"]["next_fact_candidates"]
        assert len(candidates) == 2 and all("key" not in entry for entry in candidates)
        assert [entry["key"] for entry in context.domain_context["next_fact_candidates"]] == [
            "first", "second",
        ]
        assert list(payload)[-1] == "user_message"
        assert request["temperature"] == 0.35
        assert request["max_tokens"] == 2048
        assert "response_format" not in request
        assert request["model"] == "openai/gpt-oss-120b"
    assert requests[0]["messages"][1]["content"].split("INPUT_DATA:\n")[0] == requests[1]["messages"][1]["content"].split("INPUT_DATA:\n")[0]


def test_usage_reads_real_sdk_models_and_exact_cache_metrics():
    usage = CompletionUsage.model_validate({
        "prompt_tokens": 5000, "prompt_tokens_details": {"cached_tokens": 3000},
        "completion_tokens": 400, "total_tokens": 5400,
    })
    assert GroqProvider._usage_metrics(usage) == {
        "prompt_tokens": 5000, "cached_tokens": 3000, "fresh_prompt_tokens": 2000,
        "completion_tokens": 400, "total_tokens": 5400, "cache_hit_percent": 60.0,
    }


@pytest.mark.parametrize("details", [None, {}, {"cached_tokens": None}, {"cached_tokens": "private-invalid-value"}])
def test_unavailable_cached_tokens_are_unknown_not_invented(details):
    metrics = GroqProvider._usage_metrics({
        "prompt_tokens": 5000, "completion_tokens": 400, "total_tokens": 5400,
        "prompt_tokens_details": details,
    })
    assert metrics["prompt_tokens"] == 5000
    assert metrics["cached_tokens"] is None
    assert metrics["fresh_prompt_tokens"] is None
    assert metrics["cache_hit_percent"] is None


@pytest.mark.parametrize("prompt_tokens", [0, 5000])
def test_cold_cache_has_valid_zero_hit_metrics(prompt_tokens):
    metrics = GroqProvider._usage_metrics({
        "prompt_tokens": prompt_tokens, "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens": 400,
    })
    assert metrics["cached_tokens"] == 0
    assert metrics["fresh_prompt_tokens"] == prompt_tokens
    assert metrics["cache_hit_percent"] == 0.0


def test_fresh_prompt_tokens_are_nonnegative():
    assert GroqProvider._usage_metrics({
        "prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 20},
    })["fresh_prompt_tokens"] == 0


@pytest.mark.parametrize("usage", [None, {}, SimpleNamespace(), {"prompt_tokens": "private-invalid-value"}])
def test_missing_usage_fields_never_change_a_successful_response(provider, caplog, usage):
    provider._client.chat.completions.create.return_value = completion("Local fake reply", usage)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        assert asyncio.run(provider.chat(LLMResponseContext(
            user_message="private-user-message", case_summary={}, workflow={},
        ))) == "Local fake reply"
    assert provider._client.chat.completions.create.await_count == 1
    assert "private-invalid-value" not in caplog.text
    assert "private-user-message" not in caplog.text


@pytest.mark.parametrize("operation", ["structured_CaseExtraction", "chat", "structured_DocumentAnalysis"])
def test_success_logs_numeric_usage_without_private_data(provider, caplog, operation):
    usage = SimpleNamespace(
        prompt_tokens=5000, prompt_tokens_details=SimpleNamespace(cached_tokens=3000),
        completion_tokens=400, total_tokens=5400, private_fact="private-evidence-value",
    )
    provider._client.chat.completions.create.return_value = completion("private-model-response", usage)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = asyncio.run(provider._generate(
            messages=[{"role": "system", "content": "private-system-prompt"},
                      {"role": "user", "content": "private-case-and-profile-data"}],
            temperature=0, max_tokens=2048, response_format=None, operation=operation,
        ))
    assert result == "private-model-response"
    usage_logs = [record.getMessage() for record in caplog.records if "event=llm_usage" in record.getMessage()]
    assert usage_logs == [
        f"event=llm_usage provider=groq operation={operation} model=openai/gpt-oss-120b "
        "prompt_tokens=5000 cached_tokens=3000 fresh_prompt_tokens=2000 "
        "completion_tokens=400 total_tokens=5400 cache_hit_percent=60.0"
    ]
    assert "private-" not in caplog.text
    assert "unit-test-placeholder" not in caplog.text
    assert provider._client.chat.completions.create.await_count == 1
