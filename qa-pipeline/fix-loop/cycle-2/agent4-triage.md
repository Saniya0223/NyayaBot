# Agent 4 — Cycle 2 Triage · **Closing report for the fix loop**

This is the last report of the four-agent loop. No cycle 3 will run, so this is written for a
**human picking the work up later**, not for Agent 1. Every probe below was run by me against the
working tree with `NoCallProvider` (offline). **No live Groq/LLM call was made. Nothing was fixed,
modified, committed or pushed.**

Suite state (authoritative, as verified by the orchestrator):
```
1 failed, 449 passed, 4 xfailed
FAILED tests/test_doc_generator.py::test_document_generation_notice   (pre-existing, unrelated)
```
Progression across the loop: **318 (baseline) -> 349 -> 409 -> 424 -> 449.** Never reduced.
All 4 strict xfails still fail, so each encodes a genuinely open gap, not a stale test.

> **Operational note for whoever resumes.** Do **not** run a bare `pytest -q`.
> `tests/test_browser_autofill.py::test_local_chromium_can_fill_mock_form_without_submitting`
> calls `playwright.chromium.launch()` and spawns a real browser window on every full run. Use:
> `cd backend && .venv/Scripts/python.exe -m pytest -q --deselect tests/test_browser_autofill.py::test_local_chromium_can_fill_mock_form_without_submitting`

---

## 1. Where the safety work stands

**Short answer: C1 and H2 are properly fixed and can be left. H1 cannot — it has over-corrected
into a new class of false negatives, and that is a must-fix-now.**

### C1 (suicidal disclosure answered with the attacker script) — **fixed, safe to leave**

A crisis disclosure now returns crisis copy with Tele-MANAS 14416, KIRAN, AASRA and 112, in the
user's script; it does not ask who is near them, does not ask for CCTV or witnesses, does not offer
a document, and does not repeat itself on the next turn. Cycle 2 additionally stopped it doing two
things it had started doing *in place of* the original bug: freezing the legal case forever (CF-4)
and offering a document three turns later (CF-5). CF-5's cooling-off window both engages and
**expires** at `tsc == 3`, which is the part that matters — a permanent block would have been CF-4
one layer down.

Residual: **N6-1**, a past-tense parity hole in one pattern (`I want to die` fires, `I wanted to die`
does not). Real, but narrow: the sibling patterns catch the same disclosure in almost any other
wording — I verified `I wanted to kill myself` and `I wanted to end my life` both fire. A user has
to phrase it as exactly "wanted to die" to be missed. **MEDIUM, not a blocker.**

### H2 (triage swallows every later turn) — **fixed, safe to leave**

I could not trap a user. A deliberate 12-turn adversarial push releases at T4 and never re-asks;
`triage_question` is `None` from T4 onward; no question group reaches two asks. The bounded exit is
now the ask cap rather than a regex coin-flip, which was the whole point of the item.

### H1 (over-firing safety trigger) — **fixed as reported, but over-corrected. NOT safe to leave.**

This is the one finding a human must act on, and Agent 3's framing of it as "an approach/stalking
threat is missed" **understates it by a wide margin**. The defect is not about stalking. Cycle 2's
complement rule keeps a generic threat only when its complement contains a PERSON *and* a verb from
the closed ~20-item `_PERSON_HARM_VERB` set. Any explicit threat of violence whose verb falls
outside that set is silently dropped. My own probe of `assess_safety(...).is_safety_case`:

```
False  he threatened to strangle me
False  he threatened to poison me
False  he threatened to slap me in public
False  he threatened to break my legs
False  he threatened to push me off the stairs
False  he threatened to drag me out by my hair
False  he threatened to lock me in the room
False  he threatened to leak my private photos          (sextortion)
False  he threatened to teach me a lesson  /  usne dhamki di ki dekh lunga
False  he threatened to burn my house down              (harm verb present, no PERSON -> dropped)
False  usne dhamki di ki tezaab phek dega               (Roman-script acid threat)
False  he threatened to take my children away           (CHILD_SAFETY context, but not a safety case)
---- controls that still work ----
True   he threatened to beat me / hurt me / burn me alive / have me killed
True   usne dhamki di ki acid phek dega    (English "acid" is in WEAPON_PATTERNS; "tezaab" is not)
```

End-to-end this reads worse than the list suggests. Through `process_turn`, offline:

```
"My neighbour threatened to strangle me if I complain to the police again."
  -> category=POLICE_COMPLAINT   risk_level=GREEN   is_safety_case=None   "112" in reply = False
  -> reply: "...I have enough information to proceed with the next step: Draft Police..."
```

