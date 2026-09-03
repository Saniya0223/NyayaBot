from functools import lru_cache

from app.config import settings
from app.llm.contracts import LLMProvider
from app.llm.gemini_provider import GeminiProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    # Gemini is the only production provider for this MVP. An unsupported
    # configured name is deliberately treated as unconfigured by omitting a key.
    if settings.LLM_PROVIDER != "gemini":
        return GeminiProvider(api_key="", model=settings.LLM_MODEL)
    return GeminiProvider()
