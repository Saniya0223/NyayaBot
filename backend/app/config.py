import os
from pydantic import BaseModel
from dotenv import load_dotenv


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

class Settings(BaseModel):
    APP_NAME: str = "NyayaBot - AI-Powered Legal Rights and Action Assistant"
    APP_VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nyayasahay.db")
    STORAGE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage"))
    TEMPLATES_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
    DATA_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-3.7-flash").strip()
    LLM_RECENT_MESSAGE_LIMIT: int = int(os.getenv("LLM_RECENT_MESSAGE_LIMIT", "8"))
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

settings = Settings()

os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(os.path.join(settings.STORAGE_DIR, "documents"), exist_ok=True)
os.makedirs(os.path.join(settings.STORAGE_DIR, "evidence"), exist_ok=True)