A user reporting a strangulation threat gets **no safety triage, no emergency number, and an
immediate offer to draft a document.** The same sentence with "acid" in place of "strangle"
correctly returns `RED` with the 112 line — so the difference is purely whether a token happens to
be on a list.

**This is a regression the loop introduced.** I checked the committed baseline: at `HEAD`,
`safety_triage.py` has no threat guard at all and `GENERIC_THREAT` alone satisfies `is_safety_case`
(`git show HEAD:backend/app/services/safety_triage.py`, line 310). Every string above **was** a
safety case before this work started, and cycle 1's denylist did not drop them either. Cycle 2's
inversion is what created them. We traded H1's false positives — an ordinary rent dispute getting
the attacker script — for false negatives on real violence threats. That is the wrong direction to
have the error in, and it is the only place in the product where the wrong answer is dangerous
rather than merely wrong.

**Verdict: the S1 safety work is not safe to leave. One item (N2-1 restated) must be fixed before
this reaches users. N6-1 is a legitimate MEDIUM that can ride along in the same file but does not by
itself block.**

---

## 2. Open items worth fixing, ranked

### 1. N2-1 (restated) — the complement rule drops explicit threats of violence · **HIGH · fix first**

**Problem.** `_drop_non_personal_generic_threats`
(`C:\Users\SANIYA SHARMA\Legal-Assistant\backend\app\services\safety_triage.py:329-360`) rule 3
requires the threat's complement to contain a PERSON **and** a `_PERSON_HARM_VERB`
(`safety_triage.py:190-197`). That verb set is closed and short, so a threat to strangle, poison,
slap, break bones, push, confine, drag, or leak intimate images names no verb in it and falls to
rule 4 (DROP). The conjunction also drops threats where the harm verb *is* present but the target
is not a person token (`burn my house down`). Agent 3's "approach/stalking" case is one instance of
a much larger class.

**Reproducer.** The 12 strings in §1, plus the end-to-end `process_turn` probe above, plus Agent 3's
`tests/test_cycle2_verification.py::test_cf2_an_approach_threat_is_still_a_safety_case` (strict
xfail, currently XFAIL). The boundary is pinned by
`test_cf2_the_approach_threat_gap_is_the_complement_rule_not_a_missing_signal`, which fails if a fix
over-corrects and makes the bare sentence fire for the wrong reason — keep it.

**Why it matters.** A safety false negative on the product's most consequential decision; a
regression against the pre-loop baseline; and the failure is silent — the user is not told the
threat went unrecognised, they are handed a document-drafting offer instead.

**Suggested direction — and the mechanism-vs-list call, stated plainly.**

Cycle 1's Agent 4 (me) ruled that CF-2 must not be fixed by extending a denylist. **That ruling
still holds and it applies here too — but the reason is narrower than "lists are wrong", and getting
the reason right is what stops the next person going round the same loop.**

The rule is: **do not enumerate an open class.**

- CF-2's denylist enumerated **things a person can be threatened with** (property, deposits,
  utilities, jobs, reputation, bookings, allotments...). Open class. Correctly killed.
- The current allowlist enumerates **ways to harm a person**. Agent 1 asserted this is "a small
  bounded set". **My probe falsifies that.** It is the open class of transitive violence verbs plus
  idiom (`dekh lunga`, `teach me a lesson`, `finish me`). Adding
  `strangle|poison|slap|break|push|confine|drag|leak` buys a week and then fails on the next verb.
  **So: no. Do not extend `_PERSON_HARM_VERB`. That is the same mistake in a new coat.**
- The class that **is** genuinely closed is a third one: **lawful / institutional process** — what a
  landlord, employer, bank or counterparty can legitimately threaten to do. sue · take to court ·
  file a case/FIR/complaint · report · complain · evict/vacate · terminate/fire/dismiss · blacklist ·
  withhold · recover · send a notice. Small, enumerable, finishable.

**So: change the mechanism a third time; do not grow a list.** Flip rule 3's polarity —

```
rule 3 today:    complement contains PERSON *and* HARM-VERB                 -> KEEP
rule 3 should:   complement contains PERSON                                 -> KEEP
                  ... unless its verb is in the closed LAWFUL-PROCESS set   -> DROP
```

I checked this against the five drops Agent 3 correctly praised.
`threatened to file a police case`, `threatened to blacklist my firm` and
`dhamki di ki naukri se nikal dunga` contain no PERSON token and stay dropped on rules 2/4 with no
list at all. Only `threatened to report me to the bank` and `threatened to take me to court` need
the lawful-process set — and both sit squarely inside it. So the inversion costs two entries on a
closed list and buys back the whole violence-verb class.

