# Agent 2 — Cycle 3 implementation report (S2 + S5b)

Findings covered: **H7, H6, H8, M4, S5b (C3, readiness half)**.
Constraints honoured: no commit, no branch, no push, no browser launched, no live LLM call,
`safety_triage.py` untouched, `tests/test_browser_autofill.py` ignored on every run.

> **Context.** This cycle was picked up mid-flight. A previous session had already landed the three
> new modules (`fact_extraction.py`, `jurisdiction.py`, `date_facts.py`), the H7 ladder delegation,
> and the backfill wiring on the live / offline / 429 paths, and was cut off with the tree at
> `8 failed, 435 passed, 1 xfailed`. This report covers the cycle as a whole and marks what this
> session added.

---

## Final pytest summary line

```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py

1 failed, 458 passed, 1 xfailed, 3 warnings in 31.79s
FAILED tests/test_doc_generator.py::test_document_generation_notice   (pre-existing, untouched)
```

Baseline before cycle 3 was `1 failed, 442 passed, 1 xfailed`. **458 ≥ 442** (+15 from the new
`tests/test_cycle3_verification.py`, +1 from a new rate-limit test, −1 net from a restructured API
test). The only failure is the pre-existing, unrelated one. The only xfail is N8, confirmed with
`-rxX` as still **XFAIL**, not XPASS:

```
XFAIL tests/test_cycle2_verification.py::test_cf1_released_turns_are_not_byte_identical_replies
      - N8 (deferred to S3/S8, reproducer restored)
```

---

## Verdict on each of the 5 readiness failures: legitimate advancement, or C3 re-appearing?

Every "legitimate" verdict was checked against `action_planner.plan_next_action(profile)` directly —
`status`, `required_facts`, `blocking_missing_facts`, `non_blocking_missing_facts` — and against
`missing_document_fields`, rather than assumed from the readiness label.

| # | test | verdict | evidence |
|---|---|---|---|
| 1 | `test_salary_notice_request_bypasses_proactive_case_readiness…` → `READY_FOR_DOCUMENT` | **Legitimate.** Test updated + renamed. | `formal_demand_sent` is `READY`, `required_facts=['opposite_party_name']` (present), `blocking_missing_facts=[]`; the three outstanding facts are reported **non-blocking** by the planner itself; every SALARY_DEMAND_NOTICE required field present → `missing_document_fields == []`. The document is earned. |
| 2 | `test_salary_pdf_request_opens_confirmation…` → `READY_FOR_DOCUMENT` | **Legitimate.** Test updated. | Same fixture minus `user_state`, which M4's Jaipur→Rajasthan derivation now supplies. Same planner evidence. |
| 3 | `test_consumer_follow_up_context…` → `READY_FOR_ACTION` | **Legitimate, and self-limiting.** Test updated. | Traced turn by turn: t1 `UNDERSTANDING` → t2 `READY_FOR_LEGAL_GUIDANCE` → t3 `READY_FOR_ACTION` once the seller is named. `blocking_missing_facts=[]`, non-blocking `['incident_date','invoice_available','user_state']`. It **stops** at `READY_FOR_ACTION`: `missing_document_fields == ['user_name','user_city','disputed_amount']`, so no `READY_FOR_DOCUMENT`. I added that assertion — it is the proof the fix did not open the floodgates. |
| 4 | `test_gemini_turn_uses_recent_history…` → a chat call where `response_context is None` was expected | **Not advancement at all — a real code bug I introduced-and-fixed.** | The H8 year invariant stripped `"2026"` from `vacating_date`, the extraction prompt re-asserted `"10 August 2026"` on the next turn, and `_apply_extraction` read that as a **factual conflict** — which hijacks the turn, so *every subsequent document request was silently swallowed*. Fixed in code (`same_date_ignoring_year` wired in); the test is **unchanged**. |
| 5 | `test_live_flow_records_a_ranked_question_then_resolves_the_next_yes` → a `DOCUMENT_CONFIRMATION` appears | **Legitimate.** Test updated. | That "yes" supplied `deposit_payment_proof_available`, the last fact the tenancy letter waited on. The resulting profile is the same state its sibling `test_live_flow_records_only_an_eligible_document_offer` asserts *should* produce exactly this offer — and that sibling is green, unmodified. |

**Where C3 *was* re-appearing, and it was not in any of those five:** the 8-case replay found
**W1-05** (death threat, Lucknow) being offered `POLICE_COMPLAINT_BNSS` with an empty record. That was
fixed in code, not in a test — see H7 and the safety-brake gate below.

---

## Ruling: the rate-limit conflict (`test_groq_rate_limits.py`)

**I kept the backfill on the 429 path and changed the test.** Reasoning:

The test's poison mocks encode three separate claims, and only one of them actually fired.

| mock | claim | fires under the fix? |
|---|---|---|
| `agent.process_turn = AssertionError("No regex fact extraction in rate-limit fallback")` | the offline composer must not run a whole turn here | **no** — `backfill_facts` is a module function, never `agent.process_turn`. Kept verbatim (message reworded). |
| `service._apply_extraction = AssertionError("Failed extraction must not apply facts")` | a *failed LLM extraction* must not write facts | **no** — still holds, and still asserted. |
| `service._refresh_workflow = AssertionError("Existing case must be preserved")` | readiness must not be recomputed | **yes** — this is the real conflict. |

