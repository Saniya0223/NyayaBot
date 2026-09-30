# Cycle 4 — Orchestrator ground truth (verified against source, not inferred)

Written by the orchestrator while Agent 1 planned, by reading the actual code paths.
Agent 2: where this contradicts `agent1-plan.md`, **this file wins on facts** — but read Agent 1's
reasoning about *what to do* about them. Every line below cites file:line and was checked.

## 1. The `(opposite_party_name)` leak — the vector is not where it looks

`llm_conversation.py:1479-1498` already has `_blocking_fact_phrases()`, which maps keys to
`FactDefinition.meaning` and whose docstring claims "The raw key never leaves this function".
**That claim is true of that function and irrelevant to the bug.** The key reaches the model by a
different road:

`domains/registry.py:141-150` — `compact_context()` builds `next_fact_candidates` as
```python
{"key": fact.key, "meaning": fact.meaning, "purpose": ..., "priority_reason": ..., "stage": ...}
```
and `llm_conversation.py:388` passes that straight into `LLMResponseContext.domain_context`.
So the model is handed the literal string `opposite_party_name` every single turn and told
(`groq_provider.py:82`) to "ask only the first supplied next_fact_candidate". It parroted the key.

`groq_provider.py:116` already instructs "never expose internal field IDs or state names".
**The prompt-only defence is already in place and already failed.** Do not fix this by
strengthening the wording.

**The backend does not need the model to see `key`.** `llm_conversation.py:517-518` binds
`pending_interaction` from `context.domain_context["next_fact_candidates"][0]` — index 0, which the
backend already holds. Nothing downstream reads the key *out of the model's reply*.

Deterministic fix, two layers:
- Drop `key` from the dict the model receives (keep it in the backend-side copy used for binding),
  or replace it with an opaque ordinal. `meaning` is what the model should be asking from.
- A post-response scrub as belt-and-braces: strip a trailing `(<known_fact_key>)` parenthetical,
  matched against the real fact-key set, from `reply_text`. This is a **closed set** — the fact keys
  are enumerable from `domain_registry` — so unlike the cycle-2/3 denylists, it is completable.

## 2. H4 (Model Tenancy Act cited as Rajasthan law) — the corpus already says the right thing

`backend/app/data/model_tenancy_provisions.json` labels every provision:
```json
"act": "Model Tenancy Act / State Rent Control Acts"
```
and `agents/rag_node.py:116-122` stamps each one with
```json
"document_type": "Model law - State adoption must be verified"
```
That metadata is correct, and it is already delivered to the model — `StatutoryCitation`
(`rag_node.py:136-148`) carries `document_type`, and `_verified_sources` passes the full
`model_dump()` into `legal_sources`.

**So H4 is not a hallucination and not a data error. The model was given the caveat and dropped it.**
Fix accordingly: a deterministic caveat appended by Python when any cited provision's
`document_type` contains a model-law/adoption-unverified marker. Corpus-metadata-driven, so it needs
no list of laws. Rajasthan has not enacted the MTA 2021, so presenting s.11 as binding on a Jaipur
tenant is a real error — but the caveat, not deletion, is the proportionate fix: the deposit norm is
still the right thing to tell them about, it just isn't enforceable law there.

## 3. C2 / C4 (fabricated SCC citation, invented win percentages) — unverifiable by construction

The entire curated corpus is **three files, 17 provisions**:
`consumer_protection_act_2019.json` (7), `model_tenancy_provisions.json` (4), `rti_act_2005.json`.
Fields per item: `section, act, title, description, applicable_situations` + stamped source metadata.

There is **no case law anywhere in the corpus**, and no field that could hold a success rate.
Therefore *every* case name and *every* percentage the model emits is unverifiable — not
"sometimes wrong". That makes these two a category ban, not a fact-check: nothing to check against.

## 4. M8 (no-corpus domains never disclosed) — the signal already exists and is already computed

`rag_node.py:163-168`: if the domain has no corpus, `retrieve_for_context` returns `[]` and the
comment says it will "never substitute an unrelated statute". Correct, and silent.

Careful — `legal_sources` is **not** empty for those domains: `_verified_sources`
(`llm_conversation.py:1711-1714`) appends `domain.rag.official_sources` (portal links) afterwards.
The precise signal is the one the code already derives at `llm_conversation.py:393-396`:
```python
profile.legal_sources = [s for s in legal_sources if s.get("act") and s.get("section")]
```
**An empty `act`+`section` set is the exact, deterministic, already-available predicate for
"no provision was retrieved, so any section number in this reply is unverifiable."**
`domains/{cyber_fraud,employment,legacy}.py` all declare `corpus_ids=()`; only
`consumer.py:66` (`CONSUMER`) and `tenancy.py:61` (`TENANCY`) have one.

## 5. Scope warning that applies to the whole set

CONSUMER and HOUSING_TENANT have **real** corpora, and W1-02's jurisdiction reasoning and W1-04's
1930 / cybercrime.gov.in sequencing were **correct output**. A guard that strips citations wholesale
destroys the product's main value. Whatever is built must key off "was this provision retrieved",
never off "does this look like a citation".

The cycles 2-3 lesson holds: Indian statutes and case names are an **open class**; a denylist of
fake ones is impossible. The closed, checkable thing is *what the corpus contains* (17 provisions)
and *what the fact-key set contains* (enumerable from the registry). Build only on those.

## 6. M7 (invented helpline numbers) + L1 (1930 attributed to RBI) — the code is innocent, and that is the problem

Every number the codebase itself emits is real and correctly attributed. Checked all of them:

| Number | Where | Attribution in code | Correct? |
|---|---|---|---|
| 1930 | `action_planner.py:110`, `conversation_agent.py:589`, `workflows.json:226`, `groq_provider.py:74` | "Cyber Helpline", "National Cyber Crime Portal & 1930" | yes |
| 14416 | `safety_triage.py:45` | Tele-MANAS | yes |
| 1800-599-0019 | `safety_triage.py:46` | KIRAN | yes |
| 112 / 181 / 1098 | `safety_triage.py:542,553` | emergency / women's / Childline | yes |
| 1091 | `groq_provider.py:74` | women's helpline | yes |

**The code never once says RBI.** So L1 and M7 are purely model-generated: the model invented a
number in one case and re-attributed a real one in the other. Nothing in the pipeline can catch
either, because there is **no single registry of helplines** — they are scattered across
`safety_triage.py`, `portal_selector.py`, `action_planner.py`, `workflows.json`,
`conversation_agent.py` and *both* provider prompts. There is no one place that states
"number -> (name, operating authority)", so there is nothing for a reply to be checked against.

That is the actual defect to fix, and it makes M7/L1 a **closed set** — India's national helplines
are finite and enumerable. A curated `helplines.json` (number, name, authority, scope) supports two
deterministic checks that need no denylist:
- a helpline-shaped token in a reply that is **not** in the registry is invented -> strip or replace;
- a registry number that appears next to the **wrong** authority -> correct the authority.

Note the second check is what catches L1 specifically, and a "is this number real?" check alone
would have **passed** L1 — 1930 is real. Misattribution needs its own rule.

## 7. Verified baseline for this cycle

Measured by the orchestrator, not copied from BRIEF.md:
```
1 failed, 458 passed, 1 xfailed, 3 warnings in 34.79s
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, unrelated
```
Command: `cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`