**Calibration argument — the other half of the answer.** CF-2 was written when a safety false
positive was *permanent*: cycle 1's triage hijack pinned the case forever, so aggressive dropping
was rational. **That world no longer exists.** I measured today's cost of a false positive on an
ordinary housing case (5-turn offline replay): four turns of safety questions, then release at T5
with `safety_triage_complete=True`, and the case is live, not frozen. Bounded and recoverable. The
cost of a false negative is not bounded. The guard should now be tuned to over-fire; it is currently
tuned to under-fire.

(One residue worth knowing: on the offline path the category stays `POLICE_COMPLAINT` even after
release, because `_maybe_reclassify` needs a confident LLM classification to act on the
`category_source="safety_triage"` marker cycle 1 added. That is an S3 item, not a reason to keep the
guard tight.)

**Two literals to fold into the same change** — script/parity gaps in patterns that already exist,
which is the class CF-6 explicitly authorised, not new coverage: Roman `tezaab` alongside Devanagari
`तेज़ाब` in `WEAPON_PATTERNS`, and `threatened to take my children away`, which currently yields a
`CHILD_SAFETY` context but `is_safety_case False`.

---

### 2. N6-1 — `want_to_die` has no past tense · **MEDIUM · ride along with item 1**

**Problem.** `safety_triage.py:95` —
`("want_to_die", r"\b(?:want|wanna|wish) to die\b|\bbetter off (?:dead|without me)\b|...")`.
The alternation has no `wanted|wished`, while the sibling `kill_myself` pattern handles past tense.

**Reproducer.** `tests/test_cycle2_verification.py::test_cf6_past_tense_want_to_die_is_detected`
(strict xfail, 2 params). Confirmed by me: `I want to die` -> True; `I wanted to die` -> False;
`I wanted to end my life` -> True; `I wanted to kill myself` -> True. It is a **parity defect inside
one pattern**, not a coverage request.

**Why it matters.** Past-tense disclosure is already locked as a crisis by cycle 1
(`last week I tried to kill myself` is a live regression test), so this is an inconsistency in
behaviour the product has already committed to — not a new policy decision.

**Suggested direction.** Add `wanted|wished` to that one alternation, and stop. This does **not**
reopen CF-6's hard stop on wordlist expansion: Agent 2's note binds later cycles against adding
coverage "without new evidence", and a parity defect inside an existing pattern is exactly the
carve-out cycle 1 wrote. Do not read it as licence for anything more. Re-check the addition against
CF-3's third-party suppression so that `wo marna chahta tha` does not become the *user's* crisis.

---

### 3. N8-R — offline composer returns byte-identical replies · **MEDIUM (offline path only) · stays deferred, but correct the record**

**Problem.** Once triage releases, the general `limited_demo` composer returns the same paragraph to
every subsequent question. Agent 3 measured **2 distinct replies across 9 turns** (one of them x7).

**Reproducer.** `tests/test_cycle2_verification.py::test_cf1_released_turns_are_not_byte_identical_replies`
(strict xfail). I re-ran that file — `25 passed, 4 xfailed` — and it XFAILs on the current tree.

**Correction to the record.** `cycle-2/agent2-changes.md` states under "Residual risk" that N8
*"is no longer reproducible through the cycle-1 test"* and *"has no active reproducer"*. The first
clause is true; **the second is now false and should be read as superseded.** N8 has an active,
strict reproducer, and it reproduces harder than before: cycle 1's lock inspected only a 3-turn
tail, which is why it appeared to have gone away. Extend the tail and it breaks.

**Why it matters, and why it is still not item 1.** It is the demo composer, not the safety branch —
`triage_question` is `None` throughout, so it does not reopen CF-1 or H2. But cycles 1 and 2 made
the safety branch release to the legal flow far more often, so this composer is now on a hot path,
and the person receiving seven identical paragraphs is one who has just come out of safety triage.

**Suggested direction.** Stays with **S3/S8**, as both prior triages ruled. Fix it where the reply is
composed; do not touch the safety code for it.

---

### 4. N3-1 — `process_turn` returns one mutated profile object · **LOW (test integrity) · no code change**

Covered in full in §3, because the question asked is "how much prior evidence does this undermine",
and the answer is: essentially none.

---

## 3. Dropped / not worth chasing

