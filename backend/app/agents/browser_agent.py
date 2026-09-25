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
    category: str
    portal_label: str
    portal_url: str
    task: str
    status: SessionStatus = SessionStatus.PENDING
    screenshot_b64: Optional[str] = None   # latest screenshot (base64 PNG)
    message: str = ""                       # status message shown to user
    error: Optional[str] = None
    steps: list[str] = field(default_factory=list)
    current_url: str = ""
    # CDP live-control fields
    cdp_port: Optional[int] = None          # Chrome remote-debugging port for this session
    cdp_ready: bool = False                 # True once Chrome is accepting CDP connections
    # Internal asyncio primitives (not serialised to the client)
    _approve_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


# In-memory registry of active sessions
_sessions: dict[str, BrowserSession] = {}


def get_session(session_id: str) -> Optional[BrowserSession]:
    return _sessions.get(session_id)


def create_session(
    case_id: str,
    category: str,
    portal_label: str,
    portal_url: str,
    task: str,
) -> BrowserSession:
    session = BrowserSession(
        session_id=str(uuid.uuid4()),
        case_id=case_id,
        category=category,
        portal_label=portal_label,
        portal_url=portal_url,
        current_url=portal_url,
        task=task,
    )
    _sessions[session.session_id] = session
    return session


# ── Screenshot helper ────────────────────────────────────────────────────────

