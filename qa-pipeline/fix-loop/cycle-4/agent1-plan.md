# Agent 1 — Cycle 4 Plan

**Set S4 — legal-content guardrails: C2, C4, H4, M7, M8, L1, plus D1 (cycle-3 deferral).**

Scope: diagnosis and direction only. **Agent 2 implements and may deviate where it has a better
idea** — the acceptance criteria and §5 "Definition of done" are binding; module names, regexes and
code shape are suggestions. If Agent 2 deviates, say so and why in `agent2-changes.md`.

Constraints carried from `BRIEF.md`: no commits, no branches, no pushes, no live Groq/Gemini calls,
never run bare pytest (`--ignore=tests/test_browser_autofill.py`), never launch a browser or server,
never reduce the passing count. Baseline: `1 failed, 458 passed, 1 xfailed`; the one failure is the
pre-existing `tests/test_doc_generator.py::test_document_generation_notice`.

---

## 0. The finding that decides the whole cycle's shape

Before anything else, read these three lines. They were **already in the chat system prompt at the
commit wave-1 ran against** — verified with `git show HEAD:backend/app/llm/groq_provider.py`, lines
82-84, and identically at `app/llm/gemini_provider.py:83-85`:

```
Do not alter state, invent facts, cite laws not present in verified sources, promise outcomes, or fabricate deadlines.
If verified sources are empty, clearly say the exact legal provision still needs verification instead of guessing.
Never describe the Model Tenancy Act, 2021 as binding local law unless the supplied context confirms State adoption;
identify it as model guidance and say the applicable State tenancy/rent law must be checked.
```

C2 violated line 82 and line 83. H4 violated lines 84-85. And the model was *also* told
machine-readably that the domain has no corpus: `domain_context.rag.corpus_ids` is `[]` for
EMPLOYMENT, CYBER_FRAUD, POLICE_COMPLAINT and GENERAL (`app/domains/employment.py:53`,
`cyber_fraud.py:52`, `legacy.py:45`, `legacy.py:65`) and that field is shipped to the model in
`domain_registry.compact_context` (`app/domains/registry.py:155-158`).

**The prompt-only fix has already been tried, in the exact words the QA report would have
recommended, and it failed on a healthy non-degraded turn.** Every mechanism in this plan is
therefore deterministic Python. Prompt edits appear here only as supplements, never as the load
bearing layer, and no acceptance criterion in §5 depends on model compliance.

A second load-bearing fact: `_tag_response` (`app/services/llm_conversation.py:1846-1851`) is the
single funnel through which **all 14** return paths of the conversation service pass — the live
provider path, the offline `limited_demo` path, the 429 `_rate_limit_fallback`, the provider-error
path, the safety short-circuit, the document gates, and the two `main.py` call sites (1101, 1203).
It receives the `ChatTurnResponse`, which carries `case_profile` — and therefore `category`,
`user_state`, `key_facts` and `legal_sources`. That is everything a guard needs. One edit there
covers the rate-limited path, which is what design constraint 1 demands.

There is exactly **one** model-text surface that does *not* pass through it:
`app/services/case_summary.py:80-103::generate_case_summary`, called from `app/main.py:975`, whose
output has a literal **"Relevant Laws"** heading and is then **cached into
`profile.ai_summary_cache` and persisted** (`main.py:981-983`). An unguarded fabricated citation
there is served forever. It must be guarded too, and guarded *inside* `generate_case_summary` so the
cache stores the guarded text.

---

## 1. Order within the cycle, and why

