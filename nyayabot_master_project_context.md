# MASTER PROJECT CONTEXT — NYAYABOT / NYAY SETU

You are continuing development of an existing project called **NyayaBot / Nyay Setu**.

This is not a toy chatbot and should not be treated as a simple college form-filling demo.

The intended product is a conversation-first Indian legal-information and legal-action assistant.

Users should be able to describe a problem naturally, and NyayaBot should:

1. Understand what happened.
2. Extract relevant structured facts.
3. Ask only useful follow-up questions.
4. Confirm consequential facts where appropriate.
5. Maintain a durable case profile.
6. Retrieve verified Indian legal information.
7. Explain rights/options in plain language.
8. Track evidence.
9. Track what the user has actually done.
10. Recommend the next practical action.
11. Generate legal documents when appropriate.
12. Help fill supported government portals/forms.
13. Reassess the case as new facts/actions/evidence arrive.
14. Indicate when professional legal help should be considered.
15. Handle urgent safety situations before normal legal intake.

The project should remain practical, trustworthy, scalable, and usable for real-world legal self-help.

---

## 1. IMPORTANT WORKING REPOSITORY

**CURRENT ACTIVE WORKING COPY**

```text
C:\Users\SANIYA SHARMA\Legal-Assistant
```

Backend:

```text
C:\Users\SANIYA SHARMA\Legal-Assistant\backend
```

Frontend:

```text
C:\Users\SANIYA SHARMA\Legal-Assistant\frontend
```

Startup script:

```text
C:\Users\SANIYA SHARMA\Legal-Assistant\start-nyayabot.ps1
```

There is also another clone:

```text
C:\Users\SANIYA SHARMA\NyayaBot
```

Both historically pointed to:

```text
https://github.com/Saniya0223/NyayaBot.git
```

Before every substantial task run:

```powershell
git rev-parse --show-toplevel
git remote get-url origin
git status --short
```

Expected root:

```text
C:\Users\SANIYA SHARMA\Legal-Assistant
```

If the root is different, STOP and report it.

Do not casually edit the duplicate `NyayaBot` folder. Do not delete either folder. Do not reset, stash, or discard uncommitted work unless explicitly asked.

Historically:

```text
NyayaBot:
d2f6810... "Documents"

Legal-Assistant:
d2328e1... "Fix contextual follow-up resolution in chat"
```

Legal-Assistant later accumulated much newer uncommitted work.

---

## 2. GIT / AGENT PREFERENCES

The user wants to commit and push manually.

Do not:

- git commit
- git push
- add Claude/Codex co-author metadata
- reset existing work
- discard unrelated changes
- stash without permission
- rewrite history

After development work, report:

- files changed
- tests run
- test results
- whether `git diff --check` passed

---

## 3. LOCAL RUN COMMANDS

Backend:

```powershell
cd "C:\Users\SANIYA SHARMA\Legal-Assistant\backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend:

```powershell
cd "C:\Users\SANIYA SHARMA\Legal-Assistant\frontend"
npm run dev
```

Typical URLs:

```text
Frontend: http://localhost:3000
Backend:  http://localhost:8000
```

Prefer the backend `.venv` Python explicitly.

`start-nyayabot.ps1` also starts both from the correct Legal-Assistant repository.

---

## 4. STACK

Frontend:
- React / Next.js-style app
- Tailwind-based UI

Backend:
- Python
- FastAPI
- SQLAlchemy
- SQLite in development

LLM providers:
- Groq primary
- Gemini provider also exists

Current model:

```text
openai/gpt-oss-120b
```

Typical backend `.env` values:

```env
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
GROQ_API_KEY=...
GEMINI_API_KEY=...
OCR_SPACE_API_KEY=...
```

Never print or commit real API keys.

---

## 5. CORE ARCHITECTURE

LLM should mainly do:
- natural-language understanding
- structured extraction
- explanation
- evidence interpretation
- conversational responses

Deterministic Python/backend should own:
- workflow stage
- case state
- fact persistence
- evidence state
- document readiness
- document intent
- safety
- professional-help assessment
- deadlines where deterministic
- suggested next actions
- ownership
- document generation state
- PendingInteraction state

Do not move consequential legal state decisions back into opaque model prose.

---

## 6. CHAT PIPELINE

Primary endpoint:

```text
POST /api/v1/chat/message
```

Conceptual flow:

```text
User message
→ structured CaseExtraction
→ deterministic Python state update
→ RAG / workflow / evidence / professional-help / docs
→ final conversational LLM response
```

Historically:

1. `extract_case_updates()` at temperature around 0.0, structured JSON
2. deterministic state update
3. `chat()` at temperature around 0.35

Extraction receives:
- newest user message
- recent conversation
- compact case summary
- PendingInteraction
- domain/document metadata
- language/script info

Final chat may receive:
- current message
- recent history
- compact case
- workflow
- domain context
- verified RAG sources
- evidence summary
- professional-help assessment
- relevant profile/memory
- document state
- language style

---

## 7. GROQ PROMPT CACHING — ALREADY IMPLEMENTED

Do not redo prompt caching.

Relevant files:

```text
backend/app/llm/groq_provider.py
backend/tests/test_groq_prompt_caching.py
```

CaseExtraction was reorganized from:

```text
SYSTEM:
extraction instructions
→ JSON schema

