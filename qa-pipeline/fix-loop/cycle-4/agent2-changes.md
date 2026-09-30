# Agent 2 — Cycle 4 changes (set S4)

**Status: COMPLETE.**

Set S4 = D1 (prompt key leak) + C2, C4, H4, M7, M8, L1 (legal-content hallucination guards).

Baseline before my changes: `1 failed, 458 passed, 1 xfailed`.
After all code changes, before new tests: `1 failed, 458 passed, 1 xfailed` (same single
pre-existing `tests/test_doc_generator.py::test_document_generation_notice`).

---

## New files

| File | Purpose |
|---|---|
| `backend/app/services/response_guard.py` | The guard. Pure functions, no I/O, no provider, no async. |
| `backend/app/services/helplines.py` | The single registry of helpline numbers -> (service, operator). Imports the crisis lines from `safety_triage` rather than restating them. |
| `backend/app/services/model_law_adoption.py` | MTA-2021 State adoption register. **Deliberately empty**; every State resolves `UNVERIFIED`. |
| `backend/app/services/document_citations.py` | The curated document citation map, moved out of `doc_generator.generate_document`'s local scope so the guard can read it. |
| `backend/tests/fixtures/wave1_replies.json` | Eight verbatim wave-1 replies, copied from `qa-pipeline/wave-1/transcripts.json` with their real U+2248 / U+202F / U+2011 bytes (round-trip asserted at extraction time). |

---

## D1 — the internal fact key leaked to the user (cycle-3 DoD #7)

**Problem.** W1-02 t2: *"landlord ya property manager ka **naam** (opposite_party_name)
chahiye"*. `domains/registry.py:141-150` builds each `next_fact_candidates` entry with
`"key": fact.key` — the internal profile field name — and `llm_conversation.py:388` hands it
to the provider, which serialised it verbatim. The prompt already said "never expose
internal field IDs" (`groq_provider.py:116`) and was ignored.

**Actual fix.**
1. `app/llm/contracts.py` — new `LLMResponseContext.model_payload()`, the single
   serialisation entry point for every provider. It rebuilds `next_fact_candidates` as a
   **new list of new dicts** with `REDACTED_CANDIDATE_KEYS = ("key",)` removed, so the
   in-memory context is untouched: `llm_conversation._finish_chat_response` reads
   `context.domain_context["next_fact_candidates"][0]["key"]` *after* `chat()` returns and
   passes it to `fact_candidate()`.
2. `app/llm/groq_provider.py::GroqProvider.chat` and
   `app/llm/gemini_provider.py::GeminiProvider.chat` call `model_payload()` instead of
   `model_dump(mode="json")`. Groq's `user_message` reorder is preserved.
3. Belt-and-braces in the guard: `response_guard._strip_fact_key_parentheticals` removes a
   parenthetical whose entire content is a snake_case token in the **live** fact-key set
   (`domain_registry.resolve(category).facts` + `StructuredCaseProfile.model_fields`).
   Verified against the verbatim W1-02 t2 string.

`purpose`, `meaning`, `priority_reason` and `stage` are **kept** — the prompt tells the model
to "use its purpose and order", so removing `purpose` would change behaviour.

**Test whose expectation legitimately changed.**
`tests/test_groq_prompt_caching.py::test_final_chat_preserves_static_instructions_and_all_dynamic_context`
asserted `payload == context.model_dump(mode="json")`, which is now false by design. It now
asserts `payload == context.model_payload()`, that every field **other than**
`domain_context` is still byte-identical to `model_dump()`, that no candidate carries `key`,
and that the in-memory context still does. Strictly stronger than before; nothing weakened.

**Status: fixed.**

---

## M8 — the no-corpus limitation was never disclosed

**Correction to the record (confirm to Agent 4).** The report's *"`sources` was null on all 31
turns"* is a **QA harness artifact**. Every turn in `transcripts.json` records
`"sources": null` *and* `"mode": null`, including the turns the report itself calls
`mode=limited_demo`. The API response model has neither field: it exposes `llm_mode`
(`app/schemas/chat.py:153`) and puts citations at `case_profile.legal_sources` (`:108`), and
the harness stored only a 13-key profile subset that excludes `legal_sources`. **Wave 2 must
read `llm_mode` and `case_profile.legal_sources`** or it will re-report M8 as unfixed.

**Actual fix.**
1. `llm_conversation._verified_sources` now tags every entry with `kind`:
   `VERIFIED_PROVISION` (`"verified_provision"`) for retrieved corpus provisions,
   `OFFICIAL_SOURCE_LINK` (`"official_source_link"`) for `domain.rag.official_sources`
   entries. Both lists hold **the same dict objects**, so
   `test_llm_conversation.py:516`'s identity-of-dicts membership assertion still holds.