| # | Item | Findings | Why here |
|---|---|---|---|
| **1** | Strip the internal fact key from the model payload | **D1** (cycle-3 DoD #7) | Smallest item in the cycle, independent of everything else, and it closes an unmet definition-of-done from the previous cycle. Two providers, one shared helper. Do it first so it cannot be crowded out again. |
| **2** | Source provenance + `legal_sources_status` | **M8**, and the data half of **C2** | The guard cannot tell a verified provision from a bare Act-name link until this exists. Today `legal_sources` mixes both indistinguishably (see §2.2) — that mixture is what seeded C2. This is the guard's input contract, so it lands before the guard. |
| **3** | `response_guard.py` — pure functions, no wiring | **C2**, **C4**, **M7**, **L1** | The whole mechanism, written and unit-tested in isolation against recorded strings from `wave-1/transcripts.json`. Zero blast radius while it is unwired. |
| **4** | Wire the guard in: `_tag_response` + `generate_case_summary` | all of the above | One edit each. Then the no-op regression suite over every deterministic composer (§3). This is where the blast radius lives, so it lands after the guard is already proven in isolation. |
| **5** | Model-law adoption gate | **H4** | Last of the substantive items: it is the only one that needs *curated legal knowledge*, which is the same class of risk we are fixing. The honest default (§2.5) makes it safe, but it deserves the calmest slot. |
| **6** | Tidy: helpline registry carries the operator name | **L1** | Rides item 3's registry; a two-line rewrite rule. Separate row only so Agent 3 can check it independently. |

Rationale for 2-before-3: the closed, checkable thing in this cycle is *what the product already
vouches for*. Item 2 makes that set machine-readable. Rationale for 3-before-4: the guard is a text
transformer over strings we already have on disk, so it can be fully proven with no service, no
provider and no fixture — and then the wiring step is a two-line diff whose only job is coverage.

---

## 2. Per-finding detail

### D1 — the internal field name leaked to the user (cycle-3 deferral, DoD #7)

**Problem.** W1-02 turn 2, verbatim:

> *"Aapke formal notice ke liye landlord ya property manager ka **naam (opposite_party_name)**
> chahiye. Kya aap uska naam share kar sakte hain?"*

**Root cause (confirmed in source).** `domain_registry.compact_context`
(`app/domains/registry.py:141-150`) builds each `next_fact_candidates` entry as
`{"key", "meaning", "purpose", "priority_reason", "stage"}`. `key` is the internal profile field
name. `llm_conversation.py:386-390` trims the list to one entry and hands the whole dict to the
model inside `LLMResponseContext.domain_context`; both providers serialise it verbatim
(`groq_provider.py:187`, `gemini_provider.py:188`). The prompt says *"Never expose internal IDs,
priorities, readiness labels, scoring, or field names"* — a value the model can see in its input is a
value it will eventually echo, and it did.

**Proposed fix.** Give `LLMResponseContext` one serialisation entry point and redact there, so no
future provider can reintroduce the leak:

1. Add a method to `LLMResponseContext` (`app/llm/contracts.py`), e.g. `model_payload()`, that
   returns `model_dump(mode="json")` with `domain_context["next_fact_candidates"]` rebuilt as
   `[{k: v for k, v in entry.items() if k != "key"} for entry in ...]`.
   **Build a new list and a new dict — do not mutate in place.** `llm_conversation.py:517-518`
   reads `context.domain_context["next_fact_candidates"][0]` *after* the provider returns and
   passes it to `fact_candidate()`, which needs `key` (`pending_interaction.py:110`). Relying on
   `model_dump` to deep-copy a `Dict[str, Any]` field is an implementation detail; an explicit copy
   is not.
2. `GroqProvider.chat` (`groq_provider.py:186-193`) and `GeminiProvider.chat`
   (`gemini_provider.py:185-189`) call it instead of `context.model_dump(mode="json")`. Groq's
   existing `payload["user_message"] = payload.pop("user_message")` reorder stays.
3. Belt-and-braces in the guard (item 3): delete a parenthetical whose entire content is a snake_case
   token that matches a known fact key. **This is the one place in the cycle where an enumerated list
   is legitimate** — fact keys are a closed set, read live from
   `domain_registry.resolve(category).facts` plus `StructuredCaseProfile.model_fields`, never a
   hand-typed wordlist.

**What must not break.** Pending-interaction binding. `fact_candidate` must still receive `key`, so
`test_pending_interaction.py` and the `next_fact_candidates` assertions in
`test_llm_conversation.py` (including the no-re-asking assertion cycle 3 left untouched) must stay
green. `llm_conversation.py:1833` reads `compact_context(...)["next_fact_candidates"]` directly for
`_safe_next_prompt` — that call does not go through the provider and must keep its `key`.

**Risk / blast radius.** Low. The model loses a token it was never meant to use. `meaning` is the
product's own human phrasing and remains, so question quality is unaffected. `priority_reason`
(`BLOCKING`/`HIGH`) and `stage` are also internal-ish; stripping them is Agent 2's call, but if it
does, say so — the prompt tells the model to *"Use its purpose and order"*, so removing `purpose`
would change behaviour and must not be done.

**How Agent 3 verifies (no LLM).** Construct an `LLMResponseContext` with a populated
`domain_context`; assert `"key" not in payload["domain_context"]["next_fact_candidates"][0]` and
`"opposite_party_name" not in json.dumps(payload)`; then assert
`context.domain_context["next_fact_candidates"][0]["key"] == "opposite_party_name"` **after** the
call, proving the source object is untouched. Add an end-to-end `FakeGeminiProvider` turn that
asserts the resulting `pending_interaction.target_keys == ["opposite_party_name"]`.

---

### M8 — the no-corpus limitation is never disclosed (and the "31 null sources" evidence is a harness artifact)

**Problem (as reported).** *"The no-corpus limitation is never disclosed; `sources` was null on all
31 turns. EMPLOYMENT, CYBER_FRAUD and POLICE_COMPLAINT have no corpus at all."*

**Root cause — correct the record first.** I checked `wave-1/transcripts.json`. Every turn records
`"sources": null` **and** `"mode": null`, including W1-04 and W1-05 which the report itself
describes as `mode=limited_demo`. The API response model has no `sources` field and no `mode`
field: it exposes `llm_mode` and puts citations at `case_profile.legal_sources`
(`app/schemas/chat.py:144-156`, `:108`). The harness also stored only a 13-key subset of the
profile, which does not include `legal_sources`. **`sources: null` on all 31 turns is a QA harness
read of two non-existent keys, not evidence that retrieval returned nothing.** Whoever runs wave 2
must fix the harness to read `llm_mode` and `case_profile.legal_sources`, or wave 2 will re-report
this as unfixed. Flag it to Agent 4.

**The real defects underneath M8, both confirmed in source:**

1. **`legal_sources` conflates two different kinds of thing.** `_verified_sources`
   (`llm_conversation.py:1698-1729`) builds a list of retrieved corpus provisions
   (`act`, `section`, `title`, `description`, `source_url`, `document_type`, …) and then appends
   `domain.rag.official_sources` entries, which are only `{title, authority, url}`
   (`app/domains/contracts.py:202-207`). For EMPLOYMENT the appended entry is
   `title="Code on Wages, 2019"` (`app/domains/employment.py:55`) — **an Act name with no section
   and no text, sitting in a list labelled `legal_sources`, formally indistinguishable from a
   verified provision.** That is, in my reading, the direct seed of C2's
   *"Code on Wages, 2019 – Section 9(1)"* with an invented verbatim quote: the model was handed the
   Act name and nothing to quote from it.
2. **For the three corpus-less domains `profile.legal_sources` is empty by construction.**
   `llm_conversation.py:391-395` filters to `source.get("act") and source.get("section")`; the
   corpus-less domains have no such entries, so the workspace "Laws & rights" panel shows
   *"Relevant laws will appear once identified for this case"*
   (`frontend/src/components/CaseWorkspacePanel.tsx:124`) — indistinguishable from "we haven't got
   there yet". The user is never told the difference between *not yet retrieved* and *nothing exists
   to retrieve*.

**Proposed fix.** Deterministic, in `_verified_sources` and the response context.

1. Tag provenance on every entry, e.g. `"kind": "verified_provision"` for corpus citations and
   `"kind": "official_source_link"` for `official_sources` entries. **Apply the same key to both
   the context list and `profile.legal_sources`** — `tests/test_llm_conversation.py:516` asserts
   `all(source in provider.response_context.legal_sources for source in
   response.case_profile.legal_sources)`, an identity-of-dicts assertion that breaks if only one
   side gains a key.
2. Add a `legal_sources_status` block to `LLMResponseContext`, deterministically computed:
   `{"corpus_available": bool(domain.rag.corpus_ids), "verified_provisions": <int>,
   "case_law_available": False}`. `case_law_available` is hard-`False`: there is no case-law corpus
   anywhere in the repo (`app/data/` holds exactly three statute files —
   `consumer_protection_act_2019.json`, `model_tenancy_provisions.json`, `rti_act_2005.json`).
3. Record the same on the profile for the UI, e.g.
   `profile.key_facts["verified_sources_available"] = bool(profile.legal_sources)`, so the workspace
   can distinguish "none yet" from "none exists for this domain". A frontend copy change in
   `CaseWorkspacePanel.tsx:124` is welcome but optional this cycle.
4. **The user-facing disclosure is the guard's job (item 3), and it is conditional.** Append the
   disclosure line **only when the reply actually makes a legal-provision claim** — that is, when
   the guard finds a citation token or a statute name in the reply — never on every corpus-less
   turn. Two reasons: nagging every turn is bad product, and
   `tests/test_api.py:170` asserts `"_" not in early_body["reply_text"]` on an **EMPLOYMENT** case.
5. **The disclosure line names the authority, not the URL.** EMPLOYMENT's official source URL is
   `https://labour.gov.in/sites/default/files/the_code_on_wages_2019_no._29_of_2019.pdf` — six
   underscores. Pasting it into a reply breaks `test_api.py:170` outright, and a raw URL in chat
   prose is not something a user can eyeball anyway. Say *"the official text published by the
   Ministry of Labour & Employment"*; the URL already reaches the user through the workspace panel.

**What must not break.** `tests/test_llm_conversation.py:514-516`;
`tests/test_case_summary.py:56` (`provider.calls[0].legal_sources == []`);
`tests/test_rag_query_builder.py:195-198` (extraction context must still have no `legal_sources`);
`tests/test_groq_prompt_caching.py:182`, which passes `legal_sources=[{"text": ..., "section": ...}]`
with **no `act` key** — the guard and the provenance code must tolerate missing keys and never
`KeyError`.

**Risk / blast radius.** Low-medium. `profile.legal_sources` is persisted and read by
`case_summary.summary_input` (`identified_laws`) and by the frontend's typed interface
(`frontend/src/lib/api.ts:316-325`). Adding a key is additive; the TS type does not forbid extras.
Do not rename or remove `act`/`section`/`document_type` — `CaseWorkspacePanel.tsx:117,120` keys off
all three.

**How Agent 3 verifies (no LLM).** Unit-test `_verified_sources` directly with a synthetic
EMPLOYMENT profile: every entry has `kind`, no entry has both `act` and `section`,
`legal_sources_status["corpus_available"] is False`, `verified_provisions == 0`. Same for CONSUMER:
`corpus_available is True`, `verified_provisions >= 1`, and at least one
`kind == "verified_provision"` entry. Then a `FakeGeminiProvider` turn on EMPLOYMENT whose `chat()`
returns a reply containing a statute claim → the disclosure line is present; and one returning a
reply with no statute claim → the disclosure line is **absent**, and `"_" not in reply_text`.

---

### C2 (CRITICAL) — fabricated Supreme Court citation and invented verbatim statutory text

**Problem.** W1-03 turn 3, a healthy non-degraded turn (`degraded_quota: False`), verbatim from
`transcripts.json`:

> **Code on Wages, 2019 – Section 9(1)**
> *"Wages shall be paid in full and without any unauthorized deductions to the employee's account or
> in cash, as per the employer's practice, **before the expiry of the wage period**."*
>
> **Supreme Court case you can quote**
> **M/s. M. S. R. Enterprises v. State of Karnataka**, (2020) 6 SCC 123.
> The Court held that any delay beyond the period prescribed in Section 9 of the Code on Wages
> amounts to a violation …
>
> You can refer to Section 9(1) of the Code on Wages and cite the *M.S.R. Enterprises* decision when
> drafting your legal notice.

Both fabricated. s.9 is the floor-wage provision; the timing rule is s.17. The reply ends by telling
the user to put both into a legal notice.

**Root cause (confirmed in source).** Three layers, all necessary:
1. EMPLOYMENT has `corpus_ids=()` (`app/domains/employment.py:53`), so
   `StatutoryRAG.retrieve_for_context` returns `[]` at `app/agents/rag_node.py:160-165`. Nothing was
   retrieved, so nothing constrained the answer.
2. The only thing `legal_sources` *did* contain was the bare `official_sources` entry
   `title="Code on Wages, 2019"` with no section and no text — the gap described in M8 above. The
   model was given the Act's name and asked to be useful about a wage-timing question.
3. The prompt forbade exactly this (§0) and was ignored. There is no output-side check anywhere:
   `_finish_chat_response` (`llm_conversation.py:500-533`) passes `reply` through untouched.

**Design: three failure modes, three mechanisms.** Design constraint 3 is right that these are
different problems, and the right actions differ:

| | Failure | Checkable rule | Action |
|---|---|---|---|
| **(a)** | Statute citation in a **corpus-less** domain | `domain.rag.corpus_ids == ()` → *no* section number in that domain can be verified | **Strip** the section token and any quoted statutory text; keep the Act name; append the disclosure line |
| **(b)** | Provision **is** in the corpus but misapplied (H4) | corpus metadata says `document_type == "Model law - State adoption must be verified"` (`rag_node.py:118`) | **Caveat**, deterministically appended, never strip — see H4 |
| **(c)** | **Case-law** citation, any domain | the corpora contain **zero** case law, so this needs no allowlist at all — it is unconditionally unverifiable | **Strip** the sentence, append the case-law disclosure |

**(c) is the cleanest rule in the whole set and should be implemented first**: a universal,
unconditional, allowlist-free prohibition, justified by a fact about the repo rather than by any
judgement about a particular case name. It is also the highest-harm one: a fabricated judgment cited
in a legal notice is the failure most likely to get a real user laughed out of a real forum.

**Proposed fix.** New module `app/services/response_guard.py` — a sibling of `safety_triage.py`,
pure functions, no I/O, no provider, no async. Signature roughly
`guard_reply(reply: str, profile: StructuredCaseProfile) -> GuardResult` where `GuardResult` carries
the rewritten text plus a list of machine-readable `redactions` for logging and for Agent 3's
assertions. Invoked from `_tag_response` (item 4).

1. **The verified-citation allowlist is the union of what the product already vouches for**, built
   live from code, never hand-typed:
   - the turn's retrieved provisions — entries of `profile.legal_sources` with
     `kind == "verified_provision"`;
   - **the curated citation map in `app/services/doc_generator.py:135-159`**, filtered to the
     doc types applicable to this case's domain. This matters: that map already stands behind
     Indian Contract Act s.73, BNSS s.173, IT Act s.66D, CPA s.35 and RTI s.6(1), several of which
     are *not* in any RAG corpus. Without this union the bot would generate a notice citing IT Act
     s.66D and then delete "s.66D" from the chat message explaining that same notice. Do not ship
     that contradiction;
   - Act **names** (no sections) from `domain.rag.official_sources`, which licenses naming the Act
     but never a section of it.
2. **Statute-citation matching, and its normalisation rules** — this is where over-filtering will
   happen if it happens, so be conservative:
   - Find `(?:Section|Sec\.?|S\.|Article|धारा)\s*<number>` where `<number>` is
     `\d+[A-Z]?(?:\(\d+\))*` with optional `&`/`and`/`,`/`-`/`to` runs.
   - Corpus sections are stored as human strings: `"Section 34 & 35"`, `"Section 2(11)"`,
     `"Section 69"`. Expand each to a set of bare section numbers — `{34, 35}`, `{2(11)}`, `{69}`.
   - **A cited sub-clause of an allowlisted section is verified.** `69(2)` against corpus `69`
     passes: the report itself faults the bot for *not* mentioning CPA s.69(2), so a rule that
     deleted it would be self-defeating.
   - **A section number with no Act named is verified if the number is in the domain's own
     allowlist.** Do not require the reply to repeat the Act name; models don't.
   - **Require Act-name matching only to reject**: a citation is unverified when the reply names an
     Act that is in no allowlist for this domain (e.g. "Code on Wages" for a corpus-less EMPLOYMENT
     case; "Payment of Wages Act / Industrial Disputes Act" in W1-06 t4) **or** when the domain has
     no corpus at all. Act-name comparison must be loose — casefold, strip `,`/`.`, treat "Act 2019"
     and "Act, 2019" as equal — because a strict compare against the corpus's own sloppy
     `"Model Tenancy Act / State Rent Control Acts"` string would delete correct citations.
   - **When in doubt, caveat, do not delete.** If the guard cannot decide, it leaves the text and
     appends the disclosure. A deleted-but-correct citation is a silent harm the user cannot detect.
3. **Case-law matching.** Two independent shapes, either one triggers:
   `\bv(?:s?\.|ersus)\s` between two capitalised runs, and a reporter citation
   `\(?(?:19|20)\d{2}\)?\s*\d+\s*(?:SCC|SCR|AIR|SCALE|SCJ|SCW|Bom|Del|Mad|Cal|Kar|All|Guj|Raj|P&H)\b`.
   Action: remove the whole sentence containing the match; then drop any markdown heading left with
   nothing under it (in W1-03 t3 that is `**Supreme Court case you can quote**`). Append: the bot has
   no verified case-law database, cannot cite judgments, and no case name should be put in a legal
   notice without independent verification. Sentence granularity is what also kills
   *"You can refer to Section 9(1) … and cite the M.S.R. Enterprises decision when drafting your
   legal notice"*.
4. **Quoted statutory text.** When a stripped citation is immediately adjacent to a quotation
   (`"…"`, `*"…"*`, `> …`) attributed to it, remove the quotation too. A section number the user
   might mis-cite is bad; a fabricated *verbatim quote* they might paste into a notice is worse.
5. **Prompt supplement (not load-bearing).** Add `legal_sources_status` to the payload (item 2) and
   one line: when `corpus_available` is false, name the Act only as background and give no section
   number. Expect nothing from it; §5 does not test it.

**What must not break.** See §3 in full. Specifically: the CONSUMER corpus's seven provisions
(2(11), 2(47), 2(34), 34 & 35, 47, 58, 69 — and 69(2)) and the TENANCY corpus's four (11, 15, 21, 30)
must pass through untouched. `tests/test_doc_generator.py` and `tests/test_document_registry.py`
must be unaffected — the guard runs on chat replies, never on generated documents.

**Risk / blast radius.** This is the highest-risk item in the cycle: a false positive silently
deletes correct law and makes the product vaguer than it is today. Mitigations: the union allowlist,
loose Act-name comparison, sub-clause tolerance, caveat-on-doubt, and a `redactions` list so every
removal is observable in a test and in the log rather than invisible. The guard must be **idempotent**
(running it twice changes nothing) because the retry path re-guards a reply on resume.

**How Agent 3 verifies (no LLM).** These are model-output defects, so test the guard **directly
against recorded strings**. Concretely: load `qa-pipeline/wave-1/transcripts.json` in the test — or
better, copy the exact reply strings into `tests/fixtures/` so the test does not depend on a path
outside `backend/` — and assert:
- W1-03 t3 verbatim + synthetic EMPLOYMENT profile → output contains **no** `"(2020) 6 SCC 123"`,
  no `"M. S. R. Enterprises"`, no `"Section 9(1)"`, no `"Supreme Court case you can quote"`; contains
  the case-law disclosure and the unverified-provision disclosure; still contains the useful prose
  about wage-period timing; `redactions` names one `case_law` and one `statute_no_corpus`.
- The same string guarded twice == guarded once (idempotence).
- **Negative locks:** a CONSUMER reply citing "Section 35 of the Consumer Protection Act, 2019",
  one citing bare "Section 69(2)", one citing "Section 2(11)", and a CYBER_FRAUD reply citing
  "Section 66D of the Information Technology Act, 2000" → **byte-identical output, zero redactions.**
- End-to-end: a `FakeGeminiProvider` whose `chat()` returns the verbatim W1-03 t3 string; run
  `process_turn`; assert the same removals on `response.reply_text`, proving the wiring, with
  `provider.chat_calls == 1` and no network.

---

### C4 (CRITICAL) — invented numeric win probabilities

**Problem.** W1-06 turn 4, verbatim:

> **Rough chance of a favourable outcome:** **≈ 30 %**
> **Rough chance of a favourable outcome:** **≈ 40 %**
> - **Tour-operator refund:** ~30 % chance, mainly limited by the two-year limitation period.
> - **Employer settlement:** ~40 % chance, limited by the three-year limitation period …

Four occurrences, all in that one turn. Nothing computes these numbers; the report's praise that
*"No case was ever told it would win"* survives only because the model hedged the wording, not
because anything stopped it.

**Root cause.** No output check exists. Nothing in the prompt forbids a probability estimate either —
it forbids promising *outcomes*, which the model evidently read as satisfied by "rough" and "≈".

**Proposed fix.** A rule in `response_guard.py`. This is the cheapest item in the cycle and the one
with the smallest legitimate-use overlap.

1. Match a percentage quantity `(?:≈|~|approx\.?|about|around|roughly)?\s*\d{1,3}\s*%` occurring
   within ~60 characters of an outcome word. **The outcome-word list is the closed side of the
   rule** — `chance`, `chances`, `odds`, `probability`, `likelihood`, `likely to succeed`, `success
   rate`, `favourable`, `favorable`, `win`, `jeetne`, `safalta`, `संभावना`, `जीतने` — and it is what
   keeps the rule from being a blanket percentage ban.
2. Replace the quantity with deterministic prose in the user's script: the strength of a case cannot
   be reduced to a number, and the factors that matter are X and Y (keep the model's own factor list,
   which was actually good). Where the percentage *is* the whole line — `**Rough chance of a
   favourable outcome:** **≈ 30 %**` — remove the line.
3. **Unicode is load-bearing here.** The recorded string uses `≈` U+2248, `~`, and **U+202F NARROW
   NO-BREAK SPACE** between the digits and `%`, and U+2011 NON-BREAKING HYPHEN elsewhere in the same
   reply. Python's `\s` does match U+202F, but a hand-written `[ ]` or `\x20` will not, and dash
   classes must be written `[-‐-―]`. Agent 3's test must use the **verbatim** transcript
   string, not a retyped ASCII approximation, or it will pass against code that fails in production.

**What must not break.** Legitimate percentages: interest at "18% per annum", "12% GST",
"100% refund", "2% per month", "a 50% deposit". None of these sit near an outcome word. Add each as
an explicit negative test.

**Risk / blast radius.** Low. Isolated rule, no state, no interaction with citations.

**How Agent 3 verifies.** Guard the verbatim W1-06 t4 string (with its real U+2248/U+202F bytes):
zero `%` characters remain in an outcome context, all four occurrences gone, the two factor tables
survive, `redactions` lists four `outcome_probability` entries. Negative: the five legitimate
percentage strings above pass through byte-identical.

---

### H4 (HIGH) — the Model Tenancy Act presented as operative Rajasthan law

**Problem.** W1-02, verbatim. Turn 1 hedged once and then dropped it:

> *"Model Tenancy Act (ya aapke state ke Rent Control Act, agar adopt kiya gaya ho) ke **Section 11**
> ke mutabik …"* … *"Saath hi yeh bhi batayein ki aap Model Tenancy Act ke Section 11 ka hawala de
> rahe hain."*   ← the user is told to cite it in a notice, unhedged
> *"Iske liye aapko local rent authority ka address aur jurisdiction confirm karna hoga (Rajasthan ke
> liye "Jaipur Rent Authority" ya similar)."*

