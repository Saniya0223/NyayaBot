import os
from pydantic import BaseModel, Field
from dotenv import load_dotenv


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

class Settings(BaseModel):
    APP_NAME: str = "NyayaBot - AI-Powered Legal Rights and Action Assistant"
    APP_VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(BACKEND_DIR, 'nyayasahay.db').replace(os.sep, '/')}",
    )
    STORAGE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage"))
    TEMPLATES_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
    DATA_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    SHUNYALABS_API_KEY: str = os.getenv("SHUNYALABS_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b").strip()
    LLM_RECENT_MESSAGE_LIMIT: int = int(os.getenv("LLM_RECENT_MESSAGE_LIMIT", "8"))
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    # Longest provider-indicated wait NyayaBot honours before its single retry.
    # One turn is two Groq calls (extraction, then chat) totalling ~10-13k tokens,
    # above the free tier's 8,000 tokens/minute, so the second call is routinely
    # told to wait 5-50 s. A 1-2 s cap turned every such turn into a fallback.
    # 60 s covers the per-minute window; hourly/daily caps (retry-after of
    # minutes to hours) still exceed it and fall back immediately.
    GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS: float = Field(
        default=float(os.getenv("GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS", "60")),
        ge=0, le=120, validate_default=True,
    )
    # OCR.space is used only for scanned PDF pages and images; the key never leaves the backend.
    OCR_SPACE_API_KEY: str = os.getenv("OCR_SPACE_API_KEY", "")
    OCR_SPACE_ENDPOINT: str = os.getenv("OCR_SPACE_ENDPOINT", "https://api.ocr.space/parse/image")
    OCR_SPACE_TIMEOUT_SECONDS: float = float(os.getenv("OCR_SPACE_TIMEOUT_SECONDS", "45"))
    # Browser-extension Auto-Fill. A session code links one case to the extension.
    AUTOFILL_SESSION_TTL_MINUTES: int = int(os.getenv("AUTOFILL_SESSION_TTL_MINUTES", "30"))
    # Development only: serve a local test form shaped like the cyber-crime portal.
    AUTOFILL_TEST_FORM: bool = _env_bool("AUTOFILL_TEST_FORM", False)
    AUTH_SESSION_COOKIE_NAME: str = os.getenv("AUTH_SESSION_COOKIE_NAME", "nyayabot_session")
    AUTH_SESSION_TTL_HOURS: int = int(os.getenv("AUTH_SESSION_TTL_HOURS", "168"))
    AUTH_COOKIE_SECURE: bool = _env_bool("AUTH_COOKIE_SECURE", False)
    AUTH_COOKIE_SAMESITE: str = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    AUTH_COOKIE_DOMAIN: str | None = os.getenv("AUTH_COOKIE_DOMAIN") or None
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    ]

settings = Settings()

if "*" in settings.CORS_ORIGINS:
    raise ValueError("CORS_ORIGINS cannot contain '*' when credentialed auth is enabled")

os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(os.path.join(settings.STORAGE_DIR, "documents"), exist_ok=True)
os.makedirs(os.path.join(settings.STORAGE_DIR, "evidence"), exist_ok=True)
