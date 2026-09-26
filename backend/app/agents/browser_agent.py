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
import socket
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

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
    return /\\b(submit|register complaint|file complaint|lodge complaint|pay now)\\b/.test(label);
  };
  document.addEventListener('submit', (event) => {
    if (!window.__NYAYA_ALLOW_FINAL_SUBMIT__) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('button, input[type=submit], [role=button]');
    if (button && !window.__NYAYA_ALLOW_FINAL_SUBMIT__ &&
        (button.type === 'submit' || finalAction(button))) {
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

        # ── Build the full task prompt with explicit URL ──────────────────────
        full_task = (
            f"Go to {session.portal_url}\n"
            f"Once the page loads, find the complaint or grievance registration section.\n"
            f"Fill in the complaint form fields with the following details:\n"
            f"{session.task}\n\n"
            "STRICT MANDATORY RULES:\n"
            "1. NEVER click any final 'Submit', 'Register', 'Pay', or 'File' button.\n"
            "2. When the fields are filled, call 'pause_for_review' or stop.\n"
            "3. If an OTP, Aadhaar, password or CAPTCHA appears, call 'pause_for_sensitive_input'.\n"
            "4. Never submit sensitive user credentials without human review."
        )

        if deterministic:
            # ── Local mock portal: real Playwright fills on this same page ────
            session.message = f"Filling {session.portal_label} from your profile and this case…"
            await _fill_known_fields(page, session)
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
                if not session.screenshot_b64:
                    session.screenshot_b64 = await _take_screenshot(page)
                session.review_fields = await _collect_review_fields(page)
        except Exception as exc:
            logger.warning("session=%s event=review_capture_failed type=%s", session.session_id, type(exc).__name__)

        # ── Enforce HITL review: Always pause for user approval before done ───
        if session.status not in (SessionStatus.CANCELLED, SessionStatus.ERROR):
            session.status = SessionStatus.PAUSED
            session.pause_kind = "review"
            session.message = (
                "Review the prepared form and complete any missing fields. Nothing has been submitted. "
                "Approve review before manually submitting in the browser."
            )
            session.steps.append("Prepared form is waiting for human review.")
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
