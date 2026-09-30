# Agent 2 — Cycle 1 Implementation (Set S1: safety triage correctness)

Implemented findings **C1, H1, H2**. No commits, no branches, no push. No live Groq/LLM call —
every new test runs against `NoCallProvider`, which raises if a provider method is reached.

## Files changed

| File | Why |
|---|---|
| `backend/app/services/safety_triage.py` | Self-harm detection, `CRISIS_SUPPORT` stage + copy, trigger-scope fix, non-personal threat exclusion, current-turn signal fields |
| `backend/app/services/llm_conversation.py` | Crisis reply branch and short-circuit, bounded/progress-aware safety intake, safety-route release, `category_source` marker + reclassification |
| `backend/app/agents/conversation_agent.py` | 1-line-class regex fix in `_amount_from_text` (latent crash exposed by the H1 fix — see C1/H1 residual risk) |
| `backend/tests/test_safety_crisis_and_scope.py` | **new** — 31 tests |

No existing test file was modified. No existing assertion was weakened.

---

## C1 — Suicidal disclosure answered with the "is your attacker nearby?" script

**Problem.** W1-08 turn 2 (*"…jeene ka koi fayda nahi hai, sab khatam kar dun"*) matched no
self-harm pattern (none existed), became a safety case only because `wife` hit
`PARTNER_PATTERNS`, and fell into the `immediate_danger is None` branch — which emits the
attacker-proximity question. Turn 3 repeated it byte-for-byte. No crisis number existed anywhere
in the repo.

**What I changed.**

*`safety_triage.py`*
- New `SELF_HARM` context and `CRISIS_SUPPORT` stage constants; `CRISIS_HELPLINES` /
  `EMERGENCY_NUMBER` as single-source constants so no model can invent a helpline.
- `SELF_HARM_PATTERNS` (10 groups, English / Roman Hinglish / Devanagari) — high confidence,
  fire alone. `SELF_HARM_AMBIGUOUS_PATTERNS` (`sab khatam kar dun`, `end it all`,
  `marna chahta`) fire **only** alongside a `DESPAIR_PATTERNS` cue or a high-confidence hit.
  `_self_harm_hits()` implements that tiering.
- Precision guards: `mar jaun(ga)` is first-person-intransitive, so `maar dunga` (kill another)
  cannot match it; `SELF_HARM_IDIOM_EXCLUSION` drops `mar jaunga` when the turn is about
  starvation (`bhookhe mar jayenge`).
- `SELF_HARM` ranked first in the `priority` tuple, so partner + ideation reports `SELF_HARM`.
- A self-harm hit sets `is_safety_case` on its own and forces `stage = CRISIS_SUPPORT`, which
  outranks the whole `immediate_danger` ladder.
- New `crisis_support_copy(style)` in all three registers, wired in via a `stage` argument to
  `_localized_safety_copy`. It carries Tele-MANAS 14416, KIRAN 1800-599-0019, AASRA 9820466726
  and 112, says the help is free and 24x7, offers to resume the matter later, contains exactly
  one gentle question, and no evidence / attacker-proximity / document wording.
- Self-harm labels are deliberately **not** in `HARM_SIGNAL_LABELS`, so a crisis disclosure is
  never written into `threat_details` as if it were evidence of a threat.

*`llm_conversation.py`*
- `_paced_safety_reply` returns the crisis copy and **returns immediately** when
  `safety.stage == CRISIS_SUPPORT` — it never reaches the `missing`-fact ladder, so no
  CCTV/witness/evidence question and no quick replies can be attached. It sets
  `key_facts["crisis_support_offered"] = True` and clears `last_safety_question_group`.
- `_process_safety_turn` no longer forces `category_override="POLICE_COMPLAINT"` on a crisis
  turn (the classifier decides; for W1-08 the case stays CYBER_FRAUD) and does not park a
  `SAFETY_PAUSED` document request. `risk_level` stays AMBER; the existing nulling of
  `recommended_doc_type` / `recommended_next_action` / `is_ready_for_document` is untouched.
- `process_turn` augments `prior_safety` with `crisis_support_offered` from `key_facts`, because
  `safety_status` is rewritten from the current assessment every turn and would lose the flag.
- `_safety_route_required` releases the turn once crisis support has been offered and the new
  turn carries no fresh harm/self-harm signal, so W1-08 turn 3 gets the cyber-fraud answer.
  A second self-harm disclosure re-arms `CRISIS_SUPPORT`.

**Deviations from the plan.**
1. Plan step 5 said the crisis copy should "offer to continue the money matter". I made the
   closing line domain-neutral ("…hum aapke maamle par aage baat karenge"), because self-harm is
   not money-specific and the copy is shared by every domain.
