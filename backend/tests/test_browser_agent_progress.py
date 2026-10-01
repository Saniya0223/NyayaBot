"""Unit tests for honest outcome detection in the Auto-Fill browser agent.

Pure tests of `_classify_agent_progress` / `_no_progress_message` against fake
`history` objects. No playwright, no browser_use.Agent, no network, no LLM call,
no Chromium. `browser_agent.py` only imports those lazily inside
`run_browser_session`, so importing the module here never touches them.
"""

from app.agents import browser_agent as ba


class FakeHistory:
    """Stands in for `browser_use.AgentHistoryList`: exposes only the two read
    methods `_classify_agent_progress` uses, nothing else."""

    def __init__(self, action_names, errors=None):
        self._action_names = list(action_names)
        self._errors = list(errors or [])

    def action_names(self):
        return list(self._action_names)

    def errors(self):
        return list(self._errors)


class BrokenHistory:
    """A malformed `history` whose accessors raise - must fail closed (no progress),
    never crash and never default to success."""

    def action_names(self):
        raise RuntimeError("malformed history")

    def errors(self):
        raise RuntimeError("malformed history")


# ── Scenario 1: zero useful actions -> NOT prepared-for-review ────────────────

def test_empty_history_is_not_progress():
    made_progress, rate_limited, actions = ba._classify_agent_progress(FakeHistory([]))
    assert made_progress is False
    assert rate_limited is False
    assert actions == []


def test_only_a_wait_action_is_not_progress():
    made_progress, _, _ = ba._classify_agent_progress(FakeHistory(["wait"]))
    assert made_progress is False


# ── Scenario 2: repeated rate-limit errors, no progress -> truthful failure ───

def test_repeated_rate_limit_errors_with_no_progress_is_flagged_rate_limited():
    history = FakeHistory(
        action_names=["go_to_url"],
        errors=[
            "Error calling LLM: on tokens per minute (TPM): Limit 8000, Used 8000",
            "Error calling LLM: on tokens per minute (TPM): Limit 8000, Used 8000",
            "Error calling LLM: on tokens per minute (TPM): Limit 8000, Used 8000",
        ],
    )
    made_progress, rate_limited, _ = ba._classify_agent_progress(history)
    assert made_progress is False
    assert rate_limited is True


def test_rate_limit_message_is_truthful_and_does_not_claim_success():
    msg = ba._no_progress_message(rate_limited=True)
    assert "rate-limited" in msg
    lowered = msg.lower()
    assert "prepared form" not in lowered
    assert "ready for review" not in lowered
    assert "nothing has been filled or submitted" in lowered


def test_generic_no_progress_message_does_not_claim_success():
    msg = ba._no_progress_message(rate_limited=False)
    lowered = msg.lower()
    assert "prepared form" not in lowered
    assert "couldn't complete the form preparation" in lowered
    assert "nothing has been filled or submitted" in lowered


# ── Scenario 3: NyayaBot's own pause action fired -> unambiguous success ──────

def test_pause_for_review_action_is_unambiguous_success():
    history = FakeHistory(["go_to_url", "click_element_by_index", "pause_for_review"])
    made_progress, rate_limited, _ = ba._classify_agent_progress(history)
    assert made_progress is True
    assert rate_limited is False


def test_pause_for_sensitive_input_action_is_also_unambiguous_success():
    history = FakeHistory(["go_to_url", "pause_for_sensitive_input"])
    made_progress, _, _ = ba._classify_agent_progress(history)
    assert made_progress is True


def test_pause_for_review_counts_as_success_even_alongside_recorded_errors():
    """A transient error earlier in the run must not veto success once the agent
    genuinely reached and signalled the review step."""
    history = FakeHistory(
        action_names=["go_to_url", "input_text", "pause_for_review"],
        errors=["Could not parse response, retrying"],
    )
    made_progress, _, _ = ba._classify_agent_progress(history)
    assert made_progress is True


# ── Scenario 4: known fields filled, action trace shows it -> prepared ────────

def test_filling_several_fields_is_progress():
    history = FakeHistory(["go_to_url", "input_text", "input_text", "click_element_by_index"])
    made_progress, _, _ = ba._classify_agent_progress(history)
    assert made_progress is True


# ── Scenario 5: navigation only, form never reached -> NOT prepared ───────────

def test_navigation_only_is_not_progress():
    history = FakeHistory(["go_to_url", "go_to_url", "scroll", "wait"])
    made_progress, _, _ = ba._classify_agent_progress(history)
    assert made_progress is False


def test_navigation_and_wait_with_no_errors_is_still_not_progress():
    """No exception occurred and the run 'succeeded' in the sense of not crashing -
    that alone must not be read as the form being prepared."""
    history = FakeHistory(["go_to_url", "wait"], errors=[])
    made_progress, rate_limited, _ = ba._classify_agent_progress(history)
    assert made_progress is False
    assert rate_limited is False


# ── Scenario 6: partial progress, run not finished -> still counts as progress ─

def test_one_field_filled_without_reaching_review_still_counts_as_progress():
    """The agent filled a real field but stopped (e.g. ran out of steps) before
    calling pause_for_review. This is 'meaningful preparation happened', not
    'nothing useful happened', so it must not be collapsed into total failure -
    the existing review UI already invites the user to 'complete any missing
    fields', which is exactly what a partial fill needs."""
    history = FakeHistory(["go_to_url", "input_text"])
    made_progress, rate_limited, _ = ba._classify_agent_progress(history)
    assert made_progress is True
    assert rate_limited is False


# ── Defensive: a malformed history must fail closed, never crash or default open

def test_malformed_history_fails_closed_not_open():
    made_progress, rate_limited, actions = ba._classify_agent_progress(BrokenHistory())
    assert made_progress is False
    assert rate_limited is False
    assert actions == []


# ── Scenario 7 (existing mock-portal tests remain passing) is not re-verified
# here: this file adds coverage, it does not touch `_fill_known_fields` or the
# `deterministic` branch in `run_browser_session`, both of which are unmodified
# by this change. See `tests/test_browser_autofill.py` for that path's own tests
# (excluded from the default run because two of its ten tests launch a real
# browser - see qa-pipeline/fix-loop/BRIEF.md).
