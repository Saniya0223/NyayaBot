# Agent 1 — Cycle 2 Plan

Input: Agent 4's cycle-1 triage (CF-1 … CF-6). Diagnosis and direction only.
**Agent 2 implements and may deviate where it has a better idea** — the acceptance criteria in §4
are binding; helper names, regex shapes and code layout are suggestions. Where I have validated a
mechanism experimentally I say so and give the corpus; where I have not, I say that too.

Constraints from `BRIEF.md` still hold: no commits, no branches, no push, no live Groq call,
never reduce the passing count, do not touch unrelated uncommitted work.

**Baseline re-confirmed by my own run just now:**
```
cd backend && .venv/Scripts/python.exe -m pytest -q
1 failed, 409 passed, 15 xfailed, 3 warnings in 40.07s
FAILED tests/test_doc_generator.py::test_document_generation_notice   (pre-existing, unrelated)
```

---

## 1. This cycle's scope, and why

**Scope: S1-remainder (CF-1, CF-2, CF-3, CF-4, CF-6) + S5a, the safety half of the document gate
(CF-5 / finding C3).**

I accept Agent 4's coupling argument in full, and my own reading of the source confirms it:

- CF-4's correct fix is "stop writing seven attacker-facts into the blocking set and let the ladder
  move". Today the *only* thing keeping a document away from a user who disclosed suicidal ideation
  is that the ladder is stuck — and CF-5 proves that brake does not even work. Fixing CF-4 without
  an explicit document gate makes CF-5 strictly worse. They ship together or not at all.
- CF-1, CF-2, CF-3 and CF-6 are all in `safety_triage.py` + `llm_conversation.py`, the two files
  cycle 1 already edited, and all are the same failure shape S1 was opened for.

**One reasoned deviation from Agent 4: I take only the *safety half* of C3 this cycle, not all of
C3.** C3's own fix text says "every document-affordance branch must call `document_routing_allowed()`
first". I checked what that would cost, and the full readiness half breaks three existing green
tests that deliberately create a `document_request` on an early, low-readiness case:

| test | what it does | why a readiness gate breaks it |
|---|---|---|
| `test_llm_conversation.py::test_document_request_without_prepare_action_never_claims_a_pdf_is_queued` | asserts `document_request["status"] == "BLOCKED"` on a fresh GENERAL case | never reaches `_document_request_response`, so no status is written |
| `test_pending_interaction.py::test_document_confirmation_uses_only_supported_case_document` | `profile_for()` is a bare `_init_case_profile`; asserts the request is created | bare profile is below `READY_FOR_ACTION` |
| `test_pending_interaction.py` (document-offer round trip, ~l.247) | same shape | same |

Those tests encode a real product decision — a *user-requested* document is honoured early and the
form then collects the missing fields — and reversing it is a design question, not a bug fix. It is
also much cheaper once S2 lands, because today almost every case is stuck at `UNDERSTANDING_CASE`
only because `opposite_party_name` is never captured (H7); a blanket readiness gate on top of that
would refuse *every* document in the product. So:

- **S5a (this cycle):** the safety dimension of the gate — crisis cooldown, unresolved safety
  triage, RED — enforced at **every** document-affordance branch. This is the part that is a safety
  defect and the part CF-4 depends on.
- **S5b (deferred, see §5):** the readiness dimension (the W1-03 "draft it on turn 1" behaviour).
  Runs after S2, and will require updating the three test expectations above. Flagged now so
  nobody thinks C3 is closed.

Everything else Agent 4 dropped or deferred stays dropped or deferred. I am not reopening N8, N9,
N10, `repeated_harassment`, the `mar jaunga` hyperbole, or `_safety_turn_moved_on`'s strictness —
**with one exception forced by evidence, documented under CF-1 below.**

---

## 2. Per-item detail

### CF-1 (HIGH) — `URGENT_GUIDANCE` is an unbounded loop with no reliable exit

**Problem.** Reproduced exactly, threaded, offline (`NoCallProvider(configured=False)`):

```
t1 W1-05 T1                                            -> CHECK_IMMEDIATE_DANGER
t2 "haan, wo abhi mere ghar ke bahar khada hai"        -> 'Pehle kisi safe jagah par jaiye aur 112…'
t3 "Kaunsi section ke under FIR likhna compulsory hai?" -> 'Move to safety first and call 112…'
t4 "Exact section number kya hai?"                      -> byte-identical to t3
t5 "Please koi to jawab do?"                            -> byte-identical to t4
t6 "section batao please?"                              -> byte-identical to t5
t7 "main ab safe hun, ghar badal liya. Ab FIR ka section batao?" -> STILL the 112 sentence
t8 "section number kya hai?"                            -> STILL the 112 sentence
safety_question_asks = {'immediate_danger': 1}   safety_intake_turns = 8
safety_triage_complete = None                    immediate_danger = True
```

**Root cause — three separate defects, all confirmed in source.**

1. `llm_conversation.py:604-605`
   ```python
   if safety.immediate_danger is True:
       return True
   ```
   sits **above** the `key_facts.get("safety_triage_complete")` check at `:608`. So even if the cap
   fired and set `safety_triage_complete`, routing would still return `True`. Ordering alone makes
   the branch unescapable.
2. `llm_conversation.py:794-799`, the urgent branch of `_paced_safety_reply`, writes
   `key_facts["last_safety_question_group"] = "urgent_safety"` directly instead of calling
   `_record_safety_ask`, and never tests `turns > SAFETY_INTAKE_MAX_TURNS`. So
   `_safety_group_exhausted(key_facts, "urgent_safety")` can never become true — note the observed
   `safety_question_asks = {'immediate_danger': 1}` above: the urgent group has **no** counter at
   all, though `safety_intake_turns` did reach 8. The group is exempt from both of Agent 2's caps.
3. `immediate_danger` is sticky through `prior_safety` (`safety_triage.py:506-507`), and the only
   thing that clears it is `SAFE_NOW_PATTERNS` (`safety_triage.py:152-156`). `main ab safe hun`
   misses `(?:main|hum) (?:abhi |filhal )?safe (?:hun|hain)` on `ab` vs `abhi`. Turn 7 is the proof:
   the user *states they are safe* and is still trapped.