2. The plan suggested `end it all` as high-confidence English; I moved it to the corroborated
   tier alongside `marna chahta` (which is ambiguous with "usko marna chahta hoon").
3. On the crisis release I deliberately **do not** set `safety_triage_complete = True`. Leaving
   it unset keeps `document_routing_allowed` (`case_readiness.py:204`) and the API-side
   `safety_blocked` check (`main.py:584`) shut for a self-harm-flagged case, so the case can
   never auto-offer a document. The turn is still released to the legal flow via a separate
   `crisis_support_offered` branch, so the loop is gone either way.
4. The crisis stage is tracked so it survives a turn: `stage == CRISIS_SUPPORT` when this turn
   carries self-harm **or** `SELF_HARM` is in the (sticky) contexts and crisis support was never
   offered. That is slightly more machinery than the plan described, but without it a crisis
   disclosure on a turn the router happened to skip would silently lose the crisis branch.

**Tests added.** (`tests/test_safety_crisis_and_scope.py`)
- `test_self_harm_disclosure_routes_to_crisis_support_not_the_attacker_script` — verbatim W1-08
  turn 2: `safety_context == SELF_HARM` despite `wife`, all four numbers present, no
  `khatre mein hain` / `aapke paas hai`, no CCTV/witness/evidence word, ≤1 question,
  `assert_no_document`, `provider.calls == []`.
- `test_self_harm_crisis_reply_matches_the_user_script` — English, Devanagari, Hinglish variants
  each answer in their own script.
- `test_ambiguous_endings_are_not_read_as_self_harm` — 4 negatives incl.
  `"mera kaam khatam kar dun to bata dena"`, `"main is case ko khatam karna chahta hoon"`,
  `"mere pati ne kaha maar dunga"`.
- `test_corroborated_ambiguous_phrase_does_count_as_self_harm` — the despair-cue path fires.
- `test_crisis_turn_does_not_hijack_the_category_or_pause_a_document` — CYBER_FRAUD preserved,
  no `document_request`, a fresh crisis case is not POLICE_COMPLAINT.
- `test_a_document_request_inside_a_crisis_turn_is_not_parked`.
- `test_w1_08_replay_does_not_repeat_the_crisis_script_on_the_next_turn` — turn 3 differs from
  turn 2 and contains no helpline.
- `test_a_second_self_harm_disclosure_reopens_crisis_support`.

**Residual risk.**
- Roman-Hinglish self-harm wording is enormous; this covers the common forms, not all of them.
  A miss falls back to the old (now correctly scoped) safety ladder, not to nothing.
- `risk_level` stays AMBER on a crisis turn, per the plan. Making it RED would be more
  conservative but would also permanently pin readiness at `READY_FOR_LEGAL_GUIDANCE` for the
  rest of the case; documents are already blocked by the unresolved-triage gate.
- `mar jaunga` is guarded only against the starvation idiom. Other hyperbolic uses
  ("is traffic me mar jaunga") would still route to crisis support — a gentle false positive,
  not a dangerous one.

---

## H1 — "pati"/"wife" alone converts an ordinary dispute into a permanent police/safety case

**Problem.** Three independent defects: `DOMESTIC_OR_PARTNER` was in the `is_safety_case` trigger
tuple; `THREAT_PATTERNS[0]` had no object constraint so *"threatening to put our luggage outside"*
re-triggered it; and `_maybe_reclassify` refused to migrate any non-GENERAL case, so the
triage-imposed POLICE_COMPLAINT was permanent.

**What I changed.**

*`safety_triage.py`*
- Removed `DOMESTIC_OR_PARTNER` from the trigger tuple (now
  `(*EXTERNAL_HARM_CONTEXTS, SELF_HARM)` where `EXTERNAL_HARM_CONTEXTS = (GENERIC_THREAT,
  PHYSICAL_VIOLENCE, STALKING, WEAPON)`). It stays in `contexts` and stays high in the `priority`
  tuple — a modifier, exactly like `CHILD_SAFETY`.
- `_drop_non_personal_generic_threats(labels, text)` + `NON_PERSONAL_THREAT_PATTERN`. It fires
  **only** when every threat label in the turn is in `GENERIC_THREAT_LABELS`
  (`threat`/`dhamki`/`intimidation`), so `death_threat`, `future_harm`, `kill_threat` and
  `hurt_threat` can never be discarded. Objects covered: evict/vacate/khali karna, luggage/
  samaan/belongings, sue/legal action/court case, lock the room, cut water/electricity,
  terminate/fire/blacklist, withhold deposit/salary, report me to the police, raise the rent.
  Applied to the history-derived contexts too, so a prior turn cannot smuggle it back in.
- `repeated_harassment` left alone this cycle, as the plan asked.

