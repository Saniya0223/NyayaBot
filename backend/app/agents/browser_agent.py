"""
browser_agent.py
----------------
Agentic browser automation for NyayaBot.

Flow:
  1. Frontend calls POST /api/v1/browser/start  → session_id returned
  2. Agent opens a real Chromium window, navigates to the correct govt portal,
     and fills the form using case data from the user's profile.
  3. Before submitting, the agent PAUSES and captures a screenshot.
  4. The screenshot + current status is polled by the frontend via
     GET /api/v1/browser/status/{session_id}
  5. The frontend also connects to the live CDP WebSocket proxy
     GET /api/v1/browser/cdp/{session_id} for real-time browser view + control.
  6. User can:
       a. Approve  → POST /api/v1/browser/approve/{session_id}  → agent submits
       b. Cancel   → POST /api/v1/browser/cancel/{session_id}   → agent stops
       c. Interact directly in the live browser canvas inside NyayaBot panel
  7. Sensitive fields (Aadhaar, OTP) are never filled by the agent —
     the session pauses and the user types directly in the live canvas.

All sessions are stored in an in-memory dict (sufficient for MVP).
For production, move to Redis or a DB-backed queue.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from app.services.form_filler import fill_page
from app.services.form_navigator import MAX_NAV_STEPS, navigate_once, settle_page

logger = logging.getLogger("uvicorn.error")


def _find_free_port() -> int:
    """Bind to port 0 to let the OS pick a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]

# ── Session state ────────────────────────────────────────────────────────────

class SessionStatus(str, Enum):
    PENDING   = "pending"       # not yet started
    RUNNING   = "running"       # agent is filling the form
    PAUSED    = "paused"        # waiting for human review / OTP
    APPROVED  = "approved"      # user approved — agent will submit
    CANCELLED = "cancelled"     # user cancelled
    DONE      = "done"          # successfully submitted
    ERROR     = "error"         # something went wrong


@dataclass
class BrowserSession:
    session_id: str
    case_id: str
    user_id: str
    category: str
    portal_label: str
    portal_url: str
    task: str
    status: SessionStatus = SessionStatus.PENDING
    screenshot_b64: Optional[str] = None   # latest screenshot (base64 PNG)
    message: str = ""                       # status message shown to user
    error: Optional[str] = None
    error_code: Optional[str] = None
    steps: list[str] = field(default_factory=list)
    review_fields: list[dict[str, str]] = field(default_factory=list)
    current_url: str = ""
    # CDP live-control fields
    cdp_port: Optional[int] = None          # Chrome remote-debugging port for this session
    cdp_ready: bool = False                 # True once Chrome is accepting CDP connections
    pause_kind: Optional[str] = None
    # Local mock portal only: form field name -> value, filled by Playwright without an LLM.
    fill_values: Optional[dict[str, str]] = field(default=None, repr=False)
    # Real portals: structured case values for rule-based filling (key -> text).
    case_data: Optional[dict[str, str]] = field(default=None, repr=False)
    entry_hints: tuple[str, ...] = ()
    entry_avoid: tuple[str, ...] = ()
    # Internal asyncio primitives (not serialised to the client)
    _approve_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _loop: Optional[asyncio.AbstractEventLoop] = field(default=None, repr=False)


# In-memory registry of active sessions
_sessions: dict[str, BrowserSession] = {}


def get_session(session_id: str) -> Optional[BrowserSession]:
    return _sessions.get(session_id)


def create_session(
    case_id: str,
    user_id: str,
    category: str,
    portal_label: str,
    portal_url: str,
    task: str,
    fill_values: Optional[dict[str, str]] = None,
    case_data: Optional[dict[str, str]] = None,
    entry_hints: tuple[str, ...] = (),
    entry_avoid: tuple[str, ...] = (),
) -> BrowserSession:
    session = BrowserSession(
        session_id=str(uuid.uuid4()),
        case_id=case_id,
        user_id=user_id,
        category=category,
        portal_label=portal_label,
        portal_url=portal_url,
        current_url=portal_url,
        task=task,
        fill_values=fill_values,
        case_data=case_data,
        entry_hints=entry_hints,
        entry_avoid=entry_avoid,
    )
    _sessions[session.session_id] = session
    return session


# ── Screenshot helper ────────────────────────────────────────────────────────

async def _take_screenshot(page) -> str:
    """Capture a screenshot and return it as a base64-encoded PNG string."""
    png_bytes = await page.screenshot(full_page=False)
    return base64.b64encode(png_bytes).decode()


async def _collect_review_fields(page) -> list[dict[str, str]]:
    """Expose labels and completion state, never the actual form values."""
    return await page.locator("input, textarea, select").evaluate_all("""elements =>
      elements.filter(el => el.type !== 'hidden' && el.getClientRects().length)
        .slice(0, 60).map(el => {
          const label = Array.from(el.labels?.[0]?.childNodes || [])
            .filter(node => node.nodeType === Node.TEXT_NODE)
            .map(node => node.textContent).join(' ').trim() || el.getAttribute('aria-label') ||
            el.getAttribute('placeholder') || el.name || 'Unlabelled field';
          const sensitive = /password|otp|captcha|aadhaar|pan/i.test(label) ||
            el.type === 'password';
          const filled = el.type === 'checkbox' ? el.checked : Boolean(el.value);
          return {label: String(label).slice(0, 90), status: sensitive ? 'manual' :
            filled ? 'filled' : 'missing'};
        })
    """)


