# NyayaBot

NyayaBot is a conversation-first Indian legal-information MVP. Gemini handles natural-language understanding,
structured fact extraction, document-text analysis, and conversational replies. Deterministic application code owns
the case profile, conflict checks, evidence state, legal workflow, source retrieval, document eligibility, and safety
rules.

## Gemini setup

1. Copy `backend/.env.example` to `backend/.env`.
2. Add a server-side `GEMINI_API_KEY`.
3. Keep `LLM_PROVIDER=gemini` and choose the available Gemini model with `LLM_MODEL`.
4. Create/use the backend virtual environment, install dependencies and Chromium, then run the backend:

   ```powershell
   cd backend
   py -3.11 -m venv .venv  # only if backend/.venv does not already exist
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m playwright install chromium
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

5. Run the frontend in a second terminal:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

The Gemini key is read only by the backend and must never use a `NEXT_PUBLIC_` variable. Without a key, the API and
chat header explicitly show **limited demo mode** and replies use the local workflow fallback; the app never labels
those replies as Gemini-generated.

Government Portal Auto-Fill is optional. Its backend uses `browser-use` and Playwright from `backend/.venv`;
Chromium must be installed separately with the command above. Check `GET /api/v1/browser/runtime`
while signed in for component availability. The browser must run on the machine hosting the backend;
for Linux/container deployments install browser dependencies with
`python -m playwright install --with-deps chromium`. Do not run browser installation at every startup.
The agent prepares fields for review; it does not claim a portal submission was completed. CAPTCHA, OTP,
login and final submission require user interaction. Portal-specific pages may change and need manual review.

For local Auto-Fill development, start the backend with `AUTOFILL_MOCK_PORTAL=1` (PowerShell:
`$env:AUTOFILL_MOCK_PORTAL="1"`). Every Auto-Fill session then opens the local test page at
`/api/v1/browser/mock-portal` instead of a government portal. Playwright fills it from the signed-in profile and the
current case without calling an LLM, and nothing is submitted. Leave the variable unset otherwise; the page returns 404.

Evidence uploads keep the original file and are processed in the background. PDFs are read page by page with PyMuPDF;
only pages without a usable text layer, and uploaded images, are sent to OCR.space. Set `OCR_SPACE_API_KEY` in
`backend/.env` to enable OCR (it is read only by the backend). Without it, digital PDFs, DOCX and text files are still
extracted and scanned pages report that OCR is unavailable. Extracted findings are analyzed with the configured LLM and
stay unconfirmed until the user confirms them in chat.

Check runtime status at `GET http://localhost:8000/api/v1/llm/status`. Successful provider calls log only the provider,
model, operation name, and result—not prompts, extracted facts, messages, or the API key.

## Verification

```powershell
cd backend
py -3.11 -m pytest -q

cd ..\frontend
npm run lint
npm run build
```

## Troubleshooting the provider

If replies begin with "Gemini is temporarily unavailable", the backend fell back to limited demo mode for that turn.
The case profile, workflow stage, and history are still preserved. Check the server log line for the cause:

- `status_code=429` - the free tier allows only 20 requests per day *per model*. Either wait for the daily reset or
  point `LLM_MODEL` at a different available model, which has its own daily allowance.
- `status_code=404` - the configured `LLM_MODEL` is not available to this key.
- `status_code=400` - a request field the model rejects. Gemini 3.x models refuse `thinking_budget=0`, so the provider
  sends `thinking_level="low"` and automatically retries once with the thinking control removed.

Gemini 3.x cannot disable thinking, and thinking tokens are billed against `max_output_tokens`. The chat budget
(`CHAT_MAX_OUTPUT_TOKENS`) must therefore cover reasoning *and* the visible reply; a reply that still hits the cap
is rejected rather than shown as a fragment.

Each chat turn costs two requests (one structured extraction, one reply), so a single conversation consumes the free
daily allowance quickly while testing.

For a real-provider acceptance check, configure the key, confirm `/api/v1/llm/status` returns `mode: "gemini"`, then
send at least two related chat turns and verify the backend records successful extraction and chat API calls for both
turns.
