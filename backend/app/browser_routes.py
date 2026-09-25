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
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.session import get_db
from app.db.models import ChatCaseSessionModel, UserModel
from app.agents.browser_agent import (
    approve_session,
    cancel_session,
    create_session,
    get_session,
    run_browser_session,
    session_to_dict,
    SessionStatus,
)
from app.services.portal_selector import build_task, select_portal

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/v1/browser", tags=["browser-agent"])


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
        "reference_number":   _get("case_number"),
    }


@router.post("/start/{case_id}")
async def start_browser_session(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Launch a browser agent session for the given case.
    The agent opens the appropriate govt portal and starts filling the form.
    Returns a session_id to poll for status.
    """
    # Load the case chat record (owned case or demo session)
    record = (
        db.query(ChatCaseSessionModel)
        .filter(
            ChatCaseSessionModel.case_id == case_id,
            (ChatCaseSessionModel.user_id == current_user.id) | (ChatCaseSessionModel.is_demo.is_(True)),
        )
        .first()
    )
    if not record:
        record = (
            db.query(ChatCaseSessionModel)
            .filter(ChatCaseSessionModel.case_id == case_id)
            .first()
        )
    if not record:
        raise HTTPException(status_code=404, detail="Case not found")

    category: str = (record.profile_data or {}).get("category", "")
    portal = select_portal(category)
    case_data = _extract_case_data(record, current_user)
    task = build_task(portal, case_data)

    session = create_session(
        case_id=case_id,
        category=category,
        portal_label=portal.label,
        portal_url=portal.url,
        task=task,
    )

    # Fire-and-forget: run the agent in the background
    asyncio.create_task(run_browser_session(session))

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
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session_to_dict(session)


@router.post("/approve/{session_id}")
def approve_browser_session(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Signal the paused agent to proceed (submit the form, or continue past a
    sensitive field / CAPTCHA the user just handled manually).
    """
    ok = approve_session(session_id)
    if not ok:
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        raise HTTPException(
            status_code=400,
            detail=f"Session is not in a paused state (current: {session.status})",
        )
    return {"ok": True, "message": "Agent will continue."}


@router.post("/cancel/{session_id}")
def cancel_browser_session(
    session_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    """
    Cancel a running or paused browser session.
    The browser window will close and no data will be submitted.
    """
    ok = cancel_session(session_id)
    if not ok:
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
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
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "cdp_port":   session.cdp_port,
        "cdp_ready":  session.cdp_ready,
    }


@router.websocket("/cdp/{session_id}")
async def cdp_proxy(
    websocket: WebSocket,
    session_id: str,
):
    """
    WebSocket proxy: bridges the NyayaBot frontend canvas to Chrome’s live
    Chrome DevTools Protocol (CDP) endpoint for this session.

    The frontend sends CDP commands (JSON) and receives CDP events including
    Page.screencastFrame for the live video feed and can send
    Input.dispatchMouseEvent / Input.dispatchKeyEvent to interact with the form.
    """
    session = get_session(session_id)
    if not session or not session.cdp_port:
        await websocket.close(code=1008, reason="No CDP port for this session")
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
                f"http://localhost:{session.cdp_port}/json/list", timeout=5.0
            )
            pages = resp.json()
            if pages:
                cdp_ws_url = pages[0]["webSocketDebuggerUrl"]
    except Exception as exc:
        logger.error(
            "session=%s event=cdp_page_list_error err=%s", session_id, exc
        )
        await websocket.close(code=1011, reason="Could not reach Chrome DevTools")
        return

    if not cdp_ws_url:
        await websocket.close(code=1011, reason="No open page found in Chrome")
        return

    await websocket.accept()
    logger.info(
        "session=%s event=cdp_proxy_connected cdp_url=%s", session_id, cdp_ws_url
    )

    # Bidirectional proxy between the frontend WS and Chrome’s CDP WS
    try:
        import websockets as ws_lib  # transitive dep via playwright

        async with ws_lib.connect(cdp_ws_url, max_size=16 * 1024 * 1024) as chrome_ws:

            async def frontend_to_chrome():
                try:
                    async for msg in websocket.iter_text():
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
        logger.error("session=%s event=cdp_proxy_error err=%s", session_id, exc)