| item | one-line reason |
|---|---|
| **N3-1 as a product bug** | It is a testing trap, not a defect — `process_turn` returning a live mutable profile is an ordinary object-identity contract, not something to change at the end of the effort. |
| **Prior evidence allegedly undermined by N3-1** | I audited every `responses[k].case_profile` read in `tests/test_cycle1_verification.py`. There are three. `:220` (`responses[1]` of a 3-turn replay) is genuinely vacuous — **and it is redundant with `:221`, which asserts the identical thing against final state.** `:330` (`responses[3]` of a 4-turn replay) and `:466` (`responses[2]` of a 3-turn replay) both index the **last** turn and are sound. Every other assertion reads `reply_text`, a per-turn string, and is unaffected. **Net: one redundant assertion. No conclusion of cycle 1 or cycle 2 is overturned.** The requirement going forward is procedural only: snapshot state per turn — `tests/test_cycle2_verification.py::replay()` already does exactly this, so reuse it rather than writing a third harness. |
| Extending `_PERSON_HARM_VERB` | Open class. See §2 item 1 — this is the trap, not the fix. |
| Further `SELF_HARM_PATTERNS` coverage beyond N6-1 | Cycle 1's hard stop stands. The next increment of recall is a corroborated weak-cue tier (weak ideation cue + DESPAIR cue), not more literals. |
| Typo'd ideation forms (`jeene ka koi fyda nahi`) | Ruled not worth chasing in cycle 1; nothing has changed. |
| `"is traffic me mar jaunga"` hyperbole false positive | Fails safe, and CF-4/CF-5 made the cost bounded — one unnecessary helpline message. |
| `risk_level` AMBER vs RED on a crisis turn | Settled three times. RED would pin readiness harder than the pinning CF-4 removed. |
| `repeated_harassment` breadth | Still unevidenced by any QA wave; belongs with S3/S4 if it ever surfaces. |
| N9 — Hinglish -> English style flip | Cosmetic, S8. I saw it again in my own probes (T3/T4 of a Hinglish thread answered in English). Its only real cost is making other bugs *look* like two bugs. |
| `_amount_from_text` narrowing | Verified not a defect in cycle 1. Closed. |
| `tests/test_doc_generator.py::test_document_generation_notice` | Pre-existing, unrelated, out of scope for the whole loop. Whoever resumes should decide separately whether to fix or delete it — it has been red throughout and is now pure noise. |
| `tests/test_browser_autofill.py` launching Chromium on every full run | Not a finding from the QA wave, but it makes the suite hostile to run. Worth marking with a `@pytest.mark.slow` / opt-in marker the next time anyone touches the test config. |

---

## 4. State of the original 24 findings

Mapped to cycle 1's set map. **Be clear about the shape of this: the loop ran two cycles, both
entirely on S1 plus the safety half of S5. Six of the eight sets were never started.**

| finding | sev | set | state |
|---|---|---|---|
| **C1** suicidal disclosure gets the attacker script | CRIT | S1 | **FIXED** — crisis branch, four helplines, no loop, case not frozen, no document offer. Residual N6-1 (MEDIUM). |
| **H1** `pati`/`wife` + bare `threat` flips case to police/safety | HIGH | S1 | **FIXED as reported, OVER-CORRECTED.** False positives gone; new false negatives on real violence threats (N2-1 restated, HIGH). **Not closed.** |
| **H2** triage swallows every later turn | HIGH | S1 | **FIXED** — bounded caps and ordered release on both the `immediate_danger` and `URGENT_GUIDANCE` arms; verified against a 12-turn adversarial push. |
| **C3** document gate bypassed | CRIT | S5 | **HALF FIXED.** S5a (safety half — crisis cooldown, unresolved triage, RED, enforced at every affordance) closed and verified to expire. **S5b (readiness half — refusing a user-requested document below `READY_FOR_ACTION`, the W1-03 "draft it on turn 1" behaviour) NOT STARTED.** Gated on S2; will require updating 3 currently-green tests. |
| **C2** fabricated SCC citation + invented statute quote | CRIT | S4 | **NOT STARTED** |
| **C4** invented numeric win probabilities | CRIT | S4 | **NOT STARTED** |
| **C5** time-barred claim confirmed as within limitation | CRIT | S6 | **NOT STARTED** (gated on S2) |
| **H3** bank's false "FIR first" demand endorsed as RBI policy | HIGH | S7 | **NOT STARTED** |
| **H4** Model Tenancy Act asserted as operative Rajasthan law | HIGH | S4 | **NOT STARTED** |
| **H5** classification never fires when the substantive turn is lost | HIGH | S3 | **NOT STARTED** |
| **H6** amount extractor drops unmarked and marked amounts | HIGH | S2 | **NOT STARTED** |
| **H7** `opposite_party_name` captured in 0 of 8 cases | HIGH | S2 | **NOT STARTED** — highest-leverage single fix in the wave. |
| **H8** unstated year silently assumed ("July" -> "July 2023") | HIGH | S2 | **NOT STARTED** |
| **H9** RBI limited-liability window wrong ("48 hours") | HIGH | S7 | **NOT STARTED** |
| **M1** bare "yes" not bound to the question just asked | MED | S3 | **NOT STARTED** |
| **M2** Hinglish user answered in English | MED | S8 | **NOT STARTED** (+ N9) |
| **M3** case titles never gain a qualifier | MED | S8 | **NOT STARTED** |
| **M4** `user_state` null in all 8 cases | MED | S2 | **NOT STARTED** |
| **M5** readiness regressed to `PRE_INTAKE` (one-way ladder violated) | MED | S3 | **NOT STARTED** |
| **M6** "i need help" advances readiness off `PRE_INTAKE` | MED | S3 | **NOT STARTED** |
| **M7** invented helpline numbers | MED | S4 | **NOT STARTED** |
| **M8** no-corpus limitation never disclosed; `sources` null on all 31 turns | MED | S4 | **NOT STARTED** |
| **L1** 1930 attributed to RBI | LOW | S4 | **NOT STARTED** |
| **L2** `"paid me"` substring is a case-RESOLVED trigger | LOW | S3 | **NOT STARTED** |