USER:
fixed instruction
→ newest message
→ recent messages
→ case summary
→ pending interaction
→ language/script
→ domain catalog
→ document catalog
```

to:

```text
STABLE PREFIX:
SYSTEM:
extraction instructions
→ canonical full JSON schema

USER:
fixed instruction
→ canonical domain catalog
→ document catalog

DYNAMIC SUFFIX:
case summary
→ pending interaction
→ language/script
→ recent messages
→ newest user message
```

Purpose: improve Groq automatic prefix caching.

No extraction fields were removed. Model remains `openai/gpt-oss-120b`. Context size was not yet optimized.

Canonical serialization was added for schemas/catalogs.

Safe usage logging now includes numeric metrics such as:

```text
event=llm_usage
provider=groq
operation=structured_CaseExtraction
model=openai/gpt-oss-120b
prompt_tokens=...
cached_tokens=...
fresh_prompt_tokens=...
completion_tokens=...
total_tokens=...
cache_hit_percent=...
```

Do not log prompts, user content, evidence, profile data, or keys.

Live cache hits are not yet confidently verified.

---

## 8. GROQ RATE-LIMIT / RETRY FIX — ALREADY IMPLEMENTED

Historical bug:

```text
Groq SDK retry
+
NyayaBot application retry
=
up to 6 network attempts for one logical provider operation
```

Now:

```text
Groq SDK max_retries=0
```

NyayaBot owns retry decisions.

Current intended behavior:

```text
normally 1 network request
maximum 2 network requests
```

Only a very short provider-indicated retry is allowed.

Config:

```text
GROQ_RATE_LIMIT_MAX_RETRY_WAIT_SECONDS
```

Expected bound: 0–2 sec; discussed default about 1 sec.

Examples:

```text
Retry-After 0.2 sec
→ wait and retry once

Retry-After 5 sec
→ no retry
→ local fallback

Retry-After 40 sec
→ no retry
→ local fallback

missing timing
→ do not guess
→ local fallback
```

Safe logging includes:

```text
event=groq_rate_limit
provider
operation
model
http_status
retry_after_seconds
remaining_tokens
token_reset_seconds
remaining_requests
request_reset_seconds
decision
```

A final rate-limited extraction should stop the turn and not call final chat or any other optional Groq work.

Fallback itself should use zero Groq calls.

Recent files included:

```text
backend/app/config.py
backend/app/llm/contracts.py
backend/app/llm/groq_provider.py
backend/app/services/llm_conversation.py
backend/tests/test_groq_rate_limits.py
frontend/tests/chatRetry.test.mjs
```

---

## 9. "TRY GROQ AGAIN" SEMANTICS — ALREADY FIXED

Old bug:
Clicking the button created a new user message whose literal text was:

```text
Try Groq again
```

That could contaminate extraction/history/RAG/memory/case facts.

Current intended behavior:

### Extraction failed
Retry original user message using stored extraction context.

### Final chat failed
Retry only response generation using saved final-response context. Do not rerun extraction or reapply state.

History:
- failed assistant reply replaced in place
- original user message/IDs stay unchanged

Idempotency:
- failed-response ID is retry key
- atomic DB claims
- completed retry returns saved result
- stale retry rejected

Button label remains UI metadata and must not enter:
- extraction
- history
- RAG
- memory
- case facts

One click = one retry POST using IDs.

Files included:

```text
backend/app/main.py
backend/app/schemas/chat.py
backend/app/services/chat_retry.py
backend/app/services/llm_conversation.py
backend/tests/test_chat_retry.py
frontend/src/components/ChatInterface.tsx
frontend/src/lib/api.ts
frontend/tests/chatRetry.test.mjs
```

Latest reported verification:
- 151 backend tests passed
- 11 frontend tests passed
- TypeScript passed
- targeted ESLint passed
- `git diff --check` passed

---

## 10. CURRENT IMMEDIATE BUG UNDER INVESTIGATION

On 28 Sep 2026 around 8:06 PM, user says they made their first chat attempt of the day.

Groq dashboard showed approximately:

```text
8:06:04 PM
200
input 4933
output 777