**Proposed fix** — `llm_conversation.py`, deterministic, no prompt work.

1. **Bound the urgent branch** (`_paced_safety_reply`, ~:794). Call
   `self._record_safety_ask(key_facts, "urgent_safety")` on each urgent reply. When
   `self._safety_group_exhausted(key_facts, "urgent_safety")` or `turns > SAFETY_INTAKE_MAX_TURNS`,
   set `key_facts["safety_triage_complete"] = True` and `key_facts["urgent_guidance_given"] = True`,
   emit the urgent guidance one last time, and stop asking. Same shape Agent 2 already built for the
   `immediate_danger is None` branch at `:777-782` — reuse it, do not invent a second mechanism.
2. **Reorder `_safety_route_required`** (`:584-624`) so the release checks are evaluated before the
   `immediate_danger is True` short-circuit:
   ```python
   if safety.stage == CRISIS_SUPPORT:            # unchanged, still first
       return True
   if key_facts.get("safety_triage_complete"):   # MOVED ABOVE the urgent short-circuit
       return bool(safety.fresh_harm_signal)
   if safety.immediate_danger is True:
       return True
   ...
   ```
   The bounded exit is then the cap, not a regex.
3. **Keep the 112 line visible after release.** A released urgent case must not silently drop the
   emergency guidance. Requirement, not implementation: while `key_facts["urgent_guidance_given"]`
   is set and `risk_level == "RED"`, every subsequent reply must still carry the one-line emergency
   instruction at the top, in the user's script, idempotently (never doubled). The two composition
   sites are the offline branch (`llm_conversation.py:218-219`, `prefix + fallback.reply_text`) and
   `_finish_chat_response` (`:451-477`). One short line only — no question, no second helpline
   block, and it must not contain "document" / "draft" / "legal notice".
4. **Ride-along N7 — reset the global turn budget on a genuinely new threat.** `safety_intake_turns`
   is a per-case total that never resets, so after the cap a new threat closes triage on the same
   turn and emits the self-contradictory *"Agar aapko abhi turant khatra hai … Immediate-safety
   check complete hai."* Fix: when `key_facts.get("safety_triage_complete")` **and**
   `safety.fresh_harm_signal`, reset `safety_intake_turns = 0`, clear `safety_question_asks`,
   `safety_reassurance_given`, `safety_triage_complete` and `last_safety_question_group` before the
   reply is chosen. `assess_safety` already sets `immediate_danger = None` for this case
   (`safety_triage.py:505-508`), so the fresh danger question follows naturally. Guard the reset so
   it cannot fire on the same turn the cap closes triage.
5. **One narrowly-scoped regex fix, and only one.** Add `ab` to the `safe_now` alternation:
   `(?:main|hum) (?:ab |abhi |filhal )?safe (?:hun|hain)`. This is a missing inflection of a pattern
   that already exists, exactly the class of defect CF-6 §1 describes — **not** an invitation to
   grow `SAFE_NOW_PATTERNS`. Nothing else in that group changes, this cycle or any later one,
   without new evidence. The correctness of CF-1 must not depend on it: items 1–2 are the fix, this
   is a courtesy.

**Why this layer.** `_safety_route_required` runs at `llm_conversation.py:173`, before the
`provider.status.configured` check at `:193`, so a deterministic fix here holds on both the LLM path
and the rate-limited path. A prompt change cannot reach this code at all.

**Risk / blast radius.**
- `test_safety_language_triage.py:183` asserts `reply_text.startswith("Move to safety first")` for
  the *first* urgent turn. Only repeats change; keep the first ask byte-identical.
- `test_safety_crisis_and_scope.py::test_a_new_threat_after_release_reopens_safety_routing` asserts
  `"112" in fourth.reply_text` after a reopened threat — the N7 reset must keep that true.
- `test_safety_crisis_and_scope.py::test_safety_intake_closes_itself_after_the_turn_cap` asserts
  five *distinct* replies over `("mere pati ne mujhe dhamki di", "nahi", "hmm ok" ×3)`. None of
  those carry a fresh harm signal, so the N7 reset must not fire there.
- Releasing the urgent turn means an LLM call where there was none. Under a 429 the existing
  `limited_demo` fallback handles it. Correct behaviour.

**How Agent 3 should verify.**
- The 8-turn replay above: no reply after the cap repeats the urgent triage copy; by t4
  `safety_question_asks["urgent_safety"] == 2` and `safety_triage_complete is True`; t7 and t8 are
  not the 112 sentence; under `NoCallProvider(configured=True)` the released turns reach `extract`,
  proving they left the safety branch.
- Every post-release reply of that case still contains `112`, exactly once per reply.
- N7: `["mere pati ne mujhe dhamki di", "nahi", "hmm ok" ×4, "aaj usne phir se dhamki di ki wo mujhe
  chhodega nahi"]` — the last turn asks the immediate-danger question again and the reply does not
  simultaneously say triage is complete.
- First-ask copy for both urgent and check-danger branches unchanged (byte compare against the
  existing green assertions).

**⚠ One xfail in this item cannot flip as written — read this before you start.**
`test_active_danger_branch_must_not_repeat_the_same_question_forever` ends with
`assert len(set(tail)) == len(tail)` over three *released* turns. I verified that after release the
offline composer returns byte-identical replies to different questions — that is **N8**, which
Agent 4 deliberately deferred to S3/S8:
```
W1-05 replay, t3 and t4 both released to the legal flow:
  t3 == t4  ->  True    ('Limited demo mode — … Jo hua use thoda aur batayein…')
  t4 != t5  ->  only because the language style flipped Hinglish->English (N9), by accident
```
So a *correct* CF-1 fix still fails that assertion, for a reason that is not CF-1.

