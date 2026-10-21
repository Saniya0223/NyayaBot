"""
autofill_routes.py
------------------
Endpoints for the NyayaBot browser extension ("Fill on Portal").

  POST   /api/v1/autofill/session      NyayaBot page, signed in: start; returns a session code
  GET    /api/v1/autofill/session      extension, Bearer code: portal + which values exist
  POST   /api/v1/autofill/plan         extension, Bearer code: scanned fields -> checked fill plan
  POST   /api/v1/autofill/report       extension, Bearer code: what was filled / reviewed
  DELETE /api/v1/autofill/session      extension, Bearer code: cancel
  GET    /api/v1/autofill/portals      NyayaBot page, signed in: which portals fit a case
  GET    /api/v1/autofill/test-*       development only (AUTOFILL_TEST_FORM=1): local portal layouts

The server, not the extension, decides which case a code belongs to. Case values
only leave the server inside a plan, and only for a page on the expected portal.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.db.models import AutofillSessionModel, ChatCaseSessionModel, UserModel
from app.db.session import get_db
from app.llm.factory import get_llm_provider
from app.services import portal_autofill as pa

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/v1/autofill", tags=["autofill-extension"])

_STATIC = Path(__file__).parent / "static"
# Local copy of each portal's layout, by real portal key (development only).
_TEST_PAGE_FILES = {
    "cybercrime": _STATIC / "autofill_cybercrime_test_form.html",
    "ejagriti": _STATIC / "autofill_ejagriti_test_form.html",
    "cpgrams": _STATIC / "autofill_cpgrams_test_form.html",
    "rbi_cms": _STATIC / "autofill_rbi_test_form.html",
    "samadhan": _STATIC / "autofill_samadhan_test_form.html",
}


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _portal(key: str, request: Request) -> pa.PortalProfile:
    if key in pa.TEST_PORTALS:
        return pa.local_test_portal(str(request.base_url), kind=key)
    return pa.PORTALS[key]


def _portal_payload(portal: pa.PortalProfile) -> dict:
    return {
        "key": portal.key, "label": portal.label, "url": portal.url, "hosts": list(portal.hosts),
        "entry_hints": list(portal.entry_hints), "entry_avoid": list(portal.entry_avoid),
    }


# ── Start (NyayaBot page, signed in) ──────────────────────────────────────────


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=64)
    # "auto": the first portal for the case type; or a portal key the case type allows
    # (see GET /portals); "test-...": a local layout, only while AUTOFILL_TEST_FORM is on.
    portal: Optional[str] = Field(default="auto", max_length=40, pattern=r"^[a-z_-]+$")


@router.post("/session", status_code=201)
def start_autofill_session(
    body: StartRequest, request: Request,
    db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    record = (
        db.query(ChatCaseSessionModel)
        .filter(ChatCaseSessionModel.case_id == body.case_id, ChatCaseSessionModel.user_id == current_user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Case not found")
    category = str((record.profile_data or {}).get("category") or "")
    wanted = body.portal or "auto"
    if wanted in pa.TEST_PORTALS:
        if not settings.AUTOFILL_TEST_FORM:
            raise HTTPException(status_code=404, detail="Not found")
        portal = pa.local_test_portal(str(request.base_url), kind=wanted)
    else:
        fitting = pa.portals_for_category(category)
        portal = fitting[0] if wanted == "auto" and fitting else next((p for p in fitting if p.key == wanted), None)
        if portal is None:
            raise HTTPException(status_code=409, detail={
                "code": "PORTAL_NOT_SUPPORTED",
                "message": "Fill on Portal doesn't have a portal for this kind of case yet.",
            })

    now = datetime.utcnow()
    # One live code per case: starting again retires the previous one.
    db.query(AutofillSessionModel).filter(
        AutofillSessionModel.user_id == current_user.id,
        AutofillSessionModel.case_id == record.case_id,
        AutofillSessionModel.status.in_(("active", "reviewed")),
    ).update({"status": "cancelled"}, synchronize_session=False)
    db.query(AutofillSessionModel).filter(AutofillSessionModel.expires_at <= now).delete(synchronize_session=False)
    code = secrets.token_urlsafe(32)
    session = AutofillSessionModel(
        user_id=current_user.id, case_id=record.case_id, portal=portal.key, code_hash=_digest(code),
        status="active", expires_at=now + timedelta(minutes=settings.AUTOFILL_SESSION_TTL_MINUTES),
    )
    db.add(session)
    db.commit()
    logger.info("autofill_session=%s case_id=%s portal=%s event=autofill_session_started",
                session.id, record.case_id, portal.key)
    return {
        "session_id": session.id, "code": code, "expires_at": session.expires_at.isoformat() + "Z",
        "portal": _portal_payload(portal),
    }


@router.get("/portals")
def list_autofill_portals(
    case_id: str, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    """The portals that fit this case, for the NyayaBot page's "Fill on Portal" choice.

    `test` is the local layout to open instead while AUTOFILL_TEST_FORM is on.
    """
    record = (
        db.query(ChatCaseSessionModel)
        .filter(ChatCaseSessionModel.case_id == case_id[:64], ChatCaseSessionModel.user_id == current_user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Case not found")
    category = str((record.profile_data or {}).get("category") or "")
    return {
        "test_mode": bool(settings.AUTOFILL_TEST_FORM),
        "portals": [
            {"key": p.key, "label": p.label, "when": p.choose_when,
             "test": pa.test_kind_for(p.key) if settings.AUTOFILL_TEST_FORM else None}
            for p in pa.portals_for_category(category)
        ],
    }


# ── Extension calls (Bearer session code) ─────────────────────────────────────


class _Bound:
    def __init__(self, session: AutofillSessionModel, record: ChatCaseSessionModel, user: UserModel,
                 portal: pa.PortalProfile):
        self.session, self.record, self.user, self.portal = session, record, user, portal


def bound_session(
    request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db),
) -> _Bound:
    error = HTTPException(status_code=401, detail="This Fill on Portal session has ended. Start again from NyayaBot.")
    scheme, _, code = authorization.partition(" ")
    if scheme.lower() != "bearer" or not code.strip():
        raise error
    session = db.query(AutofillSessionModel).filter(AutofillSessionModel.code_hash == _digest(code.strip())).first()
    if not session or session.status == "cancelled" or session.expires_at <= datetime.utcnow():
        raise error
    record = (
        db.query(ChatCaseSessionModel)
        .filter(ChatCaseSessionModel.case_id == session.case_id, ChatCaseSessionModel.user_id == session.user_id)
        .first()
    )
    user = db.query(UserModel).filter(UserModel.id == session.user_id).first()
    if not record or not user:
        raise error
    if session.portal in pa.TEST_PORTALS and not settings.AUTOFILL_TEST_FORM:
        raise error
    return _Bound(session, record, user, _portal(session.portal, request))


@router.get("/session")
def get_autofill_session(bound: _Bound = Depends(bound_session)):
    """Which values exist, never the values themselves: those travel only inside a plan."""
    profile = bound.record.profile_data or {}
    values = pa.case_values(profile, bound.user)
    conflicts = pa.conflicted_keys(profile)
    return {
        "session_id": bound.session.id,
        "status": bound.session.status,
        "expires_at": bound.session.expires_at.isoformat() + "Z",
        "case_title": str(profile.get("title") or "Your case")[:120],
        "portal": _portal_payload(bound.portal),
        "values": [
            {"key": key, "label": pa.VALUE_LABELS.get(key, key),
             "status": "conflict" if key in conflicts else ("ready" if values.get(key) else "missing")}
            for key in bound.portal.value_keys
        ],
    }


@router.post("/plan", response_model=pa.FillPlan)
async def plan_autofill(
    body: pa.PlanRequest, bound: _Bound = Depends(bound_session),
):
    if not bound.portal.allows_url(body.page_url):
        raise HTTPException(status_code=403, detail={
            "code": "WRONG_SITE", "message": f"This page is not {bound.portal.label}. NyayaBot fills only that portal.",
        })
    profile = bound.record.profile_data or {}
    plan = await pa.build_plan(
        body, bound.portal, pa.case_values(profile, bound.user),
        conflicts=pa.conflicted_keys(profile), provider=get_llm_provider(), profile=profile,
    )
    logger.info(
        "autofill_session=%s event=autofill_plan fields=%s actions=%s needs_approval=%s missing=%s "
        "conflict=%s manual=%s sensitive=%s already_filled=%s unmatched=%s ai_used=%s reason=%s",
        bound.session.id, len(body.fields), len(plan.actions), sum(a.needs_approval for a in plan.actions),
        len(plan.missing), len(plan.conflict), len(plan.manual), len(plan.sensitive),
        len(plan.already_filled), len(plan.unmatched), plan.ai_used, plan.reason or "-",
    )
    return plan


class NavLink(BaseModel):
    """One visible link, button or labelled dropdown, as read by the extension. Text and target only."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, le=1_000_000)
    tag: str = Field(default="a", max_length=20)
    type: str = Field(default="", max_length=20)
    text: str = Field(default="", max_length=200)
    href: str = Field(default="", max_length=2000)
    context: str = Field(default="", max_length=200)
    in_dialog: bool = False
    disabled: bool = False
    submits_form: bool = False
    # "dropdown": a labelled list control (text = its label, value = what it shows now);
    # "option": an item in an open list. Only a portal's own route steps ever use them.
    kind: Literal["link", "dropdown", "option"] = "link"
    value: str = Field(default="", max_length=200)


class NavigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_url: str = Field(max_length=2000)
    links: list[NavLink] = Field(default_factory=list, max_length=600)
    visited: list[str] = Field(default_factory=list, max_length=20)
    # Open pop-ups on the page. While one is open nothing else is clicked: the user answers it.
    dialogs: list[pa.DialogInfo] = Field(default_factory=list, max_length=5)


@router.post("/navigate")
def choose_autofill_navigation(body: NavigateRequest, bound: _Bound = Depends(bound_session)):
    """The one control that leads toward the complaint form, decided from the portal's profile.

    A portal with a `route` follows its listed steps in order (see pa.route_action).
    Otherwise the generic chooser picks a link by the portal's entry hints, and only
    on its public entry pages. Nothing portal-specific is decided in the extension.
    """
    from urllib.parse import urlparse

    from app.services import form_navigator as nav

    portal = bound.portal
    if not portal.allows_url(body.page_url):
        raise HTTPException(status_code=403, detail={
            "code": "WRONG_SITE", "message": f"This page is not {portal.label}.",
        })
    host = urlparse(body.page_url).hostname or ""
    # Only what the extension should do when there is no step: kept out of the reply
    # when it is the default, so the extension's behaviour for other portals is unchanged.
    nothing: dict = {"index": None, "text": ""}
    if not portal.choice_questions:
        nothing["offer_choices"] = False
    if portal.not_found_hint:
        nothing["message"] = portal.not_found_hint

    if body.dialogs:
        message = pa.dialog_guidance(portal, body.dialogs[0])
        logger.info("autofill_session=%s event=autofill_navigate dialog=%r", bound.session.id, body.dialogs[0].title[:60])
        return {"index": None, "text": "", "action": "user", "reason": "dialog", "message": message}

    if portal.route:
        links = [link for link in body.links if nav._same_site_link(link.href, host)]
        action = pa.route_action(portal, links, body.visited, on_entry_page=portal.is_entry_page(body.page_url))
        logger.info("autofill_session=%s event=autofill_navigate links=%s route_step=%r",
                    bound.session.id, len(body.links), (action or {}).get("step") or (action or {}).get("text"))
        return action or {**nothing, "reason": "no_route_step"}

    if not portal.is_entry_page(body.page_url):
        # Inside the logged-in complaint area: menu links such as "Report Cyber Crime"
        # would restart the user's questionnaire, so NyayaBot never navigates there.
        logger.info("autofill_session=%s event=autofill_navigate links=%s chosen=None reason=not_entry_page",
                    bound.session.id, len(body.links))
        return {**nothing, "reason": "not_entry_page"}
    # Dropdowns and list options are only ever used by a portal's own route steps.
    candidates = [nav.candidate_from_scan(link.model_dump()) for link in body.links if link.kind == "link"]
    choice = nav.choose_navigation(
        candidates, page_host=host, hints=portal.entry_hints, avoid=portal.entry_avoid,
        visited={nav._norm(text) for text in body.visited},
    )
    logger.info("autofill_session=%s event=autofill_navigate links=%s chosen=%r",
                bound.session.id, len(body.links), choice.text if choice else None)
    if choice is None:
        return nothing
    return {"index": choice.index, "text": choice.text}


