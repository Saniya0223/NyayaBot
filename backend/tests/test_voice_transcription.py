"""Voice upload checks; all Groq calls are mocked."""

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app import voice_routes


def client(monkeypatch):
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test-user")
    monkeypatch.setattr(voice_routes.settings, "GROQ_API_KEY", "test-only-key")
    return TestClient(app)


def test_voice_upload_returns_text_without_creating_chat_turn(monkeypatch):
    received = []

    async def fake_transcribe(content, extension, language):
        received.append((content, extension, language))
        return "My landlord has not returned my deposit."

    monkeypatch.setattr(voice_routes, "transcribe_audio", fake_transcribe)
    response = client(monkeypatch).post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.webm", b"fake-audio", "audio/webm")},
        data={"language": "auto"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "My landlord has not returned my deposit."}
    assert response.headers["cache-control"] == "no-store"
    assert received == [(b"fake-audio", ".webm", "auto")]


def test_voice_upload_rejects_unsupported_and_oversized_audio(monkeypatch):
    app_client = client(monkeypatch)
    unsupported = app_client.post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.txt", b"not audio", "text/plain")},
    )
    assert unsupported.status_code == 415
    oversized = app_client.post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.webm", b"x" * (voice_routes.MAX_VOICE_BYTES + 1), "audio/webm")},
    )
    assert oversized.status_code == 413


def test_voice_upload_needs_login():
    app = FastAPI()
    app.include_router(voice_routes.router)
    response = TestClient(app).post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.webm", b"fake-audio", "audio/webm")},
    )
    assert response.status_code == 401


def test_voice_upload_reports_missing_key_without_contacting_groq(monkeypatch):
    app_client = client(monkeypatch)
    monkeypatch.setattr(voice_routes.settings, "GROQ_API_KEY", "")
    response = app_client.post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.webm", b"fake-audio", "audio/webm")},
    )
    assert response.status_code == 503
    assert "existing Groq key" in response.json()["detail"]


def test_empty_transcript_is_not_presented_as_user_message(monkeypatch):
    async def fake_transcribe(*_args):
        return ""

    monkeypatch.setattr(voice_routes, "transcribe_audio", fake_transcribe)
    response = client(monkeypatch).post(
        "/api/v1/chat/transcribe",
        files={"upload": ("voice.webm", b"fake-audio", "audio/webm")},
    )
    assert response.status_code == 422
    assert "No words were detected" in response.json()["detail"]


def test_transcription_uses_one_groq_request_and_omits_forced_language(monkeypatch):
    calls = []

    class FakeTranscriptions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="  My salary was not paid.  ")

    class FakeGroq:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(voice_routes, "AsyncGroq", FakeGroq)
    monkeypatch.setattr(voice_routes.settings, "GROQ_API_KEY", "test-only-key")
    text = asyncio.run(voice_routes.transcribe_audio(b"fake-audio", ".webm", "auto"))
    assert text == "My salary was not paid."
    assert len(calls) == 1
    assert calls[0]["file"] == ("voice.webm", b"fake-audio")
    assert calls[0]["model"] == "whisper-large-v3-turbo"
    assert "language" not in calls[0]
