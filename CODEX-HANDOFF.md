# NyayaBot QA fix loop — handoff

You are taking over an in-progress engineering effort. Six cycles are done, cycle 7 is next.
Everything you need is in this repo; nothing depends on the previous session's memory.

**The method is part of the task, not an implementation detail.** The user asked specifically for a
four-role loop — plan, implement, adversarially test, triage — repeating each cycle, on one *set* of
related findings at a time rather than all 24 at once. Run it with subagents if you have them, or as
four separate sequential passes if you do not, but **keep the roles genuinely separate**. Do not
collapse "implement" and "verify" into one step. §2 defines the roles; §10 is the per-cycle rhythm.
This matters concretely: in cycle 6 the implementer's own tests all passed while the fix had a serious
hole, because those tests shared the assumptions of the implementation. A separate adversarial pass
found eight defects in it, including one regression the cycle itself had introduced.

Read this file end to end before touching code. Then read, in order:

1. `qa-pipeline/fix-loop/BRIEF.md` — the loop's shared ground truth and hard constraints
2. `qa-pipeline/fix-loop/cycle-6/agent3-test-report.md` — what is broken right now
3. `qa-pipeline/fix-loop/cycle-6/orchestrator-ground-truth.md` — verified facts, including a defect found after cycle 6's implementer finished
4. `qa-pipeline/wave-1/report.md` — the original 24 findings this whole effort is closing.
   This is the **local, authoritative copy** and the one to work from. It was published as a
   Claude artifact at <https://claude.ai/artifact/Aup4BEMdjLY1siDDZsdt5p>, which is where the
   user originally pointed at it; that link sits behind their account and you probably cannot
   open it, so use the local file. If the two ever differ, the local file wins.

---

## 1. The project

**NyayaBot** is a conversation-first Indian legal-information MVP.

- `backend/` — FastAPI + SQLAlchemy + SQLite. Python at `backend/.venv/Scripts/python.exe`.
- `frontend/` — Next.js.
- LLM provider: Groq `openai/gpt-oss-120b`, with a Gemini provider alongside.

**Architecture that matters for this work.** It is deliberately hybrid: the LLM does language
understanding, and **deterministic Python owns case state, workflow stage, safety triage, document
eligibility and output guarding**. When a fix can be deterministic, make it deterministic. Prompts
fail exactly when you need them, because the free-tier provider is rate-limited most of the time and
the fallback path is heavily exercised.

Readiness ladder (one-way):
`PRE_INTAKE -> UNDERSTANDING_CASE -> READY_FOR_LEGAL_GUIDANCE -> READY_FOR_ACTION -> READY_FOR_DOCUMENT`

Domains: `CONSUMER`, `HOUSING_TENANT`, `EMPLOYMENT`, `CYBER_FRAUD`, `POLICE_COMPLAINT`, `GENERAL`.
**Only CONSUMER and HOUSING_TENANT have a statute corpus.** The corpus is tiny and you must know its
exact contents before changing any citation logic — 17 provisions in three files:

- `backend/app/data/consumer_protection_act_2019.json` — 7 provisions
- `backend/app/data/model_tenancy_provisions.json` — 4 provisions
- `backend/app/data/rti_act_2005.json` — bound to no domain, used by the RTI application generator

There is **no case law anywhere in the corpus**, and no field that could hold a success rate. That is
why every case name and every win percentage the model emits is unverifiable *by construction*, and
why those are category bans rather than fact-checks.

---

## 2. The task you are continuing

A QA report lists **24 findings** against the bot, each evidence-quoted from real transcripts. The
job is to close them, working in **cycles**, each cycle taking one *set* of related findings rather
than all 24 at once. Findings whose fixes touch the same file or share a mechanism belong in the same
set.

**The report:** `qa-pipeline/wave-1/report.md`, published at
<https://claude.ai/artifact/Aup4BEMdjLY1siDDZsdt5p>. Work from the local file — the link is behind
the user's account. Its raw evidence is beside it: `qa-pipeline/wave-1/transcripts.json` (the actual
bot replies, with their real Unicode bytes) and `qa-pipeline/wave-1/server-evidence.log`. The findings
are IDed `C1`–`C5` (critical), `H1`–`H9` (high), `M1`–`M8` (medium), `L1`–`L2` (low), and those IDs
are used throughout every cycle document, the test names and the rest of this file.

**Treat the report as evidence, not as fact.** Its transcript quotes are reliable; several of its
root-cause diagnoses were wrong, and §7 (lesson 7) lists which. Verify a diagnosis against source before
you implement against it.

