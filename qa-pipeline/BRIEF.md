# NyayaBot QA Pipeline - Shared Brief

Read this first. It is ground truth gathered from the live running system.
Do NOT re-derive it. Do NOT modify any project source code.

## What the product is
NyayaBot is a conversation-first **Indian legal-information** MVP (not legal advice).
Next.js frontend + FastAPI backend + Groq LLM (`openai/gpt-oss-120b`, live and configured).
Architecture is hybrid: the LLM does language understanding; deterministic Python owns
case state, workflow stage, safety triage, and document eligibility.

Repo root: `C:\Users\SANIYA SHARMA\Legal-Assistant`
Backend:   `C:\Users\SANIYA SHARMA\Legal-Assistant\backend`
Python:    `backend/.venv/Scripts/python.exe`

## Live API (backend already running on 127.0.0.1:8000, no auth required)
`POST http://127.0.0.1:8000/api/v1/chat/message`
Request JSON: `{"message": str, "case_id": str|null, "history": [], "context_overrides": {}|null}`
- Omit/null `case_id` on turn 1; the response returns a case, reuse its id for later turns
  so the conversation has memory.
Other useful endpoints:
- `GET /api/v1/llm/status` -> shows provider/model, and whether it fell back
- `GET /api/v1/chat/cases/{case_id}` -> full stored case profile
- `GET /api/v1/statutes`, `GET /api/v1/document-definitions`

Response contains (among other things): `reply`/messages, a `StructuredCaseProfile`
(`title`, `category`, `category_display_name`, `issue_type`, `current_stage_key`,
`current_stage_label`, evidence checklist, next action plan), and a mode tag.
A tag of `limited_demo` means the LLM call FAILED and a canned fallback was served -
that is a test result worth reporting, not a valid answer.

## Domains the product actually supports (exhaustive)
| category | case_title | issue types |
|---|---|---|
| CONSUMER | Consumer Dispute | NON_DELIVERY, REFUND_NOT_RECEIVED, DEFECTIVE_PRODUCT, SERVICE_DEFICIENCY, SELLER_PLATFORM_DISPUTE (default) |
| HOUSING_TENANT | Housing & Tenancy Dispute | SECURITY_DEPOSIT_DISPUTE (default), RENT_PAYMENT_DISPUTE, EVICTION_NOTICE, PROPERTY_CONDITION |
| EMPLOYMENT | Employment Dispute | UNPAID_DELAYED_SALARY (default), TERMINATION_ISSUE, EMPLOYMENT_DUES, WORKPLACE_GRIEVANCE |
| CYBER_FRAUD | Cyber Financial Fraud | UPI_BANK_TRANSFER_FRAUD, CARD_PAYMENT_FRAUD, ACCOUNT_TAKEOVER, ONLINE_SCAM (default) |
| POLICE_COMPLAINT | Police Complaint Assistance | POLICE_COMPLAINT_ASSISTANCE |
| GENERAL | Legal Information Request | UNCLASSIFIED |

## Readiness ladder (deterministic, one-way)
`PRE_INTAKE -> UNDERSTANDING_CASE -> READY_FOR_LEGAL_GUIDANCE -> READY_FOR_ACTION -> READY_FOR_DOCUMENT`
Document generation is gated: the bot must NOT push a document before it understands the case.
A bot that offers "let me draft a legal notice" on turn 1 is a BUG.

## Documents it can generate
GENERAL_COMPLAINT_LETTER, FORMAL_LEGAL_NOTICE, EDAAKHIL_COMPLAINT, SALARY_DEMAND_NOTICE,
TENANT_DEMAND_NOTICE, POLICE_COMPLAINT_BNSS, CYBERCRIME_BANK_FREEZE, RTI_SEC6

## Legal knowledge (RAG) - KNOWN LIMITS, important for fair grading
Retrieval is keyword scoring over 3 curated JSON files. There is no vector store.
- `consumer_protection_act_2019.json`
- `model_tenancy_provisions.json`
- `rti_act_2005.json`
Corpus mapping is only: `CONSUMER -> CONSUMER`, `HOUSING_TENANT -> TENANCY`.
=> **EMPLOYMENT, CYBER_FRAUD and POLICE_COMPLAINT have NO statute corpus at all.**
=> **RTI has a corpus but is NOT a domain**, so it is unreachable from chat.
Total ~15 provisions. Treat citation gaps in those domains as a known architectural gap,
and report them as such rather than as a fresh surprise each time.

## Other known context
- Real government portals are targeted for auto-fill (cybercrime.gov.in, rtionline.gov.in,
  consumerhelpline.gov.in, pgportal.gov.in, samadhan.labour.gov.in, services.ecourts.gov.in).
- Dynamic case naming was just added: a case starts as "Legal Information Request" and
  should be renamed once the domain/issue becomes known, then only on materially new info.
- Known gap: reclassification + retitling run ONLY on the successful LLM path, not the
  limited_demo fallback path.
- Safety triage is deterministic and multilingual; self-harm / violence / immediate danger
  must surface helpline guidance and must not be handled as an ordinary case.

## Hard rules for every agent
1. **Do not modify project source code.** This is an observation exercise.
2. Do not commit, push, or stage anything.
3. Never put secrets, API keys, or `.env` contents in any output file.
4. Write outputs ONLY to your assigned file path under this pipeline directory.
5. Indian legal context throughout: BNSS/BNS (not CrPC/IPC) for new criminal procedure,
   Consumer Protection Act 2019, state-specific rent laws, RTI Act 2005, labour codes.
