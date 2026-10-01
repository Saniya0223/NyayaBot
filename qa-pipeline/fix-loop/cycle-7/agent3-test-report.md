# Cycle 7 Agent 3 — adversarial test report

## First gate: fabricated Model Tenancy Act licensing

**Status at first adversarial pass: incomplete.** The exact-name change closes the three recorded prefix/suffix cases, but the citation parser can still discard important parts of an Act name before that exact check. The same change also makes ordinary, genuine citations with a parenthesized year disappear in corpus-less domains.

### Independent probes

| Probe | Actual result | Why it matters |
|---|---|---|
| `Section 11 of the Model Tenancy Act (Rajasthan), 2022 requires the deposit back.` | `_named_acts` returns `model tenancy act`; `guard_reply().rules()` is only `model_law_adoption_caveat`. No statute rule. | A fabricated State enactment takes the canonical positive license. |
| `Section 11 of the Model Tenancy Act (Narnia edition), 2024 ...`, `(2024)`, and `Act: Rajasthan 2022` | Each returns only `model_law_adoption_caveat`. | The parser stops at `Act` and ignores a material suffix. |
| `Section 11 of the rajasthan model tenancy act, 2022 ...` | `_named_acts` returns `[]`; only `model_law_adoption_caveat` fires. | A lowercase fabricated name is treated as an Act-less section of the tenancy corpus. |
| `Section 66D of the Information Technology Act (2000) covers cheating by personation.` in `CYBER_FRAUD` | `statute_unverified`; `Section 66D` is replaced with `the applicable provision`. | A citation already printed in the product's own cyber document is silently deleted. |
| `Section 173 of the Bharatiya Nagarik Suraksha Sanhita (2023) governs the FIR.` in `POLICE_COMPLAINT` | `statute_unverified`; `Section 173` is replaced. | The same false deletion affects another curated document citation. |

The first three are false acceptance through the parser and Act-less path, despite `CitationAllowlist.licensed` itself using exact names. The last two are a regression in preservation: `ACT_NAME_RE` drops `(2000)` and `(2023)`, so the newly exact positive match fails. In corpus-less domains `_classify` then rejects the genuine citation. Both directions matter for the first gate.

The following controls passed: the three original fabricated `Rajasthan/Jaipur/Narnia Model Tenancy Act` cases, direct canonical `Model Tenancy Act, 2021` licensing with adoption caveat, prefixed fake names and different years where the parser reads the whole name, real `Rajasthan Rent Control Act` caveating, and explicit fake Act deletion in a corpus-less `EMPLOYMENT` profile. Mixed sections `11 and 15` under a parenthetical fake suffix showed the same false positive as Section 11 alone. A subsequent bare section in another sentence takes the domain-scoped Act-less route; this did not change the diagnosis.

### Regression locks added

Seven strict-xfail parameters in `backend/tests/test_cycle4_adversarial.py`:

- Four fabricated Act suffixes must trigger a statute rule, including `(Rajasthan), 2022` and `(2024)`.
- One lowercase fabricated Act must trigger a statute rule.
- Two curated citations with a parenthesized year must retain their exact section token in their own domain.

Local deterministic command from `backend/`:

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_cycle4_adversarial.py -k "fabricated_model_act_suffix or lowercase_fabricated_model_act or curated_citation_with_parenthesized_year or fabricated_state_model_tenancy_act or only_the_canonical_model_tenancy_name"
```

Result: **4 passed, 7 xfailed, 100 deselected**. `git diff --check` exits 0. I did not edit application code or the Agent 1/2 files. I did not run a browser, server, live LLM call, commit, or push. The full suite should be rerun after these failures are corrected, before advancing to another finding.

Agent 2 subsequently corrected the parser and promoted these seven parameters. Its immediate browser-excluding full suite reported **1 unrelated failure, 789 passed, 44 xfailed**; see `agent2-changes.md`. That closed this first gate before C4 implementation.

## Ordered C4 step: probability versus legitimate rates

**Status at that adversarial pass: two material gaps remained after Agent 2's initial C4 change.** Per-figure classification fixed the recorded W1-06 percentages and many rate contrasts, but it still mistook a later refund percentage for a win probability when both sat in one sentence. It also treated every comma as a clause break, allowing an invented chance through when an explanatory phrase was set off by commas.

| Probe | Actual result | Required behavior |
|---|---|---|
| `Your chance of winning is 30% and the refund is 100% of the amount paid.` | Both percentages removed; result reads `Your chance of winning is and the refund is of the amount paid.` | Remove `30%`, keep the factual `100%` refund. |
| `Jeetne ke chances 30% hain aur refund 100% hai.` and the equivalent Devanagari sentence | Both percentages removed. | Same distinction in each script. |
| `- **Win chance:** 30% and **Refund:** 100%.` | Both figures removed from one markdown line. | Preserve the refund rate. |
| `Your chance, based on these facts, is 30%.` | No rule fires; invented `30%` survives. | Remove the asserted probability. |
| Equivalent Hinglish and Devanagari sentences with explanatory commas | No rule fires; invented `30%` survives. | Remove the asserted probability across scripts. |

The first issue is `_percent_role` retaining the earlier probability noun across `and/aur/और`, while its strong-rate label check does not recognize the later `refund` subject. The second is `_CLAUSE_BREAK_RE` treating a comma inside an explanatory aside as the end of the governing clause. These are opposite harm directions: correct advice deleted and an unsupported win percentage presented to the user. A genuine `GST is 18%` after a chance is preserved by the current strong-rate label, and a comma-separated `Chance is 30%, and refund is 100%` is preserved correctly; those controls narrow the gap.

Seven strict-xfail parameters were added in `backend/tests/test_cycle4_adversarial.py`: four mixed chance/refund lines and three explanatory-comma chance lines. Focused deterministic command from `backend/`:

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_cycle4_adversarial.py tests/test_response_guard.py -k "conjoined_refund_percentage or probability_with_explanatory_commas or probability_and_rate_contrasts_include_ranges or w1_06_t4 or probability_rows"
```