Turn 2 dropped the hedge entirely:

> *"Model Tenancy Act / State Rent Control Acts ki **Section 30** ke mutabik, tenancy-related
> disputes ko "local jurisdiction" ke rent authority ya rent court mein hi adjudicate kiya jata
> hai."*
> *"**Jaipur Rent Authority ya District Court** ka exact address aur contact details confirm karein
> (Jaipur Municipal Corporation ya **Rajasthan State Rent Authority ke website** par mil sakta hai)."*

**Root cause — and one correction to the report.** The report says "invented 'Section 11' and
'Section 30'". **They are not invented.** Both are literally in
`backend/app/data/model_tenancy_provisions.json`:

- `"Section 11" — "Security Deposit and Refund Obligation"`, act
  `"Model Tenancy Act / State Rent Control Acts"`
- `"Section 30" — "Jurisdiction of Rent Authority and Rent Court"`, whose description reads
  *"shall be adjudicated by the Rent Authority and Rent Court of the local jurisdiction."*

This is failure mode (b): the corpus leaking. Three concrete defects:

1. **The corpus's own `act` field conflates a model law with enacted state law** —
   `"Model Tenancy Act / State Rent Control Acts"`. A citation retrieved under that name is
   pre-laundered; no downstream code can tell which of the two it is.