8:06:07 PM
429

8:06:09 PM
429

8:06:12 PM
429

8:06:13 PM
429

8:06:19 PM
429

8:06:23 PM
429

8:06:26 PM
200
input 4318
output 404

8:06:27 PM
429
```

UI displayed:

```text
Groq is temporarily unavailable. I have switched this turn to limited demo mode and preserved your case progress.
```

This is suspicious because one provider operation should now make only 1–2 network attempts.

Potential causes:
- multiple active LLM operations firing
- another legacy provider path
- duplicate frontend submission
- background task
- automatic summary/evidence/classification
- multiple server instances
- stale process
- an active call path not covered by the new retry wrapper

The active repository path has been confirmed correct.

Next investigation should be READ-ONLY.

For one clean chat message, capture backend lines containing:

```text
POST /api/v1/chat/message
event=llm_usage
event=groq_rate_limit
operation=
decision=
cached_tokens=
fresh_prompt_tokens=
turn_fell_back...
```

Also inspect:
- `start-nyayabot.ps1`
- duplicate uvicorn/Python processes
- multiple workers
- frontend double submit
- React effects
- polling
- workspace auto-calls
- automatic summary
- classification
- evidence background tasks
- legacy agents

Do not rewrite architecture before identifying which operation produced each Groq request.

---

## 11. MISLEADING UI STATUS

UI has shown:

```text
Limited demo
Groq key required · local workflow fallback
```

This is misleading because successful Groq HTTP 200 calls prove the key is configured.

Eventually distinguish:

```text
GROQ_NOT_CONFIGURED
RATE_LIMITED
PROVIDER_UNAVAILABLE
AUTH_ERROR
```

Do not mix this copy fix into unrelated investigations unless asked.

---

## 12. FREE GROQ CONSTRAINT

User does not want to purchase a higher Groq plan.

Recent requests were around:

```text
4933 input + 777 output
4318 input + 404 output
```

A single cold extraction can consume much of a small short-window token allowance, then final chat may 429.

Prompt caching helps, but first cold requests can still be expensive.

Future optimizations have been discussed but not yet requested:
- zero-LLM greeting path
- zero-LLM obvious PendingInteraction replies
- smaller extraction context
- active-domain-only definitions
- relevance-based final-chat context
- adaptive output budgets

Do not redesign now unless explicitly asked.

---

## 13. CONVERSATION-FIRST UX

NyayaBot must feel like conversation, not a form.

PRE_INTAKE:

```text
User: hi
Bot: What happened?
```

Do not immediately show category selection, document flow, or long questionnaires.

Conceptual readiness:

```text
PRE_INTAKE
→ UNDERSTANDING_CASE
→ READY_FOR_LEGAL_GUIDANCE
→ READY_FOR_ACTION
→ READY_FOR_DOCUMENT
```

Facts have semantic purpose/relevance/priority, not merely mandatory vs missing.

Optional facts may remain unknown.

Direct questions should be answered first when safe/useful.

One primary question per turn.

Do not repeat known facts.

`False` / `no` is known, not missing.

---

## 14. LANGUAGE RULES

Roman Hinglish → Roman Hinglish.

Example:

```text
mera landlord deposit nahi de raha
```

Do not switch to Devanagari unless user wrote in Devanagari.

Devanagari Hindi → Devanagari.

English → English.

---

## 15. SAFETY

Safety triage before ordinary intake.

Concepts:

```text
safety_level:
GREEN
AMBER
RED

