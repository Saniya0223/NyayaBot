from functools import lru_cache

from app.config import settings
from app.llm.contracts import LLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.groq_provider import GroqProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    if settings.LLM_PROVIDER == "groq":
        return GroqProvider()
    if settings.LLM_PROVIDER == "gemini":
        return GeminiProvider()
    # Unsupported configured provider defaults to unconfigured Groq provider
    return GroqProvider(api_key="", model=settings.LLM_MODEL)
