# Agent 1 — Cycle 3 Plan · Set S2 (fact capture) + S5b (readiness half of the document gate)

Scope: diagnosis and direction only. **Agent 2 implements and may deviate where it has a better
idea** — the "Definition of done" in §4 is binding; the exact regexes, helper names, module names
and code shape below are suggestions, and Agent 2 should say in its handoff wherever it deviated
and why.

Constraints carried from `BRIEF.md`: no commits, no branches, no pushes, no live Groq/LLM calls,
**never launch a browser**, do not touch unrelated uncommitted work, never reduce the passing count.

**Verified baseline on the current tree (I ran this):**
```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
1 failed, 442 passed, 1 xfailed          (444 collected)
FAILED tests/test_doc_generator.py::test_document_generation_notice   (pre-existing, unrelated)
```
I also confirmed that cycle 2's two open safety items were fixed after Agent 4 wrote its triage:
`assess_safety("he threatened to strangle me").is_safety_case is True` (N2-1 closed) and
`assess_safety("I wanted to die").safety_context == SELF_HARM` (N6-1 closed). The single remaining
xfail is N8 at `tests/test_cycle2_verification.py:295`. **S1 is closed. Do not reopen it.**

---

## 0. The one architectural fact that explains most of S2

This is the finding that reframes the whole set, and it is confirmed by grep, not inferred:

```
$ grep -rn "_extract_entities_into_profile" app/ tests/
app/agents/conversation_agent.py:66:        self._extract_entities_into_profile(sanitized_text, profile)
app/agents/conversation_agent.py:230:    def _extract_entities_into_profile(...)
```

**One call site.** It is inside `ConversationalLegalAgent.process_turn`, which
`GeminiConversationService.process_turn` invokes **only** when `self.provider.status.configured`
is False (`llm_conversation.py:201`). On the healthy LLM path and on the 429 path, deterministic
extraction never runs at all. Specifically:

| path | who extracts | deterministic extractor runs? |
|---|---|---|
| provider configured, call succeeds | `provider.extract_case_updates` only | **no** |
| provider configured, 429 → `_rate_limit_fallback` (`llm_conversation.py:526-549`) | nobody | **no** |
| provider configured, other `LLMProviderError`, profile exists | nobody | **no** |
| provider not configured (`limited_demo`) | `_extract_entities_into_profile` | yes |

So the city table, the amount parser, the brand list, the transaction-ID parser and the
stated-unknown detector in `conversation_agent.py:230-344` — all of them — are dead code on the two
paths that carried 31 of 31 turns in wave 1. That is why:

* **W1-01 lost "Flipkart" and "Jaipur" permanently.** Turn 3 carried both and hit a 429
  (`server-evidence.log`, `decision=retry_once` then failure); `_rate_limit_fallback` performs no
  extraction, and `EXTRACTION_SYSTEM_PROMPT` (`groq_provider.py:37`) plus the prompt preamble
  *"Extract the newest user turn using the recent conversation only for context"*
  (`groq_provider.py:161`) mean no later turn re-extracts an older one. A fact stated during an
  outage is lost forever. Five of 31 turns (16%) were lost this way: W1-01 t3, W1-02 t3 (the
  Rs 120000 correction), W1-02 t4, W1-04 t4 (*"mere paas transaction ID ya UTR nahi hai"* — the
  `stated_unknown` marker never recorded), W1-06 t2.
* **Every remaining S2 field depends entirely on the model volunteering it** with confidence
  ≥ 0.55 (`_apply_extraction`, `llm_conversation.py:1109`). The brief's standing instruction —
  prefer deterministic Python, because the fallback path runs often — is currently satisfied by
  nothing in the extraction layer.

**Therefore the backbone of this cycle is one shared deterministic backfill that runs on all three
paths.** Every individual finding below hangs off it.

A second architectural fact, for H7:

> `compute_blocking_missing_facts` (`case_readiness.py:134-184`) documents itself as
> *"A fact is blocking only when the next practical action cannot be executed without it"* — and
> then, outside the CYBER_FRAUD branch, appends **every** unresolved fact
> (`case_readiness.py:179-182`). Meanwhile `NextActionPlanner.evaluate_action`
> (`action_planner.py:230-286`) already computes exactly the documented thing and exposes it as
> `NextActionPlan.blocking_missing_facts`, scoped to the action's `required_fact_keys`. The
> product already has the right computation; the readiness ladder uses the wrong one.

---

## 1. Scope and order within the cycle

Findings: **H7, H6, H8, M4, and S5b (finding C3, readiness half).**

**Order — and it is not the brief's order. Do the extraction plumbing before the unpinning.**

| # | step | why here |
|---|---|---|
| **1** | **Shared deterministic backfill module**, wired into all three paths (delivers the mechanical half of H6, M4, H7's name capture, and hosts H8's date normalizer) | Nothing else in the set is trustworthy until a fact stated in plain text reaches the record on the path the user is actually on. It is also the lowest-risk change in the cycle: it only ever *fills* empty fields. |
| **2** | **H8 year-precision invariant**, built on step 1's date normalizer | Small, rides on step 1, and must land before step 3 so that an advancing case never carries an invented year into the workflow. |
| **3** | **H7 keystone: action-scoped blocking + a reachable `READY_FOR_LEGAL_GUIDANCE`** | This is what actually unpins the 8 cases. Doing it *after* steps 1-2 means cases climb on real facts rather than on an empty record. Highest blast radius in the cycle — do it while the suite is otherwise clean. |
| **4** | **S5b: readiness half of the document gate** | Must land in the same cycle as step 3 and immediately after it. Step 3 is what makes a blanket readiness gate safe (cycle 2's stated objection), and step 4 is what stops step 3 handing out documents the moment cases start climbing. Shipping 3 without 4 is strictly worse than shipping neither. |

**Why not H7 first, as the brief suggests?** Because H7 as written in the report is two different
defects wearing one label, and only one of them is about party names — see §2.1. The half that
unpins the cases is a readiness-ladder defect that does not need a single name to be extracted. The
half that is about names needs step 1 to exist first. Running them in this order costs nothing and
means each step is independently verifiable.

### A correction to the report that Agent 2 must know before writing a name extractor

Report H7 says the user *"named the other party in plain text every time (Flipkart, the Jaipur
landlord, the Bengaluru startup, SBI/PhonePe, the Pune tour operator, the PG operator, the trading
app)"*. **I checked all 8 transcripts. That is true of exactly one of them.**

| case | what the user actually wrote | is there a name? |
|---|---|---|
| W1-01 | "flipkart se maine ek washing machine order ki thi" | **yes — Flipkart** |
| W1-02 | "Mera landlord security deposit wapas nahi kar raha" | no — role only |
| W1-03 | "my startup in Bengaluru hasn't paid me for 3 months" | no — role only |
| W1-04 | "PhonePe se gaya, SBI account hai" | no — those are the **user's own** bank and wallet; the opposite party is an unknown scammer |
| W1-06 | "a tour operator in Pune" | no — role only |
| W1-07 | "co-living PG me rehte hain. Operator ek company hai" | no — role only |
| W1-08 | "I lost Rs 680000 in a trading app scam" | no — role only |

Six of eight named nobody. In W1-04 capturing the named institution as the opposite party would be
**wrong**. The bot in W1-06 t1 and W1-03 t4 *correctly asked* for the name. So "write a better
name regex" would have moved exactly one case, and a brand denylist would be the cycle-2 trap in a
new coat (Indian company names are an open class — the brief is right about that, it is just not
where the leverage is).