2. **The one honest signal is never surfaced to the user.** `rag_node.py:118` sets
   `document_type: "Model law - State adoption must be verified"` on every TENANCY entry. The
   frontend does render it (`CaseWorkspacePanel.tsx:120`), but only inside a collapsed `<details>` in
   the side panel — never in the reply text, and never as a constraint on what the reply may say.
3. **"Jaipur Rent Authority" is the model localising the corpus's own generic phrase.** The
   description says "the Rent Authority and Rent Court of the local jurisdiction"; the case state
   says Jaipur; the model composed the two into a named institution, then invented a website for it.

Rajasthan has not enacted the MTA, so the turn-2 assertion is simply wrong law.

**Proposed fix. Caveat, never strip.** These are the product's only real tenancy provisions;
deleting them would gut the domain. Four parts:

1. **An adoption register, whose default is "unverified".** A small deterministic table keyed by
   state, e.g. in `app/services/jurisdiction.py` (already exists, cycle 3) or beside the corpus.
   **Agent 2 must not populate an adoption claim it cannot source.** The register starts empty or
   comment-only; every state resolves to `UNVERIFIED`, and `UNVERIFIED` behaves exactly like
   `NOT_ADOPTED` for output purposes. This is the point: the register's job is to let a future
   curator *add* a sourced entry, not to let this cycle guess. Fabricating adoption status while
   fixing a fabrication finding would be the worst possible outcome of this cycle.