2. New `llm_conversation._legal_sources_status` -> `{"corpus_available", "verified_provisions",
   "case_law_available"}`. `case_law_available` is a hard `False`. Shipped to the model on the
   new `LLMResponseContext.legal_sources_status` field, and computed for the case brief too.
3. `profile.key_facts["verified_sources_available"]` records the same for the workspace, so
   the panel can distinguish "not retrieved yet" from "no corpus exists". (Frontend copy
   change deliberately **not** made — out of scope for this cycle, noted as undone below.)
4. The user-facing disclosure is the guard's job and is **conditional**: it is appended only
   when the reply actually made a provision claim the guard could not confirm. It **names the
   authority, never the URL** (`"the official text published by the Ministry of Labour &
   Employment"`), because EMPLOYMENT's official source URL has six underscores and
   `tests/test_api.py:170` asserts `"_" not in reply_text` on an EMPLOYMENT case.

**Status: fixed** (backend). Frontend copy not changed.

---

## C2 — fabricated Supreme Court citation and invented verbatim statutory text

`app/services/response_guard.py`. Three mechanisms, per the plan's failure-mode table.

**(c) Case law — unconditional, allowlist-free.** `_apply_case_law`. Two independent shapes:
`X v./vs./versus Y` between capitalised runs, and a reporter citation
`(YYYY) N SCC M` / `AIR YYYY SC N`. Action: remove the whole markdown block the citation
anchors (bullet plus its indented continuation), then remove any later sentence that refers
to the same case, then drop a heading whose content block was removed. Later-reference
needles are derived **from the reply itself**: the first party with honorifics stripped
(`M/s. M. S. R. Enterprises` -> `MSRENTERPRISES`), which is what also catches the differently
spaced `*M.S.R. Enterprises*`. Single-word needles need 12+ characters so generic nouns like
`Enterprises` never become one. Also: `_SENTENCE_SPLIT_RE` does not split after a
single-letter initial, or `M.S.R.` would have become three fragments.

**(a) Statute in a corpus-less domain.** `_apply_statute` + `build_allowlist`.
Allowlist = union of (i) this turn's retrieved provisions, (ii) the **whole** statute corpus
for `domain.rag.corpus_ids`, (iii) the whole curated `DOCUMENT_CITATIONS` map, (iv) the RTI
corpus, plus Act **names** from `official_sources`. Rules: match on section number alone; a
cited sub-clause of an allowlisted section is verified (`69(2)` against corpus `69`); Act
names compared loosely and used **only to reject**; no corpus for the domain -> every section
number in it is unverifiable; otherwise, cannot decide -> **caveat, keep the text**. A
stripped citation also drops an adjacent quotation-only line — a fabricated verbatim quote
pasted into a notice is worse than a mis-cited number.

**Deviations from the plan, with reasons:**
- *Allowlist includes the full domain corpus, not only this turn's four retrieved
  provisions.* Retrieval limit is 4; a CONSUMER turn that retrieved two provisions would
  otherwise have the guard delete a correct citation of a third. The corpus is curated
  repository content, so the set stays closed.
- *`DOCUMENT_CITATIONS` is applied globally, not filtered to the domain's bound documents.*
  `RTI_SEC6` is bound to no domain, so a domain filter would have deleted RTI s.6(1), which
  §3 item 3 lists as must-not-filter.
- *Allowlist stores exact section tokens; only the **cited** token expands to parents.*
  Expanding both sides would allowlist a bare `Section 2` everywhere merely because the
  corpus holds `Section 2(11)`, letting a fabricated `Section 7 of the Code on Wages`
  through.

**Verified against the verbatim W1-03 t3 string** (EMPLOYMENT profile): no
`(2020) 6 SCC 123`, no `M. S. R. Enterprises`, no `v. State of Karnataka`, no orphaned
`**Supreme Court case you can quote**`, no `Section 9(1)`, no quoted statutory text; the
wage-period prose survives; both disclosures present; `redactions` names `case_law` and
`statute_unverified`; idempotent.

**Known residue:** the opening sentence *"...and a Supreme Court decision that interprets
it."* survives — it names no case and matches neither shape, and widening the rule to catch
"Supreme Court" as prose would delete a legitimate institution name. The appended case-law
disclosure directly contradicts it. Recorded as partial.

**Status: fixed** for the citation itself; the dangling forward-reference sentence is partial.