async def _take_screenshot(page) -> str:
    """Capture a screenshot and return it as a base64-encoded PNG string."""
    png_bytes = await page.screenshot(full_page=False)
    return base64.b64encode(png_bytes).decode()


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
            from browser_use.llm import ChatGoogleGenerativeAI
            gemini_model = settings.LLM_MODEL if "gemini" in (settings.LLM_MODEL or "") else "gemini-2.0-flash"
            try:
                return ChatGoogleGenerativeAI(model=gemini_model, api_key=gemini_key), False
            except TypeError:
                return ChatGoogleGenerativeAI(model_name=gemini_model, google_api_key=gemini_key), False
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
    try:
        # Lazy imports so the server still boots even if browser-use isn't installed yet
        from browser_use import Agent, Controller
        from browser_use.browser.browser import Browser, BrowserConfig

        session.status = SessionStatus.RUNNING
        session.message = f"Opening {session.portal_label}…"
        session.steps.append(f"Navigating to {session.portal_label} ({session.portal_url})")

        # ── LLM (Groq / Gemini) ──────────────────────────────────────────────
        llm, is_groq = _resolve_llm()

        # ── Browser — visible + remote-debugging enabled for live CDP panel ──
        cdp_port = _find_free_port()
        session.cdp_port = cdp_port
        session.cdp_ready = False

        browser = Browser(
            config=BrowserConfig(
                headless=False,
                disable_security=False,
                extra_chromium_args=[f"--remote-debugging-port={cdp_port}"],
            )
        )

        # Poll until Chrome exposes its DevTools endpoint (up to 10 s)
        try:
            import httpx as _httpx
            for _attempt in range(20):
                try:
                    async with _httpx.AsyncClient() as _hc:
                        _r = await _hc.get(
                            f"http://localhost:{cdp_port}/json/list", timeout=1.0
                        )
                        if _r.status_code == 200 and _r.json():
                            session.cdp_ready = True
                            session.steps.append(
                                f"CDP live-control ready on port {cdp_port}."
                            )
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        except ImportError:
            logger.warning("httpx not installed — CDP readiness check skipped")

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
            except Exception as e:
                logger.warning("Could not capture screenshot: %s", e)

            session.status = SessionStatus.PAUSED
            session.message = (
                reason or
                "Form details filled. Review the screenshot, edit anything in the "
                "browser window if needed, then click Approve or Cancel."
            )
            session.steps.append("Agent called pause_for_review. Waiting for human approval.")
            logger.info("session=%s event=paused_for_review", session.session_id)

            # Block the agent coroutine until the user approves or cancels
            done, _ = await asyncio.wait(
                [
                    asyncio.ensure_future(session._approve_event.wait()),
                    asyncio.ensure_future(session._cancel_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if session._cancel_event.is_set():
                raise RuntimeError("User cancelled the session.")

            session.status = SessionStatus.RUNNING
            session.message = "Submitting the form with your approval…"
            session.steps.append("Human approved. Proceeding with submission.")

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
            except Exception as e:
                logger.warning("Could not capture screenshot: %s", e)

            session.status = SessionStatus.PAUSED
            session.message = (
                f"Please enter your {field_name} directly in the browser window. "
                "Click Continue in NyayaBot when you're done."
            )
            session.steps.append(f"Paused for sensitive input: {field_name}")
            logger.info(
                "session=%s event=paused_for_sensitive field=%s",
                session.session_id,
                field_name,
            )

            done, _ = await asyncio.wait(
                [
                    asyncio.ensure_future(session._approve_event.wait()),
                    asyncio.ensure_future(session._cancel_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            session._approve_event.clear()

            if session._cancel_event.is_set():
                raise RuntimeError("User cancelled the session.")

            session.status = SessionStatus.RUNNING
            session.message = "Continuing to fill the form…"
            session.steps.append(f"Sensitive input {field_name} provided. Continuing form filling.")

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

        # ── Run the agent ────────────────────────────────────────────────────
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

        # ── Capture screenshots and action trace ──────────────────────────────
        try:
            screenshots = history.screenshots()
            if screenshots:
                session.screenshot_b64 = screenshots[-1]
        except Exception as e:
            logger.warning("Could not extract screenshot from history: %s", e)

        if hasattr(agent, "browser_context") and agent.browser_context:
            try:
                page = await agent.browser_context.get_current_page()
                if page:
                    if not session.screenshot_b64:
                        session.screenshot_b64 = await _take_screenshot(page)
                    if hasattr(page, "url") and page.url:
                        session.current_url = page.url
            except Exception as e:
                logger.warning("Could not capture page screenshot from browser_context: %s", e)

        try:
            valid_urls = [u for u in history.urls() if u and "about:blank" not in u]
            if valid_urls:
                session.current_url = valid_urls[-1]
            for url in valid_urls:
                session.steps.append(f"Page: {url}")
            for action in history.model_actions():
                action_str = str(action)
                if len(action_str) > 80:
                    action_str = action_str[:77] + "..."
                session.steps.append(f"Agent step: {action_str}")
        except Exception as e:
            logger.warning("Could not parse history steps: %s", e)

        # ── Enforce HITL review: Always pause for user approval before done ───
        if session.status not in (SessionStatus.CANCELLED, SessionStatus.ERROR):
            session.status = SessionStatus.PAUSED
            session.message = (
                "Form details filled. Use the live browser panel to review or edit any field. "
                "Nothing has been submitted. Click 'Approve / Submit' to file, or 'Cancel' to abort."
            )
            session.steps.append("Form fields populated. Paused for human review via live browser panel.")

            # FIX: clear approve_event so a previous pause cycle can't skip this gate
            session._approve_event.clear()

            # Wait for human approval
            done, _ = await asyncio.wait(
                [
                    asyncio.ensure_future(session._approve_event.wait()),
                    asyncio.ensure_future(session._cancel_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if session._cancel_event.is_set():
                session.status = SessionStatus.CANCELLED
                session.message = "Session cancelled by user. No data was submitted."
                session.steps.append("Cancelled by user. No data submitted.")
            elif session._approve_event.is_set():
                session.status = SessionStatus.DONE
                session.message = "✅ Submission approved by you. Completed."
                session.steps.append("Approved by user. Final submission complete.")

        await browser.close()

    except RuntimeError as exc:
        # User-triggered cancellation
        session.status = SessionStatus.CANCELLED
        session.message = str(exc)
        logger.info("session=%s event=cancelled reason=%s", session.session_id, exc)

    except ImportError as exc:
        session.status = SessionStatus.ERROR
        session.error = (
            "browser-use is not installed. Run: "
            "pip install browser-use playwright && playwright install chromium"
        )
        logger.error("session=%s event=import_error err=%s", session.session_id, exc)

    except Exception as exc:  # noqa: BLE001
        session.status = SessionStatus.ERROR
        session.error = str(exc)
        session.message = "An unexpected error occurred. See server logs."
        logger.exception("session=%s event=error", session.session_id)


# ── Public helpers called by the FastAPI routes ──────────────────────────────

def approve_session(session_id: str) -> bool:
    """Signal the paused agent to proceed (submit)."""
    session = get_session(session_id)
    if session and session.status == SessionStatus.PAUSED:
        session._approve_event.set()
        return True
    return False


def cancel_session(session_id: str) -> bool:
    """Signal the paused agent to stop."""
    session = get_session(session_id)
    if session and session.status in (SessionStatus.PAUSED, SessionStatus.RUNNING):
        session._cancel_event.set()
        return True
    return False


def session_to_dict(session: BrowserSession) -> dict:
    """Serialise a session for the API response (excludes asyncio internals)."""
    return {
        "session_id":    session.session_id,
        "case_id":       session.case_id,
        "category":      session.category,
        "portal_label":  session.portal_label,
        "portal_url":    session.current_url or session.portal_url,
        "status":        session.status.value,
        "message":       session.message,
        "screenshot_b64": session.screenshot_b64,
        "error":         session.error,
        "steps":         session.steps,
        # CDP live-control
        "cdp_port":      session.cdp_port,
        "cdp_ready":     session.cdp_ready,
    }
