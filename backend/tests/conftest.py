import os


# Automated tests must never consume a developer's Gemini quota merely because
# a local backend/.env exists. Provider behavior is covered with explicit fakes.
os.environ["GEMINI_API_KEY"] = ""