So the name on the first mock overstates what the test actually enforces, and the collision is with
the third mock plus `assert profile.model_dump(mode="json") == before`.

That byte-identity assertion pinned two different things together:

* **(a) a failed LLM extraction must never apply facts.** This is the real safety property. It is
  untouched and is now asserted *harder* than before (the `_apply_extraction` poison mock stays, and
  the specific stored values are checked individually).
* **(b) the user's own message must not be read at all on this path.** This is over-broad, and it is
  the defect. `_rate_limit_fallback` performed no extraction of any kind, while
  `EXTRACTION_SYSTEM_PROMPT` only ever reads the newest turn — so a fact stated during an outage was
  lost **permanently**. Five of wave 1's 31 turns went that way, including W1-01's "Flipkart" and
  "Jaipur" (t3), W1-02's Rs 120000 correction (t3), and W1-04's *"mere paas transaction ID ya UTR
  nahi hai"* (t4, the `stated_unknown` marker never recorded).

**Reading the user's own words with a deterministic parser is not the same act as trusting the output
of a call that failed.** The first is the brief's standing instruction ("prefer deterministic Python,
because the fallback path runs often"); the second is what the test is really protecting.

`_refresh_workflow` is kept on this path too: it is pure with respect to the profile, it only ever
derives from state that just became *more* complete, and without it a fact recovered by the backfill
would not reach `readiness` (or the response's `suggested_action`) until the next successful turn.
It passes the same `safety` object and the same `document_routing_allowed` gate as everywhere else.

**What was lost by changing the test:** the literal guarantee "the profile dict is identical after a
429". It is replaced by the stronger and more useful form — *nothing already known is changed* —
asserted field by field, plus a new lock that the deterministic layer does **not** overwrite an
existing amount even when the turn text contains a different one.

---

## H7 — `opposite_party_name` captured in 0 of 8 cases; every case pinned at `UNDERSTANDING_CASE`

**Problem.** `compute_blocking_missing_facts` documented itself as *"a fact is blocking only when the
next practical action cannot be executed without it"* and then appended **every** unresolved fact
(`case_readiness.py`, the old `else` branch). For CONSUMER that is 6–8 facts, all of them, before
anything could happen. Combined with `READY_FOR_LEGAL_GUIDANCE` being unreachable for a classified
case, the effective ladder was `UNDERSTANDING_CASE → READY_FOR_ACTION`, a cliff no real conversation
crossed: readiness was `UNDERSTANDING_CASE` on 26 of wave 1's 31 turns and `PRE_INTAKE` on the other 5.

**What I changed.**

*Already in place from the previous session:*
* `case_readiness.compute_blocking_missing_facts` — the non-GENERAL, non-CYBER_FRAUD branch now
  delegates to `action_planner.plan_next_action(profile).blocking_missing_facts`, minus
  `stated_unknown`. CYBER_FRAUD and GENERAL branches unchanged.
* `case_readiness.compute_readiness` — `domain_registry.issue_understood(profile)` is now the
  `UNDERSTANDING_CASE` test, and `blocking_missing` returns `READY_FOR_LEGAL_GUIDANCE` for a
  classified case (`UNDERSTANDING_CASE` for GENERAL, byte-identical to before).
* `fact_extraction._party_candidate` — structural frames (corporate suffix, Hinglish postposition,
  English transactional/adversarial, role apposition, brand-adjacent) with a closed rejection list,
  plus `opposite_party_role` capture from the product's own role vocabulary.

*Added this session — two real defects the 8-case replay exposed:*

1. **An action with `required_fact_keys=[]` scoped the gate to the empty set.** Both
   POLICE_COMPLAINT actions declare none (`action_planner.py:194-213`), so once triage released, a
   police case sailed to `READY_FOR_ACTION` with **nothing on the record** and
   `POLICE_COMPLAINT_BNSS` was offered. Confirmed on a W1-05 replay: the document appeared on turn 3.
   `compute_blocking_missing_facts` now treats an empty requirement set as *"the planner has nothing
   to say here"*, not *"nothing is needed"*, and falls back to the domain's own understanding facts.
   Guidance still flows (the case sits at `READY_FOR_LEGAL_GUIDANCE`); only the action rung and the
   paperwork behind it wait.
2. **Two party-extraction false positives** wrote garbage into `opposite_party_name`, which then
   feeds documents and case titles:
   * W1-05 t3 *"Aur SP ko complaint kaise karun?"* → `"Aur SP"`.
   * W1-07 t2 *"...vacate by Sunday. Our agreement..."* → `"Sunday. Our"`.

   Both slipped through because `_rejected_party` only rejected when **every** token was a
   role/place/month/weekday word, and their second token was an ordinary word. Fixed by (i) rejecting
   when the **head** of the run is one of those (a proper-noun run never begins with "aur" or
   "Sunday"), (ii) adding Hinglish/English function words to the closed list, and (iii) cutting a
   candidate at the first full stop followed by whitespace, so a run cannot cross a sentence
   boundary (`_PROPER_RUN` allows `.` inside a token so "Pvt. Ltd." survives, which is what let
   "Sunday. Our" match).

**Deviations from the plan.** The empty-`required_fact_keys` fallback is not in Agent 1's plan; Agent 1
predicted the POLICE_COMPLAINT hazard in §5 but proposed no mechanism. This is the mechanism.
Agent 1's §2.1(f) prompt-leak strip (`next_fact_candidates[*]["key"]`) is **not implemented** — see
"Deliberately not done".

**Residual risk.** The action planner is now consulted from `case_readiness` on every turn, including
safety and GENERAL turns (GENERAL short-circuits before reaching it). It is pure. Party capture is
structural, so a new sentence shape can still produce a false positive; every structural guess is
stored with `confidence: 0.6, needs_confirmation: True` and never overwrites a model- or
user-supplied value, so the model can correct it.

---

## H6 — amounts dropped in both directions

**Problem.** W1-01 t4's whole turn was `18499` and `disputed_amount` stayed `0.0` while the reply
called it an order number. W1-02 t2's *"Deposit Rs 85000 tha"* carried the marker the parser requires
and still landed nowhere. The parser was never the bug: it was never *called*, because
`_extract_entities_into_profile` had exactly one call site, reachable only when the provider is not
configured.

**What I changed** (previous session, verified this session):
* `app/services/fact_extraction.py::backfill_facts` — the shared deterministic backfill, wired into
  all three paths: `conversation_agent._extract_entities_into_profile` (offline),
  `llm_conversation.process_turn` right after `_apply_extraction` (live), and
  `llm_conversation._rate_limit_fallback` (429). Fills only what is empty; `0`/`0.0` counts as empty
  for money, matching `_apply_extraction`.
* A bare-number turn binds to a validated MONEY/IDENTIFIER pending if there is one, and otherwise
  records `key_facts["unbound_numeric"]` rather than guessing.
* The loose category keyword heuristics (`"refund" → seller_contacted = True`) stayed in
  `conversation_agent`, on the offline path only, exactly as the plan required.

*Added this session:* **HOUSING_TENANT gained a non-required `disputed_amount` fact**
(plan §2.2(3), which the previous session had not done — `app/domains/` was entirely unmodified when
I picked this up). Tenancy had no money fact at all, so `compact_context` never listed the deposit
figure as a candidate and the model had no structural cue that it was wanted — while the tenancy
escalation action in `action_planner.py` already **required** `disputed_amount`. The domain
definition and the action planner disagreed. It is `required_for_understanding=False` so it does not
re-pin tenancy cases.

**Deviations.** `_detect_conflicts` was left in `ConversationalLegalAgent.process_turn` and not moved
into the backfill, as instructed. Plan §2.1(e) — adding `opposite_party_role` to `ExtractedCaseFacts`
and to each domain as a `FactDefinition` — was **not** done (also a previous-session gap). The role is
captured and stored in `key_facts["opposite_party_role"]`, which is all the reply layer needs and all
that definition-of-done #6 requires; the schema addition only matters once the *model* is asked to
emit it, which nothing does yet.

**Residual risk.** W1-02's *"Ek correction — deposit actually Rs 120000 tha, 85000 nahi"* still does
not update the stored `85000`. That is the non-overwrite rule working as specified (plan §2.2, and
locked by definition-of-done #11); correcting a stated fact is M1/S3's job, not this cycle's.

**Known gap, verified:** definition-of-done #10's first half does **not** hold for CONSUMER. A bare
`18499` answering a money question cannot bind, because CONSUMER has **no MONEY-typed domain fact** —
`disputed_amount` is a direct profile field, not a `FactDefinition`, so `_pending_numeric_target`
finds nothing and `valid_for_case` rejects the pending. The second half does hold
(`key_facts["unbound_numeric"] == {"value": "18499", …}`, and the number is not guessed at). Binding
would work today for EMPLOYMENT (`monthly_salary`) and CYBER_FRAUD (`disputed_amount`,
`transaction_id`). Giving CONSUMER a money fact is the same one-line change I made for tenancy, but
CONSUMER is the domain with the most tests hanging off its candidate ordering and I did not want to
reorder its question ranking in the same cycle as the ladder rewrite. Recommended for the next cycle.

---

## H8 — a month without a year becomes an invented year

**Problem.** W1-06 t3 said *"I resigned in July"* with no year; the reply asserted "July 2023", then
computed a deadline and told the user their live claim was time-barred.

**What I changed.**

*Previous session:* `app/services/date_facts.py` with `normalize_date_phrase` (exact / month_year /
month_only / year_only / relative), `strip_unstated_year`, `years_in` and `same_date_ignoring_year`;
`fact_extraction.enforce_date_year_invariant` applies the containment rule on every path and logs at
INFO when a year is stripped.

*Added this session — a genuine regression the invariant introduced, and the H8 document guard:*

1. **The year invariant fought the extraction prompt on every turn.** The invariant strips a year the
   user never wrote, and the prompt re-asserts the full date on the next turn, so stored `"10 August"`
   vs model `"10 August 2026"` looked like a **factual conflict** in `_apply_extraction` — which
   hijacks the whole turn. Effect: after one tenancy turn, *every subsequent document request was
   silently swallowed* (this was the root cause of the reported
   `test_gemini_turn_uses_recent_history_and_updated_workflow_context` failure, which was a code bug,
   not a pinned expectation). `same_date_ignoring_year` existed but had **no call site**. It is now
   wired into `_apply_extraction`: a DATE-typed candidate that differs from the stored value only by a
   year is not a conflict; it is let through, and `backfill_facts → enforce_date_year_invariant`,
   which runs immediately after, re-applies the rule against what the user has actually written. A
   year stated later is recovered that way; one still unstated is stripped again. A `confirmed` value
   is still protected by the guard above it.
2. **Stated years are now remembered on the case** (`key_facts["stated_years"]`, via
   `record_stated_years`). The invariant is a containment check against the user's own words, and the
   only text available at call time is the trimmed 8-message history window — so a year stated on
   turn 1 would scroll out and look invented on turn 9. Accumulating the evidence on the case makes
   the rule window-independent and monotone.
3. **`_missing_document_fields` now treats a `year_known is False` date as missing** — the S5b-adjacent
   half of H8 that Agent 1 asked for. `"July"` is a non-empty string and used to count as present,
   which meant it could be rendered straight into a date slot on a legal document.

**Deviations.** Agent 1's §2.3(3) asks for a `date_precision` map in `_compact_case` and one extra
line in `CHAT_SYSTEM_PROMPT`. **Not done** — see "Deliberately not done". The deterministic record
(`fact_metadata[...]["year_known"]`) exists and is honest, which is the precondition S6 actually
needs; the prompt line is a supplement, and I did not want to spend the prompt-surface change on it in
the same cycle that rewrote the readiness clause.

**Residual risk.** Stripping a year the model supplied is a deliberate loss of (fabricated)
information, logged at INFO. `S6` must read `year_known` before any limitation arithmetic.

---

## M4 — `user_state` null in all 8 cases; Devanagari city not captured

**Problem.** `user_state` was `null` on every turn of all 8 cases, including the four where the city
*was* captured. The only city table in the backend was a 16-entry Latin-only dict inside the
dead-on-the-live-path extractor.

**What I changed.**

*Previous session:* `app/services/jurisdiction.py` — states + UTs and ~100–150 cities with Devanagari
forms and alternate romanisations; `resolve_jurisdiction(text)` and `state_for_city(city)`.
`backfill_facts` derives over the **profile**, not only the message, so a city that arrived on an
earlier turn from the model yields a state. First-city-wins preserved. An unrecognised city leaves
`user_state` `None` — never a guess.

*Added this session:* a safety turn returns before extraction, so W1-05's *"मैं लखनऊ में रहता हूँ"* —
stated in the same breath as the death threat — was never recorded and the case had **no jurisdiction
for the rest of the conversation**. `_process_safety_turn` now calls a new, narrow
`backfill_jurisdiction(text, profile)`. Only the jurisdiction lookup runs there, **not** the full
backfill: a threat narrative is the worst possible place to guess a counterparty, an amount or an
incident date, and jurisdiction is inert — it never unlocks an action or a document on its own. That
turn already nulls every document affordance before returning.

**Verified:** `resolve_jurisdiction("मैं लखनऊ में रहता हूँ") == ("Lucknow", "Uttar Pradesh")`;
`("I am from Indore…") == ("Indore", "Madhya Pradesh")`; `("I live in Kotdwar") == (None, None)`;
W1-02's Jaipur→Bengaluru move leaves `user_city == "Jaipur"`, `user_state == "Rajasthan"`.

**Residual risk.** The table is reference data with a safe default — a missing entry fails closed and
silent (`user_state` stays `None`), never to a wrong jurisdiction. This is why a table is right here
and was wrong for cycle 2's threat verbs; the code comment says so.

---

## S5b — the readiness half of the document gate (finding C3)

**Problem.** `document_routing_allowed` calls itself *"the single gate every document recommendation
must pass"* and was consulted at exactly **one** site, inside `_refresh_workflow`, and only to
suppress the *proactive* recommendation. A user-**requested** document never passed through it. W1-03
turn 1 — *"my startup in Bengaluru hasn't paid me for 3 months. just draft me the legal notice right
now"* — got *"I can prepare this document…"* in 3.01s with `readiness=UNDERSTANDING_CASE`,
`opposite_party_name=null`, `disputed_amount=0.0`.

**What I changed** (all this session):

1. **One gate, every branch.** `_document_affordance_blocked` is replaced by
   `_document_block_reason(profile, safety) -> None | "SAFETY" | "READINESS"`, which delegates to
   `document_routing_allowed`. All three affordance branches (`_is_optional_skip` resume, the offline
   path, the live handoff branch) call it. `_document_affordance_blocked` remains as a thin bool
   wrapper.
2. **"Not yet" is a different answer from "not now".** The crisis deferral deliberately names no
   paperwork — right after a self-harm disclosure, wrong for W1-03. New
   `_document_not_ready_reply` acknowledges the request, says the document can be prepared, and names
   the one or two blocking facts in plain language in the user's script, using
   `FactDefinition.meaning` (the product's own human phrasing). **No fact key ever leaves that
   function** — W1-02 t2 told a user verbatim it needed *"landlord ya property manager ka naam
   (opposite_party_name)"*. Ordering is preserved: crisis and RED are checked first, so the crisis
   copy still wins.
3. **The request is remembered and resumes itself.** `document_request` is parked as
   `{"intent": "USER_REQUESTED", "status": "NEEDS_CASE_FACTS", "document_type": …,
   "blocking_facts": [...], "message": …}` and is picked up automatically on the turn readiness
   reaches `READY_FOR_ACTION`, mirroring the existing `SAFETY_PAUSED` resume. A user who asked on turn
   1 does not have to ask again.
4. **"Unsupported" still beats "not yet".** If `resolve_requested_document` cannot resolve a document
   for this case, the existing `BLOCKED` refusal keeps precedence — it is more precise and more
   useful, and readiness has nothing to add to it. This is what keeps
   `test_document_request_without_prepare_action_never_claims_a_pdf_is_queued` green **unchanged**,
   per Agent 1's ruling.
5. **The API is gated too.** `assess_document_generation` takes a new `readiness_blocked: bool`
   (symmetric with `safety_blocked`) and appends a blocker;
   `main._assess_case_document` computes it from `profile.readiness`.
   `POST /api/v1/documents/generate` below `READY_FOR_ACTION` is now 422. `profile is None` keeps
   legacy/imported cases behaving exactly as before rather than failing closed on state we cannot
   read.
6. **`CHAT_SYSTEM_PROMPT` line 104 is gone** from both `groq_provider.py` and `gemini_provider.py` —
   it was C3 written down as policy. It is replaced by the `READY_FOR_LEGAL_GUIDANCE` clause the
   prompt had never seen (Agent 1 §5): give substantive preliminary guidance, name the next practical
   step in prose, ask at most one high-value question, do not offer or name a document.

**Product-behaviour change, stated plainly so a human can overrule it:** a user who demands a document
on turn 1 no longer gets a confirmation modal on turn 1. They get a plain-language explanation of what
is still needed, and the request resumes by itself once it is available. That is the intended
correction of a CRITICAL finding.

**Deviations.** `NEEDS_CASE_FACTS` is a new `document_request.status` value. Checked the frontend
first as the plan asked: `frontend/src/lib/api.ts:336` types it as a bare `string` and no component
switches on it, so nothing can crash.

**Residual risk.** `recommended_next_action` is still `None` at `READY_FOR_LEGAL_GUIDANCE`
(`_refresh_workflow` nulls it whenever `document_routing_allowed` is False), so the UI shows a case
that has visibly progressed but offers no action. Acceptable for this cycle; noted as follow-up, as
Agent 1 said.

---

## Hard gate 1 — 8-case offline replay, no exception

Every wave-1 case replayed from `qa-pipeline/wave-1/transcripts.json`, per-turn snapshots
(`replay()` style — `process_turn` returns the one live mutated profile), in **two** modes:

* **offline** — `NoCallProvider(configured=False)`; `provider.calls == []` asserted on every case.
* **classified** — a configured stub that supplies the transcript's real category and **no facts at
  all**, so the entire record comes from the deterministic layer. This is what actually drives
  `_refresh_workflow`'s tail, `document_offer()`, the `DOCUMENT_CONFIRMATION` path in
  `_finish_chat_response`, and `_missing_document_fields` against live state for the first time.

**16 replays, 62 turns, `EXCEPTIONS: NONE`.**

Readiness reached, per turn (classified mode; offline shown where it differs):

| case | domain | t1 | t2 | t3 | t4 | t5 |
|---|---|---|---|---|---|---|
| W1-01 | CONSUMER | UNDERSTANDING | UNDERSTANDING | **READY_FOR_ACTION** | READY_FOR_ACTION | READY_FOR_ACTION |
| W1-02 | HOUSING_TENANT | **RFLG** | RFLG | RFLG | RFLG | — |
| W1-03 | EMPLOYMENT | UNDERSTANDING | UNDERSTANDING | UNDERSTANDING | UNDERSTANDING | — |
| W1-04 | CYBER_FRAUD | UNDERSTANDING | **RFLG** | RFLG | RFLG | — |
| W1-05 | POLICE_COMPLAINT | UNDERSTANDING | **RFLG** | RFLG | RFLG | — |
| | *(identical in offline mode)* | | | | | |
| W1-06 | CONSUMER | UNDERSTANDING | UNDERSTANDING | UNDERSTANDING | UNDERSTANDING | — |
| W1-07 | HOUSING_TENANT | UNDERSTANDING | UNDERSTANDING | UNDERSTANDING | — | — |
| W1-08 | CYBER_FRAUD | **RFLG** | RFLG | RFLG | — | — |

(RFLG = `READY_FOR_LEGAL_GUIDANCE`. Offline mode differs only where the offline classifier leaves a
case GENERAL: W1-01, W1-03 and W1-04 stay GENERAL offline and therefore stay at
`UNDERSTANDING_CASE` / `PRE_INTAKE`.)

Facts recovered that wave 1 lost:

| case | before | after |
|---|---|---|
| W1-01 t3 | Flipkart and Jaipur lost to a 429 | `opposite_party_name="Flipkart"`, `user_city="Jaipur"`, `user_state="Rajasthan"` |
| W1-02 t2 | `disputed_amount` 0.0 | `85000.0`; Jaipur/Rajasthan held across the Bengaluru move |
| W1-04 t2 | — | `47500.0` |
| W1-06 t1 | `user_state` null | Pune → Maharashtra |
| W1-05 t1 | Lucknow lost to the safety-turn early return | Lucknow → Uttar Pradesh |
| W1-07 t1 | Gurugram lost to the triage hijack | Gurugram → Haryana, `46000.0` |
| W1-08 t1 | `user_state` null | Indore → Madhya Pradesh |
| W1-03 t1 | — | Bengaluru → Karnataka, `145000.0` at t2 |

**Document affordances across all 62 turns: exactly one case.** W1-01 in classified mode reaches
`READY_FOR_ACTION` at t3 (Flipkart named) and a `FORMAL_LEGAL_NOTICE` is recommended; at t5 the user's
"yes" answers the resulting `DOCUMENT_CONFIRMATION` and the request opens as `NEEDS_REQUIRED_FIELDS`
(it asks for the missing fields rather than generating — `disputed_amount` is still 0). This is a
visible behaviour change and is flagged here rather than left to be discovered, per Agent 1 §5.

**W1-03 — the C3 case — produces no document affordance on any turn in either mode.** Offline it is
`BLOCKED` (GENERAL has no matching template); classified it is `NEEDS_CASE_FACTS`, with no
`open_confirmation_modal`, no `recommended_doc_type`, and a reply that names the missing facts in
plain language.

## Hard gate 2 — W1-05 safety-brake proof

**This gate initially FAILED and required a code fix.** On the first replay, W1-05 (death threat in
Lucknow) reached `READY_FOR_ACTION` at t2 and was offered `POLICE_COMPLAINT_BNSS` at t3 and t4, in
both modes. The mechanism was exactly the one Agent 1 predicted: POLICE_COMPLAINT's two actions
declare `required_fact_keys=[]`, so once `compute_blocking_missing_facts` became action-scoped it
scoped to the empty set. The RED brakes never engaged because this is an **AMBER** case
(`profile.risk_level` had already dropped to GREEN by t3 and `safety.safety_level` for that turn is
GREEN; only the durable `safety_status.safety_level` is AMBER), and `safety_triage_complete` was True
by t3. The brakes held — they were simply never reached, because nothing else was stopping the case.

After the `required_fact_keys=[]` fallback described under H7:

```
W1-05 [offline]     t1 UNDERSTANDING_CASE  t2 READY_FOR_LEGAL_GUIDANCE  t3 RFLG  t4 RFLG
W1-05 [classified]  t1 UNDERSTANDING_CASE  t2 READY_FOR_LEGAL_GUIDANCE  t3 RFLG  t4 RFLG
      doc=None  recommended_next_action=None  pending=None  document_request=None  modal=False
      city=Lucknow  state=Uttar Pradesh  party=None
      on every turn of both replays.
```

This is now a permanent lock, not a scratch-script finding:
`tests/test_cycle3_verification.py::test_w1_05_death_threat_is_never_offered_a_document`
replays W1-05 verbatim and asserts, on **every** turn, that `recommended_doc_type`,
`recommended_next_action` and `document_request` are all empty, that no pending is a
`DOCUMENT_CONFIRMATION`, and that no `suggested_action` carries `open_confirmation_modal` — plus
`test_an_action_requiring_no_facts_does_not_unblock_the_case`, which locks the mechanism directly.

**W1-05 offers no document, in either mode, on any turn.** The case still climbs to
`READY_FOR_LEGAL_GUIDANCE`, so the user gets the BNSS guidance they asked for — the fix withholds the
paperwork, not the help.

No case in any replay reaches `READY_FOR_ACTION` while its safety assessment is RED or while
`safety_triage_complete` is unset.

---

## Tests added / changed, with justification for every changed assertion

### Changed

**`tests/test_llm_conversation.py`**

1. `test_salary_notice_request_bypasses_proactive_case_readiness_without_promising_pdf`
   → **renamed** `test_salary_notice_request_is_allowed_once_the_case_is_ready_without_promising_pdf`.
   *Its name was the bug.* The fixture sets `opposite_party_name`, `unpaid_months`,
   `disputed_amount`, `employee_role`, city and state; EMPLOYMENT's `formal_demand_sent` action
   requires only `opposite_party_name` and reports `monthly_salary`, `hr_contacted`,
   `employment_proof_available` as explicitly **non-blocking**; every SALARY_DEMAND_NOTICE required
   field is present. Verified directly: `plan.status == READY`, `plan.blocking_missing_facts == []`,
   `missing_document_fields == []`. The document is *earned*, not bypassed.
   * `readiness == "UNDERSTANDING_CASE"` → `"READY_FOR_DOCUMENT"` (×3).
   * `recommended_next_action is None` → a `SYSTEM_SUGGESTED` `PREPARE_DOC`.
   * `recommended_doc_type is None` → `"SALARY_DEMAND_NOTICE"`.
   * **Unchanged, verbatim:** the intake-missing assertion (those facts are still asked for), no PDF
     promise (`"queued" not in reply`), no model-drafted notice, `open_confirmation_modal is True`,
     `document_request["status"]`, `missing_document_fields == []`, `optional_skipped`,
     `provider.response_context is None`.

2. `test_salary_pdf_request_opens_confirmation_without_unrelated_followups`
   * `readiness == "UNDERSTANDING_CASE"` → `"READY_FOR_DOCUMENT"`, same justification.
   * **Added** `assert response.case_profile.user_state == "Rajasthan"` — the fixture never sets it;
     M4's city→state derivation supplies it from Jaipur. This documents *why* the case now clears
     jurisdiction, and locks M4 on a real path.
   * Everything else unchanged.

3. `test_consumer_follow_up_context_supports_direct_guidance_without_reasking_known_facts`
   * `context.readiness == "UNDERSTANDING_CASE"` → `"READY_FOR_ACTION"`. Traced turn by turn:
     t1 UNDERSTANDING → t2 READY_FOR_LEGAL_GUIDANCE (platform known) → t3 READY_FOR_ACTION (seller
     named, product known). `plan.status == READY`, `blocking_missing_facts == []`,
     non-blocking `['incident_date', 'invoice_available', 'user_state']`. The seller is named, the
     product and timing are known, the seller has been contacted and has not replied, and the desired
     outcome is stated — a consumer grievance letter *is* the next executable action.
   * `recommended_doc_type is None` → `"FORMAL_LEGAL_NOTICE"`.
   * **Added** `assert response.case_profile.missing_document_fields` and a second readiness assert —
     the case stops at `READY_FOR_ACTION` and is **not** promoted to `READY_FOR_DOCUMENT`, because
     `user_name`, `user_city` and `disputed_amount` are still missing. This is the assertion that
     proves the fix did not simply open the floodgates.
   * All fact assertions and the `next_fact_candidates` no-re-asking assertion unchanged.

4. `test_structured_document_intent_routes_natural_request_without_keyword_tree`
   * **Fixture only:** added `profile.opposite_party_name = "Example Employer"`. This test is about
     *structured intent routing*; without the employer name it would be measuring the S5b gate
     instead. No assertion changed.

5. `test_immediate_danger_pauses_and_preserves_explicit_document_request`
   * **Fixture only:** added `profile.opposite_party_name = "Example Employer"`, so the resumed
     request also clears the readiness gate. The test locks the *safety pause and resume*, which is
     unchanged. No assertion changed.

**`tests/test_pending_interaction.py`**

6. `test_document_confirmation_uses_only_supported_case_document`
   * **Fixture only**, per Agent 1's ruling ("fix the fixture, not the gate"). It hand-constructed a
     `DOCUMENT_CONFIRMATION` on a profile with no facts at all — a state production cannot reach,
     because a `DOCUMENT_CONFIRMATION` exists only when `document_offer()` read a `PREPARE_DOC`
     `recommended_next_action` that `document_routing_allowed` had already passed. New
     `_earned_tenancy_profile()` helper mirrors the reference shape in
     `test_live_flow_records_only_an_eligible_document_offer`. Every behavioural assertion kept,
     including the decline branch.

7. `test_live_flow_records_a_ranked_question_then_resolves_the_next_yes`
   * `second.case_profile.pending_interaction is None` → asserts a `DOCUMENT_CONFIRMATION` for
     `TENANT_DEMAND_NOTICE` plus `readiness == "READY_FOR_ACTION"`. **Justification:** that "yes"
     supplied `deposit_payment_proof_available`, the last fact the tenancy demand letter was waiting
     for. The profile is then identical to the one
     `test_live_flow_records_only_an_eligible_document_offer` asserts *should* produce exactly this
     offer. The assertion's real content — "the resolved fact pending is gone and the fact is not
     re-asked" — is preserved by the untouched `next_fact_candidates` assertion above it; `is None`
     was only ever true because before the ladder fix nothing could follow a fact pending.

**`tests/test_api.py`**

8. `test_explicit_salary_document_generates_from_understanding_case_with_optional_skipped`
   → **renamed and restructured** `test_explicit_salary_document_waits_for_case_facts_then_generates_with_optional_skipped`.
   The old name said the quiet part out loud: a case whose only content was *"my employer has not paid
   my salary for four months"* got a confirmation modal on turn 2 with `readiness=UNDERSTANDING_CASE`.
   That is C3 end-to-end, encoded as expected behaviour, and is the same shape as W1-03.
   * **New first half (the C3 lock):** the turn-2 request yields `suggested_action is None`,
     `document_request["status"] == "NEEDS_CASE_FACTS"`, a reply containing no `_` (no fact key
     leaked), and `POST /api/v1/documents/generate` returns **422**.
   * **New middle:** a turn naming the employer resumes the parked request by itself.
   * **Second half unchanged, verbatim:** `suggested_action` type/intent/`open_confirmation_modal`,
     `documents == []`, no "queued", the assessment's `missing_optional_fields`/`ready_to_generate`,
     the real PDF (`%PDF`) and DOCX (`PK`) generation, and the `UNSUPPORTED_CONTRACT` → 422 case.

**`tests/test_groq_rate_limits.py`**

9. `test_final_extraction_rate_limit_stops_the_turn_and_preserves_case_state`
   → **renamed** `..._preserves_known_facts`. Full ruling above. Removed the
   `_refresh_workflow` poison mock and the byte-identity assertion; replaced with field-by-field
   checks that `disputed_amount`, `pending_conflict`, `pending_interaction`, `case_id` and `category`
   are all unchanged, plus `response.case_profile is profile`. **Kept:** `agent.process_turn` and
   `service._apply_extraction` poison mocks, `_verified_sources` poison mock, `llm_mode`,
   `quick_replies`, the await counts and all three `assert_not_awaited`.

### Added

10. `tests/test_groq_rate_limits.py::test_rate_limited_turn_still_records_facts_the_user_stated` —
    **new.** W1-01 t3 verbatim through a 429: `opposite_party_name == "Flipkart"`,
    `user_city == "Jaipur"`, `user_state == "Rajasthan"`, `llm_mode == "limited_demo"`, exactly one
    provider attempt (the one that raised). This is the positive half of the ruling above, and is the
    regression lock for the defect that lost 5 of wave 1's 31 turns.

11. `tests/test_cycle3_verification.py` — **new file, 15 tests.** All offline;
    `provider.calls == []` asserted wherever a provider stub is used. It carries the two hard gates
    permanently, plus the definition-of-done checks that had only existed as a scratch script:
    * **Safety:** the W1-05 replay (no document on any turn, `READY_FOR_LEGAL_GUIDANCE` reached,
      Lucknow/Uttar Pradesh captured, `"Aur SP"` not recorded as a party) and the
      empty-`required_fact_keys` rule directly.
    * **H7:** blocking facts equal `plan.blocking_missing_facts ∩ intake` for CONSUMER, EMPLOYMENT
      and HOUSING_TENANT; a case with nothing known stays `UNDERSTANDING_CASE`; the middle rung is
      reachable and grants no document.
    * **S5b/C3:** W1-03 t1 verbatim earns no document and leaks no fact key; a parked request resumes.
    * **H8:** year stripped when unstated, kept when stated, kept when the statement has scrolled out
      of the history window; a re-asserted year is not a conflict; `"3 mahine pehle"` resolves against
      a fixed `today`; a `year_known is False` date counts as missing for a document.
    * **H6/M4:** the backfill never overwrites a known amount; Devanagari and unknown-city
      jurisdiction; profile-level city→state derivation; first-city-wins across a move.

12. `tests/test_pending_interaction.py::test_live_flow_records_a_ranked_question_then_resolves_the_next_yes`
    — **fixture only, second change:** added `profile.disputed_amount = 50000`. The new tenancy
    `disputed_amount` fact ranks as `CORE_EVENT_FACTS` and would otherwise be the first candidate,
    displacing `deposit_payment_proof_available`. This test locks the ranked-question *mechanism*, not
    which fact ranks first, so the fixture supplies the deposit figure any real tenancy case has. No
    assertion about the mechanism changed.

**No test was weakened or deleted.** Every changed assertion is either (a) a readiness/affordance
expectation that the fix legitimately moves, with the advancement verified against
`action_planner.plan_next_action` rather than assumed, or (b) a fixture that constructed a state the
system cannot reach.

---

## Deliberately not done

* **Agent 1 §2.1(f) — stripping `key` from `next_fact_candidates` before the prompt payload.** The
  leak is real (W1-02 t2). It is a two-line change per provider but it is an *output/prompt-surface*
  change with no deterministic lock behind it, and the cycle already rewrote the prompt's readiness
  clause. Definition-of-done #7 is therefore **not met**. Recommend it as the first item of the next
  cycle, paired with S4's output guard. Note the new `_document_not_ready_reply` never emits a fact
  key, and the API test now asserts no `_` appears in that reply, so the *refusal* path is clean.
* **Agent 1 §2.3(3) — `date_precision` in `_compact_case` and the extra `CHAT_SYSTEM_PROMPT` line.**
  The deterministic record (`fact_metadata[...]["year_known"]`) exists and is honest, which is the
  precondition S6 needs; the prompt line is a supplement.
* **M5 (monotonic readiness clamp).** Explicitly forbidden by the plan, and not added. A ladder that
  can drop back is currently protective — it re-closes the document gate when context is lost.
* **`test_doc_generator.py::test_document_generation_notice`** — pre-existing and unrelated; left red.
* **N8** — remains strict-xfail, still xfailing.
* Every §3 non-goal (C5/S6, S4, S3, S7, S8) was left alone. `safety_triage.py` was not edited.

## Files changed

Changed by **this session** (the three new service modules and the ladder delegation were already on
the tree from the previous session):

```
backend/app/services/case_readiness.py        empty-required_fact_keys fallback
backend/app/services/fact_extraction.py       stated-years memory, party rejection hardening
                                              (head-of-run + sentence boundary), backfill_jurisdiction
backend/app/services/llm_conversation.py      date-conflict fix in _apply_extraction,
                                              _document_block_reason, _document_not_ready_reply /
                                              _document_not_ready_response / _document_gate_response,
                                              _blocking_fact_phrases, NEEDS_CASE_FACTS park + resume,
                                              _missing_document_fields year guard,
                                              safety-turn jurisdiction capture
backend/app/services/document_generation.py   readiness_blocked parameter
backend/app/main.py                           readiness gate on the document API
backend/app/domains/tenancy.py                disputed_amount fact (H6, plan §2.2(3))
backend/app/llm/groq_provider.py              removed the C3 prompt line, added READY_FOR_LEGAL_GUIDANCE
backend/app/llm/gemini_provider.py            same
backend/tests/test_cycle3_verification.py     NEW - 15 tests
backend/tests/test_llm_conversation.py        5 tests (1 renamed)
backend/tests/test_pending_interaction.py     2 tests + a shared fixture helper
backend/tests/test_api.py                     1 test renamed and restructured
backend/tests/test_groq_rate_limits.py        1 test renamed, 1 added
```

No commit, no branch, no push. No browser was launched. No live Groq/LLM call was made — every
replay used `NoCallProvider` or a local stub, and `provider.calls == []` was asserted on all 8 offline
replays. No `.env` or key material appears in this file.