---

## C4 — invented numeric win probabilities

`response_guard._apply_outcome_probability`. Two tiers:
- **Tier A (whole line):** the line is nothing but a label containing an outcome word plus a
  percentage -> remove the line. Catches `**Rough chance of a favourable outcome:** **≈ 30 %**`,
  including the markdown emphasis interleaved between label and quantity.
- **Tier B (inline):** remove the quantity, an optional trailing outcome noun and a trailing
  comma, when an outcome word is within 70 characters. Catches
  `~30 % chance, mainly limited by...` -> `mainly limited by...`.

One deterministic script-matched paragraph is appended once, saying case strength is not a
number and naming the factors that do matter.

The outcome-word list is the closed side of the rule (`chance(s)`, `odds`, `probability`,
`likelihood`, `likely to succeed/win`, `success rate`, `favou?rable`, `win/wins/winning`,
`jeetne`, `safalta`, `संभावना`, `जीतने`). Unicode is handled through classes, not literals:
`≈` U+2248, `~`, U+202F between digits and `%` (matched by `\s`), and a dash class covering
U+2010–U+2015 and U+2212.

**Verified against the verbatim W1-06 t4 string:** all four occurrences gone (2 whole-line,
2 inline), no `%` remains anywhere in the reply, both factor tables and the "Which one to
chase first?" section survive, `₹ 62,000` / `₹ 90,000` / `50 lakh` / `2 years` / `3 years`
untouched, idempotent. The wrong limitation arithmetic is left alone (C5 / set S6).

**Status: fixed.**

---

## H4 — Model Tenancy Act presented as operative Rajasthan law

`app/services/model_law_adoption.py` + `response_guard._apply_model_law` + the adoption caveat.

The trigger is **corpus metadata**, not a list of laws: a cited section is flagged when its
corpus entry's `document_type` matches `is_model_law_document_type` (`"model law"` /
`"adoption must be verified"`, stamped at `rag_node.py:121`). That string is matched by
`CaseWorkspacePanel.tsx:120` and was not reworded.