@router.post("/choose", response_model=pa.ChooseResponse)
async def choose_autofill_options(body: pa.ChooseRequest, bound: _Bound = Depends(bound_session)):
    """Suggest an answer to the portal's multiple-choice "what happened" questions.

    Fixed portal rules first; AI only for questions they don't settle, and only with
    what happened in the case (masked), never the person's name, contacts or IDs.
    The extension clicks nothing until the user approves a suggestion.
    """
    if not bound.portal.allows_url(body.page_url):
        raise HTTPException(status_code=403, detail={
            "code": "WRONG_SITE", "message": f"This page is not {bound.portal.label}.",
        })
    if not bound.portal.choice_questions:
        # This portal has no "what happened" question cards (see its profile): page
        # cards and filters must never be offered as answers, or sent to an LLM.
        return pa.ChooseResponse()
    result = await pa.propose_choices(body, bound.portal, bound.record.profile_data or {}, get_llm_provider())
    logger.info("autofill_session=%s event=autofill_choose groups=%s proposals=%s sources=%s ai_used=%s",
                bound.session.id, len(body.groups), len(result.proposals),
                ",".join(p.source for p in result.proposals) or "-", result.ai_used)
    return result


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["page_filled", "review_confirmed"]
    filled: int = Field(default=0, ge=0, le=500)
    failed: int = Field(default=0, ge=0, le=500)