**Tally: 3 findings closed (C1, H1-as-reported, H2), 1 half-closed (C3), 20 untouched.**
Plus 4 issues the loop discovered in its own work and left open: N2-1 (restated, HIGH),
N6-1 (MEDIUM), N8-R (MEDIUM), N3-1 (LOW, methodology).

**Set status:** S1 **not closed** (N2-1) · S5a closed · S5b, S2, S4, S6, S3, S7, S8 **not started**.
Cycle 2's revised priority for whatever runs next:
**S2 -> S5b (with S2) -> S4 -> S6 -> S3 -> S7 -> S8.**

---

## 5. Recommended next step for a human

**Do this first: fix N2-1 (restated), in
`C:\Users\SANIYA SHARMA\Legal-Assistant\backend\app\services\safety_triage.py`.**

It is the only thing in the tree that is *dangerous* rather than merely wrong; it is a regression
this effort introduced, so it did not exist before the work began and should not survive it; and it
is small — one rule's polarity in `_drop_non_personal_generic_threats`, a closed lawful-process verb
set, and two script-parity literals. Read §2 item 1 before starting: the tempting fix (add verbs to
`_PERSON_HARM_VERB`) is the wrong one and will fail again within a week.

Take **N6-1** in the same sitting — same file, one alternation, and it closes out C1 properly.

Then re-run the two verification files and the suite (with the browser test deselected). The
acceptance criteria are already written as strict xfails:
`test_cf2_an_approach_threat_is_still_a_safety_case` and
`test_cf6_past_tense_want_to_die_is_detected` must flip to passing, while
`test_cf2_the_approach_threat_gap_is_the_complement_rule_not_a_missing_signal` must **not** be
deleted — it is what catches an over-correction. Add the 12 strings from §1 as a parametrized
regression lock and keep Agent 3's 5 correct-drop strings as the negative half, because the entire
difficulty of this fix is holding both ends at once.

**After that, resume at S2 — specifically H7 (`opposite_party_name`, captured in 0 of 8 cases).**
Everything downstream waits on it: S5b cannot be judged until cases can actually climb the readiness
ladder, S6's limitation arithmetic is worse than useless when fed an invented date (H8), and S4's
RAG guardrails see better input once extraction works. It is the highest-leverage single fix left in
the report.

Three practical notes for whoever resumes:

- `_maybe_reclassify` and `_apply_extraction` are touched by both S2 and S3, and cycle 1 added a
  `category_source` marker to the first. Cycle 2 changed `safety_triage_resolved`, which
  `case_readiness.py` calls at four sites. **Re-read both files; do not trust line numbers from any
  plan in this folder.**
- Thread conversation state with a per-turn snapshot
  (`tests/test_cycle2_verification.py::replay()`). Asserting on `responses[k].case_profile`
  mid-conversation reads final state and can pass for the wrong reason.
- The working tree still holds this entire effort **uncommitted**, alongside other people's
  uncommitted work. Nothing in cycles 1 or 2 was committed, branched or pushed, per the standing
  constraint. Whoever resumes should decide deliberately how to land it.

---

*No commit, no branch, no push. No live Groq/LLM call — every probe above ran offline against
`NoCallProvider` with `provider.calls` asserted empty. No source file was modified by me. No `.env`
or key material appears in this report.*