The user's original specification of the loop, which you should keep following:

```
Agent 1 (diagnose + plan) -> Agent 2 (implement) -> Agent 3 (adversarially test) -> Agent 4 (triage) -> back to Agent 1
```

- **Agent 1** reads the outstanding issues, diagnoses root causes, and writes an implementation plan
  with a definition of done and explicit preservation locks. Does not change code.
- **Agent 2** implements the plan, using its own judgement — it is expected to deviate where the plan
  is wrong, and to state each deviation with a reason.
- **Agent 3** attacks the implementation adversarially and reports what is still broken. Writes tests
  and probes, not fixes.
- **Agent 4** triages Agent 3's findings down to the **considerable** ones and hands them to Agent 1
  for the next cycle. The user was explicit: *"don't give irrelevant small issues that waste time and
  are not that significant for now."* Cutting aggressively is the point of this role.

**If you cannot spawn subagents, run the four roles yourself, sequentially, in separate passes — and
keep them genuinely separate.** The loop's value is adversarial independence: in cycle 6 the
implementer's own tests passed while the fix had a serious hole, because its tests shared the
assumptions of its implementation. An orchestrator probe found it, and the adversarial pass then found
seven more. Do not let implementation and verification collapse into one step.

Write each cycle's artifacts to `qa-pipeline/fix-loop/cycle-N/`, following the existing naming:
`agent1-plan.md`, `agent2-changes.md`, `agent3-test-report.md`, `agent4-triage.md`.

**Report to the user at the end of every cycle** — what was resolved, what code changed, what
remains. This is a standing instruction; they asked for it after two cycles ran with only
intermittent updates. Keep it to a table plus a few lines; the detail lives in the cycle directory.

---

## 3. HARD CONSTRAINTS — these are not negotiable

1. **NEVER LAUNCH A BROWSER.** `backend/tests/test_browser_autofill.py` contains two tests that
   launch a real Chromium via Playwright. Running the full suite opens a browser window on the user's
   laptop and interrupts their work. **They have asked three times for this to stop.** Always run
   tests as:
   ```
   cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
   ```
   Never run bare `pytest`. Never start a server. Never drive a browser, Playwright or browser-use.
2. **Do not commit, push, or create branches** unless the user explicitly asks in that moment.
3. **Do not add Claude, Codex, or any AI tool as author or co-author** of a commit or PR.
4. **Do not make live Groq/Gemini API calls.** The daily token allowance is exhausted, and the free
   tier is 8,000 tokens/minute against ~10,000–13,000 per conversation turn — so a single turn cannot
   fit in one minute's budget. Test with pytest and deterministic fakes only. The established pattern
   is in `backend/tests/test_groq_prompt_caching.py` and `backend/tests/test_llm_conversation.py`.
5. **Never reduce the passing test count.** It only goes up.
6. **Do not modify `backend/app/services/safety_triage.py`.** That set is closed, and an earlier cycle
   introduced a real safety regression there that had to be repaired — nine genuine violence threats
   stopped registering as safety cases. Leave it alone.
7. **Do not modify `backend/app/services/helplines.py`** by adding a number you cannot source from the
   product itself. A wrong entry there recreates the original bug, harder to spot.
8. No secrets, API keys or `.env` contents in any output file.
9. Use `backend/.venv/Scripts/python.exe`, never bare `python`.
10. The console is **cp1252**. Escape non-ASCII when printing probe output or you will get a
    `UnicodeEncodeError` instead of a result.

---

## 4. Current state

All six cycles are **committed**. `HEAD` is
`a523e0e Improved legal response safety and fixed legal citations` (cycles 5 and 6), on top of
`a58c4f6 Added response guardrails, safety architecture fixed and date handling fixed` (cycles 1–4).
The working tree should be clean apart from this handoff file.

**Do not trust the previous paragraph either — run `git log --oneline -3` and `git status` yourself.**
Commits land between sessions; an earlier draft of this file described cycles 5–6 as uncommitted and
was already stale when it was read. Every status claim in this document has that same shelf life,
which is why §10 tells you to re-measure rather than believe.

### Cycle 6 is complete in substance but missing its triage file

The loop normally ends each cycle with `agent4-triage.md`. **Cycle 6 has no such file** — that agent
was killed by an infrastructure outage before producing anything. Do not go looking for it, and do not
treat its absence as work you have to redo.