immediate_danger:
true
false
unknown
```

Threats/domestic violence/intimate partner violence/immediate danger must be handled before normal documents/workflow.

Example:

```text
mere pati ne mujhe jaan se marne ki dhamki di
```

First priority = immediate danger/safety, not document generation.

---

## 16. MAIN LEGAL DOMAINS

- Housing / Tenancy
- Employment
- Consumer
- Cyber Fraud
- Police Complaint

Use shared conversation architecture with domain metadata/policies, not one giant separate system prompt per domain.

---

## 17. EXAMPLE WORKFLOWS

Tenancy:

```text
informal request
→ formal notice
→ awaiting response
→ appropriate authority/civil recovery
```

Employment:

```text
HR follow-up
→ salary demand
→ awaiting settlement
→ labour authority/formal route
```

Consumer:

```text
support contacted
→ grievance
→ awaiting seller
→ e-Daakhil/formal complaint
```

Cyber:

```text
fraud
→ bank + 1930
→ cybercrime portal
→ cyber cell/police/FIR as appropriate
```

Police:

```text
SHO
→ written complaint
→ SP escalation
→ magistrate route where appropriate
```

Legal Journey records actual events only, not hypothetical steps.

---

## 18. DOCUMENT SYSTEM

Target docs include:
- tenant deposit demand
- salary demand
- consumer grievance
- e-Daakhil complaint
- police complaint
- cyber bank-freeze request
- RTI
- general complaint

Output: PDF/DOCX.

Separate:

```text
case_readiness
```

from:

```text
document_intent:
NONE
SYSTEM_SUGGESTED
USER_REQUESTED
```

and document generation status.

Explicit user request such as:

```text
generate notice
make the PDF
```

should enter document flow even if overall case readiness is not complete, unless blocked by safety/unsupported type/genuinely required data.

Optional fields never block generation.

Do not claim a file is generated unless it actually exists.

---

## 19. PENDINGINTERACTION

Introduced to solve contextual short replies.

Conceptual types:

```text
FACT_CONFIRMATION
EVIDENCE_CONFIRMATION
ACTION_CONFIRMATION
DOCUMENT_CONFIRMATION
CHOICE
CLARIFICATION
```

Examples:

```text
payment proof? + yes
→ payment_proof_available=true

HR contacted? + not yet
→ false

two choices + both
→ both resolved