**I authorise exactly one edit to one xfail reproducer, and no other.** Agent 2 must re-scope that
test so it measures the safety branch rather than the demo composer — the cleanest route is the one
Agent 3 already used elsewhere (`tests/test_cycle1_verification.py:300-319`): run the replay under
`NoCallProvider(configured=True)` and assert the released turns reach `extract`, plus
`safety_question_asks["urgent_safety"] <= SAFETY_MAX_ASKS_PER_GROUP` and
`safety_triage_complete is True`. **The distinctness claim must not be lost:** move it into a new
`xfail(strict=True)` named for N8, with a reason line pointing at the limited-demo composer and at
set S3/S8. Net xfail count must not drop. Record this in the handoff so Agent 4 can audit it. No
other xfail may be edited, relaxed or deleted.

---

### CF-2 (HIGH) — the non-personal-threat guard is the wrong shape

**Problem.** `NON_PERSONAL_THREAT_PATTERN` (`safety_triage.py:128-140`) enumerates objects. Every
synonym-level variant falls through. Re-confirmed, plus Agent 3's four:
```
case=True  sig=['threat'] :: landlord threatened to cut off our electricity if we complain
case=True  sig=['threat'] :: my employer threatened to withhold my bonus and gratuity
case=True  sig=['threat'] :: the builder threatened to cancel our booking
case=True  sig=['dhamki'] :: society ne dhamki di ki parking band kar denge
case=True  sig=['threat'] :: society threatened to cut our water supply
case=True  sig=['dhamki'] :: landlord ne dhamki di ki saman bahar phenk dega
```
Through `process_turn` this is finding H1 verbatim: `category=POLICE_COMPLAINT`, `risk=AMBER`, the
attacker-proximity script.

**Root cause.** `_drop_non_personal_generic_threats` (`safety_triage.py:234-246`) asks "is the
object one I listed?" The set of things a person can be threatened with is open; the guard can
never be finished.

**Proposed fix — invert to a personhood allowlist, but scoped to the threat's *complement*.**

Agent 4's direction is right and I am adopting it, with one refinement I had to make because a bare
"require a person token" allowlist **breaks two green tests**:

```
tests/test_safety_language_triage.py:189  "mere pati ne dhamki di lekin abhi main safe hun"
      -> asserts safety_level == AMBER.  No `mujhe` anywhere.
tests/test_intake_before_document.py:111  "my neighbour sent threatening WhatsApp messages yesterday"
      -> asserts is_safety_case is True.  No `me` anywhere.
```
Both are correct product behaviour. The discriminator is not "is a person named" but **"does the
threat state an object at all, and if so is that object a person being harmed"**. Every false
positive has an explicit complement (`threatened to <VP>`, `dhamki di ki <clause>`, `<VP> ki
dhamki`); every true positive either has no complement, or has a complement containing person-harm.
That is a closed test against an open one — the ways to harm a person are a small bounded set, the
things you can threaten are not.

Rule, applied only when **every** threat label in the turn is in `GENERIC_THREAT_LABELS`
(`threat` / `dhamki` / `intimidation`) — `death_threat`, `kill_threat`, `future_harm` and
`hurt_threat` continue to bypass the guard entirely, exactly as today:

```
1. the threat's direct object is the person  ("threatened me", "mujhe dhamki di")   -> KEEP
2. no complement clause found                 ("threatening messages", "dhamki di")  -> KEEP
3. complement found, and it contains a PERSON target AND a PERSON-HARM verb          -> KEEP
4. otherwise                                                                         -> DROP
```

Complement extraction (three structural forms, not an object list):
- English: `threat(?:en|ens|ened|ening)\s+(?:to|that|with)\s+(?P<c>.+)`
- Hinglish/Hindi post-posed: `(?:dhamk[iy]|धमकी)\s*(?:\w+\s+){0,3}?(?:ki|कि)\s+(?P<c>.+)`
- Hinglish/Hindi pre-posed: `(?P<c>.+?\b\w+ne)\s+(?:ki|की)\s+(?:dhamk[iy]|धमकी)` — requiring the
  complement to end in an infinitive (`…ne`) is what stops the very common word `ki` from
  swallowing unrelated text; verified: `"mere pati ki dhamki se main dar gayi"` correctly finds no
  complement, `"usne mujhe ghar se nikalne ki dhamki di"` correctly finds one.

`PERSON`: `me | us | myself | my (wife|husband|family|children|kids|daughter|son|mother|father|
parents|sister|brother) | our (family|children) | mujhe | mujhko | hamein | humein | hame |
mere (pariwar|bachchon|biwi|patni|pati) | मुझे | हमें | मेरे परिवार`.
`PERSON-HARM verb`: `kill | murder | beat | hit | attack | assault | harm | hurt | injure | stab |
shoot | burn | rape | molest | abduct | kidnap | acid | maar | maarne | peet | jaan se | jaan le |
khatam kar | utha le | zinda nahi | chhod(unga|ega|enge)? nahi | nahi chhod(unga|ega) | tezaab |
मार | पीट | जान से | नहीं छोड़ | तेज़ाब`.

