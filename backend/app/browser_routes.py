"""
browser_routes.py
-----------------
FastAPI router that exposes the browser agent endpoints.

Endpoints:
  POST /api/v1/browser/start/{case_id}      → launch browser session
  GET  /api/v1/browser/status/{session_id}  → poll status + screenshot
  POST /api/v1/browser/approve/{session_id} → user approves / continues
  POST /api/v1/browser/cancel/{session_id}  → user cancels
  GET  /api/v1/browser/cdp-info/{session_id}→ returns cdp_port + cdp_ready
  WS   /api/v1/browser/cdp/{session_id}    → live CDP WebSocket proxy
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.db.session import get_db
from app.db.models import AuthSessionModel, ChatCaseSessionModel, UserModel
from app.services.browser_runtime import browser_runtime_status
from app.agents.browser_agent import (
    approve_session,
    cancel_session,
    create_session,
    get_session,
    session_to_dict,
    SessionStatus,
    start_browser_session_task,
)
from app.services.portal_selector import build_task, mock_portal, mock_portal_enabled, select_portal

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/v1/browser", tags=["browser-agent"])


def _owned_session(session_id: str, user_id: str):
    session = get_session(session_id)
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


_MOCK_PORTAL_FILE = Path(__file__).parent / "static" / "autofill_mock_portal.html"


@router.get("/mock-portal", include_in_schema=False)
def autofill_mock_portal():
    """Test-only form for local Auto-Fill development; holds no data and posts nowhere."""
    if not mock_portal_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_MOCK_PORTAL_FILE, media_type="text/html")


def _mock_portal_values(record: ChatCaseSessionModel, user: UserModel) -> dict[str, str]:
    """Identity comes from the signed-in profile, grievance details from this case only."""
    profile: dict = record.profile_data or {}
    key_facts: dict = profile.get("key_facts") or {}

    def _case(*keys: str) -> str:
        for k in keys:
            v = profile.get(k) or key_facts.get(k)
            if v:
                return str(v).strip()
        return ""

    amount = profile.get("disputed_amount") or key_facts.get("disputed_amount") or 0
    try:
        amount_text = f"{float(amount):g}" if float(amount) > 0 else ""
    except (TypeError, ValueError):
        amount_text = ""
    return {
        "full_name": user.full_name or "",
        "email": user.email or "",
        "phone": user.phone or "",
        "state": user.state or "",
        "city": user.city or "",
        "pin_code": user.pin_code or "",
        "address": user.full_address or "",
        "complaint_category": str(profile.get("category") or "").upper(),
        "amount": amount_text,
        "transaction_ref": _case("transaction_id", "reference_number", "cnr_number"),
        "description": _case("incident_narrative", "incident_summary", "description", "title"),
    }


@router.get("/runtime")
async def browser_runtime(current_user: UserModel = Depends(get_current_user)):
    return await asyncio.to_thread(browser_runtime_status)


def _extract_case_data(record: ChatCaseSessionModel, user: UserModel) -> dict:
    """Pull the fields the portal task templates need from the stored profile."""
    profile: dict = record.profile_data or {}
    key_facts: dict = profile.get("key_facts", {})

    def _get(*keys: str, default: str = "") -> str:
        for k in keys:
            v = profile.get(k) or key_facts.get(k) or ""
            if v:
                return str(v)
        return default

    return {
        "complainant_name":   _get("user_name") or user.full_name or "",
        "complainant_address": _get("user_address") or user.full_address or "",
        "complainant_city":   _get("user_city") or user.city or "",
        "complainant_state":  _get("user_state") or user.state or "",
        "complainant_phone":  _get("user_phone") or user.phone or "",
        "opposite_party_name": _get("opposite_party_name", "bank_name", "police_station_name"),
        "opposite_party_address": _get("opposite_party_address"),
        "property_address":   _get("property_address"),
        "incident_narrative": _get("incident_narrative", "title"),
        "disputed_amount":    _get("disputed_amount"),
        "bank_name":          _get("bank_name"),
        "transaction_id":     _get("transaction_id"),
        "reference_number":   _get("cnr_number"),
    }


@router.post("/start/{case_id}")
async def start_browser_session(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Launch a browser agent session for the given case.
    The agent opens the appropriate govt portal and starts filling the form.
    Returns a session_id to poll for status.
    """
    runtime = await asyncio.to_thread(browser_runtime_status)
    if not runtime["available"]:
        logger.warning("event=browser_runtime_unavailable components=%s", runtime)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BROWSER_RUNTIME_UNAVAILABLE",
                "message": "Auto-Fill browser could not start. The browser runtime is not available.",
            },
        )

    # Only the case owner may launch a browser with its data.
    record = (
        db.query(ChatCaseSessionModel)
        .filter(
            ChatCaseSessionModel.case_id == case_id,
            ChatCaseSessionModel.user_id == current_user.id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Case not found")

    category: str = (record.profile_data or {}).get("category", "")
    use_mock = mock_portal_enabled()
    portal = mock_portal(str(request.base_url)) if use_mock else select_portal(category)
    case_data = _extract_case_data(record, current_user)
    task = build_task(portal, case_data)

    session = create_session(
        case_id=case_id,
        user_id=current_user.id,
        category=category,
        portal_label=portal.label,
        portal_url=portal.url,
        task=task,
        fill_values=_mock_portal_values(record, current_user) if use_mock else None,
    )

    # Fire-and-forget: run the agent in the background
    start_browser_session_task(session)

    logger.info(
        "case_id=%s session_id=%s portal=%s event=browser_session_started",
        case_id,
        session.session_id,
        portal.label,
    )

    return session_to_dict(session)


@router.get("/status/{session_id}")
def get_browser_session_status(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Poll the current state of a browser session.
    Returns status, message, and the latest screenshot (base64 PNG) if available.
    """
    session = _owned_session(session_id, current_user.id)
    return session_to_dict(session)


@router.post("/approve/{session_id}")
def approve_browser_session(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Continue a user-only step, or approve review for manual submission.
    """
    session = _owned_session(session_id, current_user.id)
    ok = approve_session(session_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"Session is not in a paused state (current: {session.status})",
        )
    return {"ok": True, "message": "Review recorded or manual step continued."}


@router.post("/cancel/{session_id}")
def cancel_browser_session(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Cancel a running or paused browser session.
    Close the browser window. Any manual portal submission is not inferred.
    """
    session = _owned_session(session_id, current_user.id)
    ok = cancel_session(session_id)
    if not ok:
        # Already done/cancelled — not an error, just report current state
        return {"ok": False, "message": f"Session already in state: {session.status}"}
    return {"ok": True, "message": "Session cancelled."}


# ── CDP live-control ───────────────────────────────────────────────────────────

@router.get("/cdp-info/{session_id}")
def cdp_info(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Returns CDP readiness info so the frontend knows when to connect the live panel.
    """
    session = _owned_session(session_id, current_user.id)
    return {
        "session_id": session_id,
        "cdp_ready":  session.cdp_ready,
    }


@router.websocket("/cdp/{session_id}")
async def cdp_proxy(
    websocket: WebSocket,
    session_id: str,
    db: Session = Depends(get_db),
):
    """
    WebSocket proxy: bridges the NyayaBot frontend canvas to Chrome’s live
    Chrome DevTools Protocol (CDP) endpoint for this session.

    The frontend sends CDP commands (JSON) and receives CDP events including
    Page.screencastFrame for the live video feed and can send
    Input.dispatchMouseEvent / Input.dispatchKeyEvent to interact with the form.
    """
    origin = websocket.headers.get("origin", "")
    api_origin = f"{'https' if websocket.url.scheme == 'wss' else 'http'}://{websocket.url.netloc}"
    lan_origin = re.fullmatch(
        r"http://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):3000",
        origin,
    )
    if not origin or (origin != api_origin and origin not in settings.CORS_ORIGINS and not lan_origin):
        await websocket.close(code=1008, reason="Untrusted origin")
        return
    token = websocket.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
    auth = (
        db.query(AuthSessionModel)
        .filter(AuthSessionModel.token_hash == hashlib.sha256(token.encode()).hexdigest())
        .first()
        if token else None
    )
    session = get_session(session_id)
    if (
        not auth or auth.expires_at <= datetime.utcnow() or not auth.user
        or not session or session.user_id != auth.user_id or not session.cdp_port
    ):
        await websocket.close(code=1008, reason="Session unavailable")
        return

    # Wait up to 8 s for Chrome to be ready (in case frontend connects very early)
    if not session.cdp_ready:
        for _ in range(16):
            await asyncio.sleep(0.5)
            if session.cdp_ready:
                break
        else:
            await websocket.close(code=1011, reason="Chrome DevTools not ready")
            return

    # Fetch the page WebSocket debugger URL from Chrome
    cdp_ws_url: str | None = None
    try:
        async with httpx.AsyncClient() as hc:
            resp = await hc.get(
                f"http://127.0.0.1:{session.cdp_port}/json/list", timeout=5.0
            )
            pages = resp.json()
            if pages:
                cdp_ws_url = pages[0]["webSocketDebuggerUrl"]
    except Exception as exc:
        logger.error("session=%s event=cdp_page_list_error type=%s", session_id, type(exc).__name__)
        await websocket.close(code=1011, reason="Could not reach Chrome DevTools")
        return

    if not cdp_ws_url:
        await websocket.close(code=1011, reason="No open page found in Chrome")
        return

    await websocket.accept()
    logger.info("session=%s event=cdp_proxy_connected", session_id)

    # Bidirectional proxy between the frontend WS and Chrome’s CDP WS
    try:
        import websockets as ws_lib  # transitive dep via playwright

        async with ws_lib.connect(cdp_ws_url, max_size=16 * 1024 * 1024) as chrome_ws:

            async def frontend_to_chrome():
                try:
                    async for msg in websocket.iter_text():
                        try:
                            method = json.loads(msg).get("method")
                        except (ValueError, AttributeError):
                            continue
                        view_methods = {"Page.startScreencast", "Page.screencastFrameAck"}
                        input_methods = {"Input.dispatchMouseEvent", "Input.dispatchKeyEvent"}
                        if method not in view_methods and not (
                            method in input_methods
                            and session.status in {SessionStatus.PAUSED, SessionStatus.APPROVED}
                        ):
                            continue
                        await chrome_ws.send(msg)
                except (WebSocketDisconnect, Exception):
                    pass

            async def chrome_to_frontend():
                try:
                    async for msg in chrome_ws:
                        text = msg if isinstance(msg, str) else msg.decode()
                        await websocket.send_text(text)
                except Exception:
                    pass

            tasks = [
                asyncio.create_task(frontend_to_chrome()),
                asyncio.create_task(chrome_to_frontend()),
            ]
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

    except ImportError:
        logger.error("websockets library not installed; cannot proxy CDP.")
        await websocket.close(
            code=1011,
            reason="websockets package missing on server. Run: pip install websockets",
        )
    except Exception as exc:
        logger.error("session=%s event=cdp_proxy_error type=%s", session_id, type(exc).__name__)