2. **The caveat is appended deterministically by the guard**, in the user's script, whenever the
   reply cites a TENANCY provision (detected by allowlist membership, not by a keyword) and the state
   is not a sourced `ADOPTED` entry: the Model Tenancy Act, 2021 is a central *model* law that each
   State must enact for itself; it has not been confirmed as enacted in `<state>`, so the applicable
   State tenancy or rent-control law must be checked before relying on a section number. The model is
   no longer allowed to decide whether to hedge.
3. **Neutralise the "cite this in your notice" instruction.** When the caveat applies and the reply
   tells the user to quote/cite/`hawala dena` the provision in a notice, petition or complaint,
   rewrite that clause to say the provision is a starting point and the equivalent provision of the
   applicable State law must be confirmed first. Sentence-level, same machinery as C2(c).
4. **Constructed-authority *contact* claims.** Narrow rule, aimed at the actionable harm only:
   a pattern of `<Place> Rent Authority|Rent Court|Rent Tribunal` (or any `<Place> <Authority>`
   construction) combined with a **website / portal / site / address / contact details** claim, where
   no allowlisted URL is present, is replaced by: the applicable State's rent authority or civil
   court, which the user will need to confirm for `<state>`.
   **Do not touch the institution name on its own, and do not touch the jurisdiction reasoning.**
   W1-02 t2's *"jurisdiction property ke location par hoti hai … Jaipur mein file karni padegi,
   Bengaluru mein nahi"* was singled out for praise in report §5 and must come through verbatim.
5. **Data fix, small and worth doing:** split the corpus's `act` field so an MTA provision is named
   `"Model Tenancy Act, 2021"` and nothing else. If the four entries are also meant to stand for
   state rent-control equivalents, that belongs in a separate field (e.g.
   `"analogous_to": "State Rent Control Acts"`), not in the Act's name. Keep the `section`/`title`
   strings unchanged — `tests/test_rag_query_builder.py` and the corpus ranking depend on the text.

**What must not break.** The praised W1-02 t2 jurisdiction reasoning (verbatim). The four TENANCY
citations must still *appear* — the caveat adds, it never removes. `rag_node.py`'s
`document_type` string is matched by `CaseWorkspacePanel.tsx:120` with
`.toLowerCase().includes('model law')` — do not reword it to something that stops matching.
`tests/test_intake_before_document.py::test_G_tenancy_asks_issue_facts_not_document_fields` and all
of `test_domain_architecture.py` (`validate_corpora` at `registry.py:211-217` raises on unknown
corpus IDs) must stay green.

