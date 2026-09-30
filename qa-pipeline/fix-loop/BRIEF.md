# NyayaBot Fix Loop — Shared Brief

Read this first. It is ground truth. Do not re-derive it.

## The loop
```
Agent 1 (diagnose + plan)  ->  Agent 2 (implement)  ->  Agent 3 (test)  ->  Agent 4 (triage)  -> back to Agent 1
```
Agent 1 works on **one set of related issues at a time**, not all 24 at once.
Issues whose fixes touch the same file or overlap in mechanism belong in the same set.

## Where things are
- Repo root: `C:\Users\SANIYA SHARMA\Legal-Assistant`
- Backend:   `C:\Users\SANIYA SHARMA\Legal-Assistant\backend`
- Python:    `backend\.venv\Scripts\python.exe`  (always use this, not bare `python`)
- The QA findings report: `qa-pipeline\wave-1\report.md`  (24 findings, all evidence-quoted)
- Raw evidence: `qa-pipeline\wave-1\transcripts.json`, `qa-pipeline\wave-1\server-evidence.log`
- Loop artifacts: `qa-pipeline\fix-loop\cycle-N\`

## NEVER LAUNCH A BROWSER  (added after cycles 1-2)
`tests/test_browser_autofill.py` contains **two** tests that launch a real Chromium via
Playwright. Running the full suite opens a browser window on the user's laptop and interrupts
their work. They have asked twice for this to stop.

**Always run tests like this:**
```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
```
Never run bare `pytest`. Never start a server, never drive a browser, never call browser-use
or Playwright.

## Test baseline (current, after cycle 4)
```
1 failed, 752 passed, 34 xfailed     (with tests/test_browser_autofill.py ignored)
FAILED tests/test_doc_generator.py::test_document_generation_notice
```
That one failure is **pre-existing and unrelated**. Do not count it as your breakage, and do not
"fix" it unless a cycle is explicitly about it.

The 34 xfails are the **scoreboard**, all `strict=True`: N8 (offline fallback composer repeats
replies, deferred to S3/S8) plus **33 cycle-4 reproducers** in `tests/test_cycle4_adversarial.py`.
Strict means a fix flips one to a reported FAILURE, so a gap cannot be closed silently. Cycle 5's
job is to turn the ones triaged as considerable into passing locks — see
`cycle-4/agent4-triage.md`, which forwards 12 and names the cut list.

## What cycles 1-4 already did
Set **S1** (C1, H1, H2) is closed, and **S5a** (crisis half of the document gate) is done.
Already heavily modified: `safety_triage.py`, `llm_conversation.py`, `case_readiness.py`, plus a
small crash fix in `conversation_agent.py::_amount_from_text`.
**C3 is NOT closed** - the readiness half of the document gate (S5b) was deferred to pair with S2.
Cycle 3 did **H7** (`case_readiness.compute_blocking_missing_facts` now delegates to the action
planner) and added `fact_extraction.py`, `jurisdiction.py`, `date_facts.py`.
Cycle 4 did **S4** - the hallucination guards. **L1 and M8 are closed**; D1, C2, C4, H4, M7 are
**partially** closed. New: `response_guard.py`, `helplines.py`, `model_law_adoption.py`,
`document_citations.py`. The guard runs at `llm_conversation._tag_response` (the single funnel for
all 16 return paths) and inside `case_summary.generate_case_summary` (so `ai_summary_cache` stores
guarded text).

**Do not modify `app/services/safety_triage.py`** - S1 is closed, and cycle 2 introduced a real
safety regression there that had to be repaired. Cycle 4 correctly only read it.

Remaining sets: **S5** (see `cycle-4/agent4-triage.md`), **S6** (C5 limitation arithmetic), **S3**
(H5, M1, M5, M6, L2), **S7** (H3, H9), **S8** (M2, M3).

Two corrections to the wave-1 report, both confirmed in source - do not re-derive:
- **H4's "invented Section 11 / Section 30" is wrong.** Both are literally in
  `app/data/model_tenancy_provisions.json`. H4 is corpus leakage plus a conflated `act` field.
- **M8's "`sources` was null on all 31 turns" was a QA harness artifact**, not evidence. The API has
  no top-level `sources` or `mode`; it exposes `llm_mode` and `case_profile.legal_sources`.
  `run_wave.py` has been patched to read the real fields.

## HARD CONSTRAINTS — every agent
1. **Do not commit. Do not push. Do not create branches.** The user has said this repeatedly.
2. **Do not make live Groq/LLM API calls.** The daily token allowance is exhausted and the
   free tier is 8,000 tokens/min against ~10,000-13,000 per conversation turn. Test with
   pytest and deterministic unit tests only. Fakes/mocks are already used throughout the suite
   (see `tests/test_groq_prompt_caching.py` for the established pattern).
3. **Never reduce the passing test count.** 318 must stay 318 or go up.
4. The working tree already contains other people's uncommitted work. Only touch what your
   task requires; never revert or reformat unrelated code.
5. No secrets, API keys or `.env` contents in any output file.

## Architecture context you need
Hybrid design: the LLM does language understanding; **deterministic Python owns case state,
workflow stage, safety triage and document eligibility**. When a fix can be deterministic,
prefer that over prompt engineering — prompts fail when the provider is rate-limited, and
the fallback path is heavily exercised in practice.

Readiness ladder (one-way):
`PRE_INTAKE -> UNDERSTANDING_CASE -> READY_FOR_LEGAL_GUIDANCE -> READY_FOR_ACTION -> READY_FOR_DOCUMENT`

Domains: CONSUMER, HOUSING_TENANT, EMPLOYMENT, CYBER_FRAUD, POLICE_COMPLAINT, GENERAL.
Only CONSUMER and HOUSING_TENANT have a statute corpus. EMPLOYMENT, CYBER_FRAUD and
POLICE_COMPLAINT have **none** — this is why fabricated citations appear there.

Key files:
- `backend/app/services/safety_triage.py`      — deterministic triage
- `backend/app/services/case_readiness.py`     — readiness ladder, blocking facts, document gate
- `backend/app/services/llm_conversation.py`   — turn orchestration, reclassification, RAG assembly
- `backend/app/services/case_naming.py`        — dynamic case titles
- `backend/app/agents/conversation_agent.py`   — deterministic extraction, action detection
- `backend/app/llm/groq_provider.py`           — provider, prompts, retry
- `backend/app/domains/`                       — domain registry and definitions

## Reporting format between agents
Every handoff report must state, for each issue:
- **Problem** — what is wrong, with the evidence quote or file:line
- **Proposed/actual fix** — specific, naming file and function
- **Status** — for Agent 3/4 reports: fixed / partially fixed / not fixed, with proof

Write reports as markdown files in `qa-pipeline\fix-loop\cycle-N\`. Keep them tight and
specific; these are working documents between agents, not prose essays.