async def _wait_for_continue_or_cancel(session: BrowserSession) -> None:
    waits = [
        asyncio.create_task(session._approve_event.wait()),
        asyncio.create_task(session._cancel_event.wait()),
    ]
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
    if session._cancel_event.is_set():
        raise asyncio.CancelledError()
    session._approve_event.clear()


_SUBMISSION_GUARD = """
(() => {
  window.__NYAYA_ALLOW_FINAL_SUBMIT__ = false;
  const finalAction = (node) => {
    const label = [node?.innerText, node?.value, node?.getAttribute?.('aria-label')]
      .filter(Boolean).join(' ').toLowerCase();
    return /\\b(submit|register complaint|file complaint|lodge complaint|pay now|pay|payment|make payment|proceed to pay)\\b/.test(label);
  };
  // A <button> with no type attribute reports type "submit" by default, even when it
  // is not inside a form and submits nothing (React pop-ups and menus are full of
  // them). Only a submit control that belongs to a form can submit one.
  const submitsForm = (node) =>
    (node.type === 'submit' || node.type === 'image') && Boolean(node.form);
  document.addEventListener('submit', (event) => {
    if (!window.__NYAYA_ALLOW_FINAL_SUBMIT__) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('button, input[type=submit], [role=button]');
    if (button && !window.__NYAYA_ALLOW_FINAL_SUBMIT__ &&
        (submitsForm(button) || finalAction(button))) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  const submit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function() {
    if (window.__NYAYA_ALLOW_FINAL_SUBMIT__) return submit.call(this);
  };
})();
"""


async def _fill_known_fields(page, session: BrowserSession) -> None:
    """Type supplied values into matching inputs; missing values stay blank for the user."""
    filled = 0
    for name, value in (session.fill_values or {}).items():
        if not value:
            continue
        field_el = page.locator(f'[name="{name}"]')
        try:
            if await field_el.count() != 1:
                continue
            tag = await field_el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    await field_el.select_option(value=value, timeout=2000)
                except Exception:
                    await field_el.select_option(label=value, timeout=2000)
            else:
                await field_el.fill(value, timeout=5000)
            filled += 1
            await asyncio.sleep(0.25)  # lets the live view show each field being filled
        except Exception:
            logger.info("session=%s event=field_left_blank field=%s", session.session_id, name)
    session.steps.append(f"Filled {filled} field(s) from your profile and this case.")


async def _hold_for_manual_submission(session: BrowserSession, browser) -> None:
    """Approval unlocks manual browser controls; it never resumes the LLM agent."""
    await _wait_for_continue_or_cancel(session)
    context = browser.browser_context
    await context.add_init_script("window.__NYAYA_ALLOW_FINAL_SUBMIT__ = true")
    for page in context.pages:
        await page.evaluate("window.__NYAYA_ALLOW_FINAL_SUBMIT__ = true")
    session.status = SessionStatus.APPROVED
    if session.pause_kind == "no_progress":
        # Nothing was prepared, so there is no review to approve; say so plainly.
        session.message = (
            "Nothing was filled automatically. You can fill the form yourself in the live browser. "
            "NyayaBot cannot confirm whether the portal accepted anything. Close this panel when finished."
        )
        session.steps.append("User took over manually; nothing had been prepared automatically.")
    else:
        session.message = (
            "Review approved. You may submit manually in the live browser. "
            "NyayaBot cannot confirm whether the portal accepted it. Close this panel when finished."
        )
        session.steps.append("User approved manual submission; the agent remains stopped.")
    await session._cancel_event.wait()