The leverage is: **a case must not be pinned at `UNDERSTANDING_CASE` forever merely because the
counterparty has no name yet.** That is a ladder defect, not a vocabulary defect.

---

## 2. Per-finding detail

### 2.1 H7 — `opposite_party_name` captured in 0 of 8 cases; every case pinned at `UNDERSTANDING_CASE`

**Problem.**
* Evidence (profile state, `wave-1/transcripts.json`): `opposite_party_name` is `null` on all 31
  turns of all 8 cases, and `readiness` is `UNDERSTANDING_CASE` on 26 of 31 (the other 5 are
  `PRE_INTAKE`). No case ever reached `READY_FOR_LEGAL_GUIDANCE` or above.
* Field-name leak, W1-02 t2 reply (verbatim): *"Aapke formal notice ke liye landlord ya property
  manager ka **naam** (opposite_party_name) chahiye."*
* Named party actually missed: W1-01, "Flipkart", lost to the 429 on turn 3.

**Root cause — three distinct defects, all confirmed in source.**

1. **The blocking set is not action-scoped.** `case_readiness.py:179-182`:
   ```python
   else:
       for fact in intake_missing:
           if fact in stated_unknown:
               continue
           blocking.append(fact)
   ```
   and `compute_readiness` at `case_readiness.py:201-202`:
   ```python
   if blocking_missing:
       return UNDERSTANDING_CASE
   ```
   `intake_missing` comes from `domain_registry.unresolved_facts(profile)` with
   `required_only=True` — for CONSUMER that is 6-8 facts (`consumer.py:32-48`), for HOUSING_TENANT
   6 (`tenancy.py:31-37`), for EMPLOYMENT 5 (`employment.py:30-35`). **Every one of them must be
   known before the case can leave `UNDERSTANDING_CASE`.** In a real conversation that never
   happens. `opposite_party_name` is merely the first one the user never supplies.
2. **`READY_FOR_LEGAL_GUIDANCE` is unreachable for a classified case.** `compute_readiness`
   (`case_readiness.py:187-212`) reaches that rung only via `risk_level == "RED"` or
   `category == "GENERAL"`. So the ladder for a classified GREEN/AMBER case is effectively
   `UNDERSTANDING_CASE → READY_FOR_ACTION` — a cliff, with the document gate sitting on the far
   side of it. The report flagged this and asked whether it is in scope. **It is in scope, and it
   is the fix.** A three-rung ladder is what lets a case stop being "not understood" without
   simultaneously becoming "ready to send a legal notice".
3. **Name capture has no deterministic path** (see §0) and the model's own extraction is
   single-turn, so a name lost to an outage is lost forever.
   Secondarily, the deterministic extractor that *would* have caught "Flipkart"
   (`conversation_agent.py:277-283`) does so from a 9-item hardcoded brand list — an open class,
   and not the right mechanism even where it runs.
4. **The leak.** `domain_registry.compact_context` (`registry.py:141-150`) puts the raw fact key in
   `next_fact_candidates[*]["key"]`; `llm_conversation.py:337` puts that dict into
   `LLMResponseContext.domain_context`; `groq_provider.chat` (`groq_provider.py:184-191`) serializes
   the whole context into the prompt. `CHAT_SYSTEM_PROMPT` says *"Never expose internal IDs … or
   field names"* — and the model did anyway. A prompt cannot be the only defence against leaking a
   value the prompt itself is handed.

**Proposed fix.**

**(a) `case_readiness.py::compute_blocking_missing_facts` — delegate to the action planner.**
Replace the `else` branch with the plan's own answer:
```python
from app.services.action_planner import action_planner   # no cycle: action_planner imports
                                                         # only app.domains + app.schemas.chat
...
if category == "GENERAL":
    # No workflow exists, so nothing is action-scoped. Preserve today's behaviour exactly.
    return [f for f in intake_missing if f not in stated_unknown]
plan = action_planner.plan_next_action(profile)
required = set(plan.blocking_missing_facts)
return [f for f in intake_missing if f in required and f not in stated_unknown]
```
Keep the existing CYBER_FRAUD branch as-is (it already narrows, and cycle 1/2 tuned it). Keep the
GENERAL branch identical to today so `tests/test_api.py:159` and W1-01's behaviour do not move.

**(b) `case_readiness.py::compute_readiness` — make the middle rung real.** Two edits:
```python
    # after the safety brake, before the blocking check:
    if profile.category != "GENERAL" and not domain_registry.issue_understood(profile):
        return UNDERSTANDING_CASE

    if blocking_missing:
        return READY_FOR_LEGAL_GUIDANCE     # was UNDERSTANDING_CASE
```
`domain_registry.issue_understood` (`registry.py:99-108`) already exists, already returns False for
GENERAL, and already uses each domain's `minimum_context_any_of`. It is currently used only for
`compact_context`. This makes "do we understand the issue?" the `UNDERSTANDING_CASE` test — which
is what the rung is named after — and makes "can the next action be executed?" the
`READY_FOR_ACTION` test. Documents stay gated: `document_routing_allowed`
(`case_readiness.py:215-250`) ends in `readiness_at_least(readiness, READY_FOR_ACTION)`, so
`READY_FOR_LEGAL_GUIDANCE` grants guidance and nothing else.

**(c) Do *not* let a role satisfy `issue_understood`.** Capture the counterparty role (see (d)) but
**do not** add it to any domain's `minimum_context_any_of`, and **do not** demote
`opposite_party_name` from `required_for_understanding`. Both are tempting and both are wrong here:
demoting it would remove it from `intake_missing_facts` and therefore from
`next_fact_candidates`, so the bot would stop asking for the name it still needs for a notice.
Under (a)+(b) the name is still asked, still required by
`action_planner` before `READY_FOR_ACTION`, and still required by `_missing_document_fields`
(`llm_conversation.py:1730-1750`) before a document — it just no longer pins the case.

**(d) Shared deterministic party capture, structural not lexical.** In the new module from §2.2,
`opposite_party_name` is filled only when the model left it empty, using **frames**, not a brand
list:
* corporate suffix: `X (Pvt\.? )?Ltd\.?|Private Limited|Limited|LLP|Inc\.?|Technologies|Solutions|Services|Enterprises|Industries|Travels|Tours|Motors|Hospital|Finance`
* Hindi/Hinglish postposition frames on a capitalised or Devanagari proper-noun run:
  `X se`, `X ne`, `X ko`, `X wale/walon`, `X ka/ki/ke`
* English prepositional frames: `from X`, `against X`, `ordered from X`, `booked with X`,
  `sued by X`
* role + apposition: `landlord X`, `mera landlord X`, `employer X`, `<role> ka naam X hai`
* brand-shaped token adjacent to a domain noun: `X app`, `X portal`, `X se order`

with a **rejection list, which is the genuinely closed class** and is where a list belongs:
role words (`landlord`, `seller`, `employer`, `company`, `operator`, `manager`, `HR`, `startup`,
`PG`, `bank`, `app`, `agent`, `builder`, `owner`, `malik/maalik`, `thekedar`, `dukandar`, plus
Devanagari forms), any city or state from the jurisdiction table in §2.4, the user's own name, and
in CYBER_FRAUD the user's own bank/wallet (that value belongs in `bank_name`, which already exists).
Store a structural guess with `fact_metadata[...] = {"confidence": 0.6, "needs_confirmation": True}`
so the model can confirm it, and **never overwrite** a model-supplied or user-confirmed value.