@router.post("/report")
def report_autofill(body: ReportRequest, bound: _Bound = Depends(bound_session), db: Session = Depends(get_db)):
    session = db.merge(bound.session)
    if body.event == "page_filled":
        session.filled_count += body.filled
        session.failed_count += body.failed
    else:
        session.status = "reviewed"
        session.reviewed_at = datetime.utcnow()
    db.commit()
    logger.info("autofill_session=%s event=autofill_%s filled=%s failed=%s",
                session.id, body.event, body.filled, body.failed)
    return {"ok": True, "status": session.status, "filled_total": session.filled_count}


@router.delete("/session")
def cancel_autofill(bound: _Bound = Depends(bound_session), db: Session = Depends(get_db)):
    session = db.merge(bound.session)
    session.status = "cancelled"
    db.commit()
    logger.info("autofill_session=%s event=autofill_cancelled", session.id)
    return {"ok": True}


# ── Development test form ─────────────────────────────────────────────────────


def _test_page(portal_key: str) -> FileResponse:
    """A local page shaped like a real portal. Posts nowhere; holds no data. Development only."""
    if not settings.AUTOFILL_TEST_FORM:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_TEST_PAGE_FILES[portal_key], media_type="text/html")


@router.get("/test-form", include_in_schema=False)
def autofill_test_form():
    return _test_page("cybercrime")


@router.get("/test-ejagriti", include_in_schema=False)
def autofill_ejagriti_test_form():
    return _test_page("ejagriti")


@router.get("/test-cpgrams", include_in_schema=False)
def autofill_cpgrams_test_form():
    return _test_page("cpgrams")


@router.get("/test-rbi", include_in_schema=False)
def autofill_rbi_test_form():
    return _test_page("rbi_cms")


@router.get("/test-samadhan", include_in_schema=False)
def autofill_samadhan_test_form():
    return _test_page("samadhan")
