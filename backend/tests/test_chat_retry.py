"""Durable, stage-specific chat retries. All providers and external HTTP are fake."""

import asyncio
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.db.models import ChatCaseSessionModel, UserMemoryModel
from app.db.session import SessionLocal
from app.llm.contracts import CaseExtraction, LLMRateLimitedError, ProviderStatus
from app.services.chat_retry import RETRY_STATE_KEY


ORIGINAL = "My landlord has not returned my deposit."


class StageProvider:
    status = ProviderStatus(
        provider="groq", model="openai/gpt-oss-120b", configured=True,
        mode="groq", message="Mock provider only",
    )

    def __init__(self):
        self.extraction_failures = 0
        self.chat_failures = 0
        self.extractions = []
        self.chats = []
        self.started = None
        self.release = None
        self.reply = "I have recorded the facts you reported."

    async def extract_case_updates(self, context):
        self.extractions.append(context.model_dump(mode="json"))
        if self.extraction_failures:
            self.extraction_failures -= 1
            raise LLMRateLimitedError("Mock extraction 429")
        return CaseExtraction.model_validate({
            "user_intent": "Recover rental deposit",
            "classification": {
                "category": "HOUSING_TENANT", "issue_type": "SECURITY_DEPOSIT_DISPUTE",
                "confidence": 0.95,
            },
            "facts": {"disputed_amount": 50000},
            "actions_detected": [{"type": "formal_demand_sent", "confidence": 0.95}],
            "evidence_detected": ["rental_agreement"],
        })

    async def chat(self, context):
        self.chats.append(context.model_dump(mode="json"))
        if self.chat_failures:
            self.chat_failures -= 1
            raise LLMRateLimitedError("Mock final chat 429")
        if self.started is not None:
            self.started.set()
            assert await asyncio.to_thread(self.release.wait, 10), "Mock retry was not released"
        return self.reply