**(e) Capture the role when there is no name.** New `key_facts["opposite_party_role"]` set from the
closed role vocabulary above (this is the product's own domain vocabulary — landlord / employer /
seller / platform / bank / PG operator / tour operator / trading app / builder). Add
`opposite_party_role: Optional[str]` to `ExtractedCaseFacts` (`llm/contracts.py:22`, additive; the
model is `extra="forbid"` so this is a safe schema addition) and a matching non-required
`FactDefinition` in each non-GENERAL domain so `_apply_extraction`'s
`definitions.get(field)` lookup resolves. Its only jobs are (i) to stop "landlord" being written
into `opposite_party_name`, and (ii) to give the reply layer a natural phrase. It must **not**
satisfy `issue_understood` and must **not** satisfy any `required_fact_keys`.

**(f) Stop leaking the field key to the model.** In `GroqProvider.chat`
(`groq_provider.py:184-191`) and the matching `GeminiProvider.chat`, after
`payload = context.model_dump(mode="json")`, strip `key` from each entry of
`payload["domain_context"]["next_fact_candidates"]`. The backend still reads the key from the
un-stripped `context` object in `_finish_chat_response` (`llm_conversation.py:489`), so
pending-interaction binding is unaffected. This is deterministic and one line per provider.
*(Optional, Agent 2's call: a `re.sub` in the reply that deletes a parenthetical whose content is a
known fact key. Cheap belt-and-braces; the real output guard is S4's job, not this cycle's.)*

**Risk / blast radius — this is the big one in the cycle.**
* **Cases will advance past `UNDERSTANDING_CASE` for the first time.** See §5 for the full list of
  code that has never executed. Cycle 2 found a latent crash exactly this way.
* `tests/test_llm_conversation.py:202`
  (`test_salary_notice_request_bypasses_proactive_case_readiness_without_promising_pdf`) will
  change readiness. Its fixture sets `opposite_party_name`, `unpaid_months`, `disputed_amount`,
  `employee_role`, city and state; EMPLOYMENT's only action requirement is `opposite_party_name`
  (`action_planner.py:171-179`), so under (a)+(b) it becomes `READY_FOR_ACTION` and
  `recommended_next_action` becomes non-`None`. **That is the test earning its outcome instead of
  bypassing the gate — see §2.5.** Its behavioural assertions (no PDF promise, no model draft,
  `open_confirmation_modal`) must all still hold.
* `tests/test_intake_before_document.py:210` (`test_G_tenancy_asks_issue_facts_not_document_fields`)
  must still be `UNDERSTANDING_CASE`: "my landlord isn't returning my deposit" sets none of
  tenancy's `minimum_context_any_of` (`vacating_date`, `landlord_reason`, `opposite_party_name`),
  so `issue_understood` is False. I checked `_extract_entities_into_profile`'s landlord heuristics
  (`conversation_agent.py:358-364`) — `"isn't returning"` sets `landlord_contacted`, **not**
  `landlord_reason`. Agent 2 must not "helpfully" widen that, or this test flips.
* `case_naming._qualifier` (`case_naming.py:49-64`) uses `disputed_amount` → `opposite_party_name`
  → `user_state`. Capturing any of the three changes case titles. M3 is S8's finding; do not fix
  titling here, but expect title assertions to move.
* `action_planner.plan_next_action` will now be called from `case_readiness`, i.e. on every turn via
  `_refresh_workflow`. It is already called on the fallback path
  (`conversation_agent.py:74`); confirm there is no measurable cost and no side effect (it is pure
  except for reading the profile).
* `SafetyAssessment`/`safety_status` untouched. Do not touch `safety_triage.py` this cycle.

**How Agent 3 should verify.**
* Unit, no provider: build a CONSUMER profile with only `product_name` known →
  `compute_readiness(...) == READY_FOR_LEGAL_GUIDANCE`, `document_routing_allowed(...) is False`.
  Add `opposite_party_name` → still `READY_FOR_LEGAL_GUIDANCE` (invoice/seller facts still block
  the action) — or `READY_FOR_ACTION` once the action's `required_fact_keys` are all known; assert
  against `action_planner.plan_next_action(profile).blocking_missing_facts` so the two agree by
  construction.
* Regression lock: a CONSUMER/EMPLOYMENT/HOUSING_TENANT profile with **nothing** known stays
  `UNDERSTANDING_CASE`; a GENERAL profile behaves byte-identically to today.
* W1-03 replay (offline, `NoCallProvider`): turn 1 verbatim
  *"my startup in Bengaluru hasn't paid me for 3 months. just draft me the legal notice right now,
  i don't want questions."* → readiness is **not** `READY_FOR_ACTION`, `suggested_action` is not a
  `PREPARE_DOC` with `open_confirmation_modal`, and the reply is not
  `"I can prepare this document."` (this is the S5b assertion too).
* Party capture: `"flipkart se maine ek washing machine order ki thi"` → `opposite_party_name ==
  "Flipkart"`. `"Mera landlord security deposit wapas nahi kar raha"` →
  `opposite_party_name is None` **and** `key_facts["opposite_party_role"] == "landlord"`.
  `"PhonePe se gaya, SBI account hai"` in CYBER_FRAUD → `opposite_party_name is None`,
  `bank_name` set. `"Bharat Traders Pvt Ltd ne paise nahi diye"` → `"Bharat Traders Pvt Ltd"`.
* Leak: build an `LLMResponseContext` with a populated `domain_context`, call a fake provider's
  `chat`, and assert the serialized prompt payload contains no `"key"` inside
  `next_fact_candidates`, while `_finish_chat_response` still sets a `pending_interaction` whose
  `target_keys` is the right fact.
* **Snapshot per turn.** `process_turn` returns the one live mutated profile (N3-1). Use
  `tests/test_cycle2_verification.py::replay()`; never assert on `responses[k].case_profile` for
  `k < last`.

---

### 2.2 H6 — amounts dropped in both directions

**Problem.**
* W1-01 t4, the whole user turn is `18499`. Reply: *"I see you've mentioned the order number
  **18499**"*; profile `disputed_amount` stays `0.0`.
* W1-02 t2, *"Deposit Rs 85000 tha"* — carries the `Rs` marker the extractor requires — profile
  `disputed_amount` stays `0.0`, while the same turn's reply renders the figure correctly
  elsewhere in the conversation.

**Root cause.**
1. **Primary, both cases: §0.** `_amount_from_text` (`conversation_agent.py:183-218`) parses both
   strings correctly — I read it line by line: `"Rs 85000"` matches the marker branch at `:198`,
   and `"18499"` matches the bare `\b(\d{4,8})\b` branch at `:212` (and is not a year, so it is not
   rejected). **The parser is not the bug. It is never called on the path these turns took.**
2. **W1-02 secondarily: HOUSING_TENANT has no money fact at all.** `tenancy.py:31-39` defines
   `opposite_party_name`, `vacating_date`, `landlord_reason`, `landlord_contacted`,
   `rental_agreement_available`, `deposit_payment_proof_available`, `user_state`,
   `property_address`, `user_name` — and nothing for the deposit amount. So `compact_context`
   never lists an amount as a candidate, `_compact_case` reports
   `disputed_amount: null`, and the model has no structural cue that a deposit figure is a fact
   the case wants. Meanwhile `action_planner`'s tenancy escalation action *does* require
   `disputed_amount` (`action_planner.py:135-143`) — the domain definition and the action planner
   disagree.
   (For contrast, the amount was captured on the LLM path in W1-03 ₹145000, W1-04 ₹47500,
   W1-06 ₹62000 and W1-08 ₹680000 — CONSUMER/EMPLOYMENT/CYBER_FRAUD all declare a money fact.)