**§6 of this document *is* cycle 6's triage**: the findings ranked by harm, regression-first, with the
cut already made. It was written from the same three inputs Agent 4 would have used
(`cycle-6/agent3-test-report.md`, `agent2-changes.md`, `orchestrator-ground-truth.md`), and its three
most serious claims were verified directly against source rather than taken from the reports. Start
from it as you would from a triage hand-off.

### Test baseline

```
1 failed, 778 passed, 47 xfailed        (with tests/test_browser_autofill.py ignored)
FAILED tests/test_doc_generator.py::test_document_generation_notice
```

Re-measure this yourself before you start; do not trust the number above without checking.

The one failure is **pre-existing and unrelated**. Do not count it as your breakage and do not fix it
unless a cycle is explicitly about it.

### The xfails are a scoreboard, not a to-do backlog of failures

Every open gap has a `@pytest.mark.xfail(strict=True)` reproducer. `strict=True` means that when you
fix the underlying bug, the test **passes**, and pytest reports that as a **FAILURE** ("XPASS"). That
is intentional: a gap cannot be closed silently, and you cannot mistake a fix for a no-op.

So during a cycle, `N failed` climbing is usually **good news** — it means reproducers are flipping.
What must never happen is `passed` going **down**.

**When a reproducer flips, remove its `xfail` decorator** so it becomes a plain regression lock. Leave
the assertion body untouched. If only *some* params of a parametrised test flip, **split the
parametrisation** rather than promoting the whole test — cycles 5 and 6 both did this, and the reason
is in `cycle-5/agent2-changes.md`.

---

## 5. Findings ledger — 24 in the report

**Closed (11):** C1, C3, H1, H2, H6, H7, H8, M4, M7, M8, L1

**Partially closed (3):** C2, C4, H4 — the guard handles the recorded evidence but has gaps around it

**Never started (10):**

| ID | Issue | Set |
|---|---|---|
| C5 | Time-barred claim confirmed as within limitation | S6 |
| H3 | Bank's false "FIR first" demand endorsed as RBI policy | S7 |
| H9 | RBI limited-liability window stated wrongly as "within 48 hours" | S7 |
| H5 | Classification never fires when the one substantive turn is lost | S3 |
| M1 | Bare "yes" not bound to the question just asked | S3 |
| M5 | Readiness regressed to PRE_INTAKE, violating the one-way ladder | S3 |
| M6 | "i need help" advances readiness with no legal content | S3 |
| L2 | `"paid me"` is a case-resolved trigger, so "hasn't paid me for 3 months" can close a case (`backend/app/agents/conversation_agent.py:412`) | S3 |
| M2 | Hinglish user answered in English when the case is stuck in GENERAL | S8 |
| M3 | Titles never gain a qualifier | S8 |

Two further items found by the loop itself, not in the original 24: **D1** (internal field names
reaching the model and the reply) and **N8** (the offline fallback composer returns near-identical
replies to different questions; its xfail lives in `tests/test_cycle2_verification.py`).

"Closed" means a passing regression lock exists. It does **not** mean re-verified against a running
bot — see §8.

---

## 6. START HERE: cycle 7

Cycle 6 closed the over-filtering it targeted but **opened new problems**, including one regression of
its own making. Fix that first: the loop is supposed to converge, and shipping a cycle that adds a
hole while closing others is worse than shipping nothing.

Ranked. The first item is a regression **we** introduced; the rest are gaps.

### 6.1 REGRESSION (ours) — `licensed()` accepts a fabricated Model Tenancy Act

Cycle 6 split the corpus label `"Model Tenancy Act / State Rent Control Acts"` into alternatives and
registered each on the **accept** path. `CitationAllowlist.licensed()` compares act names with
`named in known or known in named` — substring containment — so the short key `model tenancy act` now
licenses corpus sections 11/15/21/30 for **any** act name containing that phrase.

`Section 11 of the Fictional Model Tenancy Act of Narnia` is accepted with no statute rule firing.
It was stripped before cycle 6.

Reproducer: `test_c2_a2_a_fabricated_state_model_tenancy_act_is_not_accepted_as_verified`.
Cycle 6's `agent2-changes.md` claims `licensed()` was not loosened; **that claim is false** — verify
for yourself and do not trust it.

The split itself was correct and solves a real bug (see 7.3). The error was registering the split
alternatives on the accept path as well as the reject path. Keep the split for rejection, keep the
accept path narrow.

### 6.2 C4 under-detects win probabilities, and its core premise is abusable

Two faults, both confirmed:

- **Prose form leaks.** Cycle 6 removed the ±70-character window from the inline path, which fixed
  over-deletion and opened under-detection. Fabricated probabilities now survive whenever the
  probability noun sits earlier in the sentence with ordinary words between it and the number:
  ```
  SURVIVES  Rough chance of a favourable outcome: about 30% in your case.
  SURVIVES  Your chance of a favourable order is roughly 30% here.
  SURVIVES  Success rate in such matters is about 55% in my estimate.
  SURVIVES  Probability of recovery: 25% given the evidence you hold.
  ```
  The implementer's tests miss this because every recorded evidence line is a `label: value` shape
  with the value at end-of-line.
- **The measurand veto is abusable.** The rule keeps a percentage when a measurand follows it
  (`per annum`, `GST`, `refund`). So putting a quantity noun after a probability makes it unkillable.
  The cleanest proof uses the real evidence line with two words swapped:
  `~30 % chance` is removed; `30% refund chance` is **kept**.

  Note the previous session's suggested fix — "widen the before-test and let measurand-after be the
  veto" — does **not** close this, because the veto is exactly what the abuse exploits. Design
  something better, or deliberately choose a narrower, more conservative rule and document the misses.
- **`_OUTCOME_LINE_RE` (`response_guard.py:1017`) is the old window under another name.** Its colon is
  optional and its label group is `[^:\n]{0,90}`, so the rule is really "any line ending in a
  percentage whose preceding ≤90 characters contain an outcome word". The window was deleted from one
  path and left on the other, so a markdown rate list still loses correct rates **and** still gains a
  causeless "the strength of a case cannot be reduced to a number" note.

Reproducers: `test_c4_a_win_probability_in_ordinary_prose_is_still_removed`,
`test_c4_a_measurand_after_the_number_does_not_rescue_a_win_probability`,
`test_c4_a_rate_at_the_end_of_a_line_survives_an_outcome_word_in_its_label`,
`test_c4_a_markdown_rate_list_keeps_every_rate_and_earns_no_probability_note`,
`test_c4_a_devanagari_rate_survives_when_its_measurand_sits_in_the_label`.

### 6.3 A six-digit rupee amount is read as a PIN code

`_LOCATOR_RE` (`response_guard.py:1155`) contains `\b[1-9]\d{5}\b`. Every amount from ₹1 lakh to
₹9.99 lakh therefore matches as a "locator", and the *"go and confirm it yourself"* advice that the
H4-2 fix exists to protect is deleted again whenever the reply quotes the user's own amount.
One-character-class fix. Reproducer: `test_h4_2_a_six_digit_rupee_amount_is_not_a_locator`.

### 6.4 Cheap, independent, known-good

- **C2-a3** — the statute note says "I have removed the section numbers I could not confirm" on the
  caveat-only path, where nothing was removed. The bot misreporting its own action. Cycle 6 put this
  on a hot path, so it now fires often. Reproducer:
  `test_statute_note_does_not_claim_a_removal_that_never_happened`.
- **D1-a / D1-b** — `domain_context["jurisdiction"]["fact_key"]` still ships the raw string
  `user_state` to both providers every turn, and `missing_information` ships
  `document:complainant_name`; the reply-side scrub's closed set omits document field names so
  `(complainant_name)` passes. This is the same harm as the original finding, one field over, and it
  has now survived **three** cycles. It is cheap. Close it.
