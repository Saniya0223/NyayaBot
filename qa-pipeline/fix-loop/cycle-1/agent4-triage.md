# Agent 4 — Cycle 1 Triage (Set S1: C1, H1, H2)

Filter applied: **carry forward only what stops S1 from being genuinely solved.** Every item below
was independently re-run by me against the working tree (offline `NoCallProvider`, no live Groq
call, nothing modified, nothing committed). Where I disagree with Agent 3's severity I say so.

Suite state confirmed by my own run:
```
cd backend && .venv/Scripts/python.exe -m pytest -q
1 failed, 409 passed, 15 xfailed, 3 warnings in 28.56s
FAILED tests/test_doc_generator.py::test_document_generation_notice   (pre-existing, unrelated)
```
Baseline 318 → 409. Never reduced. Constraint 3 of the brief holds.

---

## 1. Cycle 1 outcome

The three **reported** defects are genuinely gone: a suicidal disclosure now gets crisis copy with
four real helplines instead of "is your attacker nearby?" (C1), the bare word `pati`/`wife` no
longer converts a PG dispute into a permanent police case (H1), and the W1-05 loop on the
"danger answered no" path now releases on turn 3 (H2). I re-ran Agent 3's 60 positive probes and
they pass. But **S1 as a mechanism is only half-bounded**, and the gap is not cosmetic: the safety
router still has one completely unbounded arm (active danger — a user in the worst situation is
the one who cannot get out), the threat-scope guard still misfires on any wording one synonym away
from its literal list, and the new crisis path has created a state trap of its own that freezes the
user's actual legal case forever. Net: the transcripts in the QA wave would now pass, but a user
one word off those transcripts still hits H1 and H2 verbatim. **S1 is not done.**

---

## 2. Carry forward to Agent 1

Seven of Agent 3's ten issues carry, as **six work items** (N7 rides along with N2 — same state
machine, same function). Three are dropped (§3).

---

### CF-1 — `URGENT_GUIDANCE` is an unbounded loop with no reliable exit *(Agent 3: N2 + N7)*

**Problem.** `_safety_route_required` (`backend/app/services/llm_conversation.py:604-605`) returns
`True` unconditionally when `immediate_danger is True`, before any cap is consulted. The urgent
branch of `_paced_safety_reply` (`llm_conversation.py:788-792`) returns without calling
`_record_safety_ask` and without testing `turns > SAFETY_INTAKE_MAX_TURNS`, so the `urgent_safety`
group is exempt from **both** bounds Agent 2 built. `immediate_danger` is sticky via
`prior_safety`, and the only thing that clears it is one of three narrow `SAFE_NOW_PATTERNS`
regexes (`safety_triage.py:152-156`).