1. **Adoption register is empty by construction.** `MODEL_TENANCY_ADOPTION: dict[str, str] = {}`.
   `adoption_status()` returns `UNVERIFIED` for every State, and `UNVERIFIED` behaves exactly
   like `NOT_ADOPTED`. The docstring states what a curator must supply to add an entry (the
   State's own Act/notification name, number/year, gazette reference). **No unsourced
   adoption claim exists in the file.**
2. **The caveat is appended deterministically**, as a separate trailing paragraph so no
   existing `in reply_text` assertion is split, in the user's script, naming the State.
3. **"Cite it in your notice" is neutralised** at sentence level when the sentence both cites
   a model-law section and carries a cite verb (`cite/quote/refer to/hawala/हवाला/उल्लेख`).
4. **Constructed-authority contact claims.** `<Place> Rent Authority|Rent Court|Rent Tribunal`
   plus a **website/portal/site** claim and no allowlisted URL -> the parenthetical holding
   the claim is replaced with script-matched prose.
   *Deviation:* the plan also listed `address` / `contact details` as triggers. I narrowed it
   to online-location claims only. "Confirm the address" is correct advice; the harm is
   asserting *where* a possibly non-existent authority's website is. This keeps W1-02 t1's
   `(Rajasthan ke liye "Jaipur Rent Authority" ya similar)` hedge and all the procedural
   advice untouched, and still meets DoD #7.
5. **Corpus `act` field not split.** The plan's item 5 suggested renaming the corpus `act`
   from `"Model Tenancy Act / State Rent Control Acts"` to `"Model Tenancy Act, 2021"`. I did
   **not** do it — see "deliberately left undone" below.

**Verified against the verbatim W1-02 t1 and t2 strings** (`user_state="Rajasthan"`):
`Section 11` and `Section 30` still present; the adoption caveat present in Hinglish;
`"Rajasthan State Rent Authority ke website"` gone; the Jaipur-vs-Bengaluru jurisdiction
passage byte-for-byte intact; the t1 "hawala de rahe hain" clause rewritten; `₹85,000`
intact; idempotent.

**Status: fixed** (caveat, never deletion).

---

## M7 — invented helpline numbers

`app/services/helplines.py` + `response_guard._apply_helplines`.

Registry entries, every one sourced from a number this repository already emits with the
attribution it already gives: 112, 1930, 181, 1098, 1091, 15100, plus 14416 / 1800-599-0019 /
9820466726 **imported** from `safety_triage.CRISIS_HELPLINES` and `EMERGENCY_NUMBER` so the
two can never drift. **1915 (National Consumer Helpline) was deliberately omitted** — the plan
listed it as a candidate but it appears nowhere in the codebase and I will not add a number I
cannot source from the product.

Filter:
- toll-free shape `1800[dash/space]…` not in the registry -> removed, named service kept;
- a 3–5 digit short code adjacent to a call/helpline word and not in the registry -> removed,
  with lookarounds that stop a hyphen-joined number (`1800-11-001-112`) being read as three
  separate short codes;
- exclusions: Indian-grouped amounts (`₹85,000`, `Rs 1,45,000`), 4-digit years, and any digit
  run present in the user's own turn text, `user_phone`, `transaction_id`, order/payment/
  tracking ids, `disputed_amount`, `key_facts` values or `provided_documents` metadata;
- URLs: allowlisted **by exact normalised URL**, not by host, from every domain's
  `official_sources` plus the URLs the product publishes itself. An off-allowlist URL is
  removed and the surrounding prose kept — nothing is inserted, so no underscore can reach
  `reply_text`.
- One script-matched "take the helpline from your own card/passbook/official site" sentence is
  appended once.
- **The guard never injects a number.** The registry is a filter and a model-context input.

**Verified against the verbatim W1-04 t2 and t3 strings:** `1800‑11‑001‑112` and
`1800‑11‑222‑222` (U+2011 form) gone; `24‑hour helpline` and `1930` retained;
`https://www.cybercrime.gov.in/` retained; `https://www.rbi.org.in/Scripts/Complaints.aspx`
gone; `₹47,500` retained; idempotent.

**Status: fixed.**

---

## L1 — 1930 attributed to RBI

`response_guard._fix_operator_attribution`, riding M7's registry `operator` field. Runs
**before** the number filter so `RBI 1930 helpline (1800‑11‑001‑112)` loses RBI first and then
loses the invented number. Matches only: a registry number, adjacent to a name in the closed
`ATTRIBUTABLE_AUTHORITIES` set, contradicting that number's registry operator/aliases, with a
helpline word in the window. Both orders handled (`RBI[-designated|ki] 1930` and `1930 … (RBI)`).

1930's registry operator is `"Indian Cyber Crime Coordination Centre (I4C), Ministry of Home
Affairs"`, which is what the codebase itself says ("Cyber Helpline"); the codebase never says
RBI for any number.

**Verified:** W1-04 t1 `1930 helpline (RBI) call karein` -> `1930 helpline call karein`;
W1-04 t2 `RBI 1930 helpline` -> `1930 helpline`; W1-08 t1 `RBI‑designated 1930 helpline`
-> `1930 helpline`. In all three `1930` survives. W1-08 t1's *"The Reserve Bank of India's
'Customer protection for unauthorised electronic transactions' (Notification 2017)"* sentence
passes **byte-identical** — RBI stays namable, as H9 (set S7) requires.

**Status: fixed.**

---

## Wiring / coverage

- `llm_conversation._tag_response` — the single funnel for all 14 return paths (live provider,
  offline `limited_demo`, 429 `_rate_limit_fallback`, provider-error, safety short-circuit,
  both document gates, `resume_response`, and `main.py:1101`/`:1203`). Guard runs here, and
  logs at WARNING with case id, category, mode and rule names only — never reply text, user
  message, party names or amounts.
- `case_summary.generate_case_summary` — guarded **inside** the function, before the value is
  returned to `main.py:975`, so `profile.ai_summary_cache` stores the guarded text.
- Prompt supplement (not load-bearing, no DoD criterion depends on it): five lines added after
  the existing "Do not alter state, invent facts…" line in both `groq_provider.py` and
  `gemini_provider.py`, describing `legal_sources_status`, the case-law prohibition, the
  probability prohibition and `helpline_registry`.

---

## Deliberately left undone

1. **Corpus `act` field not split** (`model_tenancy_provisions.json`,
   `"Model Tenancy Act / State Rent Control Acts"` -> `"Model Tenancy Act, 2021"` +
   `analogous_to`). The caveat is driven off `document_type`, so the rename buys nothing this
   cycle, while `rag_node._citation` reads `item["act"]` unconditionally,
   `CaseWorkspacePanel.tsx:117` keys off `act`, and the string feeds corpus ranking and the
   RAG query builder. Changing curated legal data for no behavioural gain is exactly the risk
   class this cycle exists to reduce. Recommend it as its own small change with its own test.
2. **`CaseWorkspacePanel.tsx:124` copy change** ("Relevant laws will appear once identified"
   vs "no corpus exists for this domain"). The backend signal
   (`key_facts["verified_sources_available"]`, `legal_sources_status`) is now there for it.
   The plan marked it optional; frontend files are in someone else's uncommitted work.
3. **1915 National Consumer Helpline** omitted from the registry (see M7 above).
4. **C5 limitation arithmetic, H3, H9** — out of scope (S6 / S7). The guard removes W1-06 t4's
   percentages and touches nothing else in that reply.
5. **`tests/test_doc_generator.py::test_document_generation_notice`** left red, as instructed.
   N8 left xfail.

---

## Test counts

Filled in at the end of this document once the new tests land.

---

## Test counts

Command (never bare `pytest`; the browser test is always excluded):
```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
```

| | Result |
|---|---|
| Baseline before cycle 4 | `1 failed, 458 passed, 1 xfailed, 3 warnings` |
| After cycle 4 | `1 failed, 721 passed, 1 xfailed, 3 warnings` |

`+263 passing`, zero lost. The single failure is the pre-existing, unrelated
`tests/test_doc_generator.py::test_document_generation_notice`. The single xfail is still N8.

New test files:
- `backend/tests/test_response_guard.py` — 242 tests. The guard as a pure function: the four
  verbatim transcript strings, every preservation lock from plan section 3, the no-op proof over
  every deterministic composer in all three scripts, idempotence, and the redaction contract.
- `backend/tests/test_cycle4_verification.py` — 21 tests. D1 payload redaction (both providers,
  plus a structural lock so a future provider cannot call `model_dump` again), M8 provenance and
  status, the conditional disclosure, and the five guard-coverage paths (live provider,
  `limited_demo`, 429 fallback, `resume_response`, `generate_case_summary`) plus the PII-safe
  logging assertion.

Changed test file: `backend/tests/test_groq_prompt_caching.py` (one assertion, strengthened — see D1
above). No test deleted, skipped, xfailed or weakened.

## Known limits, for Agent 3/4 to check rather than rediscover

1. **W1-03 t3's opening forward reference survives.** *"...and a Supreme Court decision that
   interprets it."* names no case and matches neither case-law shape. Widening the rule to treat
   the prose "Supreme Court" as a citation would delete a legitimate institution name. The
   appended case-law disclosure immediately contradicts the sentence, so the user is not misled
   about whether a judgment was supplied, but the sentence itself is still there.
2. **A 4-digit `19xx`/`20xx`-shaped short code is never removed**, because years are everywhere in
   legal prose ("the 1986 Act", "the 2019 Code"). Consequence: a fabricated 4-digit number in that
   range would survive. Both of M7's recorded numbers are toll-free-shaped, so no DoD criterion
   depends on it. `1915` (National Consumer Helpline, real, deliberately not in the registry)
   therefore also survives, which is the safe direction.
3. **Short codes of 6+ digits are not matched** (e.g. the retired `155260`). Same reasoning.
4. **`_apply_model_law`'s cite-instruction rewrite is sentence-local**: it fires only when one
   sentence both cites a model-law section and carries a cite verb. A Devanagari reply that cites
   s.11 in one sentence and says "put it in the notice" in the next keeps the instruction; the
   adoption caveat still lands. Deliberately conservative.
5. **The guard cannot see the user's turn text on the deterministic paths.** `_tag_response` now
   takes an optional `user_message`, passed from `_finish_chat_response` (the model-text path) and
   `_rate_limit_fallback`. The other call sites leave it empty, which is safe because the guard is
   a proven no-op on every composer they emit (see the no-op test).
6. **`legal_sources_status` and `helpline_registry` add roughly 300-400 tokens per chat request.**
   Real but small against the ~10-13k already sent. Worth watching if the free-tier 8k/min limit
   bites; both are prompt supplements and nothing in the DoD depends on them.

## Constraint compliance

- No commit, no branch, no push. `git status` is unchanged except for the files listed above.
- No live Groq/Gemini call in any test; every provider is a local fake or an `AsyncMock`.
- No browser, no Playwright, no server, no `browser-use`. `tests/test_browser_autofill.py` was
  ignored on every run and never executed.
- `app/services/safety_triage.py` was **read only** — `CRISIS_HELPLINES` and `EMERGENCY_NUMBER` are
  imported by `helplines.py`, never restated, and the file is untouched (its `M` in `git status`
  is cycles 1-2's pre-existing work).
- No secrets, API keys or `.env` content in any file produced by this cycle.
- `python` was never invoked bare; every run used `backend\.venv\Scripts\python.exe`.

**Status: COMPLETE.**