document offer + ok make
→ USER_REQUESTED correct doc
```

No PendingInteraction + "yes" must not invent a fact.

Topic switch must not be forced into pending answer.

PendingInteraction is case-scoped, short-term, not long-term memory.

---

## 20. LOW_CONTEXT_PHRASES

Examples historically include:

```text
yes
no
both
ok
not yet
i did
i have
yesterday
today
```

These are mainly for RAG-query handling.

LOW_CONTEXT for RAG does NOT mean ignore for extraction/state.

---

## 21. PROFESSIONAL-HELP ASSESSMENT

Deterministic, not pure LLM and not just a boolean.

Levels:

```text
SELF_HELP_REASONABLE
CONSIDER_LEGAL_HELP
LEGAL_HELP_RECOMMENDED
URGENT_LEGAL_HELP
```

Assessment may contain:
- level
- reasons
- professional_types
- urgency
- reassess_on
- known signals

Unknown facts should not automatically escalate.

Safety remains above this layer.

It is advisory and should not block normal workflow.

---

## 22. USER PROFILE / MEMORY / CASE HISTORY

Keep separate:

### User Profile
Permanent structured details:
- name
- DOB
- auth email
- optional phone
- state
- city
- PIN
- address
- preferred language

### Long-Term Memory
Durable preferences or explicitly remembered recurring info.

### Case
Detailed individual matter:
- title
- category
- status
- dates
- facts
- laws
- summary
- docs
- legal journey
- suggested actions

### Previous Case History
Owned previous cases, lightweight summary.

### Recent Chat
Short-term context.

Do not save every message into long-term memory.

---

## 23. HISTORICAL MEMORY BUG

In an Employment case, asking:

```text
tumhe mere baare mein kya yaad hai?
```

incorrectly mixed:
- old Consumer ₹2500
- temporary cousin-house info
- current salary

Then asking:

```text
kya mera pehle koi consumer case tha?
```

incorrectly returned no.

This suggests inconsistent source separation.

Verify current code/tests before assuming fixed.

---

## 24. LIVE CASE WORKSPACE

Desired sections:
- case type badge
- Key Facts
- Laws & Rights
- Documents
- Legal Journey
- Suggested Actions
- professional-help summary
- Generate AI Summary
- Auto-Fill Govt Form

Key Facts = explicit/reliably extracted facts.

Laws & Rights = verified legal sources.

Documents split:
- Provided by You
- Generated by NyayaBot

Legal Journey = actual events only.

Suggested Actions = future recommendations.

AI Summary = on-demand only.

---

## 25. HISTORICAL WORKSPACE BUGS

Case title used to stay:

```text
Legal Information Request
```

Desired deterministic title priority:

```text
custom title
>
issue display name
>
domain label
>
generic fallback
```

No LLM required for title.

Another issue:
Laws appeared in chat but not workspace.

Chat and workspace should use same verified legal-source pipeline.

Do not run a second LLM/RAG call just for sidebar.

---

## 26. RAG

Originally corpus roughly:
- Consumer 7
- Tenancy 4
- RTI 4

Employment/Cyber/Police needed expansion.

Old retrieval was lexical.

Historical bug:
latest message only → "yes" became RAG query.

Structured `RagQueryContext` direction:
- current case
- relevant facts
- domain
- issue
- meaningful user content

Long-term direction:
- BM25/lexical
- embeddings
- metadata filters
- reranking

But expand verified corpus first.

Applicability metadata should eventually include:
- jurisdiction
- proceeding type
- party role
- trigger
- obligation
- remedy
- `not_primary_for`

Do not invent legal provisions.

---

## 27. EVIDENCE PROCESSING

Relevant services:

```text
backend/app/services/ocr_service.py
backend/app/services/evidence_extraction.py
backend/app/services/evidence_analysis.py
backend/app/services/evidence_processing.py
```

Frontend:

```text
ProvidedEvidenceList.tsx
EvidenceDetailModal.tsx
```

Supported:
- PDF
- JPG/JPEG
- PNG
- DOCX
- TXT
- EML

`.doc` may be preserved but not extractable.

Typical max size: 10 MB.

Lifecycle:

```text
UPLOADED
→ PROCESSING
→ COMPLETED / FAILED
```

Original file retained.

Ownership required on every evidence endpoint.

Non-owner should get 404.

---

## 28. PDF / OCR

PDF:
- PyMuPDF
- page-by-page text extraction
- OCR only when needed

OCR:
- OCR.space
- `OCR_SPACE_API_KEY`
- timeout around 45 sec
- safe statuses such as OCR_UNAVAILABLE/TIMEOUT/FAILED
- max OCR pages historically ~25

Images use OCR.

DOCX paragraphs/tables extracted directly.

Partial failures should preserve extracted content.

---

## 29. EVIDENCE ANALYSIS

Structured findings may include:
- type
- verbatim value
- statement
- source page
- clarity
- notes
- candidate facts
- corroboration
- conflicts
- deadlines
- unanalyzed portions

Evidence candidates should not automatically overwrite case facts.

Conflicts should not silently overwrite.

Source labels like:

```text
[Page 2 | ocr]
```

Prompt injection inside evidence is untrusted content.

---

## 30. EVIDENCE + CHAT

Chat should receive compact current-case evidence only.

Do not send all raw evidence every turn.

Opening a case must not rerun completed evidence analysis.

Completed analysis should be stored/cached.

Evidence ≠ legal RAG corpus.

```text
Evidence = what happened
RAG = what law/source says
```

---

## 31. TEST EVIDENCE IMAGE

A synthetic UPI-style screenshot was used with dummy data:

```text
Demo Merchant
demo@upi
Rohan Test
TEST123456789
₹2500
05 Jan 2025
```

Treat it as test data only.

---

## 32. AUTO-FILL GOVERNMENT PORTAL

Auto-fill uses browser automation / Playwright-style logic.

Development should use a LOCAL MOCK GOVERNMENT PORTAL first.

Do not submit real government forms during development.

Do not bypass CAPTCHA/OTP/security.

Reported mock file:

```text
backend/app/static/autofill_mock_portal.html
```

Related areas:
- browser_agent.py
- browser_routes.py
- browser_runtime.py
- portal_selector.py
- BrowserAgentPanel.tsx
- test_browser_autofill.py

Audit current code before changing.

---

## 33. AUTHENTICATION / OWNERSHIP

Early localStorage fake auth was replaced/targeted for real server-side auth.

Expected:
- UserModel
- password/session auth
- case ownership
- evidence ownership
- document ownership
- user-isolated data

Historical vulnerability:
`/chat/cases` exposed all cases/demo seeds.

Verify current code.

---

## 34. DOCUMENTS: PROVIDED VS GENERATED

Provided by You = uploaded files/evidence.

Generated by NyayaBot = actually produced documents.

Do not mix them.

Generated docs enter Legal Journey only when truly generated.

---

## 35. AI SUMMARY

On demand only.

Do not call LLM automatically on:
- every message
- workspace open
- rerender

Cache appropriately.

---

## 36. DIRECT USER QUESTIONS

Answer useful direct questions first.

Do not refuse guidance merely because some intake field is missing.

Then ask at most one useful follow-up.

---

## 37. SAFETY VS DOCUMENTS

Safety overrides ordinary document flow.

Immediate threat → immediate safety first.

---

## 38. CURRENT UI STATE

Recent screenshot:
- left chat
- right Live Case Workspace
- sections for facts, laws, docs, journey, professional help, AI summary, auto-fill

For a `hi` case:
- "Case type not identified yet"
- "Your case is taking shape"
- "Describe your situation to get started."

Acceptable PRE_INTAKE direction.

But the banner saying:

```text
Groq key required · local workflow fallback
```

is wrong when Groq is configured.

---

## 39. TESTING PHILOSOPHY

Do not waste external quota.

By default:
- no live Groq
- no live Gemini
- no live OCR unless explicitly requested
- no real portal submission

Prefer:
- unit tests
- mocks
- deterministic tests
- local mock portal

Test in layers:
1. individual features
2. feature-to-feature
3. complete journeys

---

## 40. FEATURE CONNECTION MATRIX

```text
Profile
→ docs / Auto-Fill
NOT unrelated case facts