**Reproducer (mine, extends Agent 3's).** Eight-turn threaded replay, `NoCallProvider(configured=False)`:
```
t1 W1-05 T1                                          -> CHECK_IMMEDIATE_DANGER
t2 "haan, wo abhi mere ghar ke bahar khada hai"      -> URGENT_GUIDANCE
t3 "Kaunsi section ke under FIR likhna compulsory hai?"  -> "Pehle kisi safe jagah par jaiye aur 112..."
t4 "Exact section number kya hai?"                   -> same sentence (English flip)
t5 "Please koi to jawab do?"                         -> byte-identical to t4
t6 "section batao please?"                           -> byte-identical to t5
t7 "main ab safe hun, ghar badal liya. Ab FIR ka section batao?"  -> STILL the 112 sentence
t8 "section number kya hai?"                         -> STILL the 112 sentence
safety_question_asks = {'immediate_danger': 1}   safety_triage_complete = None   (after 8 turns)
```
Turn 7 is the part Agent 3 did not reach and it is the decisive one: the user **states they are
safe** in natural Hinglish and still cannot escape, because `main ab safe hun` misses
`(?:main|hum) (?:abhi |filhal )?safe (?:hun|hain)` on the single word `ab` vs `abhi`. Swapping in
`main abhi safe hun` releases on the same turn — so the sole exit from an infinite loop is a
one-word regex coin-flip.

**Ride-along (N7).** `safety_intake_turns` is a per-case total that never resets, so once four
safety turns are spent a genuinely new threat closes triage on the same turn instead of checking
danger, and emits self-contradictory copy: *"Agar aapko abhi turant khatra hai, to … 112 par call
kijiye. … Immediate-safety check complete hai."* Verified. Same function family, a couple of lines.

**Why it is considerable.** This is finding H2, unchanged, in the branch that matters most. A user
who has just said an attacker is outside their door asks three different legal questions and gets
the same sentence back forever, with no way out. It is also the branch where the user is least able
to work out that they need to phrase an escape hatch correctly.

**Where it belongs.** **S1 incomplete — fix now.**
**Severity: HIGH** (agree with Agent 3, and the turn-7 evidence makes it firmer).
**Suggested direction.** Apply the bound Agent 2 already built for `immediate_danger is None` to the
urgent branch too — record the ask, cap at 2, then release the turn while keeping the 112 line
pinned to the top of every subsequent reply; and reset `safety_intake_turns` when
`fresh_harm_signal` arrives after `safety_triage_complete`.

---

### CF-2 — the non-personal-threat guard is the wrong shape, not an incomplete list *(Agent 3: N1)*

**Problem.** `NON_PERSONAL_THREAT_PATTERN` (`safety_triage.py:128-140`) enumerates objects
literally. Any synonym-level variant of an object it already lists falls straight through to
`is_safety_case = True`.

**Reproducer.** I deliberately did **not** reuse Agent 3's four strings. Fresh wordings, same
tenancy/employment register as the original H1 evidence:
```
case=True   landlord threatened to cut off our electricity if we complain   (list has "cut (the |off )?electricity")
case=True   my employer threatened to withhold my bonus and gratuity        (list has withhold deposit|salary|payment)
case=True   the builder threatened to cancel our booking
case=True   society ne dhamki di ki parking band kar denge
--- controls that correctly pass ---
case=False  landlord threatened to evict us
case=False  my employer is threatening to terminate me
case=False  the PG operator threatened to throw our things out tomorrow
```
Full-turn consequence through `process_turn`:
```
"Society ne dhamki di hai ki hamara water supply cut kar denge, aur saman bahar phenk denge…"
  -> category=POLICE_COMPLAINT  risk=AMBER
     reply='Kya aap abhi turant khatre mein hain, ya woh vyakti abhi aapke paas hai?…'
```

**Why it is considerable.** That output *is* finding H1, verbatim. A utility-cutoff or
salary-withholding complaint — bread-and-butter housing and employment matters — still gets
mislabelled a police case and answered with the attacker-proximity script.

**Structural verdict — say this plainly to Agent 1.** Do **not** extend the list. I generated four
fresh failures in one minute without trying; the list cannot be finished. The guard must invert:
for the three generic labels (`threat` / `dhamki` / `intimidation`) require a **personal object**
(`me`, `us`, `mujhe`, `hamein`, `my wife/family/children`, `मुझे`, `हमें` + a harm verb) rather than
trying to enumerate impersonal ones. The positive lock `mere pati ne mujhe dhamki di` carries
`mujhe`, so an allowlist keeps it. Keep the existing denylist only as a secondary filter.

**Where it belongs.** **S1 incomplete — fix now.**
**Severity: HIGH** (agree with Agent 3; Agent 2 understated this as "an object I did not enumerate").
**Suggested direction.** Replace the object denylist with a personhood allowlist on the generic
labels; `death_threat` / `kill_threat` / `future_harm` / `hurt_threat` continue to bypass the guard
entirely, as today.

---

### CF-3 — a third party's suicide threat routes the *user* into crisis support *(Agent 3: N3)*

**Problem.** `SELF_HARM_PATTERNS` group `suicide_word` (`safety_triage.py:93`) and `kill_myself`
(`:94`, which includes `commit suicide`) have no speaker constraint, so a suicide threat **made by
someone else and reported by the user** fires the user's own crisis script.

**Reproducer (mine, wider than Agent 3's single case).**
```
self_harm=True   My tenant threatened to commit suicide if I evict him. Can I still file for eviction?
self_harm=True   mere neighbour ne kaha wo atmahatya kar lega agar maine complaint ki
self_harm=True   the borrower is threatening suicide to avoid repaying
self_harm=False  my brother told me he said he would kill himself          <- correct, only because
                                                                             "kill himself" != "kill myself"
```
Threaded, the landlord gets *"What you are carrying right now sounds very heavy… your life matters
far more than any of it"*, the eviction question is never answered, and the case is then pinned
(see CF-4): on the next turn `intake_missing_facts` is the seven attacker facts and readiness stays
`UNDERSTANDING_CASE`.

**Why it is considerable — and why it is not MEDIUM.** Agent 3 filed this MEDIUM; I am raising it
to **HIGH**. First, "I will kill myself and name you in a note" is a *recurring, well-known* coercion
tactic in Indian eviction, moneylending and police-complaint disputes — this is not an exotic input,
it is a whole category of real user. Second, it is a false positive on the exact feature this set
exists to build: the crisis script is the most emotionally loaded output the product has, and
firing it at the wrong person is the mirror image of C1, not a lesser bug. Third, it silently
bricks the legal matter. The correct handling is also the *legally* useful one — this user needs
guidance about a threat being used against them, not a helpline addressed to themselves.

**Where it belongs.** **S1 incomplete — fix now.**
**Severity: HIGH** (raised from Agent 3's MEDIUM).
**Suggested direction.** Suppress the self-harm hit when the clause subject is a third party
(`he / she / they / my tenant / my <relation> / the borrower` + `threatened to` / `said he would` /
`ne kaha wo`), the same speaker-scoping the `kill himself` case already gets for free.

---

### CF-4 — a crisis turn freezes the user's legal case permanently *(Agent 3: N5)*

**Problem.** A `CRISIS_SUPPORT` turn writes the seven attacker-oriented `SAFETY_INTAKE_FACTS`
(`safety_triage.py:201-209`) into the blocking set and never sets `safety_triage_complete` (Agent
2's deliberate deviation 3), so `compute_readiness` (`case_readiness.py:172`) pins the case at
`UNDERSTANDING_CASE` for its entire life.

**Reproducer.** Five-turn W1-08 replay (₹6.8 lakh cyber-fraud case):
```
t1 cat=CYBER_FRAUD ready=UNDERSTANDING_CASE missing=[immediate_danger, threat_details, incident_date,
                                                      repeated_incidents, physical_violence_or_weapon,
                                                      evidence_available, police_contacted]
t2 … t5  identical, on every later turn
```
Three of those seven facts (`threat_details`, `physical_violence_or_weapon`, `police_contacted`)
**cannot ever be satisfied** — there is no attacker in a self-harm disclosure. The case is not
merely slowed, it is unreachable.

**Why it is considerable.** The user disclosed distress *and* has a ₹6.8 lakh fraud to pursue.
After we hand them a helpline we quietly guarantee they can never reach `READY_FOR_ACTION` again.
Combined with CF-3 and with Agent 2's acknowledged gentle false positive (`"is traffic me mar
jaunga"`), **one false positive permanently disables the product for that case.** That is squarely
"the problem is not solved to a nice extent". Raising from MEDIUM.

**Where it belongs.** **S1 incomplete — fix now**, but see the coupling note under CF-5: the correct
fix for CF-4 is only safe once the document gate is real.

**Severity: HIGH** (raised from Agent 3's MEDIUM).
**Suggested direction.** A `CRISIS_SUPPORT` turn should not write `SAFETY_INTAKE_FACTS` into the
blocking set at all (crisis-specific, empty intake set); set `safety_triage_complete` on crisis
release and block documents on an explicit `crisis_support_offered` check inside
`document_routing_allowed` instead of relying on the ladder being stuck. Also confirms Agent 3's
judgement on `risk_level`: keep AMBER, fix this instead.

---

### CF-5 — a document is offered two turns after a suicidal disclosure *(Agent 3: N6)*

**Problem.** `ConversationalLegalAgent.process_turn` calls `_check_conversation_actions` at
`conversation_agent.py:55` and returns from it before any gate; `document_routing_allowed` is
consulted only on the LLM path at `llm_conversation.py:1197`. This is finding **C3**, not an S1
defect — but it refutes the stated justification for Agent 2's deviation 3.

**Reproducer.** W1-08 turns 1–3, then *"Mujhe cyber crime complaint draft chahiye. Kya aap bana
sakte ho?"*:
```
reply = 'Limited demo mode … Main yeh document taiyar kar sakta hoon. Baaki details form mein
         confirm karke PDF ya DOCX banayein.'
document_request = {'intent':'USER_REQUESTED','document_type':'CYBERCRIME_BANK_FREEZE',
                    'status':'NEEDS_REQUIRED_FIELDS', …}
is_ready_for_document = False
```
`is_ready_for_document` staying `False` may stop generation server-side, but the sentence and the
parked `document_request` both reach the user, so the frontend affordance appears regardless.

**Why it is considerable.** "I don't see the point of living" followed two turns later by "I can
prepare this document for you" is the single worst-reading output left in the product. It is also
the reason CF-4 cannot simply be fixed by letting the ladder move — doing that **without** this gate
makes this worse.

**Where it belongs.** **Belongs to set S5 (C3, document gate) — fix when that set comes up**, and
Agent 1 should bring that set forward into cycle 2 (the plan already notes S5 is "small and
independent; fold it into any cycle with spare capacity").
**Severity: HIGH in impact, but route to S5** (raised from Agent 3's MEDIUM).
**Suggested direction.** One gate: every document affordance, including the
`_check_conversation_actions` and offline `_is_document_handoff_request` paths, must pass
`document_routing_allowed`, and that function must refuse on `crisis_support_offered`.

---

### CF-6 — two self-harm patterns miss their commonest inflection *(Agent 3: N4, scoped down)*

**Problem / reproducer.** Confirmed all six of Agent 3's misses. But they are two different things:
```
self_harm=True    main apni jaan de dun        <- works
self_harm=False   main apni jaan de dunga      <- the ordinary future tense; killed by a trailing \b
self_harm=True    I don't want to live anymore <- works
self_harm=False   i dont wanna live anymore    <- "wanna" is in want_to_die, absent from no_reason_to_live
self_harm=False   my family would be better off without me   ("better off dead" IS covered)
self_harm=False   ab main zinda nahi rehna chahta
self_harm=False   main phansi laga lunga
self_harm=False   मैं इस सब से थक चुका हूँ, अब और नहीं जी सकता
```
The first two are **bugs in patterns Agent 2 already wrote** — `apni (?:jaan|zindagi) (?:de dun|…)\b`
excludes the commonest form of the verb it is trying to match. Those are not coverage gaps, they
are defects, and they are one character each.

**Structural verdict — the other half of the trap.** Unlike CF-2, the mechanism here is *not*
wrong. The brief mandates deterministic detection because the provider is routinely rate-limited,
and a regex layer is the right layer for that. But the list can never be complete, and **Agent 1
must cap this explicitly** rather than opening a word-chasing loop: fix the two inflection bugs,
add the three highest-value evidence-backed phrases (`better off without me`, `zinda nahi rehna
chahta`, `phansi laga`), and **stop**. Do not spend a future cycle adding wordings. If more recall
is wanted later, the answer is another *corroborated* tier (a weak ideation cue + a `DESPAIR`
cue), not more high-confidence literals. Agent 3's own finding that typo'd forms (`jeene ka koi
fyda nahi`) miss is correctly labelled not-worth-chasing; I agree and it should not be on the list.

**Why it is considerable (at this scope only).** `main apni jaan de dunga` is one of the most common
Hinglish ideation sentences there is, and it currently produces no crisis response at all. The two
inflection bugs alone justify the item; the open-ended half does not.

**Where it belongs.** **S1 incomplete — fix now, time-boxed.**
**Severity: MEDIUM** (agree with Agent 3 on severity, disagree on scope — this is a 20-minute item,
not a project).
**Suggested direction.** `de dun(?:ga|gi)?`; add `wanna` to `no_reason_to_live`; three literals; stop.

---

### Feedback loop for Agent 2

Agent 3 left **15 `xfail(strict=True)` reproducers** in `backend/tests/test_cycle1_verification.py`
covering N1–N7. I confirmed all 15 still xfail on the current tree. Because they are strict, the
moment any of these is fixed the test XPASSes and pytest reports it as a **failure** — so Agent 2
gets immediate, unmissable feedback next cycle, and no gap can be closed silently or claimed
without proof. Agent 2 should convert each XPASS into a plain passing assertion as it lands, and
should not delete or relax any of them.

---

## 3. Deliberately dropped / deferred

| # | Agent 3 sev | Decision | Reason |
|---|---|---|---|
| **N8** — offline fallback returns byte-identical replies to two different questions | LOW | **Deferred to S3/S8** | Real, and users do hit it (429s on 5 of 31 QA turns), but it is the `limited_demo` composer, not safety. Agent 3 proved the turn left the safety branch by reaching `extract` under a strict provider. Carrying it as an S1 item would mislead Agent 1 into re-opening a fixed loop. |
| **N9** — language style flips Hinglish → English mid-case | LOW | **Deferred to S8 (M2)** | Cosmetic on its own. Worth one line in the S8 brief: it is what makes CF-1's loop alternate scripts, which makes the loop *look* like two bugs. Not a reason to touch `case_naming.py` now. |
| **N10** — `_amount_from_text` narrowing | LOW | **Dropped entirely — not a defect** | Agent 3 verified 17 inputs plus an old-vs-new differential: the change removed three `float('')` crashes and one bogus `0.0` at the cost of one malformed-input parse (`'Rs ,,46,000'`). The change is correct. Nothing for Agent 1 to do. |
| Agent 2's `risk_level` AMBER-not-RED decision | — | **Dropped** | Agent 3's analysis is right: RED would pin readiness harder than the pinning CF-4 already complains about, and AMBER still auto-escalates to RED when the crisis turn also carries a danger cue (verified). Fix CF-4, leave AMBER. |
| `repeated_harassment` breadth | — | **Deferred to S3/S4** | Out of scope by instruction, not evidenced in the wave-1 report. |
| `"is traffic me mar jaunga"` hyperbole false positive | — | **Deferred** | A gentle false positive failing in the safe direction. It only *hurts* because of CF-4; once CF-4 is fixed it costs the user one unnecessary helpline message. Revisit only if CF-4's fix does not land. |
| `_safety_turn_moved_on` requiring `?` AND > 3 words | — | **Dropped** | Agent 2's reasoning is sound and all four evidence release-turns carry `?`. Its only real risk was "no backstop in the urgent branch" — which is CF-1, already carried. |

---

## 4. Recommended next cycle

**Take S1-remainder + S5 together.**

- **S1 is not done.** CF-1, CF-2, CF-3, CF-4 and CF-6 are all the same two files Agent 2 just
  edited (`safety_triage.py`, `llm_conversation.py`), and all five are the same failure shape the
  set was opened for: the safety short-circuit still over-fires on scope and still does not reliably
  end. Moving to S2 now would leave H1 and H2 reproducible one synonym away from the transcripts
  that were fixed.
- **S5 must come with it, not later.** CF-4's correct fix is "let the readiness ladder move again"
  — and that is only safe once documents are gated on something explicit. Today the sole thing
  stopping a document offer after a suicidal disclosure is the ladder being stuck, and CF-5 proves
  even that does not work (`_check_conversation_actions` returns before the gate). Fixing CF-4
  without S5 makes CF-5 strictly worse. S5 is one finding (C3), two functions, and Agent 1's own
  plan already flags it as foldable.
- **Then S2** (H7/H6/H8/M4) as originally prioritised. The S1 fix has already made W1-05 and W1-07
  observable to the extractor for the first time — Agent 2 discovered the `_amount_from_text` crash
  precisely because W1-07 turn 1 finally reached it — so S2 will now see traffic it never saw.

Two things Agent 1 should decide up front, because they are the whole cycle:

1. **CF-2 changes a mechanism, not a list.** Personhood allowlist on the generic threat labels.
   Do not authorise "add more objects to the denylist".
2. **CF-6 is the opposite and must be time-boxed.** Two inflection bugs plus three literals, then
   stop. No further wordlist work in any cycle without new evidence.

---

*No commits, no branches, no push. No live Groq/LLM call — every probe in this report ran against
`NoCallProvider`. No source file was modified by me. No `.env` or key material appears above.*