**Keep `NON_PERSONAL_THREAT_PATTERN` as a secondary filter only** (Agent 4's instruction), for the
complement-less wordings in rule 2 that still plainly name property. Do not add a single object to
it.

**I prototyped this rule and ran it over a 35-string corpus: 0 mismatches.** The corpus (all of
Agent 3's, all of Agent 4's, every threat string in the existing green tests, plus new positive
locks) is reproduced in the verification list below. Agent 2 is free to implement it differently as
long as that corpus passes.

**Risk / blast radius.**
- The two green tests quoted above are the sharp edges. Verify them explicitly, first.
- `test_safety_crisis_and_scope.py:269` `"the landlord threatened to evict us and said he will kill
  me"` carries `kill_threat`, so the guard must not even run. Confirm the "all labels generic"
  precondition is intact.
- `"usne mujhe ghar se nikalne ki dhamki di"` (threatened to evict me) now drops. That is a housing
  matter, and it is the intended direction, but note it in the handoff as a behaviour change.
- `assess_safety` calls the same helper on `prior_value` at `:447`; the new rule must be applied
  there too or a prior turn will re-inject `GENERIC_THREAT`.
- **Cross-dependency with CF-1/N7:** the N7 xfail's final message is *"aaj usne phir se dhamki di ki
  wo mujhe chhodega nahi"*. Its complement is `wo mujhe chhodega nahi` — it survives **only** if
  `chhodega nahi` is in the person-harm verb set. It is in my prototype and it must be in the
  implementation, or CF-1's ride-along silently regresses.

**How Agent 3 should verify.** All of these, as one parametrized pair of tests:

*must become `is_safety_case is False`* — `landlord threatened to cut off our electricity if we
complain` · `my employer threatened to withhold my bonus and gratuity` · `the builder threatened to
cancel our booking` · `society ne dhamki di ki parking band kar denge` · `society threatened to cut
our water supply` · `my employer is threatening to withhold my full and final settlement` ·
`landlord ne dhamki di ki saman bahar phenk dega` · `the builder threatened to cancel our allotment
if we complain to RERA` · `The manager keeps threatening to put our luggage outside the gate if we
don't vacate by Sunday` (W1-07 T2) · `my landlord is threatening to sue me if I ask for the deposit
back` · `the employer threatened to terminate me for complaining` · `society is threatening to cut
water and electricity` · `the PG operator threatened to throw our things out tomorrow` · `landlord
threatened to evict us` · `my employer is threatening to terminate me` · `bank ne dhamki di ki
recovery agent bhejenge` · `he threatened to file a police complaint against me`

*must stay `is_safety_case is True`* — `he threatened me` · `my neighbour has threatened me` ·
`my neighbour sent threatening WhatsApp messages yesterday` · `mere pati ne mujhe dhamki di` ·
`mere pati ne dhamki di lekin abhi main safe hun` · `wo mujhe dhamki de raha hai` · `usne hamein
dhamki di` · `my neighbour is intimidating me` · `he threatened my wife and children` ·
`मुझे धमकी दी` · `mera husband mujhe threaten kar raha hai` · `aaj usne phir se dhamki di ki wo
mujhe chhodega nahi` · `he threatened to harm my family` · `usne mujhe maarne ki dhamki di` ·
`he threatened to beat me up` · `usne dhamki di ki mujhe zinda nahi chhodega` · `landlord ne dhamki
di ki mujhe maar dalega`

Plus the three existing partner death-threat locks still giving `safety_context ==
DOMESTIC_OR_PARTNER`, and W1-07 T1 through `process_turn` still not returning the immediate-danger
question and not setting `POLICE_COMPLAINT`.

---

### CF-3 (HIGH) — a third party's suicide threat fires the *user's* crisis script

**Problem.** Re-confirmed:
```
self_harm=True  stage=CRISIS_SUPPORT  sig=['suicide_word','kill_myself'] :: My tenant threatened to commit suicide if I evict him. Can I still file for eviction?
self_harm=True  stage=CRISIS_SUPPORT  sig=['suicide_word']              :: mere neighbour ne kaha wo atmahatya kar lega agar maine complaint ki
self_harm=True  stage=CRISIS_SUPPORT  sig=['suicide_word','threat']     :: the borrower is threatening suicide to avoid repaying
self_harm=False                                                          :: my brother told me he said he would kill himself   (correct, by luck)
```
The landlord gets *"What you are carrying right now sounds very heavy… your life matters far more
than any of it"*, the eviction question is never answered, and the case is then frozen by CF-4.

**Root cause.** `SELF_HARM_PATTERNS` `suicide_word` (`safety_triage.py:93`) and `kill_myself`
(`:94`, which contains `commit suicide`) are bare noun/verb phrases with **no speaker constraint**.
`kill himself` escapes only because the literal happens to differ from `kill myself` — an accident,
not a mechanism. This is the mirror image of CF-2: a label with no subject test.

**Proposed fix — a first-person subject test on the self-harm hit**, in `_self_harm_hits`
(`safety_triage.py:249-263`). Mirror CF-2's logic, scoped to the clause that governs the match:

```
for each self-harm match:
    look at the window of tokens immediately preceding the match (suggest 6)
    if that window contains a THIRD-PARTY ATTRIBUTION frame
       and no FIRST-PERSON marker occurs between that frame and the match:
           discard this hit
```

- THIRD-PARTY ATTRIBUTION: a third-person subject plus a reporting/threat verb, or a third-person
  reflexive inflection —
  `(?:he|she|they|his|her|their|my \w+|the \w+)\s+(?:threatened|threatens|threatening|said|says|told|
  claims|warned|is threatening)` · `(?:usne|unhone|uska|uski|wo|woh|mera \w+ ne|mere \w+ ne)\b.*
  \b(?:kaha|bola|dhamki|keh raha)` · `(?:उसने|उन्होंने|वह|मेरे \S+ ने).*(?:कहा|बोला|धमकी)` ·
  third-person reflexive tails `kar lega|kar legi|kar lenge|le lega|himself|herself|themselves`.
- FIRST-PERSON marker: `i | i'm | im | main | mai | maine | mujhe | mera | meri | khud | apne aap |
  myself | मैं | मैंने | मुझे`.

**The default must be KEEP.** Drop only on an explicit third-party frame governing the match. A
sentence with no subject at all (`"socha tha suicide kar lun"`, `"ab lagta hai jeene ka koi fayda
nahi"`) keeps firing — verified both are currently detected and must stay so.

**Why this layer, not a prompt.** The brief mandates deterministic detection because the provider is
frequently rate-limited, and `_self_harm_hits` runs before any provider call.

**Risk / blast radius.** Every existing green self-harm probe must survive. I checked the whole
first-person corpus already in the suite and all of it either carries an explicit first-person
marker or has no subject at all, so none is at risk under the rule above. The real hazard is a
window that is too wide: `"My tenant threatened to commit suicide if I evict him"` contains `I`
**after** the match — a whole-text first-person test would keep the hit and the fix would do
nothing. The first-person test must be positional (between the attribution frame and the match),
not global. Verify that string specifically.

**Note, not in scope.** Once the hit is suppressed, that turn is a *coercion threat against the
user* and, under CF-2, its generic `threat` label also drops, so the turn falls through to the
ordinary legal flow and the landlord gets eviction guidance. That is the correct and legally useful
outcome. Whether a third-party suicide threat should be *recorded* as a legal fact (abetment
exposure) is content, and belongs to S4/S7. Do not build it now.

**How Agent 3 should verify.**
- The four CF-3 strings above: first three `self_harm is False`, `SELF_HARM not in contexts`,
  `stage != CRISIS_SUPPORT`; the fourth unchanged.
- Threaded: `"My tenant threatened to commit suicide if I evict him…"` as turn 1 of a fresh case →
  no crisis copy, no helpline numbers, readiness not pinned, the eviction question reaches the
  ordinary flow.
- First-person regression lock, all must stay `True`: `Ab lagta hai jeene ka koi fayda nahi hai,
  sab khatam kar dun` (W1-08 T2) · `I want to commit suicide` · `मैं आत्महत्या कर लूँगा` ·
  `main khudkushi kar lunga` · `I am suicidal` · `I don't want to live anymore` · `kal raat maine
  khudkushi karne ki koshish ki thi` · `I attempted suicide last month` · `socha tha suicide kar
  lun` · the buried-mid-paragraph Flipkart probe already in `test_cycle1_verification.py`.

---

### CF-4 (HIGH) — a crisis turn freezes the legal case permanently

**Problem.** Re-confirmed on the W1-08 replay — identical on every turn, for the life of the case:
```
t1 CYBER_FRAUD UNDERSTANDING_CASE ['immediate_danger','threat_details','incident_date',
                                   'repeated_incidents','physical_violence_or_weapon',
                                   'evidence_available','police_contacted']
t2 identical    t3 identical
```
Three of the seven (`threat_details`, `physical_violence_or_weapon`, `police_contacted`) are
**unsatisfiable** — there is no attacker in a self-harm disclosure. The ₹6.8 lakh fraud can never
reach `READY_FOR_ACTION` again.

**Root cause — a four-link chain, all confirmed.**
1. `case_readiness.py:90-91` — `compute_intake_missing_facts` returns all of `SAFETY_INTAKE_FACTS`
   while `safety.is_safety_case and not safety_triage_resolved(key_facts)`.
2. `safety_triage.py:603-605` — `safety_triage_resolved(key_facts)` is
   `key_facts.get("immediate_danger") is not None`. A crisis-only case never answers that question
   (correctly — crisis support bypasses the danger ladder), so it is never resolved.
3. `case_readiness.py:124-125` — `compute_blocking_missing_facts` repeats the same branch, so all
   seven become blocking.
4. `case_readiness.py:172-173` — `compute_readiness` returns `UNDERSTANDING_CASE`. Permanently.

**Proposed fix — a crisis-scoped intake contract.** Deterministic, `safety_triage.py` +
`case_readiness.py`.

1. `safety_triage.py`: add `CRISIS_INTAKE_FACTS: list[str] = []` — a person in crisis is not
   interrogated — and a selector
   `safety_intake_facts_for(key_facts) -> list[str]` returning `CRISIS_INTAKE_FACTS` when the case
   is **crisis-only** (`SELF_HARM` present in `key_facts["safety_contexts"]` and none of
   `EXTERNAL_HARM_CONTEXTS` present), else `SAFETY_INTAKE_FACTS`. `key_facts["safety_contexts"]` is
   already written by `_process_safety_turn` (`llm_conversation.py:696-697`), so the data exists.
2. Make `safety_triage_resolved` crisis-aware: also `True` when `crisis_support_offered` is set and
   the case is crisis-only. A **mixed** case (self-harm *and* a real attacker) still requires the
   danger answer — that is the correct asymmetry and must be tested.
3. `case_readiness.py`: replace the module constant with the selector at **all three** sites —
   `:90-91`, `:96-101` (the "keep safety facts in intake once triage is answered" loop) and
   `:105-108` (the filter in the return). Missing `:96-101` re-injects all seven the moment
   `safety_triage_resolved` starts returning `True`; this is the easiest thing to get wrong.
4. `llm_conversation.py::_paced_safety_reply`, crisis branch (`:766-771`): also set
   `key_facts["safety_triage_complete"] = True` for a crisis-only case, and initialise the CF-5
   cooldown counter (`key_facts["turns_since_crisis_support"] = 0`).
5. `risk_level` stays **AMBER**. Agent 3 and Agent 4 both analysed this and both are right: RED
   would pin the case harder than the pinning we are removing, and AMBER still auto-escalates to RED
   when the crisis turn also carries a danger cue.

**This item must not be merged without CF-5.** Agent 2's cycle-1 deviation 3 left
`safety_triage_complete` unset precisely to hold documents shut. That justification is being
removed, and the explicit gate has to be in place in the same change. If CF-5 slips, CF-4 slips too
— say so in the handoff rather than shipping half of it.

**Risk / blast radius.**
- I grepped the suite: **no** existing test asserts that a crisis case keeps the seven safety facts,
  and none asserts `readiness == UNDERSTANDING_CASE` after a crisis turn. Low regression risk.
- `safety_triage_resolved` is imported by `case_readiness.py` only (`:22-26`) and used at `:90`,
  `:124`, `:172`, `:202`. Every one of those four call sites changes behaviour for crisis-only
  cases — walk all four deliberately, especially `:202` in `document_routing_allowed`, which is
  where CF-5's new refusal has to land.
- Non-crisis safety cases must be byte-for-byte unchanged. `test_intake_before_document.py::test_D_*`
  is the canary: a threat case must still produce `immediate_danger` / `evidence_available` /
  `police_contacted` in `intake_missing_facts`.

**How Agent 3 should verify.**
- W1-08 three-turn replay: by turn 3, `intake_missing_facts` contains no attacker fact
  (`threat_details`, `physical_violence_or_weapon`, `police_contacted` all absent) and instead holds
  the CYBER_FRAUD domain facts; readiness is free to move above `UNDERSTANDING_CASE` once those are
  supplied.
- Mixed case lock: a turn carrying self-harm **and** an active-danger cue (`"wo abhi mere ghar ke
  bahar khada hai aur main apne aap ko khatam kar lunga"`) still requires the danger answer, still
  reports `safety_level == RED`, and still serves crisis copy.
- Non-crisis safety case unchanged (`test_D_*` and the `mere pati ne mujhe dhamki di` family).

---

### CF-5 (HIGH impact — set S5a) — a document is offered two turns after a suicidal disclosure

**Problem.** Re-confirmed on W1-08 + *"Mujhe cyber crime complaint draft chahiye. Kya aap bana sakte
ho?"*:
```
reply  = 'Limited demo mode … Main yeh document taiyar kar sakta hoon. Baaki details form mein
          confirm karke PDF ya DOCX banayein.'
document_request = {'intent':'USER_REQUESTED','document_type':'CYBERCRIME_BANK_FREEZE',
                    'status':'NEEDS_REQUIRED_FIELDS', …}
suggested_action = {'type':'PREPARE_DOC','doc_type':'CYBERCRIME_BANK_FREEZE',
                    'open_confirmation_modal': True}
is_ready_for_document = False      readiness = UNDERSTANDING_CASE
crisis_support_offered = True
```
`is_ready_for_document` being `False` stops generation server-side, but the sentence, the parked
`document_request` **and the confirmation-modal action** all reach the user.

**Root cause — correction to Agent 4's attribution, from tracing the actual reproducer.** Agent 4
named `conversation_agent.py:55` (`_check_conversation_actions`). That branch is real but it is not
what produced this output: on the offline path `_refresh_workflow` runs *after*
`workflow_agent.process_turn` (`llm_conversation.py:196`) and re-syncs `suggested_action` from the
gated `recommended_next_action` at `:202`. The affordance that survives comes from **three ungated
calls to `_document_request_response`**, none of which consults `document_routing_allowed`:

| site | path | what it writes |
|---|---|---|
| `llm_conversation.py:177-186` | optional-skip resume | `document_request`, handoff reply |
| `llm_conversation.py:205-212` | **offline / rate-limited handoff — this reproducer** | `document_request`, handoff reply, `suggested_action` |
| `llm_conversation.py:328-332` | LLM-path handoff | same |

`document_routing_allowed` is consulted only inside `_refresh_workflow` at `:1197`, which these
three branches either precede or return before. `conversation_agent.py:540-590`
(`_check_conversation_actions`, rejection branch) is a fourth site: it emits its own `PREPARE_DOC`
action and the sentence *"A formal complaint draft may now be appropriate"*. It is re-synced on the
service path but is ungated when the agent is driven directly.

**Proposed fix — one gate, consulted by every affordance.**

1. `case_readiness.py`: add
   ```python
   CRISIS_DOCUMENT_COOLDOWN_TURNS = 3

   def crisis_document_block_active(key_facts: dict) -> bool:
       if not (key_facts or {}).get("crisis_support_offered"):
           return False
       return int((key_facts or {}).get("turns_since_crisis_support") or 0) < CRISIS_DOCUMENT_COOLDOWN_TURNS
   ```
   and refuse in `document_routing_allowed` (`:189-214`) when it is true.
   **A cooling-off window, not a permanent ban, is deliberate.** A permanent block would recreate
   CF-4's trap one layer down: one gentle false positive (Agent 2's acknowledged `"is traffic me mar
   jaunga"`) would disable document generation for that case forever. Bounded and deterministic is
   the right trade. If Agent 2 disagrees, say so with a reason — but do not make it unbounded.