**Risk / blast radius.** Medium. The caveat lands on essentially every substantive HOUSING_TENANT
turn, which will change many reply strings; check every tenancy assertion in
`test_intake_before_document.py`, `test_pending_interaction.py` and `test_cycle3_verification.py` for
incidental string matches. Append the caveat as a **separate trailing paragraph** so no existing
`in reply_text` assertion is split apart.

**How Agent 3 verifies.** Guard the verbatim W1-02 t1 and t2 strings against a HOUSING_TENANT
profile with `user_state="Rajasthan"`: `"Section 11"` and `"Section 30"` **still present**; the
adoption caveat present; `"Rajasthan State Rent Authority ke website"` absent; the jurisdiction
sentence about Jaipur vs Bengaluru present byte-for-byte; the "cite it in your notice" clause
rewritten. Plus: a state with a sourced `ADOPTED` entry (synthetic, injected in the test) gets no
caveat, proving the register is actually consulted.

---

### M7 (MEDIUM) — invented helpline numbers

**Problem.** W1-04 turn 2 and turn 3, verbatim (note the U+2011 non-breaking hyphens in the source):

> *"**RBI 1930 helpline (1800‑11‑001‑112) ko call karein**"*
> *"SBI ka **24-hour helpline (1800‑11‑222‑222)**"*

Both invented. A fraud victim dialling a wrong number during the golden hour loses the window — this
is the finding whose real-world cost is highest relative to its severity label.

**Root cause.** The prompt names 1930, 112, 1091 and cybercrime.gov.in in prose
(`groq_provider.py:74`, `gemini_provider.py:66`) and nothing else. There is no registry of official
numbers in the model's context and no output check, so when the reply reached for a toll-free number
the model generated a plausible digit string.

**Proposed fix.** A curated registry plus a filter. Note what already exists and **must not be
duplicated or contradicted**: `safety_triage.py:44-49` already defines

```python
CRISIS_HELPLINES = (("Tele-MANAS", "14416"), ("KIRAN", "1800-599-0019"), ("AASRA", "9820466726"))
EMERGENCY_NUMBER = "112"
```

with the comment *"Kept here so every crisis reply, in every script, is generated from one source and
can never be invented by a model."* Cycles 1-2 got there first and got it right.

1. Create the registry (a module constant, or `app/data/helplines.json` loaded once) holding entries
   of `{number, service, operator, scope}`, and **import the crisis lines from `safety_triage.py`
   rather than restating them.** Candidate entries: 112 (emergency), 1930 (cyber-fraud), 1915
   (National Consumer Helpline), 14416 (Tele-MANAS), 1098 (Childline), 181 (women), 1091 (women,
   police), 15100 (NALSA/DLSA legal aid — already used at `conversation_agent.py:173` and
   `classifier_node.py:43`), plus the three crisis lines above.
   **Agent 2 verifies every entry against an official source and adds nothing it cannot source** — a
   wrong number in this registry is the identical harm to M7 itself, just harder to spot.
2. Inject the registry into the response context so the model has real numbers to reach for, and
   state in one prompt line that no other number may be given. Supplement, not the fix.
3. **The filter, in `response_guard.py`.** A digit run in **helpline shape** —
   `1800[-‐-―\s]?\d[\d‐-―\s-]{5,}` (toll-free) or a bare 3-4 digit short code
   adjacent to a call/helpline word — that is **not** in the registry is removed, keeping the named
   service. `SBI ka 24-hour helpline (1800‑11‑222‑222)` → `SBI ka 24-hour helpline`, plus one
   deterministic sentence: verify the bank's helpline on your own card, passbook or the bank's
   official site.
4. **URLs.** Only four distinct URL-shaped strings appear across all 31 turns:
   `https://www.cybercrime.gov.in/` (allowlisted — `cyber_fraud.py:55`), `sbi.co.in`, and
   `https://www.rbi.org.in/Scripts/Complaints.aspx`. The last is the interesting case: the *host* is
   allowlisted (`cyber_fraud.py:56` carries an `rbi.org.in` notification PDF) but that *path* is not
   one the product vouches for. **Allowlist by exact URL, not by host** — a fabricated path on a real
   host is still a dead end for the user. An off-allowlist URL is replaced by the authority's name in
   prose, or by the allowlisted URL for the same authority if there is one.
5. **Numbers already in the case are never touched.** Before removing any digit run, check it against
   the user's own turn text and the case profile (`user_phone`, `transaction_id`, `disputed_amount`,
   opposite-party contact) and against evidence findings. W1-01's order number `18499` and W1-02's
   `85000` must survive, as must a landlord's phone quoted back from an uploaded rent agreement.
6. **The guard must never *inject* a helpline number.** `tests/test_cycle1_verification.py:216`
   (`"14416" not in responses[2].reply_text`) and
   `tests/test_safety_crisis_and_scope.py:197,207` assert the crisis numbers are *absent* from
   non-crisis turns. The registry is a filter and a model-context input — it is not a footer.

**What must not break.** Report §5's praise: *"Cyber-fraud first response is well sequenced: 1930 and
cybercrime.gov.in near the top."* Both are registry/allowlist members and must pass through
untouched. The crisis copy in `safety_triage.py:530-559` (112, 181, 1098) must pass through
untouched. Indian-grouped amounts — `₹85,000`, `Rs 1,45,000` — must never be read as phone numbers;
make the amount shape `\d{1,3}(?:,\d{2})*,\d{3}` an explicit exclusion.

**Risk / blast radius.** Medium. Over-eager digit matching is the hazard; the "already in the case"
check in (5) and the amount exclusion are what contain it.

**How Agent 3 verifies.** Guard the verbatim W1-04 t2 and t3 strings: `1800-11-001-112` and
`1800-11-222-222` absent **in their U+2011 form**, `1930` and `https://www.cybercrime.gov.in/`
present, `https://www.rbi.org.in/Scripts/Complaints.aspx` absent or replaced. Negative locks: the
three `safety_triage` crisis strings in all three scripts pass byte-identical; a reply containing
`₹85,000` and `Rs 1,45,000` and `order 18499` passes byte-identical; a reply quoting a phone number
that is present in `profile.user_phone` passes byte-identical.

---

### L1 (LOW) — 1930 attributed to RBI

**Problem.** W1-04 t1 *"1930 helpline (RBI) call karein"*; t2 *"RBI 1930 helpline"*; W1-08 t1
*"Call the RBI-designated 1930 helpline"*. The number is right; the operator is not — 1930 runs under
MHA / the Indian Cyber Crime Coordination Centre (I4C).