def _resolve_llm() -> tuple[object, bool]:
    """
    Instantiate the appropriate LLM wrapper from browser_use.llm.
    Prefers Groq if GROQ_API_KEY is present, falls back to Gemini if GEMINI_API_KEY is set.
    """
    from app.config import settings

    groq_key = (settings.GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")).strip()
    gemini_key = (settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()

    if groq_key:
        os.environ["GROQ_API_KEY"] = groq_key
        model_name = (settings.LLM_MODEL or "").strip()
        if not model_name or "openai" in model_name.lower() or "gemini" in model_name.lower():
            model_name = "llama-3.3-70b-versatile"

        # 1. Try ChatGroq from browser_use.llm
        try:
            from browser_use.llm import ChatGroq
            try:
                return ChatGroq(model=model_name, api_key=groq_key), True
            except TypeError:
                return ChatGroq(model_name=model_name, groq_api_key=groq_key), True
        except (ImportError, AttributeError):
            pass

        # 2. Try ChatOpenAI from browser_use.llm pointing to Groq's endpoint
        try:
            from browser_use.llm import ChatOpenAI
            try:
                return ChatOpenAI(
                    model=model_name,
                    api_key=groq_key,
                    base_url="https://api.groq.com/openai/v1",
                ), True
            except TypeError:
                return ChatOpenAI(
                    model_name=model_name,
                    openai_api_key=groq_key,
                    openai_api_base="https://api.groq.com/openai/v1",
                ), True
        except (ImportError, AttributeError):
            pass

    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key
        try:
            from browser_use.llm import ChatGoogle
            gemini_model = settings.LLM_MODEL if "gemini" in (settings.LLM_MODEL or "") else "gemini-2.0-flash"
            try:
                return ChatGoogle(model=gemini_model, api_key=gemini_key), False
            except TypeError:
                return ChatGoogle(model=gemini_model, api_key=gemini_key), False
        except (ImportError, AttributeError):
            pass

    raise ValueError(
        "Could not initialize browser_use LLM. Please verify GROQ_API_KEY in backend/.env"
    )


# ── Honest outcome detection for the LLM-driven agent path ────────────────────
#
# `browser_use.Agent.run()` returns *normally* even when the agent made zero
# progress: its internal rate-limit handler (browser_use/agent/service.py) catches
# a provider 429/TPM error, sleeps, retries, and after `max_failures` (default 3)
# consecutive failures simply stops and returns the history it has - no exception
# reaches us. On Groq's free tier this is the common case, not the rare one: a
# browser-automation prompt carries a full page's DOM/accessibility tree, which is
# far larger than the 8,000 TPM budget this project has hit everywhere else.
#
# Before this fix, a normal return was treated as "the form is prepared" with no
# check on what actually happened, so the user saw a success message ("Review the
# prepared form") while the agent had never left the portal's landing page.

# Actions that constitute the agent actually doing something to the page, as
# opposed to just looking at it. Deliberately excludes pure navigation/scroll/wait
# actions (e.g. "go_to_url", "scroll", "wait") - reaching a page is not filling it.
_MEANINGFUL_PROGRESS_ACTIONS = frozenset({
    "input_text", "click_element_by_index", "click_element",
    "select_dropdown_option", "upload_file", "send_keys",
    "pause_for_review", "pause_for_sensitive_input",
})

_RATE_LIMIT_MARKERS = ("tokens per minute", "rate limit", "rate_limit", "429")


def _classify_agent_progress(history) -> tuple[bool, bool, list[str]]:
    """Decide, from positive evidence only, whether the agent made real progress.

    Never infer success from the *absence* of an exception - `history.is_done()`
    alone is not enough either, since an agent can call `done` having given up.
    Success requires at least one action that actually touched the page, or our
    own `pause_for_review`/`pause_for_sensitive_input` controller action firing,
    which is unambiguous positive evidence regardless of what else happened.

    `history` is whatever `Agent.run()` returned (an `AgentHistoryList`), or in
    tests, any object exposing the same `action_names()` / `errors()` API - this
    function touches nothing else, so it needs no browser_use import to test.

    Returns (made_progress, rate_limited, action_names).
    """
    try:
        action_names = list(history.action_names())
    except Exception:
        action_names = []
    try:
        errors = [str(e) for e in history.errors() if e]
    except Exception:
        errors = []

    # Explicit, unambiguous: NyayaBot's own pause action only fires once the
    # agent has decided the form is ready (or hit a field it must not touch).
    if "pause_for_review" in action_names or "pause_for_sensitive_input" in action_names:
        return True, False, action_names

    made_progress = any(name in _MEANINGFUL_PROGRESS_ACTIONS for name in action_names)
    rate_limited = any(
        marker in error.lower() for error in errors for marker in _RATE_LIMIT_MARKERS
    )
    return made_progress, rate_limited, action_names


# ── Diagnostics (log-only; none of this changes what the session does) ───────
#
# Auto-Fill failures were invisible: the agent swallows its own errors and returns
# normally, and nothing recorded which portal was chosen or whether a login page
# was in the way. These helpers write short, sanitised log lines so a failed run
# can be diagnosed from the backend terminal. They never raise.

_MAX_LOGGED_AGENT_ERRORS = 3
_MAX_ERROR_CHARS = 300
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 6+ digits, allowing spaces or dashes between them (phone, account, Aadhaar, PIN+).
_LONG_NUMBER_RE = re.compile(r"\d(?:[\s-]?\d){5,}")
_LOGIN_URL_HINTS = ("login", "signin", "sign-in", "sign_in", "logon", "auth", "otp", "captcha", "register")


def _safe_error_excerpt(text: str) -> str:
    """Collapse whitespace, mask emails and long digit runs, and cap the length."""
    cleaned = " ".join(str(text).split())
    cleaned = _EMAIL_RE.sub("<email>", cleaned)
    cleaned = _LONG_NUMBER_RE.sub("<number>", cleaned)
    return cleaned[:_MAX_ERROR_CHARS]


def summarise_agent_errors(history, limit: int = _MAX_LOGGED_AGENT_ERRORS) -> list[str]:
    """First few distinct errors from an agent run, sanitised, with repeat counts.

    Returns [] for a clean run or a malformed `history`. A repeated provider error
    (e.g. the same 400 on every step) shows once as "... [x7]" rather than flooding
    the log.
    """
    try:
        raw = [str(item) for item in history.errors() if item]
    except Exception:
        return []
    counts: dict[str, int] = {}
    for item in raw:
        excerpt = _safe_error_excerpt(item)
        counts[excerpt] = counts.get(excerpt, 0) + 1
    return [
        f"{excerpt} [x{count}]" if count > 1 else excerpt
        for excerpt, count in list(counts.items())[:limit]
    ]


_PAGE_SIGNALS_JS = """
(() => {
  const visible = el => el.getClientRects().length > 0;
  const describe = el => [el.id, el.name, el.placeholder, el.getAttribute('aria-label'),
    el.getAttribute('alt'), el.getAttribute('src'), el.className]
    .filter(v => typeof v === 'string' && v).join(' ').toLowerCase();
  const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
    .filter(el => el.type !== 'hidden' && visible(el));
  return {
    inputs: inputs.length,
    password: inputs.filter(el => el.type === 'password').length,
    otp: inputs.filter(el => /otp|one.?time/.test(describe(el))).length,
    captcha: Array.from(document.querySelectorAll('input, img, canvas, iframe'))
      .filter(el => visible(el) && /captcha/.test(describe(el))).length,
  };
})()
"""


def _is_login_wall(signals: dict) -> bool:
    """Does this page need the user to sign in or verify before a form can appear?

    A password or OTP field always means the user must act. A CAPTCHA only counts
    on a sparse, login-style page: a real complaint form can also end in a CAPTCHA,
    and pausing before filling it would be wrong. A login-looking URL path only
    counts when the page has (almost) no fields, because paths like "/Register"
    also belong to genuine complaint forms.
    """
    inputs = signals.get("inputs", 0)
    return bool(
        signals.get("password", 0)
        or signals.get("otp", 0)
        or (signals.get("captcha", 0) and inputs <= 3)
        or (signals.get("url_hint") and inputs <= 1)
    )


async def _page_login_signals(page) -> dict:
    """Counts of password / OTP / CAPTCHA elements on the current page, for logging.

    Reads element counts and the URL *path* only - never values, never the query
    string. Returns {"error": "<ExceptionName>"} if the page cannot be inspected.
    """
    try:
        raw = await page.evaluate(_PAGE_SIGNALS_JS)
        signals = {key: int(raw.get(key, 0) or 0) for key in ("inputs", "password", "otp", "captcha")}
        parsed = urlparse(str(getattr(page, "url", "") or ""))
        path = (parsed.path or "").lower()
        signals["url_hint"] = any(hint in path for hint in _LOGIN_URL_HINTS)
        signals["host"] = parsed.hostname or ""
        signals["likely_login_wall"] = _is_login_wall(signals)
        return signals
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break a session
        return {"error": type(exc).__name__}


async def _log_page_signals(session: "BrowserSession", page, stage: str) -> None:
    signals = await _page_login_signals(page)
    if "error" in signals:
        logger.info(
            "session=%s event=portal_page_signals stage=%s inspect_error=%s",
            session.session_id, stage, signals["error"],
        )
        return
    logger.info(
        "session=%s event=portal_page_signals stage=%s host=%s inputs=%s password=%s otp=%s "
        "captcha=%s url_hint=%s likely_login_wall=%s",
        session.session_id, stage, signals["host"], signals["inputs"], signals["password"],
        signals["otp"], signals["captcha"], signals["url_hint"], signals["likely_login_wall"],
    )


_LOGIN_PAUSE_MESSAGE = (
    "This portal is asking you to sign in or verify yourself before the form appears. "
    "Log in directly in the browser window - NyayaBot never sees or stores your password, "
    "OTP or CAPTCHA. When the complaint form is showing, click Continue."
)
_MAX_LOGIN_PAUSES = 3


async def _hold_for_login(session: "BrowserSession", get_page, max_rounds: int = _MAX_LOGIN_PAUSES) -> int:
    """Pause for the user to sign in whenever the current page is a login wall.

    Runs before the browser agent, so no model tokens are spent on a page that only
    the user can get past. Returns how many times it paused. Re-checks after each
    Continue because a portal can show several steps (login, then OTP). Gives up
    after `max_rounds` pauses: the user said they are done, so the agent proceeds
    rather than asking forever. If the page cannot be inspected it proceeds without
    pausing - not knowing is not a reason to block the run. Cancel propagates as
    asyncio.CancelledError, like the other pauses.
    """
    rounds = 0
    while True:
        page = await get_page()
        signals = await _page_login_signals(page) if page is not None else {"error": "NoPage"}
        if "error" in signals or not signals["likely_login_wall"]:
            break
        if rounds >= max_rounds:
            logger.info("session=%s event=login_pause_limit_reached rounds=%s", session.session_id, rounds)
            session.steps.append("Continuing after several sign-in pauses.")
            break
        rounds += 1
        try:
            session.screenshot_b64 = await _take_screenshot(page)
            session.review_fields = await _collect_review_fields(page)
        except Exception as exc:  # noqa: BLE001 - the pause matters more than the picture
            logger.warning("session=%s event=screenshot_failed type=%s", session.session_id, type(exc).__name__)
        session.status = SessionStatus.PAUSED
        session.pause_kind = "login"
        session.message = _LOGIN_PAUSE_MESSAGE
        session.steps.append("Waiting for you to sign in on the portal.")
        logger.info(
            "session=%s event=paused_for_login round=%s host=%s password=%s otp=%s captcha=%s",
            session.session_id, rounds, signals["host"], signals["password"], signals["otp"], signals["captcha"],
        )
        await _wait_for_continue_or_cancel(session)
        session.status = SessionStatus.RUNNING
        session.pause_kind = None
        session.message = "Continuing…"
        session.steps.append("Sign-in step completed by you.")
    return rounds


async def _click_toward_form(session: "BrowserSession", browser, visited: set[str], settle_seconds: float):
    """One rule-based click toward the form; follows a link that opened a new tab."""
    page = await browser.get_current_page()
    context = getattr(browser, "browser_context", None)
    tabs_before = len(context.pages) if context is not None else 0
    result = await navigate_once(
        page, hints=session.entry_hints, avoid=session.entry_avoid, visited=visited,
        session_id=session.session_id, settle_seconds=settle_seconds,
    )
    if result.clicked and context is not None and len(context.pages) > tabs_before:
        try:
            await browser.switch_tab(len(context.pages) - 1)
        except Exception as exc:  # noqa: BLE001 - staying on the old tab beats failing the run
            logger.warning("session=%s event=nav_tab_switch_failed type=%s", session.session_id, type(exc).__name__)
    return result


async def _drive_to_form(session: "BrowserSession", browser, *, settle_seconds: float = 1.0) -> tuple[int, int, int]:
    """Get from the landing page to a filled form using rules only. No model, no tokens.

    Each round: pause if the page needs the user to sign in, try to fill a form that
    is already showing, and if there is none click the one link that clearly leads
    toward it, then look again. Stops as soon as something was filled, when no clear
    link remains, or after MAX_NAV_STEPS attempts. Returns
    (fields_filled, sign_in_pauses, clicks). Fields filled == 0 means the caller may
    fall back to the browser agent. Cancel propagates; everything else is contained.
    """
    login_pauses = clicks = attempts = 0
    visited: set[str] = set()
    while True:
        # Client-rendered portals (Angular, React) draw after the load event; scanning
        # too early finds an empty page and gives up.
        try:
            await settle_page(await browser.get_current_page(), seconds=settle_seconds)
        except Exception:  # noqa: BLE001 - settling is best effort
            pass
        login_pauses += await _hold_for_login(session, browser.get_current_page)
        if not session.case_data:
            return 0, login_pauses, clicks
        session.message = f"Filling {session.portal_label} from your profile and this case…"
        try:
            page = await browser.get_current_page()
            report = await fill_page(page, session.case_data, session_id=session.session_id)
        except Exception as exc:  # noqa: BLE001 - fall back to the agent, never fail the session
            logger.warning("session=%s event=rule_fill_failed type=%s", session.session_id, type(exc).__name__)
            return 0, login_pauses, clicks
        if report.filled_count:
            shown = ", ".join(report.filled[:8]) + ("…" if report.filled_count > 8 else "")
            session.steps.append(f"Filled {report.filled_count} field(s) from your profile and this case: {shown}.")
            if report.left_for_user:
                session.steps.append(f"{len(report.left_for_user)} field(s) left for you to complete.")
            if report.sensitive:
                session.steps.append("Password, OTP, CAPTCHA and ID fields are left for you.")
            logger.info("session=%s event=agent_skipped reason=rule_fill_filled filled=%s clicks=%s",
                        session.session_id, report.filled_count, clicks)
            return report.filled_count, login_pauses, clicks
        if attempts >= MAX_NAV_STEPS:
            logger.info("session=%s event=nav_limit_reached attempts=%s", session.session_id, attempts)
            return 0, login_pauses, clicks
        session.message = f"Looking for the complaint form on {session.portal_label}…"
        try:
            result = await _click_toward_form(session, browser, visited, settle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("session=%s event=nav_failed type=%s", session.session_id, type(exc).__name__)
            return 0, login_pauses, clicks
        if result.reason in {"no_candidate", "scan_failed"}:
            return 0, login_pauses, clicks
        attempts += 1
        if result.clicked:
            clicks += 1
            session.steps.append(f"Clicked “{result.text}” to reach the complaint form.")


def _no_progress_message(rate_limited: bool) -> str:
    """Truthful replacement for the 'prepared form' message when nothing was done."""
    if rate_limited:
        return (
            "The automatic assistant could not get a response in time (the AI service is "
            "currently rate-limited) and did not prepare this form. Nothing has been filled "
            "or submitted — you can continue directly in the browser below."
        )
    return (
        "The automatic assistant couldn't complete the form preparation. Nothing has been "
        "filled or submitted — you can continue directly in the browser below."
    )


# ── Core agent runner ────────────────────────────────────────────────────────

async def run_browser_session(session: BrowserSession) -> None:
    """
    Main coroutine that drives the browser agent for a single session.
    Runs in the background; the FastAPI endpoints read/write session state.
    """
    browser = None
    profile_dir = None
    playwright_api = None
    try:
        # Lazy imports so the server still boots even if browser-use isn't installed yet
        from browser_use import Agent, Controller
        from browser_use.browser.browser import Browser, BrowserConfig
        from playwright.async_api import async_playwright

        session.status = SessionStatus.RUNNING
        session.message = f"Opening {session.portal_label}…"
        session.steps.append(f"Navigating to {session.portal_label} ({session.portal_url})")

        # ── LLM (Groq / Gemini) — not needed for the deterministic mock portal ──
        deterministic = session.fill_values is not None
        llm, is_groq = (None, False) if deterministic else _resolve_llm()
        logger.info(
            "session=%s event=browser_session_config category=%s portal_host=%s mode=%s agent_model=%s",
            session.session_id, session.category or "-", urlparse(session.portal_url).hostname,
            "deterministic" if deterministic else "agent",
            None if deterministic else getattr(llm, "model", None),
        )

        # ── Browser — visible + remote-debugging enabled for live CDP panel ──
        cdp_port = _find_free_port()
        session.cdp_port = cdp_port
        session.cdp_ready = False

        profile_dir = tempfile.TemporaryDirectory(prefix="nyayabot-browser-")
        playwright_api = await async_playwright().start()
        browser = Browser(
            playwright=playwright_api,
            browser_profile=BrowserConfig(
                headless=False,
                disable_security=False,
                args=[f"--remote-debugging-port={cdp_port}"],
                user_data_dir=profile_dir.name,
                # The agent must not close the browser when it finishes: the review step
                # still needs this same window. The finally block kills it explicitly.
                keep_alive=True,
                stealth=False,
            )
        )
        await browser.start()
        await browser.browser_context.add_init_script(_SUBMISSION_GUARD)
        for page in browser.browser_context.pages:
            await page.evaluate(_SUBMISSION_GUARD)

        # Poll until Chrome exposes its DevTools endpoint (up to 10 s)
        try:
            import httpx as _httpx
            for _attempt in range(20):
                try:
                    async with _httpx.AsyncClient() as _hc:
                        _r = await _hc.get(
                            f"http://127.0.0.1:{cdp_port}/json/list", timeout=1.0
                        )
                        if _r.status_code == 200 and _r.json():
                            session.cdp_ready = True
                            session.steps.append("Live browser view ready.")
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        except ImportError:
            logger.warning("httpx not installed — CDP readiness check skipped")

        # Open the portal on this session's own page so the live view shows it
        # immediately, independent of the agent's first step.
        page = await browser.get_current_page()
        try:
            await page.goto(session.portal_url, wait_until="domcontentloaded", timeout=30000)
            session.current_url = page.url
            session.steps.append(f"Opened {session.portal_label}.")
            await _log_page_signals(session, page, "portal_opened")
        except Exception as exc:
            logger.warning("session=%s event=portal_open_failed type=%s", session.session_id, type(exc).__name__)
            session.steps.append(f"{session.portal_label} did not finish loading in the embedded browser.")

        controller = Controller()

        # ── Custom action: pause and wait for human approval ─────────────────
        @controller.action(
            "Pause and wait for human to review the filled form before submitting"
        )
        async def pause_for_review(reason: str = ""):
            """Called by the agent instead of clicking Submit."""
            try:
                page = None
                if hasattr(browser, "get_current_page"):
                    page = await browser.get_current_page()
                elif hasattr(browser, "playwright_browser") and browser.playwright_browser:
                    contexts = browser.playwright_browser.contexts
                    if contexts and contexts[0].pages:
                        page = contexts[0].pages[-1]
                if page:
                    session.screenshot_b64 = await _take_screenshot(page)
                    session.review_fields = await _collect_review_fields(page)
            except Exception as exc:
                logger.warning("session=%s event=screenshot_failed type=%s", session.session_id, type(exc).__name__)

            session.status = SessionStatus.PAUSED
            session.pause_kind = "review"
            session.message = (
                "Review the prepared fields and complete anything missing in the live browser. "
                "Nothing has been submitted. Approve review before manually submitting."
            )
            session.steps.append("Prepared fields are waiting for human review.")
            logger.info("session=%s event=paused_for_review", session.session_id)

            # Do not resume an LLM agent into any possible final submission.
            await _hold_for_manual_submission(session, browser)
            raise asyncio.CancelledError()

        # ── Custom action: pause specifically for sensitive input ─────────────
        @controller.action(
            "Pause so the user can enter a sensitive value (Aadhaar, OTP, password) "
            "directly into the browser"
        )
        async def pause_for_sensitive_input(field_name: str = "sensitive field"):
            """Agent calls this when it encounters a sensitive field it should skip."""
            try:
                page = None
                if hasattr(browser, "get_current_page"):
                    page = await browser.get_current_page()
                elif hasattr(browser, "playwright_browser") and browser.playwright_browser:
                    contexts = browser.playwright_browser.contexts
                    if contexts and contexts[0].pages:
                        page = contexts[0].pages[-1]
                if page:
                    session.screenshot_b64 = await _take_screenshot(page)
                    session.review_fields = await _collect_review_fields(page)
            except Exception as exc:
                logger.warning("session=%s event=screenshot_failed type=%s", session.session_id, type(exc).__name__)

            session.status = SessionStatus.PAUSED
            session.pause_kind = "sensitive"
            session.message = (
                f"Please enter your {field_name} directly in the browser window. "
                "Click Continue in NyayaBot when you're done."
            )
            session.steps.append("Paused for user-only input.")
            logger.info(
                "session=%s event=paused_for_sensitive",
                session.session_id,
            )

            await _wait_for_continue_or_cancel(session)

            session.status = SessionStatus.RUNNING
            session.message = "Continuing to fill the form…"
            session.pause_kind = None
            session.steps.append("User completed the manual step; agent resumed.")

        # ── Python first: sign-in pauses, button clicks to reach the form, then filling ──
        # No model, no tokens. The agent below only runs if this could not fill anything.
        rule_filled = login_pauses = nav_clicks = 0
        if not deterministic:
            rule_filled, login_pauses, nav_clicks = await _drive_to_form(session, browser)

        # ── Build the full task prompt with explicit URL ──────────────────────
        if login_pauses or nav_clicks:
            # The user has signed in and/or NyayaBot has clicked past the landing page.
            # Sending the agent back to the home page would waste a step and drop that state.
            opening = (
                f"The browser is already open on {session.portal_label}, past its landing page or "
                "sign-in. Continue from the current page; do not go back to the home page unless you must.\n"
            )
        else:
            opening = f"Go to {session.portal_url}\n"
        full_task = (
            f"{opening}"
            f"Once the page loads, find the complaint or grievance registration section.\n"
            f"Fill in the complaint form fields with the following details:\n"
            f"{session.task}\n\n"
            "STRICT MANDATORY RULES:\n"
            "1. NEVER click any final 'Submit', 'Register', 'Pay', or 'File' button.\n"
            "2. When the fields are filled, call 'pause_for_review' or stop.\n"
            "3. If an OTP, Aadhaar, password or CAPTCHA appears, call 'pause_for_sensitive_input'.\n"
            "4. Never submit sensitive user credentials without human review."
        )

        # Whether the run earned a "form is ready for review" message. The
        # deterministic mock-portal path is untouched by this task and keeps its
        # existing, already-honest behaviour (it reports exactly how many fields
        # `_fill_known_fields` filled); only the LLM-agent branch below can flip
        # this to False, on positive evidence that nothing meaningful happened.
        agent_made_progress = True
        agent_rate_limited = False

        if deterministic:
            # ── Local mock portal: real Playwright fills on this same page ────
            session.message = f"Filling {session.portal_label} from your profile and this case…"
            await _fill_known_fields(page, session)
        elif rule_filled:
            logger.info("session=%s event=agent_skipped reason=rule_fill_filled filled=%s", session.session_id, rule_filled)
        else:
            # ── Run the agent ────────────────────────────────────────────────
            agent_kwargs = {
                "task": full_task,
                "llm": llm,
                "browser": browser,
                "controller": controller,
            }
            if is_groq:
                agent_kwargs["use_vision"] = False

            agent = Agent(**agent_kwargs)
            session.steps.append(f"Opening {session.portal_url} via browser agent...")

            history = await agent.run(max_steps=25)

            agent_made_progress, agent_rate_limited, action_names = _classify_agent_progress(history)
            logger.info(
                "session=%s event=agent_run_finished made_progress=%s rate_limited=%s actions=%s",
                session.session_id, agent_made_progress, agent_rate_limited, action_names,
            )
            # The agent swallows its own errors and returns normally, so this is the
            # only place the real cause (model rejected the request, rate limit, ...)
            # becomes visible. Sanitised and capped; see summarise_agent_errors.
            for sample in summarise_agent_errors(history):
                logger.warning("session=%s event=agent_error_sample error=%s", session.session_id, sample)

            # ── Capture screenshots and action trace ──────────────────────────
            try:
                screenshots = history.screenshots()
                if screenshots:
                    session.screenshot_b64 = screenshots[-1]
            except Exception as exc:
                logger.warning("session=%s event=history_capture_failed type=%s", session.session_id, type(exc).__name__)

        try:
            page = await browser.get_current_page()
            if page:
                await _log_page_signals(session, page, "after_run")
                if not session.screenshot_b64:
                    session.screenshot_b64 = await _take_screenshot(page)
                session.review_fields = await _collect_review_fields(page)
        except Exception as exc:
            logger.warning("session=%s event=review_capture_failed type=%s", session.session_id, type(exc).__name__)

        # ── Enforce HITL review: Always pause for user approval before done ───
        #
        # SUCCESS MUST BE EARNED: a normal return from `agent.run()` is not, on its
        # own, evidence the form was prepared - see `_classify_agent_progress`. This
        # branch only ever reaches the "prepared" wording when `agent_made_progress`
        # is True, which for the LLM-agent path requires at least one real
        # page-touching action (or our own pause action firing). The deterministic
        # mock-portal path is unaffected (`agent_made_progress` stays True there).
        if session.status not in (SessionStatus.CANCELLED, SessionStatus.ERROR):
            session.status = SessionStatus.PAUSED
            if agent_made_progress:
                session.pause_kind = "review"
                session.message = (
                    "Review the prepared form and complete any missing fields. Nothing has been submitted. "
                    "Approve review before manually submitting in the browser."
                )
                session.steps.append("Prepared form is waiting for human review.")
            else:
                # Truthful failure state: the browser is still open and usable for
                # manual filling, but nothing was prepared and we do not claim it was.
                session.pause_kind = "no_progress"
                session.message = _no_progress_message(agent_rate_limited)
                session.steps.append(
                    "Automatic form-filling made no confirmed progress on this portal."
                )
            await _hold_for_manual_submission(session, browser)
            session.status = SessionStatus.CANCELLED
            session.message = "Browser session closed. Portal submission was not confirmed."

    except asyncio.CancelledError:
        session.status = SessionStatus.CANCELLED
        session.message = "Browser session closed. Submission was not confirmed."
        logger.info("session=%s event=cancelled", session.session_id)

    except ImportError as exc:
        session.status = SessionStatus.ERROR
        session.error_code = "BROWSER_RUNTIME_UNAVAILABLE"
        session.error = "Auto-Fill browser runtime is unavailable on this server."
        logger.error("session=%s event=import_error type=%s", session.session_id, type(exc).__name__)

    except Exception as exc:  # noqa: BLE001
        session.status = SessionStatus.ERROR
        session.error_code = "BROWSER_START_FAILED"
        session.error = "Auto-Fill browser could not start or complete. Please try again."
        session.message = "Browser automation stopped. No submission was confirmed."
        logger.error("session=%s event=error type=%s", session.session_id, type(exc).__name__)
    finally:
        session.cdp_ready = False
        session.cdp_port = None
        if browser is not None:
            try:
                await browser.kill()  # stop() is a no-op with keep_alive=True
            except Exception:
                logger.warning("session=%s event=browser_cleanup_failed", session.session_id)
        if playwright_api is not None:
            try:
                await playwright_api.stop()
            except Exception:
                logger.warning("session=%s event=playwright_cleanup_failed", session.session_id)
        if profile_dir is not None:
            profile_dir.cleanup()


# ── Public helpers called by the FastAPI routes ──────────────────────────────

def start_browser_session_task(session: BrowserSession) -> None:
    """Schedule run_browser_session on a loop that can spawn Chromium.

    On Windows, `uvicorn --reload` serves requests on a SelectorEventLoop, which
    cannot create subprocesses, so Playwright fails to launch the browser. In that
    case the session runs on its own ProactorEventLoop in a daemon thread.
    """
    current = asyncio.get_running_loop()
    if sys.platform != "win32" or isinstance(current, asyncio.ProactorEventLoop):
        session._loop = current
        session._task = current.create_task(run_browser_session(session))
        return

    loop = asyncio.ProactorEventLoop()
    session._loop = loop
    session._task = loop.create_task(run_browser_session(session))

    def _run() -> None:
        try:
            loop.run_until_complete(session._task)
        except BaseException:  # run_browser_session records its own errors
            pass
        finally:
            loop.close()

    threading.Thread(target=_run, name=f"autofill-{session.session_id[:8]}", daemon=True).start()


def _call_on_session_loop(session: BrowserSession, callback) -> None:
    """asyncio primitives are not thread-safe; signal them on the session's loop."""
    loop = session._loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if loop is None or loop is running or loop.is_closed():
        callback()
    else:
        loop.call_soon_threadsafe(callback)


def approve_session(session_id: str) -> bool:
    """Continue a manual step or approve review for manual submission."""
    session = get_session(session_id)
    if session and session.status == SessionStatus.PAUSED:
        _call_on_session_loop(session, session._approve_event.set)
        return True
    return False


def cancel_session(session_id: str) -> bool:
    """Signal the paused agent to stop."""
    session = get_session(session_id)
    if session and session.status in (
        SessionStatus.PENDING, SessionStatus.PAUSED, SessionStatus.RUNNING, SessionStatus.APPROVED
    ):
        def _cancel() -> None:
            session._cancel_event.set()
            if session._task and not session._task.done():
                session._task.cancel()

        _call_on_session_loop(session, _cancel)
        return True
    return False


def session_to_dict(session: BrowserSession) -> dict:
    """Serialise a session for the API response (excludes asyncio internals)."""
    return {
        "session_id":    session.session_id,
        "case_id":       session.case_id,
        "category":      session.category,
        "portal_label":  session.portal_label,
        "portal_url":    session.portal_url,
        "status":        session.status.value,
        "message":       session.message,
        "screenshot_b64": session.screenshot_b64,
        "error":         session.error,
        "pause_kind":    session.pause_kind,
        "error_code":   session.error_code,
        "steps":         session.steps,
        "review_fields": session.review_fields,
        # CDP live-control
        "cdp_ready":     session.cdp_ready,
    }