- **C2-c3 / C2-c4** — the unnamed-authority class ("A High Court ruling", "settled case law", "The
  Supreme Court has held") triggers nothing, so the bot promises a judgment, delivers none, and then
  appends a note saying it has no case-law database. A visible self-contradiction.

### 6.5 Test integrity — fix this whichever cycle you are on

`test_c2_recorded_case_law_citation_is_removed` asserts the absence of `"(2020) 6 SCC 123"`,
`"M. S. R. Enterprises"` and `"v. State of Karnataka"` — but the transcript writes all three with
**U+202F** (narrow no-break space), so **three of its four assertions can never fail.** A passing lock
using the fixture's own bytes was added alongside it, but the vacuous test is still there.

**Audit the rest of the suite for the same defect.** Any assertion written in retyped ASCII against
evidence that contains U+2248, U+202F, U+2011 or U+20B9 is vacuous and gives false confidence. Always
slice test strings from `backend/tests/fixtures/wave1_replies.json`, never retype them.

---

## 7. Lessons this loop paid for — do not relearn them

These cost multiple cycles each. They generalise.

1. **Do not enumerate an open class.** A denylist of "things a person can be threatened with" and an
   allowlist of "ways to harm a person" both failed. The closed, checkable class turned out to be
   *lawful process*. Likewise: Indian statutes and case names are an open class, so a denylist of fake
   citations is impossible — the closed thing is *what the corpus actually contains*.
2. **Proximity is not a claim.** Three separate bugs came from deciding by co-occurrence inside a
   character window: an act name in a later clause deleted two correct provisions beside it; an
   outcome word within 70 characters deleted correct interest rates. Judge a span by **its own
   grammar** — syntactic attachment, not nearness. When you fix one of these, check whether the same
   pattern exists elsewhere in the file.
3. **Caveat on doubt, never delete.** This is the guard module's own stated rule and it was violated
   twice. Silently deleting correct law is worse than it looks, because the user cannot detect the
   loss — the product just gets vaguer. A deletion rule with no "inconclusive" branch will eventually
   delete something correct.
4. **Prompt-only fixes do not hold here — this was tested, not assumed.** At the commit the QA wave
   ran against, the prompt **already** said "do not cite laws not present in verified sources", "if
   verified sources are empty, clearly say the exact legal provision still needs verification", and
   — by name — "never describe the Model Tenancy Act, 2021 as binding local law unless the supplied
   context confirms State adoption". The model violated all three on a healthy, non-rate-limited turn
   while also being told machine-readably that the domain had no corpus. Verify with
   `git show a58c4f6~1:backend/app/llm/groq_provider.py`. Do not propose strengthening prompt wording
   as a fix.
5. **Over-filtering is the standing danger of every guard change.** CONSUMER and HOUSING_TENANT have
   real corpora, and the QA report explicitly *praised* two outputs: W1-02 t2's Jaipur-vs-Bengaluru
   jurisdiction reasoning and W1-04's 1930/cybercrime.gov.in sequencing. Those must survive
   byte-for-byte. Every cycle should carry forward explicit preservation locks.
6. **Fixing one direction opens the other.** Cycle 6 is the case study: it removed the over-deletion
   window and immediately under-detected. Test both directions of every guard change, always.
7. **Verify the plan's claims against source before implementing.** Three of the report's root causes
   were wrong: two findings blamed broken parsers when the real defect was dead-code wiring; one
   blamed a name regex when the ladder was reading the wrong computation; H4's "invented Section 11
   and Section 30" are both literally in the tenancy corpus. Diagnoses in these documents are
   evidence, not fact.

---

## 8. Not done, and blocked — the real verification gap

Everything above is verified by **unit tests against recorded transcripts**. Nothing since the
original wave has been verified against a **running bot**.

`qa-pipeline/` holds a working QA harness:

- `run_wave.py` — cookie-session auth, fresh user per wave, replays multi-turn scenarios carrying
  `case_id`, records per-turn mode, reply, profile digest and degradation, with pacing
- `wave-2/` and `wave-3/` — **16 scenarios, 54 turns, authored with full answer keys, never run**
- `RESUME.md` — the exact commands

This is blocked on the Groq daily token quota, not on code. When quota allows, run waves 2 and 3;
that is the only thing that will tell you whether the closed findings are actually closed in
production behaviour.

One harness bug was already fixed and you should know why it mattered: the harness had been grading
finding M8 against a top-level `sources` field that **the API has never had**, so every turn recorded
`null` and the finding was unfalsifiable. It now reads `llm_mode` and `case_profile.legal_sources`, and
splits out `verified_provisions` (the act+section entries) so a wave can distinguish "no provision was
retrieved" from "no portal link was offered". If a wave result looks impossibly uniform, suspect the
harness before the bot.

---

## 8b. A coverage gap you should close early: the auto-fill tests are all dark

Government-portal **auto-fill is not one of the 24 findings.** The QA wave could not have tested it —
`run_wave.py` only ever calls `/auth/signup` and `/chat/message`, so it never touched the browser or
auto-fill endpoints. Auto-fill appears in the report exactly once, inside H7, and only as a
*consequence*: because `opposite_party_name` was captured in 0 of 8 cases, every case was pinned at
`UNDERSTANDING_CASE`, so "documents, next actions and portal auto-fill can never unlock". H7 was fixed
in cycle 3, so that blocker is gone — but whether auto-fill itself works has never been re-verified
here. Its own fix predates this effort (`fb6eb1f Add evidence OCR processing and fix government portal
Auto-Fill`).

**The gap.** Every test run in this effort passes `--ignore=tests/test_browser_autofill.py`, which
excludes **all 10** tests in that file. Only **two** of them launch a browser:

- `test_local_chromium_can_fill_mock_form_without_submitting` (~line 37)
- `test_browser_use_session_opens_local_form_and_waits_for_review` (~line 195)

The other eight need no browser at all — session ownership, route rejection across users, runtime
preflight errors, the mock-portal feature flag, "start does not fall back to an unowned case". So the
passing count in §4 contains **zero** auto-fill coverage, and an auto-fill regression is currently
invisible. The blanket ignore was the safe move after a narrower attempt failed and opened Chromium on
the user's laptop a second time, but it over-excluded.

**The fix, which is small and worth doing in cycle 7.** Mark only the two browser tests and make them
opt-in permanently, so the other eight rejoin the suite:

```python
# in the two tests that call playwright / browser_use
@pytest.mark.browser
```
```ini
# backend/pytest.ini
[pytest]
markers =
    browser: launches a real browser; excluded by default
addopts = -m "not browser"
```

Then `pytest -q` is safe on its own and the `--ignore` flag is no longer load-bearing. **Until that
lands, keep using `--ignore=tests/test_browser_autofill.py` on every run** — and even afterwards,
never run the `browser` marker yourself. Constraint 1 in §3 still stands: the user has asked three
times.

Expect the passing count to rise by up to 8 when those tests rejoin. If any of them *fail* on
rejoining, that is a real pre-existing regression in auto-fill, not something you broke — report it
rather than fixing it inside an unrelated cycle.

---

## 9. Decisions for the user, not for you

Do not resolve these yourself. Surface them and let the user choose.

1. **Corpus-less domains now cite no statute at all.** Employment, cyber-fraud and police-complaint
   cases get no section numbers, because nothing can verify them. This is the intended consequence of
   fixing the fabricated-citation finding, not a bug — but it means those domains ship vaguer answers
   until someone adds a corpus. That is a product call.
2. **21 of 25 real Indian State rent statutes are still stripped**, including Uttar Pradesh's own
   Model Tenancy Act enactment and `Karnataka Rent Act, 1999` (deleted by a 10-character floor on the
   family key, since `rent act` is 8 characters). What decides survival right now is **naming style,
   not applicability**: a parenthesised formal name survives by accident of a regex blind spot, a
   plain `<State> Rent Control Act` survives by design, every other plain name is deleted. The clean
   generalisation — "an unrecognised Act name is no information, so always caveat" — is blocked by two
   currently-passing locks that require `Section 9 of the Code on Wages` to be stripped inside
   CONSUMER, and the user's "never reduce the passing count" rule protects those.
3. **Should a fabricated portal with no URL be caught?** Seven of seven such fabrications currently
   survive untouched, with no deletion and no caveat. But cycle 6's own plan requires
   `Download Form II from the Jaipur Rent Authority portal.` to be **kept**. Both cannot hold; the
   spec needs a decision.
4. **`ACT_NAME_RE` cannot match across a parenthetical**, so "Real Estate (Regulation and Development)
   Act" is not recognised as an act name at all. This blind spot is currently **load-bearing** — it is
   the only reason that citation gets caveated rather than deleted, and
   `test_parenthesised_act_name_is_caveated_not_deleted` locks the behaviour. Fixing the regex without
   first resolving item 2 would strip RERA in housing cases, i.e. ship a new instance of the bug being
   fixed, on a statute genuinely relevant to tenants.

---

## 10. Working rhythm

For each cycle:

1. Re-measure the baseline yourself. Never trust a number in a document, including this one.
2. Plan the set. State preservation locks and a definition of done whose every criterion is checkable
   without a live LLM call.
3. Implement in **ordered steps, running the full suite between each one.** This is not ceremony: in
   cycle 5 a fix deleted two correct corpus provisions, and a suite run between steps was the only
   thing that caught it.
4. Attack your own work adversarially, in a separate pass, with fresh probes rather than a re-run of
   the tests you just wrote.
5. Triage honestly. Separate three classes, because they cost differently: a hallucination reaching
   the user; correct law silently deleted; a self-contradiction shipped. Rank a regression you
   introduced above an equally-sized pre-existing gap.
6. Convert flipped reproducers to plain locks; file new reproducers as strict xfails.
7. Report to the user: findings closed, files changed, test count movement, anything newly broken,
   what the next cycle would take.

When a document here and the source disagree, **the source wins**. Say so plainly and move on — the
documents were written by agents whose claims were sometimes wrong, and several corrections in
`qa-pipeline/fix-loop/cycle-6/` exist precisely because someone checked instead of trusting.