*`llm_conversation.py`*
- `_process_safety_turn` sets `profile.key_facts["category_source"] = "safety_triage"` when it
  creates a profile with `category_override="POLICE_COMPLAINT"`.
- `_maybe_reclassify` now treats a case carrying that marker as migratable
  (`if profile.category != "GENERAL" and not triage_assigned: return False`), pops the marker on
  migration, and logs `from=safety_triage`.

*`conversation_agent.py`* — **not in the plan.** Once W1-07 turn 1 stopped being hijacked it
reached `ConversationalLegalAgent.process_turn` for the first time and crashed:
`_amount_from_text`'s `([\d,]+(?:\.\d{1,2})?)` matched the bare comma in `", Rs 46000"`, giving
`float("")` → `ValueError`. Changed the four occurrences of that capture group to
`(\d[\d,]*(?:\.\d{1,2})?)` (must start with a digit). That is a strict narrowing — it removes
only matches that were already guaranteed to raise — and with it the message now correctly
extracts ₹46,000.

**Deviations from the plan.** Only the `_amount_from_text` fix above, which the plan could not
have foreseen because the code path was unreachable before H1 was fixed. Everything else follows
the plan.

**Tests added.**
- `test_partner_word_alone_does_not_create_a_safety_case` — verbatim W1-07 turn 1:
  `is_safety_case is False`, `DOMESTIC_OR_PARTNER in contexts`, and `process_turn` returns no
  immediate-danger question, `category != POLICE_COMPLAINT`, `safety_status is None`.
- `test_non_personal_threat_does_not_create_a_safety_case` — verbatim W1-07 turn 2, standalone
  and threaded behind turn 1.
- `test_other_non_personal_threats_stay_in_the_legal_flow` — sue / terminate / cut utilities.
- `test_a_death_threat_with_an_eviction_object_is_still_a_safety_case` — the exclusion must never
  reach a death threat.
- `test_real_threats_still_trigger_with_the_partner_context` — the four regression-lock messages
  Agent 1 flagged (`jaan se marne ki dhamki`, its Devanagari form, `my husband threatened to
  kill me`, and bare `mere pati ne mujhe dhamki di`) all still trigger and still report
  `DOMESTIC_OR_PARTNER`.
- `test_devanagari_death_threat_path_is_unchanged` — W1-05 turn 1 still returns the Devanagari
  reply starting `क्या आप अभी तुरंत खतरे में हैं`, with 112, `provider.calls == []`.
- `test_triage_assigned_category_is_migratable_but_a_settled_one_is_not`.

Manual replay (offline provider): W1-07 turns 1-3 now classify **HOUSING_TENANT**, `risk GREEN`,
`safety_status None`, and no immediate-danger question on any turn.

**Residual risk.**
- The non-personal object list is a denylist; a threat object I did not enumerate
  ("threatening to tow my car") still trips a safety case. Failing towards triage is the safe
  direction, so I left it as a denylist rather than trying to prove personhood.
- `repeated_harassment` (`harassing me for money`) remains as broad as the report says. Untouched
  this cycle, as instructed — flag for S3/S4.
- `_maybe_reclassify` is also S3's (H5) territory. Whoever runs second must re-read the function;
  my change is a two-line condition plus a marker pop, not a restructure.

---

## H2 — Once triage engages it swallows every later turn

**Problem.** `_safety_route_required` returned `True` for every turn while
`safety_triage_complete` was unset; that flag was only set once *every* `SAFETY_INTAKE_FACTS`
entry was known; and `_paced_safety_reply` had no progress detector, so W1-05 turns 2, 3 and 4
returned the same sentence. The reassurance prefix was also re-emitted every turn.

**What I changed** (all in `llm_conversation.py`).

1. **Bounded, progress-aware intake.** `_paced_safety_reply` now counts asks per question group
   in `key_facts["safety_question_asks"]` (`_record_safety_ask`) and counts total safety turns in
   `key_facts["safety_intake_turns"]`. A group that has already been asked
   `SAFETY_MAX_ASKS_PER_GROUP` (2) times is skipped and its facts are appended to
   `key_facts["stated_unknown_facts"]` (`_mark_safety_group_unknown`, driven by the new
   `SAFETY_QUESTION_GROUP_FACTS` map) — appended, never overwritten, since the CYBER_FRAUD branch
   of `compute_blocking_missing_facts` shares that list. Past `SAFETY_INTAKE_MAX_TURNS` (4) the
   remaining groups are all recorded unknown and `safety_triage_complete` is set. The
   immediate-danger question is bounded the same way: after two unanswered asks it stops, keeps
   the 112 guidance, and closes triage.
