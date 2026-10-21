"""Authenticated, short-lived speech-to-text for chat voice input.

Audio is held in memory only and is never added to a case or evidence record.
The transcript is returned for user review; chat processing starts separately.
"""

import asyncio
import hashlib
import re
import time
from pathlib import Path

import httpx

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from groq import APIConnectionError, APIStatusError, APITimeoutError, AsyncGroq, RateLimitError
from pydantic import BaseModel

from app.auth import get_current_user
from app.config import settings
from app.db.models import ChatCaseSessionModel, UserModel
from app.db.session import get_db
from app.services.language_style import detect_language_script
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/v1/chat", tags=["voice-chat"])
MAX_VOICE_BYTES = 5 * 1024 * 1024
SUPPORTED_AUDIO = {
    ".webm": {"audio/webm", "video/webm"},
    ".ogg": {"audio/ogg", "application/ogg"},
    ".mp4": {"audio/mp4", "video/mp4"},
}
SHUNYA_TOKEN_URL = "https://app.shunyalabs.ai/api/auth/token"
SHUNYA_SPEECH_URL = "https://ttsv2.shunyalabs.ai/v1/audio/speech"
MAX_SPEECH_CHARACTERS = 10_000
MAX_SPEECH_BYTES = 12 * 1024 * 1024
_access_token: str | None = None
_access_token_expires_at = 0.0
_access_token_key = ""
_token_lock = asyncio.Lock()


class VoiceTranscriptionResponse(BaseModel):
    text: str


class VoiceSpeechRequest(BaseModel):
    case_id: str
    message_id: str


def _speech_text(text: str) -> str:
    """Remove presentation markup without changing the legal wording."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s*[#>*-]+\s*", "", text)
    text = re.sub(r"[`*_]", "", text)
    return re.sub(r"\s+", " ", text).strip()


async def _shunya_access_token(client: httpx.AsyncClient, *, refresh: bool = False) -> str:
    global _access_token, _access_token_expires_at, _access_token_key
    key = settings.SHUNYALABS_API_KEY.strip()
    key_fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    async with _token_lock:
        if not refresh and _access_token and _access_token_key == key_fingerprint and time.monotonic() < _access_token_expires_at:
            return _access_token
        response = await client.post(SHUNYA_TOKEN_URL, headers={"api-key": key, "accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("Shunya returned no access token")
        expires_in = int(data.get("expires_in", 900))
        _access_token, _access_token_key = token, key_fingerprint
        _access_token_expires_at = time.monotonic() + max(0, expires_in - 30)
        return token


def _shunya_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=35.0)


async def synthesize_sarita(text: str, language: str) -> bytes:
    """Synthesize one saved bot reply; never expose Shunya credentials to the client."""
    async with _shunya_client() as client:
        token = await _shunya_access_token(client)
        payload = {
            "model": "radiance-indic", "input": text, "voice": "Sarita",
            "language": language, "response_format": "mp3", "speed": 1.0,
        }
        response = await client.post(
            SHUNYA_SPEECH_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code == 401:
            token = await _shunya_access_token(client, refresh=True)
            response = await client.post(
                SHUNYA_SPEECH_URL,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        if not response.content or len(response.content) > MAX_SPEECH_BYTES:
            raise ValueError("Shunya returned an invalid audio size")
        return response.content


@router.post("/speech")
async def speak_saved_reply(
    request: VoiceSpeechRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Response:
    record = db.query(ChatCaseSessionModel).filter(
        ChatCaseSessionModel.case_id == request.case_id,
        ChatCaseSessionModel.user_id == current_user.id,
        ChatCaseSessionModel.is_demo.is_(False),
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Reply not found")
    message = next((item for item in record.messages_data or []
                    if item.get("id") == request.message_id and item.get("sender") == "bot"), None)
    if not message:
        raise HTTPException(status_code=404, detail="Reply not found")
    text = _speech_text(message.get("text") or "")
    if not text or len(text) > MAX_SPEECH_CHARACTERS:
        raise HTTPException(status_code=413, detail="Reply is too long for Sarita voice")
    if not settings.SHUNYALABS_API_KEY.strip():
        raise HTTPException(status_code=503, detail="Sarita voice is not configured on the backend")
    language = "hi" if detect_language_script(text).language in {"hindi", "hinglish"} else "en"
    try:
        audio = await synthesize_sarita(text, language)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Sarita voice is temporarily unavailable") from exc
    return Response(content=audio, media_type="audio/mpeg", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


async def transcribe_audio(content: bytes, extension: str, language: str) -> str:
    """One Groq speech request, without provider-level retries or audio storage."""
    async with AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=30.0) as client:
        request = {
            "file": (f"voice{extension}", content),
            "model": "whisper-large-v3-turbo",
            "response_format": "json",
            "temperature": 0.0,
        }
        if language == "hi":
            request["language"] = "hi"
        result = await client.audio.transcriptions.create(**request)
    return (result.text or "").strip()


@router.post("/transcribe", response_model=VoiceTranscriptionResponse)
async def transcribe_voice(
    response: Response,
    upload: UploadFile = File(...),
    language: str = Form("auto"),
    _current_user: UserModel = Depends(get_current_user),
) -> VoiceTranscriptionResponse:
    response.headers["Cache-Control"] = "no-store"
    if language not in {"auto", "hi"}:
        raise HTTPException(status_code=422, detail="Unsupported speech language")
    extension = Path(upload.filename or "").suffix.lower()
    media_type = (upload.content_type or "").split(";", 1)[0].lower()
    if media_type not in SUPPORTED_AUDIO.get(extension, set()):
        raise HTTPException(status_code=415, detail="Unsupported voice recording format")
    content = await upload.read(MAX_VOICE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="The recording is empty. Please try again.")
    if len(content) > MAX_VOICE_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too large. Keep it under one minute.")
    if not settings.GROQ_API_KEY.strip():
        raise HTTPException(status_code=503, detail="Voice transcription needs the existing Groq key configured on the backend.")
    try:
        text = await transcribe_audio(content, extension, language)
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail="Groq speech transcription is rate-limited. Please try again later or type your message.") from exc
    except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
        raise HTTPException(status_code=503, detail="Speech transcription is unavailable. Please try again or type your message.") from exc
    if not text:
        raise HTTPException(status_code=422, detail="No words were detected. Check your microphone and try again.")
    return VoiceTranscriptionResponse(text=text)
