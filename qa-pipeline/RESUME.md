# Resuming the NyayaBot QA pipeline

Waves 2 and 3 are fully authored (tests + answer keys) but were NOT executed:
the Groq daily token allowance was exhausted by wave 1. Nothing else is blocking them.

## Why it stopped
One NyayaBot turn costs ~10-13k tokens (extraction ~5-7.5k + chat ~3-5.5k).
Wave 1 = 31 turns = ~310k tokens. After that, large requests are refused with
`retry_after_seconds` equal to the time until the daily reset, while the
per-minute bucket still reads full (8000 remaining). Small requests still pass.

## To resume (after the daily Groq quota resets)

1. Start the QA backend (runtime-patched; touches no project file):

       set NYAYA_BACKEND_DIR=C:\Users\SANIYA SHARMA\Legal-Assistant\backend
       set NYAYA_PORT=8001
       "C:\Users\SANIYA SHARMA\Legal-Assistant\backend\.venv\Scripts\python.exe" qa_launcher.py

   The patch only raises the Groq retry-wait ceiling so a turn can survive a
   rate limit. Without it nearly every turn degrades to the canned fallback.

2. Run a wave:

       set NYAYA_API=http://127.0.0.1:8001
       set NYAYA_TURN_PAUSE=75
       "...\.venv\Scripts\python.exe" run_wave.py 2

   Then `run_wave.py 3`. Each writes `wave-N/transcripts.json`.

3. Grade: give an evaluator agent `BRIEF.md`, `wave-N/tests.json`,
   `wave-N/ideal.json`, `wave-N/transcripts.json` and have it write `wave-N/report.md`.
   Turns marked `"degraded_quota": true` must be excluded from quality scoring.

## Budget planning
Wave 2 = 28 turns (~300k tokens). Wave 3 = 26 turns (~280k).
Run one wave per day on the free tier, or raise the Groq tier and run both.