2. Increment the counter once per turn that reaches the legal flow, i.e. right where
   `_release_safety_intake` is called (`llm_conversation.py:174`), when `crisis_support_offered` is
   set. That gives the CF-5 reproducer a value of 2 at the document turn — blocked.
3. One private helper in `GeminiConversationService`, consulted **before** all three
   `_document_request_response` sites:
   ```python
   def _document_affordance_blocked(self, profile, safety) -> bool:
       key_facts = profile.key_facts or {}
       if crisis_document_block_active(key_facts):                          return True
       if safety.blocks_document_routing and not safety_triage_resolved(key_facts): return True
       if profile.risk_level == "RED":                                      return True
       return False
   ```
   When blocked: **do not write `profile.document_request`**, do not set a `PREPARE_DOC`
   `suggested_action`, and return a short script-matched deferral instead of the handoff line. The
   deferral must not contain `document` / `draft` / `legal notice` (so it also satisfies
   `assert_no_document` wherever that is applied) and must not name the template.
4. `conversation_agent.py`: gate the rejection branch's `PREPARE_DOC` action and its
   "complaint draft" sentence on `crisis_document_block_active(profile.key_facts)`. Import direction
   is safe — `case_readiness` does not import `conversation_agent`.

**Scope note.** This is the **safety half** of C3 only. The readiness half (a user-requested
document honoured at `UNDERSTANDING_CASE`, W1-03) is deferred to S5b for the reasons in §1. Say so
in the handoff; C3 is not closed by this cycle.

