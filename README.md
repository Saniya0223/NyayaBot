# NyayaBot

NyayaBot is a conversation-first Indian legal-information MVP. Gemini handles natural-language understanding,
structured fact extraction, document-text analysis, and conversational replies. Deterministic application code owns
the case profile, conflict checks, evidence state, legal workflow, source retrieval, document eligibility, and safety
rules.

## Gemini setup

1. Copy `backend/.env.example` to `backend/.env`.
2. Add a server-side `GEMINI_API_KEY`.
3. Keep `LLM_PROVIDER=gemini` and choose the available Gemini model with `LLM_MODEL`.
4. Install and run the backend:

   ```powershell
   cd backend
   py -3.11 -m pip install -r requirements.txt
   py -3.11 -m uvicorn app.main:app --reload
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
