import os
import tempfile
from pathlib import Path


# Automated tests must never consume a developer's Gemini quota merely because
# a local backend/.env exists. Provider behavior is covered with explicit fakes.
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["AUTH_COOKIE_SECURE"] = "false"
_TEST_DATABASE_DIR = Path(tempfile.mkdtemp(prefix="nyayabot-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DATABASE_DIR / 'test.db').as_posix()}"