**Risk / blast radius.**
- `test_llm_conversation.py::test_eligible_document_request_uses_prepare_action_without_model_draft`
  and `::test_document_request_without_prepare_action_never_claims_a_pdf_is_queued`,
  `test_pending_interaction.py::test_document_confirmation_uses_only_supported_case_document` and
  the document-offer round trip at `:247` — none of these is a safety case, so the gate must be
  inert for them. Run them first.
- `test_safety_crisis_and_scope.py::test_a_document_request_inside_a_crisis_turn_is_not_parked` and
  `::test_crisis_turn_does_not_hijack_the_category_or_pause_a_document` already assert
  `document_request is None` on the crisis turn itself; they must stay green.
- `assert_no_document` (`test_safety_language_triage.py:80-88`) checks the reply text for
  `document` / `draft` / `legal notice` — the deferral copy must respect it.

**How Agent 3 should verify.**
- W1-08 ×3 then *"Mujhe cyber crime complaint draft chahiye…"* → `document_request is None`,
  `suggested_action is None`, `"document taiyar kar sakta hoon"` absent.
- Same request on turn 6 (after the cooldown) → the affordance returns, proving the block is bounded
  and not a second trap.
- The same request phrased in English and in Devanagari, on the LLM path
  (`NoCallProvider(configured=True)` or the stub provider) and on the offline path — all four
  combinations blocked, because the bug lives in three different branches.
- Ordinary non-safety document requests unaffected: full `pytest -q` and the four named tests above.

---

### CF-6 (MEDIUM) — self-harm inflection bugs. **Time-boxed. Seven changes, then stop.**

