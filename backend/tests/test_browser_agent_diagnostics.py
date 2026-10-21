"""Phase 0 Auto-Fill diagnostics: sanitised error samples and login-wall signals.

Pure tests against fake `history` / `page` objects. No playwright, no
browser_use.Agent, no network, no LLM call, no Chromium.
"""

import asyncio
import logging

from app.agents import browser_agent as ba


class FakeHistory:
    def __init__(self, errors):
        self._errors = errors

    def errors(self):
        return list(self._errors)


class BrokenHistory:
    def errors(self):
        raise RuntimeError("malformed")


class FakePage:
    def __init__(self, raw=None, url="https://portal.example.gov.in/home", raises=None):
        self._raw, self.url, self._raises = raw, url, raises
        self.evaluated = []

    async def evaluate(self, script):
        self.evaluated.append(script)
        if self._raises:
            raise self._raises
        return self._raw


def signals(raw=None, url="https://portal.example.gov.in/home", raises=None):
    return asyncio.run(ba._page_login_signals(FakePage(raw, url, raises)))


# ── Agent error samples ───────────────────────────────────────────────────────

def test_clean_run_logs_no_error_samples():
    assert ba.summarise_agent_errors(FakeHistory([None, "", None])) == []


def test_malformed_history_returns_empty_not_a_crash():
    assert ba.summarise_agent_errors(BrokenHistory()) == []


def test_repeated_identical_error_is_shown_once_with_a_count():
    error = "Error code: 400 - Invalid JSON schema for response_format"
    assert ba.summarise_agent_errors(FakeHistory([error] * 7)) == [f"{error} [x7]"]


def test_only_the_first_few_distinct_errors_are_kept():
    samples = ba.summarise_agent_errors(FakeHistory([f"distinct error {i}" for i in range(10)]))
    assert samples == ["distinct error 0", "distinct error 1", "distinct error 2"]


def test_error_text_is_capped_and_whitespace_collapsed():
    sample = ba.summarise_agent_errors(FakeHistory(["line one\n\n   line two " + "x" * 1000]))[0]
    assert sample.startswith("line one line two xxx")
    assert len(sample) == 300
    assert "\n" not in sample


def test_personal_numbers_and_emails_are_masked_in_error_text():
    sample = ba.summarise_agent_errors(FakeHistory([
        "Failed typing 9876543210 / 4111 1111 1111 1111 / 1234-5678-9012 for asha.k@example.com"
    ]))[0]
    for private in ("9876543210", "4111", "1234-5678", "asha.k", "example.com"):
        assert private not in sample
    assert "<number>" in sample and "<email>" in sample


def test_short_numbers_and_status_codes_stay_readable():
    sample = ba.summarise_agent_errors(FakeHistory(["Error code: 429 - limit 8000, used 7994"]))[0]
    assert "429" in sample and "8000" in sample


# ── Login-wall signals ────────────────────────────────────────────────────────

def test_plain_form_page_is_not_a_login_wall():
    result = signals({"inputs": 9, "password": 0, "otp": 0, "captcha": 0})
    assert result["likely_login_wall"] is False
    assert result["inputs"] == 9 and result["host"] == "portal.example.gov.in"


def test_password_field_marks_a_login_wall():
    assert signals({"inputs": 2, "password": 1, "otp": 0, "captcha": 0})["likely_login_wall"] is True


def test_otp_field_marks_a_login_wall():
    assert signals({"inputs": 2, "password": 0, "otp": 1, "captcha": 0})["likely_login_wall"] is True


def test_captcha_marks_a_login_wall():
    assert signals({"inputs": 3, "password": 0, "otp": 0, "captcha": 1})["likely_login_wall"] is True


def test_login_style_url_path_marks_a_login_wall_even_with_no_visible_fields():
    result = signals({"inputs": 0, "password": 0, "otp": 0, "captcha": 0},
                     url="https://portal.example.gov.in/citizen/Login.aspx")
    assert result["url_hint"] is True and result["likely_login_wall"] is True


def test_url_hint_reads_the_path_only_never_the_query_string():
    result = signals({"inputs": 5, "password": 0, "otp": 0, "captcha": 0},
                     url="https://portal.example.gov.in/complaint?next=/login&token=abc")
    assert result["url_hint"] is False and result["likely_login_wall"] is False


def test_inspection_failure_is_reported_not_raised():
    assert signals(raises=RuntimeError("page closed")) == {"error": "RuntimeError"}


def test_missing_or_null_counts_are_treated_as_zero():
    result = signals({"inputs": None})
    assert result["inputs"] == 0 and result["likely_login_wall"] is False


def test_signal_script_reads_counts_only_never_field_values():
    page = FakePage({"inputs": 1, "password": 0, "otp": 0, "captcha": 0})
    asyncio.run(ba._page_login_signals(page))
    script = page.evaluated[0]
    assert ".value" not in script and "innerText" not in script and "textContent" not in script


# ── Logging wrapper ───────────────────────────────────────────────────────────

def session():
    return ba.BrowserSession(
        session_id="s-1", case_id="c-1", user_id="u-1", category="CYBER_FRAUD",
        portal_label="Portal", portal_url="https://portal.example.gov.in/", task="private task text",
    )


def test_signal_log_line_has_the_stage_and_counts_and_no_task_text(caplog):
    page = FakePage({"inputs": 2, "password": 1, "otp": 0, "captcha": 1},
                    url="https://portal.example.gov.in/login?secret=abc")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        asyncio.run(ba._log_page_signals(session(), page, "portal_opened"))
    line = caplog.text
    assert "event=portal_page_signals stage=portal_opened" in line
    assert "password=1" in line and "captcha=1" in line and "likely_login_wall=True" in line
    assert "private task text" not in line and "secret=abc" not in line


def test_signal_log_survives_an_uninspectable_page(caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        asyncio.run(ba._log_page_signals(session(), FakePage(raises=ValueError("x")), "after_run"))
    assert "stage=after_run inspect_error=ValueError" in caplog.text