**Root cause.** `groq_provider.py:74` / `gemini_provider.py:66` name 1930 with no operator, in the
same sentence as a bank freeze, so the model supplied the plausible authority.

**Proposed fix.** Rides M7's registry, which carries `operator` per entry. Two parts: the registry
entry states `operator: "Indian Cyber Crime Coordination Centre (I4C), Ministry of Home Affairs"`
and that reaches the model's context; and a guard rewrite rule — when an allowlisted number is
attributed to an authority other than its registry `operator`, drop the wrong attribution
(`RBI 1930 helpline` → `1930 cyber-fraud helpline`; `RBI-designated 1930 helpline` →
`1930 cyber-fraud helpline`). Matching on "an allowlisted number adjacent to an authority name that
contradicts the registry" is closed and checkable; do not attempt a general authority-attribution
checker.

**What must not break.** RBI's genuine role in the cyber-fraud domain — the unauthorised-transaction
liability notification (`cyber_fraud.py:56`) is real RBI material and replies may cite RBI for it.
Only the *1930 operator* attribution is corrected. Do not turn this into "delete RBI from
cyber-fraud replies" — H9 (the RBI limited-liability window) is S7's job and needs RBI namable.

**Risk / blast radius.** Very low.

**How Agent 3 verifies.** Guard the three verbatim strings above: `1930` survives in all three,
`RBI` no longer sits adjacent to it. Negative: a reply citing RBI for the liability notification
passes byte-identical.

---

## 3. What must never be filtered — explicit preservation allowlist

Agent 3 should treat every line here as a required negative test. If the guard changes any of these,
the guard is wrong, regardless of what else it fixed.

**Correct law that is in the corpus:**
1. CONSUMER: `Section 2(11)`, `2(47)`, `2(34)`, `34 & 35`, `Section 35` alone, `Section 47`,
   `Section 58`, `Section 69`, and the sub-clause `Section 69(2)`.
2. HOUSING_TENANT: `Section 11`, `15`, `21`, `30` — **these survive with a caveat added; they are
   never deleted.**
3. Citations the product already puts in generated documents (`doc_generator.py:135-159`):
   Indian Contract Act 1872 s.73, BNSS 2023 s.173, IT Act 2000 s.66D, RTI Act 2005 s.6(1),
   RBI/2017-18/15 paragraphs 6-10.
4. A section number cited with no Act named, where the number is in the domain's allowlist.

**Behaviour the report praised (§5) — all of it must come through:**
5. W1-02 t2's jurisdiction reasoning: forum follows the property's location, Jaipur not Bengaluru,
   with the postal/online follow-up anticipated. Verbatim.
6. W1-04's cyber-fraud sequencing: 1930 and cybercrime.gov.in near the top, block the instrument,
   preserve evidence, one question at the end.
7. The Devanagari death-threat triage path (W1-05 t1) and all crisis copy from
   `safety_triage.py:530-559` — 112, 181, 1098, 14416, 1800-599-0019, 9820466726 — byte-identical,
   in all three scripts.
8. Amounts in Indian grouping: `₹85,000`, `Rs 1,45,000`, `₹62,000`.
9. Hinglish and Devanagari register. The guard's inserted text must be script-matched via the
   existing `LanguageScript` / `_safety_text` conventions, never English-only.

**Things that look like the targets but are not:**
10. Percentages with no outcome word nearby: `18% per annum`, `12% GST`, `100% refund`,
    `2% per month`, `50% deposit`.
11. Real institutions named generically: `District Consumer Disputes Redressal Commission`,
    `State Commission`, `National Commission`, `e-Daakhil`, `Labour Commissioner`,
    `Industrial Tribunal`, `Rent Authority`, `Rent Court`, `NALSA`, `DLSA`. **Only a *contact/website/
    address* claim about a constructed named authority is touched — never the institution's name and
    never the procedural advice.**
12. Digit strings that belong to the user: order numbers (`18499`), transaction IDs, account last-4,
    `profile.user_phone`, and any number present in the user's own turn text.
13. Phone numbers and addresses quoted from the user's **own uploaded evidence** — a rent agreement's
    landlord phone is legitimate content, not a fabricated helpline.
14. Every deterministic reply the service composes: the cycle-3 document-gate and
    `_document_not_ready_reply` copy, `_document_deferral_reply`, `_localized_fallback_reply`,
    `_safe_next_prompt`, `_fallback_professional_help`, `_limited_demo_prefix`,
    `_temporary_failure_prefix`, `_emergency_reminder`, the `main.py:1096-1099` upload
    acknowledgement, and all `_paced_safety_reply` output. The guard runs on these too (it is wired
    at the funnel) and **must be a provable no-op on every one of them.** Make that an explicit test.
15. `tests/test_api.py:170`'s `"_" not in reply_text` on an EMPLOYMENT case: the guard must not
    introduce an underscore — which means no URL pasted into reply prose (see M8 item 5).

---

## 4. Non-goals for cycle 4