@pytest.fixture
def rig(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in retry tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    provider = StageProvider()
    service = main.gemini_conversation_service
    monkeypatch.setattr(service, "provider", provider)
    rag_queries = []

    def sources(profile, message, workflow):
        rag_queries.append(message)
        return []

    monkeypatch.setattr(service, "_verified_sources", sources)
    applications = []
    apply_extraction = service._apply_extraction

    def counted(profile, extraction):
        applications.append(extraction.model_dump(mode="json"))
        return apply_extraction(profile, extraction)

    monkeypatch.setattr(service, "_apply_extraction", counted)
    client = TestClient(main.app)
    signup = client.post("/api/v1/auth/signup", json={
        "full_name": "Retry Citizen", "email": f"retry-{uuid.uuid4().hex}@example.com",
        "password": "CorrectHorse123!",
    })
    assert signup.status_code == 201
    return client, provider, rag_queries, applications, signup.json()["id"]


def send(client, message=ORIGINAL, case_id=None):
    result = client.post("/api/v1/chat/message", json={"message": message, "case_id": case_id})
    assert result.status_code == 200, result.text
    return result.json()


def retry_body(response):
    return {"case_id": response["case_profile"]["case_id"], "message_id": response["message_id"]}


def retry(client, response):
    result = client.post("/api/v1/chat/retry", json=retry_body(response))
    assert result.status_code == 200, result.text
    return result.json()


def saved(case_id):
    with SessionLocal() as db:
        record = db.query(ChatCaseSessionModel).filter_by(case_id=case_id).one()
        return deepcopy(record.profile_data), deepcopy(record.messages_data)


def consequential(profile):
    return {key: profile[key] for key in (
        "disputed_amount", "key_facts", "fact_metadata", "timeline", "actions_completed",
        "evidence_checklist", "current_stage_key", "document_request", "legal_journey",
        "deadlines", "safety_status", "readiness", "recommended_next_action",
    )}


def assert_control_is_not_content(client, case_id, provider, rag_queries):
    profile, messages = saved(case_id)
    assert [item["text"] for item in messages if item["sender"] == "user"] == [ORIGINAL]
    assert "Try Groq again" not in json.dumps(profile)
    assert "Try Groq again" not in json.dumps(provider.extractions)
    assert "Try Groq again" not in json.dumps(provider.chats)
    assert "Try Groq again" not in json.dumps(rag_queries)
    public = client.get(f"/api/v1/chat/cases/{case_id}")
    assert public.status_code == 200
    assert RETRY_STATE_KEY not in public.text
    assert "original_message" not in public.text


def test_extraction_retry_reuses_original_message_context_and_ids(rig):
    client, provider, rag_queries, applications, user_id = rig
    provider.extraction_failures = 1
    failed = send(client)
    case_id = failed["case_profile"]["case_id"]
    before, messages = saved(case_id)
    user_id_before = messages[0]["id"]
    assert failed["retry_action"]["message_id"] == failed["message_id"]
    assert messages[1][RETRY_STATE_KEY]["stage"] == "EXTRACTION"
    assert messages[1][RETRY_STATE_KEY]["user_message_id"] == user_id_before
    assert applications == [] and provider.chats == []
    assert before["disputed_amount"] == 0  # Existing empty-profile default, not a guessed amount.

    # Retry survives a browser/client reload; it is not an in-memory UI cache.
    reloaded = TestClient(main.app)
    reloaded.cookies.update(client.cookies)
    result = retry(reloaded, failed)
    assert result["llm_mode"] == "groq"
    assert provider.extractions[0] == provider.extractions[1]
    assert [item["user_message"] for item in provider.extractions] == [ORIGINAL, ORIGINAL]
    assert len(applications) == 1
    _, messages = saved(case_id)
    assert len(messages) == 2 and messages[0]["id"] == user_id_before
    assert messages[1]["id"] == failed["message_id"] == result["message_id"]
    assert messages[1][RETRY_STATE_KEY]["status"] == "DONE"
    assert_control_is_not_content(reloaded, case_id, provider, rag_queries)
    with SessionLocal() as db:
        assert db.query(UserMemoryModel).filter_by(user_id=user_id).count() == 0


def test_final_chat_retry_does_not_reapply_state_or_rag_and_is_idempotent(rig, monkeypatch):
    client, provider, rag_queries, applications, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    case_id = failed["case_profile"]["case_id"]
    before, messages = saved(case_id)
    assert before["disputed_amount"] == 50000
    assert len(applications) == 1
    assert messages[1][RETRY_STATE_KEY]["stage"] == "FINAL_CHAT"

    def forbidden(*args, **kwargs):
        raise AssertionError("Final-chat retry must not repeat deterministic state application")

    service = main.gemini_conversation_service
    monkeypatch.setattr(service, "_apply_extraction", forbidden)
    monkeypatch.setattr(service, "_apply_actions", forbidden)
    monkeypatch.setattr(service, "_refresh_workflow", forbidden)
    monkeypatch.setattr(service.workflow_agent, "_mark_evidence", forbidden)
    result = retry(client, failed)
    after, messages = saved(case_id)
    assert result["retry_action"] is None
    assert len(provider.extractions) == 1 and len(provider.chats) == 2
    assert provider.chats[0] == provider.chats[1]
    assert rag_queries == [ORIGINAL]
    assert consequential(after) == consequential(before)
    assert after["pending_interaction"] == before["pending_interaction"]
    assert len(messages) == 2
    assert_control_is_not_content(client, case_id, provider, rag_queries)

    # Replaying the same completed request is cached, not a second operation.
    replay = retry(client, failed)
    assert replay["message_id"] == result["message_id"]
    assert len(provider.extractions) == 1 and len(provider.chats) == 2
    assert saved(case_id) == (after, messages)


def test_extraction_retry_that_reaches_chat_failure_advances_saved_stage(rig):
    client, provider, _, applications, _ = rig
    provider.extraction_failures = 1
    provider.chat_failures = 1
    failed = send(client)
    next_failure = retry(client, failed)
    assert next_failure["message_id"] == failed["message_id"]
    profile, messages = saved(failed["case_profile"]["case_id"])
    assert profile["disputed_amount"] == 50000
    assert messages[1][RETRY_STATE_KEY]["stage"] == "FINAL_CHAT"
    retry(client, next_failure)
    assert len(provider.extractions) == 2 and len(provider.chats) == 2
    assert len(applications) == 1


@pytest.mark.parametrize("stage", ["extraction", "chat"])
def test_repeated_rate_limits_remain_one_stage_attempt_per_click(rig, stage):
    client, provider, _, applications, _ = rig
    setattr(provider, f"{stage}_failures", 3)
    failed = send(client)
    result = retry(client, failed)
    assert result["llm_mode"] == "limited_demo"
    assert result["message_id"] == failed["message_id"]
    assert len(provider.extractions if stage == "extraction" else provider.chats) == 2
    assert len(applications) == (0 if stage == "extraction" else 1)
    _, messages = saved(failed["case_profile"]["case_id"])
    assert len(messages) == 2
    assert messages[1][RETRY_STATE_KEY]["status"] == "READY"


def test_retry_does_not_write_explicit_memory_again(rig):
    client, provider, _, _, user_id = rig
    provider.chat_failures = 1
    failed = send(client, "Remember that my landlord has not returned my deposit.")
    with SessionLocal() as db:
        memories = [(item.id, item.text) for item in db.query(UserMemoryModel).filter_by(user_id=user_id).all()]
    assert len(memories) == 1
    result = retry(client, failed)
    assert result["reply_text"] == failed["reply_text"]  # Preserve the memory acknowledgement.
    with SessionLocal() as db:
        assert [(item.id, item.text) for item in db.query(UserMemoryModel).filter_by(user_id=user_id).all()] == memories


def test_retry_rejects_stale_case_and_chat_text_payload(rig):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    body = retry_body(failed)
    assert client.post("/api/v1/chat/retry", json={**body, "message": "Try Groq again"}).status_code == 422
    with SessionLocal() as db:
        record = db.query(ChatCaseSessionModel).filter_by(case_id=body["case_id"]).one()
        record.profile_data = {**record.profile_data, "user_city": "Jaipur"}
        db.commit()
    assert client.post("/api/v1/chat/retry", json=body).status_code == 409
    assert len(provider.extractions) == 1 and len(provider.chats) == 1


def test_retry_requires_owner_and_saved_stage(rig):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    body = retry_body(failed)
    assert TestClient(main.app).post("/api/v1/chat/retry", json=body).status_code == 401
    other = TestClient(main.app)
    signup = other.post("/api/v1/auth/signup", json={
        "full_name": "Other Citizen", "email": f"other-{uuid.uuid4().hex}@example.com",
        "password": "CorrectHorse123!",
    })
    assert signup.status_code == 201
    assert other.post("/api/v1/chat/retry", json=body).status_code == 404
    assert client.post("/api/v1/chat/retry", json={**body, "message_id": "missing"}).status_code == 409
    with SessionLocal() as db:
        record = db.query(ChatCaseSessionModel).filter_by(case_id=body["case_id"]).one()
        messages = deepcopy(record.messages_data)
        messages[1].pop(RETRY_STATE_KEY)
        record.messages_data = messages
        db.commit()
    assert client.post("/api/v1/chat/retry", json=body).status_code == 409
    assert len(provider.chats) == 1


def test_concurrent_retry_requests_claim_the_failed_turn_only_once(rig):
    client, provider, _, applications, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    provider.started = threading.Event()
    provider.release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/api/v1/chat/retry", json=retry_body(failed))
        try:
            assert provider.started.wait(5)
            second = client.post("/api/v1/chat/retry", json=retry_body(failed))
            assert second.status_code == 409
            assert "already" in second.json()["detail"]
        finally:
            provider.release.set()
        assert first.result(timeout=10).status_code == 200
    assert len(provider.extractions) == 1 and len(provider.chats) == 2
    assert len(applications) == 1


def test_extraction_retry_preserves_and_resolves_the_original_pending_interaction(rig):
    client, provider, _, applications, _ = rig
    first = send(client)
    case_id = first["case_profile"]["case_id"]
    pending = {
        "type": "EVIDENCE_CONFIRMATION", "target_keys": ["deposit_payment_proof_available"],
        "expected_answer_type": "boolean", "case_id": case_id,
        "allowed_choices": [], "source": "domain_context",
    }
    with SessionLocal() as db:
        record = db.query(ChatCaseSessionModel).filter_by(case_id=case_id).one()
        record.profile_data = {**record.profile_data, "pending_interaction": pending}
        db.commit()
    provider.extraction_failures = 1
    failed = send(client, "yes", case_id)
    before, messages = saved(case_id)
    assert before["pending_interaction"] == pending
    assert "deposit_payment_proof_available" not in before["key_facts"]
    assert len(applications) == 1
    retry(client, failed)
    after, next_messages = saved(case_id)
    assert after["key_facts"]["deposit_payment_proof_available"] is True
    assert after["pending_interaction"] is None
    assert len(applications) == 2
    assert provider.extractions[-1] == provider.extractions[-2]
    assert len(messages) == len(next_messages) == 4
    assert [item["text"] for item in next_messages if item["sender"] == "user"] == [ORIGINAL, "yes"]


def test_final_retry_creates_one_pending_question_and_completed_replay_leaves_it_unchanged(rig):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    provider.reply = "Do you have proof of the deposit payment?"
    response = retry(client, failed)
    pending = response["case_profile"]["pending_interaction"]
    assert pending is not None
    before = saved(failed["case_profile"]["case_id"])
    replay = retry(client, failed)
    assert replay["case_profile"]["pending_interaction"] == pending
    assert saved(failed["case_profile"]["case_id"]) == before
    assert len(provider.chats) == 2


def test_newer_turn_invalidates_old_retry_without_losing_its_saved_checkpoint(rig):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    case_id = failed["case_profile"]["case_id"]
    send(client, "I have a tenancy agreement.", case_id)
    before = saved(case_id)
    assert before[1][1][RETRY_STATE_KEY]["stage"] == "FINAL_CHAT"
    assert client.post("/api/v1/chat/retry", json=retry_body(failed)).status_code == 409
    assert saved(case_id) == before
    assert len(provider.extractions) == 2 and len(provider.chats) == 2


def test_retry_completion_never_overwrites_a_case_changed_during_the_model_call(rig):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    case_id = failed["case_profile"]["case_id"]
    provider.started = threading.Event()
    provider.release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/api/v1/chat/retry", json=retry_body(failed))
        try:
            assert provider.started.wait(5)
            with SessionLocal() as db:
                record = db.query(ChatCaseSessionModel).filter_by(case_id=case_id).one()
                record.profile_data = {**record.profile_data, "user_city": "Jaipur"}
                db.commit()
        finally:
            provider.release.set()
        assert first.result(timeout=10).status_code == 409
    profile, messages = saved(case_id)
    assert profile["user_city"] == "Jaipur"
    assert messages[1]["text"] == failed["reply_text"]


def test_unexpected_retry_error_releases_claim_without_applying_state(rig, monkeypatch):
    client, provider, _, _, _ = rig
    provider.chat_failures = 1
    failed = send(client)
    case_id = failed["case_profile"]["case_id"]
    before, messages = saved(case_id)

    async def broken(context):
        raise RuntimeError("Mock unexpected failure")

    monkeypatch.setattr(provider, "chat", broken)
    catching_client = TestClient(main.app, raise_server_exceptions=False)
    catching_client.cookies.update(client.cookies)
    assert catching_client.post("/api/v1/chat/retry", json=retry_body(failed)).status_code == 500
    after, next_messages = saved(case_id)
    assert after == before and next_messages == messages
