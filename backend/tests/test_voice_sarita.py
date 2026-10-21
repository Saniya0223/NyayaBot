"""Sarita text-to-speech checks. Shunya HTTP calls are always mocked."""

import asyncio

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.db.models import CaseModel, ChatCaseSessionModel, UserModel
from app.db.session import Base, get_db
from app import voice_routes


def _app_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(UserModel(id="user-1", full_name="Tester", email="tester@example.com"))
        db.add(UserModel(id="user-2", full_name="Other", email="other@example.com"))
        db.add(CaseModel(id="case-1", user_id="user-1", title="Test", category="GENERAL"))
        db.add(ChatCaseSessionModel(
            case_id="case-1", user_id="user-1", is_demo=False, profile_data={},
            messages_data=[
                {"id": "user-message", "sender": "user", "text": "Private user text"},
                {"id": "bot-message", "sender": "bot", "text": "**नमस्ते**. आपकी जमा राशि वापस नहीं मिली।"},
            ],
        ))
        db.commit()

    def database():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: UserModel(id="user-1")
    monkeypatch.setattr(voice_routes.settings, "SHUNYALABS_API_KEY", "test-only-key")
    return TestClient(app), app


def test_speech_uses_only_owned_saved_bot_reply(monkeypatch):
    client, app = _app_client(monkeypatch)
    calls = []

    async def fake_synthesize(text, language):
        calls.append((text, language))
        return b"mock-mp3"

    monkeypatch.setattr(voice_routes, "synthesize_sarita", fake_synthesize)
    response = client.post("/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "bot-message"})
    assert response.status_code == 200
    assert response.content == b"mock-mp3"
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("नमस्ते. आपकी जमा राशि वापस नहीं मिली।", "hi")]

    assert client.post("/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "user-message"}).status_code == 404
    assert client.post("/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "not-saved"}).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: UserModel(id="user-2")
    assert client.post("/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "bot-message"}).status_code == 404
    assert len(calls) == 1


def test_speech_missing_key_never_contacts_shunya(monkeypatch):
    client, _ = _app_client(monkeypatch)
    monkeypatch.setattr(voice_routes.settings, "SHUNYALABS_API_KEY", "")
    response = client.post("/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "bot-message"})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_speech_requires_login():
    app = FastAPI()
    app.include_router(voice_routes.router)
    response = TestClient(app).post(
        "/api/v1/chat/speech", json={"case_id": "case-1", "message_id": "bot-message"},
    )
    assert response.status_code == 401


def test_shunya_token_is_cached_and_sarita_request_is_scoped(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url == voice_routes.SHUNYA_TOKEN_URL:
            assert request.headers["api-key"] == "test-only-key"
            return httpx.Response(200, json={"token": "short-lived-token", "expires_in": 900})
        assert request.url == voice_routes.SHUNYA_SPEECH_URL
        assert request.headers["authorization"] == "Bearer short-lived-token"
        assert request.read().decode("utf-8").find('"voice":"Sarita"') >= 0
        return httpx.Response(200, content=b"mock-audio", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(voice_routes.settings, "SHUNYALABS_API_KEY", "test-only-key")
    monkeypatch.setattr(voice_routes, "_access_token", None)
    monkeypatch.setattr(voice_routes, "_access_token_expires_at", 0.0)
    monkeypatch.setattr(voice_routes, "_shunya_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert asyncio.run(voice_routes.synthesize_sarita("Hello", "en")) == b"mock-audio"
    assert asyncio.run(voice_routes.synthesize_sarita("Again", "en")) == b"mock-audio"
    assert [str(request.url) for request in requests].count(voice_routes.SHUNYA_TOKEN_URL) == 1
    assert [str(request.url) for request in requests].count(voice_routes.SHUNYA_SPEECH_URL) == 2


def test_expired_token_is_refreshed_only_once(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if str(request.url) == voice_routes.SHUNYA_TOKEN_URL:
            token = f"token-{sum(str(item.url) == voice_routes.SHUNYA_TOKEN_URL for item in requests)}"
            return httpx.Response(200, json={"token": token, "expires_in": 900})
        if request.headers["authorization"] == "Bearer token-1":
            return httpx.Response(401, json={"detail": "expired"})
        assert request.headers["authorization"] == "Bearer token-2"
        return httpx.Response(200, content=b"audio")

    monkeypatch.setattr(voice_routes.settings, "SHUNYALABS_API_KEY", "test-only-key")
    monkeypatch.setattr(voice_routes, "_access_token", None)
    monkeypatch.setattr(voice_routes, "_shunya_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert asyncio.run(voice_routes.synthesize_sarita("Hello", "en")) == b"audio"
    assert sum(str(request.url) == voice_routes.SHUNYA_TOKEN_URL for request in requests) == 2
    assert sum(str(request.url) == voice_routes.SHUNYA_SPEECH_URL for request in requests) == 2