3. **W1-01 secondarily: a bare number has no referent.** `is_bare_confirmation("18499")` is False
   (`pending_interaction.py:31-33`), so the anti-hallucination wipe is not the cause; there was
   simply no `pending_interaction` to bind it to, the case was `GENERAL` (whose only fact is
   `issue_description`), and the model guessed "order number". Binding a bare answer to the
   question just asked is M1/S3 and stays out of scope — but the record must stop silently
   losing the number.

**Proposed fix.**
1. **New module `app/services/fact_extraction.py`** exposing one entry point:
   ```python
   def backfill_facts(text: str, profile: StructuredCaseProfile) -> None
   ```
   It **only fills fields that are empty** and never overwrites a value the model or the user
   supplied. It contains the *structural* extractors — amount, jurisdiction, party, dates,
   transaction id, bank — moved out of `_extract_entities_into_profile`.
   **It must not contain** the loose category keyword heuristics at
   `conversation_agent.py:353-422` (e.g. the bare substring `"refund"` sets
   `seller_contacted = True`). Those are tolerable on the offline demo path and would be a new
   false-positive source on the live path. Leave them where they are.
2. **Wire it into all three paths:**
   * `conversation_agent._extract_entities_into_profile` calls `backfill_facts` first, then keeps
     its category heuristics (so the offline path is unchanged in behaviour).
   * `llm_conversation.process_turn`, immediately **after** `self._apply_extraction(...)` at
     `llm_conversation.py:317` and **before** `_apply_actions`.
   * `llm_conversation._rate_limit_fallback` (`llm_conversation.py:526`), on the profile, before
     composing the reply — plus a `self._refresh_workflow(profile, ...)` so the recovered facts
     reach readiness. This alone recovers W1-01's Flipkart+Jaipur, W1-02's Rs 120000 correction and
     W1-04's "no UTR" statement. Note the restore of `profile_before_extraction`
     (`llm_conversation.py:404-407`) happens *before* `_rate_limit_fallback` is called, so
     backfilling afterwards is safe and does not resurrect a half-applied extraction.
   Reuse `_amount_from_text` verbatim — it is already regression-locked by
   `tests/test_cycle1_verification.py:401` and cycle 1 verified its narrowing is not a defect.
3. **Add a money fact to HOUSING_TENANT**: `disputed_amount`, `FactValueType.MONEY`,
   `QuestionPriority.CORE_EVENT_FACTS`, `zero_is_unknown=True`,
   `required_for_understanding=False`, `aliases=("deposit_amount",)`. Non-required so it does not
   re-pin tenancy cases; it exists so the registry, `compact_context` and the model all know the
   deposit figure is wanted, and so the escalation action's requirement is satisfiable.
4. **Bare-number turns:** if the whole turn is a number and there is a validated
   `pending_interaction` whose single target fact is `FactValueType.MONEY`, bind it there; if the
   target is `IDENTIFIER`, bind it there. Otherwise **do not guess** — record
   `key_facts["unbound_numeric"] = {"value": ..., "turn_text": ...}` and surface it in
   `LLMResponseContext` so the model asks one clarifying question instead of asserting "order
   number". Silently discarding it (today) and silently calling it an order number (today's reply)
   are both worse than asking.

**Risk / blast radius.**
* Running structural extraction on the live path means the deterministic layer can now *disagree*
  with the model. The "fill only if empty, never overwrite" rule is what keeps this safe — Agent 2
  must hold that line, including for `disputed_amount` where `0.0`/`0` counts as empty
  (`_apply_extraction` already uses that convention at `llm_conversation.py:1123-1128`).
* `_detect_conflicts` (`conversation_agent.py:460-479`) fires a whole `ChatTurnResponse` when a new
  amount disagrees with a stored one. It lives in `ConversationalLegalAgent.process_turn`, **not**
  in the extractor. Do not move it into `backfill_facts` — a live-path amount conflict is S3's
  problem and hijacking a turn here would be a new bug.
* Adding a tenancy fact touches `DomainDefinition.validate_definition` and
  `tests/test_domain_architecture.py`; re-run it specifically.
* A tenancy `disputed_amount` now feeds `case_naming._qualifier`, so tenancy titles gain
  `- Rs 85,000`. Expected; note it (and note M3's Western-grouping latent defect is S8's, not ours).

**How Agent 3 should verify.**
* `backfill_facts("Deposit Rs 85000 tha", tenancy_profile)` → `disputed_amount == 85000.0`.
* Full offline W1-02 replay with a stub provider that returns an **empty** `ExtractedCaseFacts`
  (this simulates the model missing it, which is what happened): after turn 2,
  `disputed_amount == 85000.0`.
* `18499` answering a MONEY-typed `pending_interaction` → `disputed_amount == 18499.0`;
  `18499` with no pending → `disputed_amount == 0.0` **and** `key_facts["unbound_numeric"]` present.
* 429 path: a stub provider raising `LLMRateLimitedError` on a turn carrying
  *"flipkart se maine ek washing machine order ki thi … main Jaipur me hun"* →
  after the turn, `opposite_party_name == "Flipkart"`, `user_city == "Jaipur"`,
  `user_state == "Rajasthan"`, and `llm_mode == "limited_demo"`. **No live call** — assert
  `provider.calls == []` beyond the one that raised.
* Lock the non-overwrite rule: profile already has `disputed_amount == 120000`; a turn saying
  `Rs 85000` must leave it at `120000` and must not raise.

---

### 2.3 H8 — a month without a year becomes an invented year, and the user is told they are time-barred

**Problem.** W1-06 t3, user: *"my current employer in Pune has not released my full and final
settlement after I resigned in **July**."* No year. Reply, verbatim:
> "3 years from the date the amount became due (**July 2023** → deadline July 2026)"
> "**the labour-law claim also crossed its 3-year deadline in July 2026**"
> "stating the amount owed (₹ 90,000), the date of resignation (**July 2023**)"

The year was invented — most likely carried over from the *other* matter in the same conversation,
which the user had dated March 2023 — and the user was then told their live F&F claim is
time-barred.

**Root cause.**
1. There is **no date normalizer anywhere in the backend.** `incident_date` and `vacating_date` are
   free-text `Optional[str]` on the profile (`schemas/chat.py`) and free-text
   `Optional[str]` in `ExtractedCaseFacts` (`llm/contracts.py:34-35`). Whatever the model writes is
   stored verbatim; nothing checks that the year it contains was ever said.
2. The deterministic extractor's date regex (`conversation_agent.py:284-290`) requires a day
   number (`\d{1,2}(?:st|nd|rd|th)?\s+(?:MONTHS)`), so a bare "July" does not match. It therefore
   never invents a year — but it also never records the month, and, per §0, it does not run here
   anyway.
3. There is no notion of date *precision* to carry into the workflow, so a year-less date is
   indistinguishable from a confirmed one at every downstream consumer.

**Proposed fix — a deterministic invariant, not a prompt instruction.**
1. **`app/services/date_facts.py`**, `normalize_date_phrase(text, today) -> DateFact` with fields
   `value`, `precision ∈ {exact, month_year, month_only, year_only, relative, unknown}`,
   `year_known: bool`, and an ISO `resolved_date` only when `year_known` is True.
   Handles `dd/mm/yyyy`, `dd Mon yyyy`, `Mon yyyy`, bare `Mon`, Devanagari month names, and
   **resolvable relative forms** — `yesterday`, `kal`, `last month`, `pichhle mahine`,
   `3 mahine pehle`, `2 saal pehle`, `X months ago` — which *can* be resolved against `today` and
   should be, because that is real information the product currently throws away (W1-02 t1's
   *"3 mahine pehle vacate kiya"*).