1. **C5 / limitation arithmetic (S6).** W1-06 t4 also contains wrong limitation maths
   (*"deadline was March 2025, which has now passed"* alongside t1's *"you are still within the
   limitation period"*). The guard removes the **percentages** from that reply and nothing else. It
   must not attempt to correct, flag or delete a limitation conclusion. Doing so half-way would
   produce a confidently wrong answer, which is worse than today's unstable one.
2. **H3 and H9 (S7).** The bank's "FIR first" demand endorsed as RBI policy, and the wrong
   *"within 48 hours"* limited-liability window. Both are domain-fact accuracy, not citation
   provenance. L1 is in scope only because it rides M7's registry.
3. **C3 and the document gate.** Closed in cycle 3. Do not touch
   `_document_block_reason`, `document_routing_allowed` or `_document_not_ready_reply` except to
   confirm the guard is a no-op on their output.
4. **S1/S2/S3 territory**: `safety_triage.py`'s pattern sets, the readiness ladder, the extractors,
   `_maybe_reclassify`. Read `safety_triage.py` to *import* `CRISIS_HELPLINES` and `EMERGENCY_NUMBER`;
   do not edit it.
5. **No regeneration path.** "Ask the model again with a stricter instruction" is rejected as a
   mechanism: it costs a live call, the daily allowance is exhausted, the free tier is 8k tokens/min
   against ~10-13k per turn, and the fallback path — where the provider is unavailable — is exactly
   where the guard must still hold. Strip-and-caveat is a pure function; regeneration is not.
6. **No whole-reply suppression.** The guard never replaces a reply wholesale. Surgical edits only,
   at sentence, bullet or line granularity, every one recorded in `redactions`.
7. **No case-law corpus, no new statutory content, no new legal reasoning.** The guard removes and
   caveats; it does not teach. Adding a sourced MTA adoption entry is the only curation permitted,
   and only if it can be sourced (§2, H4 item 1).
8. **Document generation and evidence analysis text.** `doc_generator.py`'s citations are curated by
   a human and are an *input* to the allowlist, not a target. `analyze_document` summaries quote the
   user's own files and stay unguarded this cycle.
9. **No denylist of fake citations, statute names or case names.** Cycles 2-3's lesson. Every rule
   here is anchored to a closed set that is read from code at runtime — the corpus, the document
   citation map, `domain.rag.corpus_ids`, `domain.rag.official_sources`, the helpline registry, the
   fact keys, the outcome-word list — or to an absolute (no case law exists, therefore no case
   citation is verifiable).
10. **`tests/test_doc_generator.py::test_document_generation_notice`** stays red; **N8** stays xfail.
11. **No commits, branches or pushes. No live LLM calls. No browser, no server.**

---

## 5. Definition of done for S4

Agent 3 may call this set fixed when **all** of the following hold, with proof in the cycle-4 test
report. Every criterion is checkable without a live LLM call.

1. `cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`
   shows **≥ 458 passed**, and the only failure is the pre-existing
   `tests/test_doc_generator.py::test_document_generation_notice`. No test was deleted or weakened to
   get there; any test whose expectation legitimately changed is named and justified.
2. **D1** — `LLMResponseContext`'s serialised payload for both providers contains no
   `next_fact_candidates[*]["key"]` and no `"opposite_party_name"` string from that field; the
   in-memory context object still has `key` after `chat()` returns; pending-interaction binding to
   `opposite_party_name` still works end-to-end. Cycle 3's DoD #7 is now met.
3. **C2(c), case law** — The verbatim W1-03 t3 reply, guarded, contains no `"(2020) 6 SCC 123"`, no
   `"M. S. R. Enterprises"`, no `"v. State of Karnataka"`, and no orphaned
   `"Supreme Court case you can quote"` heading; it carries the case-law disclosure. A synthetic
   reply citing any other `X v. Y, (YYYY) N SCC M` in any domain is treated identically — the rule is
   unconditional.
4. **C2(a), statute in a corpus-less domain** — The same guarded reply contains no `"Section 9(1)"`
   and no quoted statutory text attributed to it, still contains the useful wage-timing prose, and
   carries the unverified-provision disclosure naming the Ministry of Labour & Employment **without
   pasting a URL**.
5. **C2, no over-filtering** — A CONSUMER reply citing `Section 35`, `Section 2(11)` and
   `Section 69(2)` of the Consumer Protection Act, 2019; a CYBER_FRAUD reply citing IT Act s.66D; a
   POLICE_COMPLAINT reply citing BNSS s.173; and a HOUSING_TENANT reply citing MTA s.11 and s.30 all
   come through **with their citations intact** and with `redactions` empty except for H4's caveat.
6. **C4** — The verbatim W1-06 t4 reply, guarded (using the real U+2248 / U+202F bytes), contains no
   percentage in an outcome context; all four occurrences are gone; the two factor tables and the
   "which one to chase first" guidance survive. `18% per annum`, `12% GST`, `100% refund`,
   `2% per month` and `50% deposit` pass through byte-identical.
7. **H4** — The verbatim W1-02 t1 and t2 replies, guarded against a `user_state="Rajasthan"` tenancy
   profile: `Section 11` and `Section 30` still present; the adoption caveat present in the user's
   script; `"Rajasthan State Rent Authority ke website"` gone; the Jaipur-vs-Bengaluru jurisdiction
   passage present byte-for-byte. A state with a sourced `ADOPTED` register entry gets no caveat.
   **The adoption register contains no unsourced adoption claim** — Agent 3 reads it and confirms.
8. **M7** — The verbatim W1-04 t2 and t3 replies, guarded: `1800‑11‑001‑112` and `1800‑11‑222‑222`
   (U+2011 form) gone, the named services retained, the verify-with-your-bank sentence added; `1930`
   and `https://www.cybercrime.gov.in/` intact; the off-allowlist `rbi.org.in/Scripts/Complaints.aspx`
   URL gone or replaced.
9. **M7, no over-filtering** — All `safety_triage.py` crisis copy in all three scripts passes
   byte-identical; a reply containing `₹85,000`, `Rs 1,45,000` and `order 18499` passes
   byte-identical; a number matching `profile.user_phone` passes byte-identical. The guard **never
   adds** a helpline number: `test_cycle1_verification.py:216` and
   `test_safety_crisis_and_scope.py:197,207` still pass unchanged.
10. **L1** — `"RBI 1930 helpline"`, `"1930 helpline (RBI)"` and `"RBI-designated 1930 helpline"` all
    lose the RBI attribution and keep `1930`. A reply citing RBI for the unauthorised-transaction
    liability notification passes byte-identical.
11. **M8** — For an EMPLOYMENT/CYBER_FRAUD/POLICE_COMPLAINT profile, `legal_sources_status` reports
    `corpus_available: False`, `verified_provisions: 0`, `case_law_available: False`; every
    `legal_sources` entry carries a `kind`; `profile.legal_sources` and the context list still
    satisfy `test_llm_conversation.py:516`. A reply that makes a provision claim gets the disclosure;
    a reply that makes none does **not**, and `test_api.py:170`'s `"_" not in reply_text` still
    passes. The report's "sources null on 31 turns" is recorded as a **harness artifact** in the
    cycle-4 notes, with the instruction to read `llm_mode` and `case_profile.legal_sources` in wave 2.
12. **Coverage** — The guard demonstrably runs on: the live provider path, the offline
    `limited_demo` path, the 429 `_rate_limit_fallback`, the `resume_response` retry path, and
    `generate_case_summary` (guarded **before** the result is written to
    `profile.ai_summary_cache`). One test per path, each using a fake provider that returns a
    fabricated string.
13. **No-op proof** — A test enumerates every deterministic composer named in §3 item 14 and asserts
    `guard_reply(text, profile).text == text` with `redactions == []` for each.
14. **Idempotence** — `guard(guard(x)) == guard(x)` for all four transcript strings used above.
15. **Observability** — Every edit the guard makes appears in `redactions` with a machine-readable
    reason, and is logged at WARNING with case id, domain and reason — **never the reply text, the
    user's message, party names or amounts**, matching the existing PII discipline at
    `llm_conversation.py:1717-1729`.
16. No commit, no branch, no push; no live Groq/Gemini call in any test; no browser or server
    launched; no `.env` or key material in any cycle-4 artifact.