**Problem.** Re-confirmed:
```
self_harm=False :: main apni jaan de dunga            <- the commonest Hinglish ideation sentence
self_harm=True  :: main apni jaan de dun              <- the same verb, one inflection away
self_harm=False :: i dont wanna live anymore
self_harm=False :: my family would be better off without me
self_harm=False :: ab main zinda nahi rehna chahta
self_harm=False :: main phansi laga lunga
self_harm=False :: मैं इस सब से थक चुका हूँ, अब और नहीं जी सकता
```

**Root cause.** Two are outright defects in patterns cycle 1 already wrote:
- `apne_aap_ko_khatam` (`safety_triage.py:101`): `apni (?:jaan|zindagi|jindagi) (?:de dun|khatam|
  khatm)\b` — the trailing `\b` after `de dun` requires a word boundary between `n` and `g`, so it
  **excludes the ordinary future tense** of the verb it exists to match.
- `no_reason_to_live` (`:96`): `(?:don'?t|dont|do not) want to (?:live|go on)` — `wanna` is already
  in `want_to_die` (`:95`) and absent here.

The rest are coverage, and coverage here is unbounded. Agent 4 is right that regex is the correct
layer (the brief mandates deterministic detection because the provider is routinely rate-limited)
and equally right that this must not become a word-chasing loop.

**Proposed fix — exactly these seven, in `safety_triage.py`. Nothing else.**

*Defects (2):*
1. `de dun\b` → `de\s?d(?:u|un|oon)(?:ga|gi)?\b` in `apne_aap_ko_khatam`.
2. `want to` → `(?:want to|wanna)` in `no_reason_to_live`.

*Script parity for patterns that already exist in another register (2):*
3. `better off without me` beside the existing `better off dead` in `want_to_die`.
4. Devanagari for the already-covered `can't live any more`: `(?:अब )?(?:और )?नहीं जी सकता` /
   `जी नहीं सकता` in `no_reason_to_live`.

*Evidence-backed literals (3):*
5. `\bzinda nahi (?:rehna|rahna) chaht[aiu]\w*\b` + `ज़िंदा नहीं रहना चाहत`.
6. `\bphansi laga\w*\b` + `फांसी लगा|फाँसी लगा`.
7. (covered by 3 — `better off without me` counts once; if Agent 2 prefers to split it, seven
   remains the ceiling.)

**Then stop. This is a hard stop, and it binds later cycles.** No further self-harm wordlist
expansion in any cycle without new evidence from a QA wave or a real transcript. If more recall is
wanted, the answer is a **corroborated weak-cue tier** (a weak ideation cue *plus* a `DESPAIR`
cue, the structure `SELF_HARM_AMBIGUOUS_PATTERNS` already has), not more high-confidence literals.
Agent 3's own judgement that typo'd forms (`jeene ka koi fyda nahi`) are not worth chasing stands;
they are not on the list and must not be added.

**Risk / blast radius.** Small, but two things to watch. `de\s?d(?:u|un|oon)(?:ga|gi)?` broadens a
first-person-reflexive pattern — confirm it still cannot collide with `maar dunga` (a *threat*, at
`:55`), which is protected by the required `apni (jaan|zindagi)` prefix. And every addition must be
re-checked against CF-3's third-party suppression, so `"wo apni jaan de dega"` does not become a
crisis for the user.

**How Agent 3 should verify.** All six xfail params in
`test_common_ideation_wordings_should_be_detected` become `self_harm is True`; the existing negative
locks (`mera kaam khatam kar dun to bata dena`, `main is case ko khatam karna chahta hoon`,
`mere pati ne kaha maar dunga`) stay negative; the CF-3 third-party corpus stays negative including
the new wordings in third person.

---

## 3. Explicit non-goals — what Agent 2 must NOT do

1. **Do not add objects to `NON_PERSONAL_THREAT_PATTERN`.** Agent 4 produced four fresh failures in
   a minute without trying. The list cannot be finished; CF-2 changes the mechanism. The denylist
   survives only as a secondary filter for the complement-less case.
2. **Do not expand `SELF_HARM_PATTERNS` beyond CF-6's seven items**, in this cycle or any later one,
   without new evidence. The next increment of recall is a corroborated weak-cue tier, not literals.
3. **Do not expand `SAFE_NOW_PATTERNS` beyond the single `ab` inflection in CF-1 §5.** The bounded
   exit is the ask cap; the regex must never again be the only way out.
4. **Do not take the readiness half of C3** (refusing a user-requested document at
   `UNDERSTANDING_CASE`). It breaks three green tests, it is a product decision, and it is much
   cheaper after S2. It is S5b.
5. **Do not delete, relax or weaken any xfail reproducer**, with the single authorised exception
   spelled out under CF-1 — and that one must be replaced by an equivalent assertion plus a new N8
   xfail, so the net xfail count does not drop.
6. **Do not change `risk_level` from AMBER to RED on a crisis turn.** Analysed and settled twice.
7. **Do not touch** `repeated_harassment` (S3/S4), the limited-demo composer / N8 (S3/S8), the
   Hinglish→English style flip / N9 (S8), `_amount_from_text` (settled, not a defect), or
   `_safety_turn_moved_on`'s strictness (its only real risk was CF-1, which this cycle closes).
8. **Do not fix** `tests/test_doc_generator.py::test_document_generation_notice`. Pre-existing and
   out of scope.
9. **No prompt engineering for any item in this cycle.** Every fix here is deterministic Python
   upstream of the provider, and must hold when Groq is rate-limited.
10. No commits, no branches, no push, no live LLM call.

---

## 4. Definition of done

Agent 3 may call this cycle complete when **all** of the following hold, with proof:

1. `pytest -q` in `backend` shows **≥ 409 passed**; the only failure is the pre-existing
   `tests/test_doc_generator.py::test_document_generation_notice`. xfail count is **15 or more**
   (14 flip to plain passing assertions, 1 is re-scoped and 1 new N8 xfail is added — see CF-1).