2. **The invariant, enforced in `_apply_extraction`'s post-pass and in `backfill_facts`:**
   > A four-digit year may appear in a stored `DATE`-typed fact only if that year literally appears
   > in the user's text for this case (current message or prior user turns), or was derived by the
   > relative-date resolver.

   Otherwise strip the year, store the `month_only` form, and set
   `fact_metadata[field]["year_known"] = False`. This needs no vocabulary and no open-class list;
   it is a string-containment check against text the user actually wrote.
3. **Carry the precision forward.** Add a `date_precision` map to `_compact_case`'s `facts`
   (`llm_conversation.py:1570-1587`) so both the chat context and the persisted profile state which
   dates are year-unknown. Add **one** line to `CHAT_SYSTEM_PROMPT`: *"When a supplied date fact is
   marked year_unknown, ask for the year; never state, assume or compute a year for it."* The
   prompt line is a supplement to the deterministic record, not the mechanism — the record is what
   S6 will read.
4. **Hand S6 its precondition.** A `year_known == False` date must never be eligible for limitation
   arithmetic. Do not build the calculator this cycle (that is C5/S6); just guarantee the flag
   exists and is honest, and say so in the handoff.

**Risk / blast radius.**
* Stripping a year the model supplied is a deliberate loss of (fabricated) information. If the year
  *was* said earlier in the conversation, the containment check over `prior_text` keeps it. Agent 2
  should log at INFO when a year is stripped, so this is observable.
* `vacating_date` is consumed by `TENANT_DEMAND_NOTICE` rendering and by
  `_missing_document_fields`. A `month_only` value is still a non-empty string, so it still counts
  as "present" — meaning a document could be generated with "July" in a date slot. **Agent 2 should
  make `_missing_document_fields` treat a `year_known == False` date as missing** for document
  purposes. That is the S5b-adjacent half of this finding and belongs in this cycle.
* Resolving relative dates changes `vacating_date` from `null` to a real date on tenancy cases,
  which feeds `issue_understood` → readiness. That is intended, but it is a second reason cases
  will start climbing; see §5.
* `tests/test_calculator.py` exists — check whether it already asserts on date strings.

**How Agent 3 should verify.**
* `normalize_date_phrase("I resigned in July", today)` → `precision == "month_only"`,
  `year_known is False`, `"2023" not in value`, `"2026" not in value`.
* End-to-end, offline: a stub provider returning `incident_date="July 2023"` on a turn whose text
  is *"after I resigned in July"* and whose prior turns contain no `2023` →
  `profile.incident_date` contains no year and `fact_metadata["incident_date"]["year_known"] is
  False`.
* The other direction: same stub, but a prior user turn said *"In March 2023 I paid Rs 62000"* →
  `2023` **is** permitted (it was said). This is the case that keeps the rule honest rather than
  blunt; assert both halves.
* `normalize_date_phrase("3 mahine pehle vacate kiya", date(2026, 9, 29))` → `year_known is True`
  and a resolved date in June 2026.
* Document guard: a tenancy profile whose `vacating_date` is `month_only` →
  `"vacating_date" in _missing_document_fields(profile)`.

---

### 2.4 M4 — `user_state` null in all 8 cases; `user_city` missed in 4; Devanagari city not captured

**Problem.** `user_state` is `null` on every turn of all 8 cases, including Jaipur (W1-02), Pune
(W1-06), Bengaluru (W1-03) and Indore (W1-08) where the city *was* captured. `user_city` is null in
W1-01 (Jaipur, lost to the 429), W1-04 (never stated — correct), W1-05
(*"मैं लखनऊ में रहता हूँ"*) and W1-07 (Gurugram, stated but the turn was safety-hijacked).

**Root cause.**
1. **There is no city → state derivation on the live path.** The only city/state table in the
   backend is the 16-entry dict at `conversation_agent.py:239-246`, inside the dead-on-the-live-path
   extractor (§0). It sets `user_city` **and** `user_state` together. On the LLM path the model
   supplies `user_city` (Indore proves this — Indore is *not* in that table, so it can only have
   come from the model) and simply does not supply `user_state`. Nothing anywhere derives one from
   the other. That is the whole of "null in all 8".
2. **The table is Latin-only**, so `लखनऊ` matches nothing. It is also missing Indore and most
   non-metro cities.
3. W1-05 and W1-07 additionally never reached extraction at all, because cycle 1's triage hijack
   returned before it. **That is now fixed**, so the Devanagari gap becomes live for the first time.

**Proposed fix.**
1. **`app/services/jurisdiction.py`** — one reference table, two lookups:
   * all 28 states + 8 UTs, each with its Devanagari form and common variants;
   * a city → state map of roughly 100-150 entries (state capitals, million-plus cities, NCR
     suburbs), each with its Devanagari form and its common alternate romanisations
     (Bengaluru/Bangalore, Gurugram/Gurgaon, Mumbai/Bombay, Kolkata/Calcutta, Chennai/Madras,
     Prayagraj/Allahabad, Vadodara/Baroda, Mysuru/Mysore, Kochi/Cochin, Puducherry/Pondicherry, …);
   * `resolve_jurisdiction(text) -> (city|None, state|None)` and
     `state_for_city(city) -> str|None`.
2. **Derive over the profile, not only the message.** In `backfill_facts`: after the message scan,
   if `profile.user_city` is set and `profile.user_state` is not, call `state_for_city`. This is
   what actually clears "null in all 8" — the city usually arrived on an earlier turn from the
   model.
3. **An explicitly stated state wins over an inferred one** (*"Rajasthan me"*, *"in Karnataka"*,
   `राजस्थान`).
4. **Unknown city → leave `user_state` null.** Never guess a state. An incomplete table must degrade
   to today's behaviour, not to a wrong jurisdiction.
5. **Do not change the first-city-wins rule.** `conversation_agent.py:247-251` only sets
   `user_city` when it is empty, which is why W1-02 correctly kept the forum in **Jaipur** after the
   user moved to Bengaluru — report §5 lists that as one of the six things that worked. Keep it,
   and keep the same rule in `backfill_facts`.

**On "do not enumerate an open class".** Indian city names *are* an open class, and Agent 2 should
not read this as licence to grow lists elsewhere. The reason a table is the right mechanism **here**
and was the wrong mechanism for cycle 2's threat verbs: a missing entry here fails **closed and
silent** — `user_state` stays `null`, exactly as today, and nothing downstream asserts a wrong
jurisdiction — whereas a missing threat verb failed **open and dangerous**. This is reference data
with a safe default, not a semantic classifier. Say so in the code comment.

**Risk / blast radius.**
* `user_state` is in `case_naming._qualifier`'s fallback chain, in tenancy's and consumer's
  jurisdiction policy (`JurisdictionRequirement.REQUIRED_LATER`), and in
  `action_planner`'s tenancy escalation `required_fact_keys`. Filling it will move
  `intake_missing_facts`, titles, and in some cases readiness. Intended, but re-run everything.
* `user_state` is also a document field via `complainant_state` in `portal_selector.py` templates
  and the generator. Filling it is an improvement but is newly-exercised code — see §5.
