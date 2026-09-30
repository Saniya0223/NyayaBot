"""Launch NyayaBot for QA with the Groq rate-limit retry cap lifted.

Runtime-only: patches the already-constructed settings object and the provider's
retry budget in THIS process. No project file is modified.

Why: free-tier Groq allows 8,000 tokens/minute, but one NyayaBot turn costs
~10-11k (extraction ~5.1k + chat ~5-6k). The provider is told by Groq to wait
10-35s, but settings caps the retry wait at 2s, so every turn degrades to the
canned "temporarily unavailable" reply. Lifting the cap lets the turn wait for
the bucket to refill and actually complete, so QA grades the real bot.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.environ["NYAYA_BACKEND_DIR"])
sys.path.insert(0, os.getcwd())

from app.config import settings
object.__setattr__(settings, "GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS", 300.0)
object.__setattr__(settings, "LLM_TIMEOUT_SECONDS", 120.0)

import app.llm.groq_provider as gp
gp.GROQ_MAX_NETWORK_ATTEMPTS = 4

# Always wait long enough for the per-minute token bucket to actually refill.
_orig = gp.GroqProvider._rate_limit_wait
def _patched(metadata):
    base = _orig(metadata) or 0.0
    reset = metadata.get("token_reset_seconds") or 0.0
    return max(base, reset, 20.0) + 5.0
gp.GroqProvider._rate_limit_wait = staticmethod(_patched)

print(f"[qa_launcher] retry_wait_cap={settings.GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS}s "
      f"attempts={gp.GROQ_MAX_NETWORK_ATTEMPTS}", flush=True)

import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("NYAYA_PORT", "8001")), log_level="info")