2. **Release back to the legal flow.** `_safety_route_required` gained a `message` argument and a
   real decision tree: crisis and active danger always route; a completed triage only re-opens on
   a fresh harm signal; a crisis case that has been answered releases; and once
   `immediate_danger is False` the turn is released when `_safety_turn_moved_on` says the user
   asked something new. `_release_safety_intake` then sets `safety_triage_complete = True` (for
   non-crisis cases) so the readiness ladder is no longer pinned.
3. **Reassurance once.** `key_facts["safety_reassurance_given"]` gates the
   *"Theek hai—yeh jaanna zaroori hai…"* prefix to the turn on which danger first resolves.

**Deviations from the plan.**
1. The plan derived "unanswered" by comparing against `last_safety_question_group`. I used a
   per-group ask counter instead: a group is only re-selected when its fact is still missing,
   which already means it went unanswered, and a counter also survives an intervening group.
   Same guarantee, less state to get wrong.
2. **`_safety_turn_moved_on` is stricter than the plan.** The plan proposed "a question mark, or
   above `LOW_CONTEXT_MAX_WORDS`". I require a question mark **and** more than
   `LOW_CONTEXT_MAX_WORDS` words. A long sentence with no question mark is far more likely to be
   the answer to *"what exactly happened, and when?"* than a change of subject, and releasing it
   would skip `extract_safety_facts` and lose the deterministic safety fact it carries. Runaway
   intake is bounded independently by the per-group ask cap, so nothing depends on this being
   loose. All four W1-05/W1-08 evidence turns that should release contain a question mark.
   `test_a_full_sentence_answer_is_not_mistaken_for_a_topic_change` locks this.
3. Added `SafetyAssessment.self_harm`, `.fresh_harm_signal` and `.danger_signal` (current-turn
   facts, defaulting to False, additive in `to_dict()`). The plan's release rule needs "this turn
   carries no fresh harm signal", and `contexts` is sticky so it cannot answer that.
   `danger_signal` exists because `"abhi main safe hun"` is an *answer* to triage, not a topic
   change — without it the existing
   `test_roman_hinglish_style_is_preserved_after_safe_follow_up` regressed.
4. Plan risk note ("gate release on `risk_level != RED`"): not needed. Active danger
   (`immediate_danger is True`) is checked before any release path, and `document_routing_allowed`
   already refuses RED.

**Tests added.**
- `test_safety_intake_never_asks_the_same_group_a_third_time` — the `incident` group is asked
  twice, then retired to `stated_unknown_facts`, and the third turn asks a different group.
- `test_safety_intake_closes_itself_after_the_turn_cap` — five turns, all replies distinct,
  `safety_triage_complete is True`.
- `test_reassurance_line_is_emitted_only_on_the_turn_danger_resolves`.
- `test_w1_05_replay_hands_the_turn_back_once_danger_is_answered` — 4-turn threaded replay:
  `"Kya yeh pehle bhi hua hai?"` on turn 2 only; turns 3 and 4 reach the legal flow.
- `test_a_new_threat_after_release_reopens_safety_routing` — a fresh
  `jaan se marne ki dhamki` after release returns safety guidance with 112.
- `test_a_full_sentence_answer_is_not_mistaken_for_a_topic_change` (see deviation 2).

**Residual risk.**
- W1-05 turns 3 and 4 are byte-identical to *each other* in the offline replay, because the
  `limited_demo` fallback composes one generic sentence per category. That is the fallback's
  own limitation, not the safety loop; both turns demonstrably left the safety branch. Agent 1's
  done-criterion 8 ("turns 2, 3 and 4 not byte-identical") therefore holds for 2-vs-3 and 2-vs-4
  but not 3-vs-4 under `configured=False`. With a live provider they differ. The test asserts
  what is actually provable offline and says so in a comment.
- A user who changes subject without a question mark stays in triage for up to two more asks
  before the cap releases them. Deliberate (deviation 2).
- Setting `safety_triage_complete` on release relaxes `document_routing_allowed` for non-crisis
  AMBER cases, as Agent 1 predicted. In practice the remaining safety facts are still in
  `intake_missing_facts` and keep readiness at `UNDERSTANDING_CASE`, so no document unlocked in
  any replay I ran — but that brake is indirect and worth a check in S5.

---

## Deliberately not done

- `tests/test_doc_generator.py::test_document_generation_notice` — pre-existing, untouched.
- `repeated_harassment` breadth — out of scope this cycle per the plan.
- No existing test was edited, weakened or deleted; none became obsolete.
- `risk_level` for a self-harm case was left at AMBER rather than promoted to RED (see C1
  residual risk).

## Final pytest run

```
cd backend && .venv/Scripts/python.exe -m pytest -q
```

```
1 failed, 349 passed, 3 warnings in 29.39s
```

Baseline was `318 passed, 1 failed`. The single failure is the same pre-existing
`tests/test_doc_generator.py::test_document_generation_notice`.