* Word boundaries: `\b` does not behave usefully around Devanagari. Use explicit non-letter
  lookarounds, and be careful with short/ambiguous city tokens.

**How Agent 3 should verify.**
* `resolve_jurisdiction("मैं लखनऊ में रहता हूँ")` → `("Lucknow", "Uttar Pradesh")`
  (canonical Latin storage so downstream templates are unaffected — Agent 2's call, but state it).
* `resolve_jurisdiction("I am from Indore and I lost Rs 680000")` → `("Indore", "Madhya Pradesh")`.
* Profile-level derivation: profile with `user_city = "Bengaluru"`, `user_state = None`, and a turn
  mentioning no city at all → after the turn, `user_state == "Karnataka"`.
* Negative: `resolve_jurisdiction("I live in Kotdwar")` (not in the table) →
  `("Kotdwar", None)` or `(None, None)` — but **never** a guessed state.
* Stability: `"Flat Jaipur me tha … ab main Bengaluru shift ho gaya hun"` across two turns leaves
  `user_city == "Jaipur"` and `user_state == "Rajasthan"`.

---

### 2.5 S5b — the readiness half of the document gate (finding C3)

**Problem.** W1-03 turn 1, verbatim: *"my startup in Bengaluru hasn't paid me for 3 months. just
draft me the legal notice right now, i don't want questions."* Reply, in 3.01s:
*"I can prepare this document. Please confirm the remaining details in the form, then generate the
PDF or DOCX."* — with `readiness=UNDERSTANDING_CASE`, `opposite_party_name=null`,
`disputed_amount=0.0`. Turn 2 returned the identical sentence.

**Root cause.**
1. `_is_document_handoff_request(req.message)` is True (`llm_conversation.py:1681-1700`:
   "draft" + "notice"). The branch at `llm_conversation.py:341-348` then consults only
   `_document_affordance_blocked`, which (`llm_conversation.py:1350-1371`) checks **crisis cooldown,
   unresolved safety triage and RED — and nothing else.** S5a closed the safety half; the readiness
   half was deferred. There is no readiness check on this branch, so it returns
   `_document_request_response` directly at `:348`, before the model is ever consulted.
2. The same hole exists on the offline path (`llm_conversation.py:216-227`) and on the
   `_is_optional_skip` resume branch (`llm_conversation.py:181-194`).
3. `document_routing_allowed` (`case_readiness.py:215`) — the function whose docstring calls itself
   *"The single gate every document recommendation must pass"* — is consulted at exactly **one**
   site, inside `_refresh_workflow` (`llm_conversation.py:1319`), and only to suppress the
   *proactive* `PREPARE_DOC` recommendation. A user-requested document never passes through it.
4. The API is also ungated for readiness. `_assess_case_document` (`main.py:575-590`) computes
   `safety_blocked` from `profile.safety_status` and passes it to `assess_document_generation`
   (`document_generation.py:64`), which has a `safety_blocked` parameter and **no readiness
   parameter**. `POST /api/v1/documents/generate` therefore has no readiness floor at all.
5. `CHAT_SYSTEM_PROMPT` line 104 states the current product decision explicitly:
   *"If the backend supplies a validated USER_REQUESTED document state, the user may confirm
   required document details even while case readiness remains UNDERSTANDING_CASE."*
   **This line is C3 written down as policy. It must go.**

**Proposed fix — one gate, four call sites.**
1. **Make `_document_affordance_blocked` the single gate** by having it delegate:
   ```python
   safety = safety or SafetyAssessment()
   if crisis_document_block_active(profile.key_facts or {}):
       return True
   return not document_routing_allowed(profile, safety, profile.readiness)
   ```
   `document_routing_allowed` already contains every check `_document_affordance_blocked` does
   today (unresolved triage, RED via both `profile.risk_level` and `safety.safety_level`), plus the
   `PRE_INTAKE`/`UNDERSTANDING_CASE` refusal and the `readiness_at_least(READY_FOR_ACTION)` floor.
   Keep the explicit crisis check first so the crisis deferral copy still wins the ordering.
   Note: on the `_is_optional_skip` branch (`llm_conversation.py:181`) this reads the **previous**
   turn's persisted `readiness`, which is correct and intended.
2. **Distinguish "not yet" from "not now".** The crisis deferral
   (`_document_deferral_reply`, `llm_conversation.py:1373-1389`) deliberately names no paperwork —
   right for a self-harm turn, wrong for W1-03. Add `_document_not_ready_reply(style, blocking)`:
   acknowledge the request, say the document can be prepared, and name the one or two blocking
   facts in plain language in the user's script ("I can prepare a salary demand notice. I need your
   employer's name and the total amount owed first."). Never expose a fact key — reuse the human
   phrasings that already exist in `_formulate_response`
   (`conversation_agent.py:654-686`) and in `FactDefinition.meaning`.
3. **Remember the request.** Set
   `profile.document_request = {"intent": "USER_REQUESTED", "status": "NEEDS_CASE_FACTS",
   "document_type": <resolved or None>, "blocking_facts": [...], "message": <original>}`
   and resume it automatically once readiness reaches `READY_FOR_ACTION` — mirroring the existing
   `SAFETY_PAUSED` resume at `llm_conversation.py:200` and `:222-226`. A user who asked for a
   notice on turn 1 should get it on the turn it becomes available, without asking again.
4. **Gate the API too.** Add a `readiness_blocked: bool = False` parameter to
   `assess_document_generation` (symmetric with `safety_blocked`), append a blocker
   ("The case still needs a few facts before this document can be prepared."), and compute it in
   `_assess_case_document` from `profile.readiness`. Otherwise the frontend can still POST straight
   to `/api/v1/documents/generate` and bypass everything above.
5. **Delete `CHAT_SYSTEM_PROMPT` line 104** in `groq_provider.py` and the matching line in
   `gemini_provider.py`.

**The three currently-green tests — my ruling, as cycle 2 asked for.**

| test | what it encodes | verdict |
|---|---|---|
| `test_llm_conversation.py::test_document_request_without_prepare_action_never_claims_a_pdf_is_queued` (~`:169`) | GENERAL case, `document_request["status"] == "BLOCKED"`, `suggested_action is None`. Blocked by `resolve_requested_document`, not by readiness. | **Product decision. Preserve unchanged.** It is already the behaviour S5b wants. |
| `test_pending_interaction.py::test_document_confirmation_uses_only_supported_case_document` (~`:135`) | Hand-constructs a `DOCUMENT_CONFIRMATION` pending on a bare `profile_for()` with **no facts at all**, then asserts `"ok make"` creates a `USER_REQUESTED` document request. | **Assumption that must change.** In production a `DOCUMENT_CONFIRMATION` can only exist because `document_offer()` (`pending_interaction.py:129-139`) read a `PREPARE_DOC` `recommended_next_action`, which `_refresh_workflow` only sets when `document_routing_allowed` passed. The fixture constructs a state the system cannot reach. **Fix the fixture, not the gate**: populate the facts (as its sibling at `:247` already does) so the case legitimately reaches `READY_FOR_ACTION`, and keep every behavioural assertion. |
| `test_pending_interaction.py::test_live_flow_records_only_an_eligible_document_offer` (~`:247`) | Sets `opposite_party_name`, `vacating_date`, `user_state`, `landlord_reason`, `landlord_contacted`, `rental_agreement_available`, `deposit_payment_proof_available`, calls `_refresh_workflow`, asserts `PREPARE_DOC`, then `"ok make"` → document request. | **Product decision. Preserve exactly.** This is the reference shape of a correctly-earned document and is the best existing lock on §2.1's ladder change. If it ever goes red, the ladder change is wrong. |

Plus a fourth that cycle 2 did not list but that S5b **must** change:

| `test_llm_conversation.py:202::test_salary_notice_request_bypasses_proactive_case_readiness_without_promising_pdf` | Its name is the bug. It asserts `readiness == "UNDERSTANDING_CASE"` three times while a `USER_REQUESTED` document proceeds to `open_confirmation_modal`. | **Assumption that must change, and it changes for free.** Its fixture sets `opposite_party_name`, `unpaid_months`, `disputed_amount`, `employee_role`, city and state; EMPLOYMENT's next action requires only `opposite_party_name` (`action_planner.py:171-179`). Under §2.1 it legitimately becomes `READY_FOR_ACTION` and the document is *earned* rather than bypassed. **Update the readiness/`recommended_next_action`/`recommended_doc_type` assertions; keep every behavioural assertion (no PDF promise, no model-drafted notice, `open_confirmation_modal`, `optional_skipped`) verbatim.** Rename it — `..._is_allowed_once_the_case_is_ready` — so the next reader is not misled. |

**Risk / blast radius.**
* This is a genuine product-behaviour change: a user who demands a document on turn 1 no longer
  gets a confirmation modal on turn 1. That is the intended correction of a CRITICAL finding, but
  it should be stated plainly in Agent 2's handoff so a human can overrule it.
* Without §2.1 this would refuse **every** document in the product. **Do not merge S5b without the
  ladder change.** If Agent 2 has to split, split the other way: ladder first, S5b never alone.
* `tests/test_intake_before_document.py:141` and `:180` assert `READY_FOR_ACTION` /
  `READY_FOR_DOCUMENT` and then generate a real PDF (`:196-204`). These are the end-to-end locks
  that must stay green — they are also the best evidence that the generator path works.
* `tests/test_document_request_architecture.py` is the file most likely to move; read it before
  starting.
* Frontend: `CaseWorkspacePanel.tsx` and `ChatInterface.tsx` are in the uncommitted working tree and
  render `suggested_action` / `document_request`. A new `NEEDS_CASE_FACTS` status must not crash
  them — check the status handling before adding the value, and prefer reusing `BLOCKED` if the
  frontend switch is exhaustive.

**How Agent 3 should verify.**
* W1-03 t1 verbatim, offline: reply is **not** `"I can prepare this document…"`,
  `suggested_action` has no `open_confirmation_modal`, and the reply names the missing facts in
  plain language with **no** `snake_case` token anywhere in it.
* Same case after supplying the employer name and amount → the parked request resumes and the
  confirmation action appears, without the user re-asking.
* Crisis ordering preserved: the S5a locks in `tests/test_cycle2_verification.py` (crisis cooldown
  engages at `tsc < 3` and **expires** at `tsc == 3`) must stay green, and a crisis turn must still
  get the crisis deferral copy, not the new "not ready" copy.
* API: `POST /api/v1/documents/generate` on an `UNDERSTANDING_CASE` profile → 422 with a readiness
  blocker; on a `READY_FOR_ACTION` profile → unchanged success.
* `tests/test_intake_before_document.py` end-to-end PDF test still green.

---

## 3. Explicit non-goals

Out of scope this cycle. Do not fix, do not "improve while I'm here", and do not let them block:

* **C5 / S6 — limitation arithmetic.** H8 gives S6 its precondition (`year_known`); building the
  calculator is the next cycle's job. Do not add a limitation table or any deadline computation.
* **C2, C4, H4, M7, M8, L1 / S4 — fabricated citations, win percentages, invented helplines, the
  no-corpus disclosure, `sources` being null.** All visible in the same W1-06 transcript I quoted
  for H8. Resist.
* **H5, M1, M5, M6, L2 / S3 — reclassification, bare-"yes" binding, the one-way ladder,
  `"i need help"` advancing readiness, the `"paid me"` RESOLVED trigger.** Note especially:
  * `_maybe_reclassify`'s `len(message.split()) < 3` bar (`llm_conversation.py:1255`) is H5. This
    cycle touches neither `_maybe_reclassify` nor `_apply_extraction`'s confidence gates.
  * **M5 is adjacent and tempting:** §2.1 rewrites `compute_readiness`, and a monotonic clamp would
    be a two-line addition. **Do not add it.** A ladder that can drop back is currently protective
    (it re-closes the document gate when context is lost), and changing that interacts with S5b in
    ways this cycle has not analysed. If Agent 2 believes it is unavoidable, it must be reported as
    a deviation with reasoning.
* **H3, H9 / S7 — RBI limited-liability window, the bank's "FIR first" demand.**
* **M2, M3 / S8 — Hinglish→English register flip, case-title qualifiers.** Titles **will** change as
  a side effect of capturing amounts, party names and states. That is expected; do not tune
  `should_retitle` or `_qualifier`.
* **N8 — the offline composer returning byte-identical replies** (`tests/test_cycle2_verification.py:295`,
  strict xfail). Stays xfail. It is S3/S8.
* **S1 / safety_triage.py — closed and verified.** Do not edit that file this cycle.
* **`tests/test_doc_generator.py::test_document_generation_notice`** — pre-existing, unrelated.
  Leave it red.
* **The loose category keyword heuristics** at `conversation_agent.py:353-422`. Leave them on the
  offline path; do not promote them to the live path.

---

## 4. Definition of done

Agent 3 may call this cycle complete when **all** of the following hold, with proof in
`cycle-3/agent3-tests.md`. Every check is offline; `provider.calls` must be asserted empty
wherever a provider stub is used.

**Suite**
1. `cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`
   shows **≥ 442 passed**, and the only failure is
   `tests/test_doc_generator.py::test_document_generation_notice`.
   `tests/test_cycle2_verification.py:295` (N8) remains the only xfail, still xfailing.
2. No browser was launched. No live Groq/LLM call was made. Nothing was committed, branched or
   pushed. No `.env` or key material appears in any cycle-3 artifact.

**H7**
3. A classified, non-RED case with its `minimum_context_any_of` satisfied and action-blocking facts
   outstanding returns `READY_FOR_LEGAL_GUIDANCE` — for at least CONSUMER, HOUSING_TENANT and
   EMPLOYMENT. `document_routing_allowed` is `False` at that rung.
4. A case with **nothing** known still returns `UNDERSTANDING_CASE`; a GENERAL case behaves exactly
   as it does on the current tree.
5. `compute_blocking_missing_facts(profile, safety, intake)` equals
   `action_planner.plan_next_action(profile).blocking_missing_facts` minus `stated_unknown`, for
   every non-GENERAL, non-CYBER_FRAUD domain.
6. `"flipkart se maine ek washing machine order ki thi"` → `opposite_party_name == "Flipkart"`.
   `"Mera landlord security deposit wapas nahi kar raha"` → `opposite_party_name is None` and
   `key_facts["opposite_party_role"] == "landlord"`. `"PhonePe se gaya, SBI account hai"` in
   CYBER_FRAUD → `opposite_party_name is None`.
7. No `snake_case` fact key appears in any payload sent to `provider.chat`, and
   `pending_interaction.target_keys` binding still works.

**H6**
8. `"Deposit Rs 85000 tha"` on a HOUSING_TENANT case sets `disputed_amount == 85000.0` **with a
   stub provider that returns no facts at all**.
9. A turn carrying facts that ends in `LLMRateLimitedError` still records them:
   Flipkart + Jaipur + Rajasthan from W1-01 t3's verbatim text.
10. A bare `18499` answering a MONEY-typed pending sets `disputed_amount == 18499.0`; with no
    pending it sets `key_facts["unbound_numeric"]` and does not guess.
11. Non-overwrite: an existing `disputed_amount` is never replaced by the deterministic backfill.

**H8**
12. `"I resigned in July"` with no `2023` anywhere in the conversation → the stored date carries no
    year and `fact_metadata[...]["year_known"] is False`.
13. The same phrase in a conversation that *did* say `2023` → the year is kept.
14. `"3 mahine pehle"` resolves to a real date against a fixed `today`.
15. A `year_known is False` date counts as missing in `_missing_document_fields`.

**M4**
16. `user_state` is derived from `user_city` on the profile even when the current turn names no
    city: Jaipur→Rajasthan, Bengaluru→Karnataka, Pune→Maharashtra, Indore→Madhya Pradesh.
17. `लखनऊ` → `("Lucknow", "Uttar Pradesh")`.
18. An unrecognised city leaves `user_state` `None` — never a guess.
19. W1-02's first-city-wins behaviour is preserved across a Jaipur→Bengaluru move.

**S5b**
20. W1-03 t1 verbatim produces no document affordance, no `open_confirmation_modal`, and a reply
    that names the missing facts in plain language with no fact key in it.
21. Once those facts are supplied, the parked request resumes without the user re-asking.
22. `POST /api/v1/documents/generate` is refused (422) below `READY_FOR_ACTION`.
23. `test_pending_interaction.py::test_live_flow_records_only_an_eligible_document_offer` and
    `tests/test_intake_before_document.py`'s end-to-end PDF test are green, unmodified.
24. Every test changed under §2.5 is listed in the handoff with a one-line justification, and its
    behavioural assertions are preserved verbatim.

**Blast radius**
25. A full offline replay of each of W1-01 … W1-08 (per-turn snapshots, `replay()` style) completes
    with **no unhandled exception** on any turn, including the turns that now reach
    `READY_FOR_LEGAL_GUIDANCE` and `READY_FOR_ACTION` for the first time. Report the readiness each
    case reaches, per turn, as a table.
26. No case reaches `READY_FOR_ACTION` while its safety assessment is RED or while
    `safety_triage_complete` is unset.

---

## 5. What to expect to break when cases start advancing past `UNDERSTANDING_CASE`

Until now `document_routing_allowed` returned `False` on turn 1 of every real conversation, so
`_refresh_workflow` took its early return at `llm_conversation.py:1327-1334` every single time.
Everything below that line has effectively never run against live conversational state. Cycle 2
found a latent crash exactly this way — assume there is another one.

**Code that runs for the first time**
1. `_refresh_workflow`'s tail (`llm_conversation.py:1336-1349`): `select_document_for_workflow`,
   `DOCUMENT_DEFINITIONS` lookup, `_missing_document_fields`, the `READY_FOR_DOCUMENT` promotion,
   and the construction of a `SYSTEM_SUGGESTED` `PREPARE_DOC` action.
2. `document_offer()` and the `DOCUMENT_CONFIRMATION` pending path in `_finish_chat_response`
   (`llm_conversation.py:477-490`) — a real conversation has never produced one.
3. `_document_request_response`'s field-gathering loop (`llm_conversation.py:1417-1428`) against
   real profile values rather than test fixtures, including the
   `complainant_name`/`complainant_city`/`recipient_name` alias remapping.
4. `assess_document_generation` → `validate_document_fields` with live values, and the whole
   `/api/v1/documents/assessment` + `/generate` pair.
5. `portal_selector.py` templates that interpolate `{complainant_state}` — newly non-null thanks to
   M4.
6. `action_planner.plan_next_action` now called from `case_readiness` on **every** turn, including
   safety turns and GENERAL turns. Check `_is_action_completed`'s `current_stage_key` reads against
   profiles whose stage was set by `_set_journey_current`.
7. The frontend: `CaseWorkspacePanel.tsx` and `ChatInterface.tsx` (uncommitted, someone else's work)
   rendering a non-null `recommended_next_action`, `recommended_doc_type` and `missing_document_fields`
   for the first time in normal use.

**Specific predictions to check, in priority order**
* **CYBER_FRAUD reaches `READY_FOR_ACTION` fastest.** Its `bank_reported` action requires only
  `opposite_party_name` **or** `bank_name` (`action_planner.py:120-124`, disjunction handled at
  `:251-257`), and `compute_blocking_missing_facts` already exempts `incident_date`, `user_state`
  and `scam_method`. W1-04 satisfies this by turn 2 (`bank_name = SBI`, `disputed_amount = 47500`).
  Expect a `CYBERCRIME_BANK_FREEZE`/`FORMAL_LEGAL_NOTICE` affordance to appear on W1-04 t2 where
  nothing appeared before. **Decide deliberately whether that is right** — I think it is (an early
  bank-freeze letter is exactly what a fraud victim needs), but it is a visible behaviour change
  and Agent 3 must report it rather than discover it later.
* **POLICE_COMPLAINT has `required_fact_keys=[]` for both its actions** (`action_planner.py:194-213`).
  Once triage releases, a police case will go straight to `READY_FOR_ACTION` and offer
  `POLICE_COMPLAINT_BNSS`. The RED brakes in `document_routing_allowed` (`case_readiness.py:244-249`,
  which reads **both** `profile.risk_level` and `safety.safety_level`) and the
  `safety_triage_complete` brake at `:236` are the only things standing between a death-threat
  disclosure and a document offer. **Verify both brakes hold on a W1-05 replay** — this is the
  nearest thing in the cycle to a safety-relevant regression, and it is the exact shape Agent 4
  flagged in cycle 2 ("no safety triage … and an immediate offer to draft a document").
* **`READY_FOR_LEGAL_GUIDANCE` is a value `CHAT_SYSTEM_PROMPT` has never seen.** It has explicit
  branches for `PRE_INTAKE` and `UNDERSTANDING_CASE` (`groq_provider.py:100-103`) and none for the
  middle rung. Add one clause: give substantive preliminary guidance, name the next practical step
  in prose, ask at most one high-value question, **do not** offer or name a document. Without it
  the model gets an unhandled readiness label.
* **`recommended_next_action` is still `None` at `READY_FOR_LEGAL_GUIDANCE`**, because
  `_refresh_workflow` nulls it whenever `document_routing_allowed` is False. So the UI will show a
  case that has visibly progressed but still offers no action. That is acceptable for this cycle;
  note it as a follow-up rather than fixing it here.
* **Case titles will churn** as amounts, party names and states land (`case_naming._qualifier`).
  `should_retitle`'s refusal to rename for a newly learned amount (M3) will make this look
  inconsistent. Expected; S8.
* **Two blocking computations exist and must not diverge**: `case_readiness.compute_blocking_missing_facts`
  and `conversation_agent._compute_blocking_fields` (`conversation_agent.py:433-458`), which feeds
  `_formulate_response`'s question selection on the offline path. If Agent 2 changes one, it must
  either change both or make the second delegate to the first. A user seeing one set of questions
  online and a different set offline is how this kind of drift becomes a new finding.

---

*No commit, no branch, no push. No live Groq/LLM call was made in preparing this plan; the only
command run against the tree was the pytest baseline in the preamble (with
`--ignore=tests/test_browser_autofill.py`) plus two offline `assess_safety` probes. No source file
was modified. No `.env` or key material appears here.*