2. **CF-1** — In the 8-turn urgent replay: no urgent triage reply is returned more than
   `SAFETY_MAX_ASKS_PER_GROUP` times; `safety_question_asks["urgent_safety"]` is recorded and
   capped; `safety_triage_complete is True` by turn 4; turns 7 and 8 are not the 112 sentence; under
   `NoCallProvider(configured=True)` the released turns reach `extract`. Every post-release reply
   still carries the emergency line exactly once. First-ask copy unchanged.
3. **CF-1/N7** — After the cap is spent, a genuinely new threat re-opens triage with the
   immediate-danger question, and no reply says both "call 112 now" and "immediate-safety check
   complete" in the same turn.
4. **CF-2** — All 17 negative strings in the CF-2 verification list give `is_safety_case is False`;
   all 17 positive strings give `is_safety_case is True`; the three partner death-threat locks still
   give `safety_context == DOMESTIC_OR_PARTNER`; W1-07 T1 and T2 both give `False`.
5. **CF-3** — The three third-party suicide strings give `self_harm is False` and
   `stage != CRISIS_SUPPORT`; the ten first-person locks all still give `True`; the tenant-eviction
   turn reaches the ordinary legal flow with no helpline numbers in the reply.
6. **CF-4** — In the W1-08 replay, `intake_missing_facts` by turn 3 contains none of
   `threat_details`, `physical_violence_or_weapon`, `police_contacted`, and readiness is no longer
   pinned; a **mixed** self-harm + active-danger case still requires the danger answer and still
   reports RED.
7. **CF-5** — No `document_request`, no `PREPARE_DOC` action and no "I can prepare this document"
   sentence within `CRISIS_DOCUMENT_COOLDOWN_TURNS` of a crisis disclosure, on **all four** of
   {offline, LLM path} × {Hinglish, English}; and the affordance does return once the cooldown
   expires, proving the block is bounded.
8. **CF-6** — All six parametrized ideation wordings detected; all existing negative locks intact.
9. No commit, no branch, no push; no live Groq call in any test; no `.env` or key material in any
   cycle-2 artifact.

### The 15 xfail reproducers — which must flip

| reproducer (`tests/test_cycle1_verification.py`) | count | item | expected |
|---|---|---|---|
| `test_near_miss_non_personal_threats_should_not_create_a_safety_case` | 4 | CF-2 | **flip → plain pass** |
| `test_common_ideation_wordings_should_be_detected` | 6 | CF-6 | **flip → plain pass** |
| `test_a_third_partys_suicide_threat_is_not_the_users_crisis` | 1 | CF-3 | **flip → plain pass** |
| `test_a_crisis_disclosure_does_not_permanently_freeze_the_legal_case` | 1 | CF-4 | **flip → plain pass** |
| `test_no_document_is_offered_soon_after_a_crisis_disclosure` | 1 | CF-5 | **flip → plain pass** |
| `test_a_new_threat_after_the_cap_still_gets_the_immediate_danger_question` | 1 | CF-1/N7 | **flip → plain pass** (depends on `chhodega nahi` being in CF-2's harm-verb set) |
| `test_active_danger_branch_must_not_repeat_the_same_question_forever` | 1 | CF-1 | **re-scope, then pass** — its distinctness assertion measures N8, not CF-1 (verified: two released turns return byte-identical limited-demo replies). Replace with the release assertions in CF-1; move the distinctness claim to a **new** N8 `xfail(strict=True)`. |

**14 flip as written. 1 is re-scoped under the authorisation in CF-1 and then passes, with a new
N8 xfail taking its place so nothing is lost.**

---

## 5. Updated set map and revised priority

| # | Set | Findings | Status after cycle 2 (if this plan lands) |
|---|---|---|---|
| **S1** | Safety triage correctness | C1, H1, H2 | **Closed**, subject to Agent 3/4 confirming §4 |
| **S5a** | Document gate — safety half | part of C3 | **Closed** |
| **S5b** | Document gate — readiness half | rest of C3 | **Open.** Refusing a user-requested document below `READY_FOR_ACTION` (W1-03). Requires updating 3 green tests. Gated on S2: until H7 lands, almost every case sits at `UNDERSTANDING_CASE` and this gate would refuse everything. |
| **S2** | Fact capture — parties, amounts, dates, jurisdiction | H7, H6, H8, M4 | **Open — next cycle.** |
| **S4** | Legal-content guardrails | C2, C4, H4, M7, M8, L1 | Open |
| **S6** | Limitation & deadline arithmetic | C5 | Open, gated on S2 |
| **S3** | Case state machine (+ N8 offline composer) | H5, M1, M5, M6, L2, N8 | Open |
| **S7** | Domain procedural accuracy (cyber-fraud / banking) | H3, H9 | Open |
| **S8** | Presentation — language register, case titles (+ N9) | M2, M3, N9 | Open |

**Revised priority: S2 → S5b (folded into S2's cycle) → S4 → S6 → S3 → S7 → S8.**

Reasons for the change from cycle 1's order:
- **S2 moves to the front and S5b rides with it.** They are now coupled in the opposite direction
  from S1/S5a: S5b cannot be judged until cases can actually climb the ladder, and H7 (0 of 8
  opposite parties captured) is what stops them. Doing them together lets one cycle answer "should
  an early user-requested document be refused, or should it open intake?" against a ladder that
  moves.
- **S4 stays third.** Fabricated citations are the worst *content* defect, but S2 feeds the RAG
  query, so S4 sees better input after S2.
- **S3 drops below S6** and gains N8. After cycle 1 and cycle 2 the safety branch releases turns to
  the legal flow far more often, so the limited-demo composer returning byte-identical replies is
  now on a hot path, not a rare one. It belongs with the state-machine work.
- **S6 stays gated on S2**, unchanged: a limitation calculator fed by an invented date produces a
  confident wrong answer, which is worse than today's unstable one.

Carry-forward notes for whoever runs S2/S3:
- `_maybe_reclassify` and `_apply_extraction` are touched by both S2 and S3, and cycle 1 already
  added a `category_source` marker to `_maybe_reclassify`. Re-read the file; do not trust line
  numbers from any earlier plan.
- Cycle 2 changes `safety_triage_resolved`, which `case_readiness.py` calls at four sites. Anyone
  editing readiness afterwards should read it fresh.