Result: **5 passed, 7 xfailed, 352 deselected**. The five passes include the four Agent 2 probability/rate contrasts and the byte-faithful W1-06 recorded-reply lock. `git diff --check` exits 0. I did not edit application code, run a browser/server/live LLM, or commit/push. Re-run the full browser-excluding suite after these seven failures are corrected before moving to H4-2.

Agent 2 subsequently corrected both and promoted the seven parameters. Its immediate browser-excluding full suite reported **1 unrelated failure, 825 passed, 19 xfailed**; see `agent2-changes.md`. The ordered H4-2, C2-a3, D1, C2-c3/c4, and fixture-byte steps followed with full-suite gates after each; the last Agent 2 gate was **1 unrelated failure, 870 passed, 8 xfailed**.

## Final independent pass over the completed cycle 7 set

**Status: cycle 7 should not close yet.** Seven new strict-xfail parameters expose three material residual classes. These are fresh local deterministic probes against the final source; no application code was changed in this pass.

| Area | Exact reproducer | Actual behavior | Consequence |
|---|---|---|---|
| Act licensing | `Section 11 of the Model Tenancy Act [Rajasthan], 2022 governs deposits.` and `... Act - Rajasthan, 2022 ...` | `_named_acts` returns only `model tenancy act`; `guard_reply` reports only `model_law_adoption_caveat`. | Bracket/dash qualifiers are dropped before exact matching, so a fabricated State enactment still inherits a positive citation license. |
| C4, correct rate | `Your chance is uncertain, but interest, if awarded, is 18%.` | Entire sentence is removed and an outcome-probability note is appended. | Paired-comma recovery reaches back to `chance` across the `but interest` clause, deleting a legitimate rate. A parallel `GST, where applicable, is 18%` probe did the same. |
| C4, invented chance | `Your chance of success, roughly 30%.` | No redaction; the unsupported percentage survives. | One explanatory comma is not recovered by the paired-comma branch. A Roman Hinglish form behaves the same way. |
| Unnamed case law | `Check your bank statement, because the Supreme Court has held that deposits must be returned.` | No redaction or case-law disclosure. | The unrelated `Check` before the court assertion trips the verification-prefix exception. |
| Unnamed case law | `The Supreme Court has ruled that deposits must be refunded within one month.` | No redaction or case-law disclosure. | `has ruled` is a common holding form missing from the detector. |
| Advice preservation | `A High Court ruling supports your claim, so keep your receipts and messages.` | The entire sentence, including the receipt advice, is removed. | The removal is at sentence granularity even when only one clause is unsupported. |

Seven strict-xfail parameters were added to `backend/tests/test_cycle4_adversarial.py`: two Act qualifiers, two C4 cases, and three court-claim/advice cases. They assert the actual user-facing outcome, not merely an internal regex match. Focused command from `backend/`:

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_cycle4_adversarial.py tests/test_response_guard.py tests/test_cycle4_verification.py
```

Result: **399 passed, 14 xfailed**. The 14 comprise seven pre-existing State rent Act xfails plus seven new parameters. A narrower run covering these probes, H4 locator/amount cases, statute-note truth, D1 serialized payload and document-key scrub, mixed C4 cases, W1-06 and all recorded-reply idempotency locks reported **36 passed, 7 xfailed, 370 deselected**. `git diff --check` exits 0. The last Agent 2 browser-excluding whole-suite gate before my new xfails was **1 known document-generator failure, 870 passed, 8 xfailed**; the eighth old xfail is the deferred N8 offline-composer reproducer in `test_cycle2_verification.py`. A fresh full-suite count belongs after the seven new gaps are fixed. No browser test was run here.

Coverage reviewed without a new defect: labelled PIN and street locators still trigger H4 protection, while six-digit claim amounts alone do not; English/Hinglish/Devanagari statute notes accurately distinguish stripped, retained, and mixed citations; `LLMResponseContext.model_payload()` removes jurisdiction `fact_key` and candidate `key` from the JSON-mode copy while both providers call that method; document missing-information labels and `(complainant_name)` reply scrub pass; byte-faithful W1-06, W1-03, W1-02, W1-04, and recorded-reply idempotency locks pass in the focused suites. I did not edit the concurrent browser-agent files, launch a browser/server, make a live Groq/Gemini call, or commit/push/branch.