Long-term memory
→ preferences
NOT detailed case state

Current case
→ chat/workspace/workflow/docs
NOT another case

Previous cases
→ lightweight history
NOT current-case fields

Evidence
→ candidate facts/current-case context
NOT legal RAG

RAG
→ chat + Laws & Rights
NOT evidence facts

Workflow
→ suggested next action
NOT invented completed journey

Professional Help
→ chat + Suggested Actions
NOT workflow blocker

Generated doc
→ Documents + Legal Journey after actual generation
NOT uploaded evidence

PendingInteraction
→ short-term resolution
NOT long-term memory
```

---

## 41. SECURITY TESTS

Test:
- multi-user isolation
- case ownership
- evidence ownership
- document ownership
- previous-case history ownership
- generated file protection
- profile leakage prevention

---

## 42. FULL JOURNEY TESTS

Tenancy:
```text
withheld deposit
→ facts
→ proof
→ guidance
→ notice
→ generated document
→ journey update
→ escalation
```

Employment:
```text
unpaid salary
→ HR facts
→ evidence
→ demand
→ professional-help reassessment
```

Consumer:
```text
seller dispute
→ contact
→ grievance
→ evidence
→ consumer route
→ e-Daakhil possibility
```

Cyber:
```text
fraud
→ bank/1930
→ evidence
→ portal/police
→ reassessment if complications
```

---

## 43. IMPORTANT FILES

Commonly relevant:

```text
backend/app/main.py
backend/app/services/llm_conversation.py
backend/app/llm/groq_provider.py
backend/app/llm/gemini_provider.py
backend/app/llm/contracts.py
backend/app/agents/rag_node.py
backend/app/agents/conversation_agent.py
backend/app/services/case_readiness.py
backend/app/services/chat_retry.py
backend/app/services/evidence_processing.py
backend/app/services/evidence_extraction.py
backend/app/services/evidence_analysis.py
backend/app/services/ocr_service.py
backend/app/agents/browser_agent.py
frontend/src/components/ChatInterface.tsx
frontend/src/components/CaseWorkspacePanel.tsx
frontend/src/components/ProvidedEvidenceList.tsx
frontend/src/components/EvidenceDetailModal.tsx
frontend/src/components/BrowserAgentPanel.tsx
frontend/src/lib/api.ts
```

Inspect actual repo paths rather than assuming exact filenames.

---

## 44. LEGACY AGENTS

Older agents may include:
- classifier_node
- calculator_node
- intake_node
- orchestrator

Some may be legacy only.

Live conversation path is mainly:
- conversation_agent
- llm_conversation

Label code paths ACTIVE vs LEGACY/UNUSED when auditing.

---

## 45. CURRENT GROQ USAGE INVESTIGATION

For one clean user message:

1. Count frontend `/api/v1/chat/message` POSTs.
2. Trace backend provider operations.
3. Correlate with Groq dashboard times.
4. Identify every `operation=...`.
5. Determine whether requests are:
   - CaseExtraction
   - final chat
   - classifier
   - evidence
   - summary
   - retry
   - browser
   - legacy path
6. Check `start-nyayabot.ps1`.
7. Check for duplicate Python/uvicorn processes.
8. Verify one click = one frontend request.
9. Ensure polling doesn't call LLM.
10. Ensure workspace opening doesn't call LLM.

Do not use live Groq for diagnosis unless user explicitly asks for a live test.

---

## 46. WHY THE 8:06 SCREENSHOT MATTERS

It proves:
- Groq key works
- successful calls happen
- UI "key required" text is false
- one call can be large
- several requests happened in a short interval

It does not prove which NyayaBot operation caused each request.

Use backend `operation` logs.

---

## 47. DO NOT OVER-OPTIMIZE ARCHITECTURE NOW

User explicitly does not want to redesign architecture right now.

Do not proactively:
- merge the two LLM calls
- switch model
- split providers
- add complicated queues
- rewrite context architecture
- build a new orchestration framework
- aggressively trim every prompt

unless asked.

---

## 48. FUTURE TOKEN OPTIMIZATIONS — LATER

Potential later optimizations:

Zero-LLM fast paths:
```text
hi
hello
thanks
okay
yes
no
haan
nahi
not yet
both
i have
i did
ok make
```

where deterministic resolution is safe.

Potential extraction reductions:
- active domain only
- fewer catalogs
- smaller history
- relevant profile/memory only
- no workspace/professional-help/RAG/raw evidence unless needed

Potential final-chat reductions:
- relevant profile/memory only
- bounded evidence findings
- top RAG sources
- compact workflow

Do not implement merely because listed here.

---

## 49. USER PREFERENCES

User is learning GenAI/chatbot architecture and prefers:
- simple explanations
- direct answers
- diagrams/flows
- examples
- ready-to-paste Claude/Codex prompts

When coding:
- inspect first
- modify minimally
- preserve working features
- avoid broad cleanup
- write regression tests
- avoid live paid/quota-consuming calls

When reporting:
- what was found
- what changed
- what was not changed
- files changed
- tests
- failures
- live API usage yes/no
- commit/push yes/no

---

## 50. LEGAL TRUST PRINCIPLES

- Do not hallucinate laws.
- Distinguish verified sources from assumptions.
- Preserve jurisdiction/applicability.
- Do not imply guaranteed legal outcomes.
- Safety before ordinary workflow.
- Use actual user facts before hypothetical assumptions.
- Confirm consequential facts before document generation.
- Professional-help escalation should be dynamic and reasoned.

---

## 51. CURRENT PRIORITY

User wants to pause architecture optimization and fix other product issues.

Immediate technical investigation:
Why did one apparent first chat on 28 Sep 2026 around 8:06 PM correspond to:

```text
200
many 429s
another 200
another 429
```

despite bounded provider retries?

Start with a READ-ONLY trace:

```text
frontend send
→ POST /api/v1/chat/message
→ backend operation sequence
→ provider calls
→ operation-specific logs
```

Also inspect duplicate processes and automatic/background LLM triggers.

Do not rewrite anything first.

---

## 52. FIRST ACTION FOR CLAUDE CODE

Before modifying anything:

1. Verify repo root.
2. Verify origin.
3. Show `git status --short`.
4. Inspect current uncommitted changes.
5. Do not discard existing work.
6. Read current files instead of assuming this handoff perfectly reflects implementation.
7. Treat current source code as implementation source of truth.
8. Do not run live Groq/Gemini/OCR unless explicitly asked.
9. Do not commit/push.

If current code differs from this context, show the difference before changing it.
