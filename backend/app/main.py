import asyncio
import mimetypes
import os
import re
import uuid
import logging
from datetime import datetime
from typing import List

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.auth_routes import router as auth_router
from app.profile_routes import router as profile_router
from app.browser_routes import router as browser_router
from app.services.browser_runtime import browser_runtime_status
from app.agents.conversation_agent import conversational_agent
from app.agents.intake_node import IntakeFactExtractor
from app.agents.orchestrator import legal_orchestrator
from app.agents.rag_node import statutory_rag
from app.config import settings
from app.llm.contracts import ProviderStatus
from app.llm.factory import get_llm_provider
from app.db.models import (
    CaseModel,
    CaseTimelineEventModel,
    ChatCaseSessionModel,
    EvidenceFileModel,
    FactGraphModel,
    GeneratedDocumentModel,
    UserMemoryModel,
    UserModel,
)
from app.db.migrations import apply_additive_migrations
from app.db.session import Base, engine, get_db
from app.schemas.case import CaseResponse, ClarificationAnswer, IntakeRequest
from app.schemas.chat import (
    ChatMessage,
    ChatSessionResponse,
    CaseSummaryResponse,
    ChatTurnRequest,
    ChatTurnResponse,
    DocumentUploadExtractionRequest,
    StructuredCaseProfile,
)
from app.schemas.document import (
    DocumentDefinitionSchema,
    DocumentAssessment,
    DocumentGenerateRequest,
    DocumentResponse,
    PortalFilingDossier,
)
from app.schemas.fact_graph import EvidenceItem, FactGraphSchema, FinancialBreakdown, PartyInfo
from app.services.doc_generator import doc_generator
from app.services.document_registry import (
    list_document_definitions,
)
from app.services.document_generation import assess_document_generation
from app.services.professional_help import evaluate_professional_help
from app.services.dossier_generator import DossierGenerator
from app.services.upload_intelligence import MAX_UPLOAD_BYTES, sanitize_file_name, validate_upload
from app.services.evidence_processing import (
    build_review_turn,
    evidence_chat_context,
    evidence_detail,
    evidence_summary,
    process_evidence,
    retry_stage,
)
from app.services.llm_conversation import gemini_conversation_service
from app.services.case_summary import generate_case_summary, summary_fingerprint, summary_ready
from app.services.user_context import CASE_HISTORY_LOOKUP, case_history_lookup_reply, chat_user_context, explicit_memory_text
from app.llm.contracts import LLMProviderError


Base.metadata.create_all(bind=engine)
apply_additive_migrations(engine)

app = FastAPI(
    title="NyayaBot",
    version=settings.APP_VERSION,
    description="Conversation-first legal information, workflow, and document-assistance MVP for India",
)
PRIVATE_LAN_ORIGIN_REGEX = (
    r"^http://("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"):3000$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Also allow the dev frontend reached over the LAN (phone/tablet on the same
    # Wi-Fi). Private address ranges only, port 3000 only, so this stays scoped
    # to local development rather than opening the API to arbitrary origins.
    allow_origin_regex=PRIVATE_LAN_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(browser_router)


@app.on_event("startup")
async def log_auto_fill_browser_runtime() -> None:
    """Optional Auto-Fill must never prevent the rest of NyayaBot from booting."""
    try:
        # Playwright's sync API refuses to run on the event-loop thread.
        runtime = await asyncio.to_thread(browser_runtime_status)
        logging.getLogger("uvicorn.error").info(
            "Auto-Fill browser runtime: browser-use=%s Playwright=%s Chromium=%s",
            runtime["browser_use"], runtime["playwright"], runtime["chromium"],
        )
        if not runtime["available"]:
            logging.getLogger("uvicorn.error").warning(
                "Auto-Fill unavailable: install requirements and run python -m playwright install chromium"
            )
    except Exception:
        logging.getLogger("uvicorn.error").warning("Auto-Fill runtime diagnostic unavailable")


@app.middleware("http")
async def reject_untrusted_write_origins(request: Request, call_next):
    """Block browser cookie-authenticated writes from untrusted origins."""

    origin = request.headers.get("origin")
    if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
        api_origin = f"{request.url.scheme}://{request.url.netloc}"
        allowed = (
            origin == api_origin
            or origin in settings.CORS_ORIGINS
            or re.fullmatch(PRIVATE_LAN_ORIGIN_REGEX, origin) is not None
        )
        if not allowed:
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
    return await call_next(request)

logger = logging.getLogger("uvicorn.error")
llm_status = get_llm_provider().status
logger.info(
    "provider=%s model=%s configured=%s mode=%s event=llm_initialized",
    llm_status.provider,
    llm_status.model,
    llm_status.configured,
    llm_status.mode,
)


def _safe_profile(record: ChatCaseSessionModel) -> StructuredCaseProfile:
    profile = StructuredCaseProfile.model_validate(record.profile_data)
    profile.professional_help = evaluate_professional_help(profile)
    profile.provided_documents = [
        evidence_summary(item)
        for item in sorted(record.case.evidence_files, key=lambda file: file.uploaded_at or datetime.min)
    ] if record.case else []
    return profile


def _get_owned_case(db: Session, case_id: str, current_user: UserModel) -> CaseModel:
    case = (
        db.query(CaseModel)
        .filter(CaseModel.id == case_id, CaseModel.user_id == current_user.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _get_owned_chat_record(
    db: Session,
    case_id: str,
    current_user: UserModel,
) -> ChatCaseSessionModel:
    record = (
        db.query(ChatCaseSessionModel)
        .filter(
            ChatCaseSessionModel.case_id == case_id,
            ChatCaseSessionModel.user_id == current_user.id,
            ChatCaseSessionModel.is_demo.is_(False),
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Case not found")
    return record


def _load_chat_profile(record: ChatCaseSessionModel) -> StructuredCaseProfile:
    return _safe_profile(record)


def _profile_to_fact_graph(profile: StructuredCaseProfile, narrative: str) -> FactGraphSchema:
    evidence = [
        EvidenceItem(
            doc_type=item.id.upper(),
            doc_name=item.name,
            is_available=item.is_available,
            annexure_label=item.annexure_label,
        )
        for item in profile.evidence_checklist
        if item.is_available
    ]
    return FactGraphSchema(
        complainant=PartyInfo(
            name=profile.user_name or "",
            address=profile.key_facts.get("user_address"),
            city=profile.user_city,
            state=profile.user_state,
            phone=profile.user_phone,
        ),
        opposite_party=PartyInfo(
            name=profile.opposite_party_name or profile.bank_name or profile.police_station_name or "",
            address=profile.opposite_party_address or profile.property_address,
            city=profile.user_city,
            state=profile.user_state,
        ),
        incident_narrative=narrative,
        incident_date=profile.vacating_date or profile.incident_date,
        category=profile.category,
        sub_category=profile.issue_type,
        financials=FinancialBreakdown(
            amount_paid=profile.disputed_amount,
            refund_claimed=profile.disputed_amount,
            total_claim_amount=profile.disputed_amount,
        ),
        evidence_inventory=evidence,
        missing_facts=profile.missing_required_fields,
        is_complete=profile.is_ready_for_document,
        completion_score=max(0.2, 1 - (len(profile.missing_required_fields) * 0.12)),
    )


def _sync_profile_to_legacy_case(
    profile: StructuredCaseProfile,
    narrative: str,
    db: Session,
    user_id: str | None,
) -> None:
    case = db.query(CaseModel).filter(CaseModel.id == profile.case_id).first()
    if not case:
        case = CaseModel(
            id=profile.case_id,
            user_id=user_id,
            case_number=profile.case_number,
            title=profile.title,
            category=profile.category,
        )
        db.add(case)
        db.flush()
    elif case.user_id != user_id:
        raise HTTPException(status_code=404, detail="Case not found")
    case.title = profile.title
    case.category = profile.category
    case.status = profile.current_stage_label
    case.pecuniary_value = profile.disputed_amount
    case.cause_of_action_date = profile.vacating_date or profile.incident_date
    case.appropriate_forum = f"Authority competent for {profile.user_city or 'the relevant local area'}"
    case.severity_level = "ESCALATED_LAWYER" if profile.risk_level == "RED" else "STANDARD"

    fact_graph = _profile_to_fact_graph(profile, narrative)
    if not case.fact_graph:
        case.fact_graph = FactGraphModel(case_id=profile.case_id)
    case.fact_graph.complainant_data = fact_graph.complainant.model_dump()
    case.fact_graph.opposite_party_data = fact_graph.opposite_party.model_dump()
    case.fact_graph.incident_narrative = narrative
    case.fact_graph.financial_breakdown = fact_graph.financials.model_dump()
    case.fact_graph.evidence_inventory = [item.model_dump() for item in fact_graph.evidence_inventory]
    case.fact_graph.missing_facts = profile.missing_required_fields
    case.fact_graph.is_complete = profile.is_ready_for_document
    case.fact_graph.completion_score = fact_graph.completion_score


def _save_chat_session(
    profile: StructuredCaseProfile,
    messages: List[ChatMessage],
    db: Session,
    user_id: str | None,
    *,
    is_demo: bool = False,
) -> None:
    profile.professional_help = evaluate_professional_help(profile)
    record = db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == profile.case_id).first()
    if not record:
        record = ChatCaseSessionModel(
            case_id=profile.case_id,
            user_id=user_id,
            is_demo=is_demo,
            profile_data={},
            messages_data=[],
        )
        db.add(record)
    elif record.user_id != user_id or bool(record.is_demo) != is_demo:
        raise HTTPException(status_code=404, detail="Case not found")
    record.profile_data = profile.model_dump(mode="json")
    record.messages_data = [message.model_dump(mode="json") for message in messages]
    record.updated_at = datetime.utcnow()
    narrative = "\n".join(message.text for message in messages if message.sender == "user") or profile.title
    _sync_profile_to_legacy_case(profile, narrative, db, user_id)
    db.commit()


def _messages_for_session(record: ChatCaseSessionModel) -> List[ChatMessage]:
    return [ChatMessage.model_validate(message) for message in record.messages_data]


def _seed_demo_sessions(db: Session) -> None:
    demos = {
        "demo-tenant": [
            "My landlord isn't returning my ₹50,000 deposit.",
            "Noida, Uttar Pradesh. I moved out on 10 August 2026.",
            "Yes, both",
            "He just says he'll return it later.",
        ],
        "demo-consumer": [
            "Amazon won't refund ₹20,000 for a defective laptop.",
            "I bought a laptop on 12 August 2026 and I live in Delhi.",
            "I have the invoice and Amazon rejected the refund in writing.",
            "I sent the grievance today by email.",
        ],
        "demo-cyber": [
            "I lost ₹35,000 in a UPI fraud.",
            "It happened on 30 August 2026. UTR ABC123456789 and my bank is HDFC.",
            "I reported it to the bank and called 1930.",
        ],
    }
    for case_id, turns in demos.items():
        if db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == case_id).first():
            continue
        profile = None
        messages: List[ChatMessage] = []
        for turn in turns:
            response = conversational_agent.process_turn(
                ChatTurnRequest(message=turn, case_id=case_id), profile
            )
            profile = response.case_profile
            messages.extend(
                [
                    ChatMessage(id=str(uuid.uuid4()), sender="user", text=turn),
                    ChatMessage(
                        id=response.message_id,
                        sender="bot",
                        text=response.reply_text,
                        quick_replies=response.quick_replies,
                        suggested_action=response.suggested_action,
                    ),
                ]
            )
        if profile:
            _save_chat_session(profile, messages, db, None, is_demo=True)


@app.get("/")
def root():
    return {
        "app": "NyayaBot",
        "version": settings.APP_VERSION,
        "status": "online",
        "jurisdiction": "India",
        "supported_domains": ["consumer", "employment", "tenant", "cybercrime", "police"],
    }


@app.post("/api/v1/intake", response_model=CaseResponse)
def handle_intake(
    req: IntakeRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    if not req.user_narrative or len(req.user_narrative.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please describe the issue in at least 10 characters.",
        )
    if req.case_id:
        _get_owned_case(db, req.case_id, current_user)
    return legal_orchestrator.process_intake(req, db, current_user.id)


@app.post("/api/v1/clarifications", response_model=CaseResponse)
def submit_clarifications(
    payload: ClarificationAnswer,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    case = _get_owned_case(db, payload.case_id, current_user)
    if not case.fact_graph:
        raise HTTPException(status_code=404, detail="Case facts not found")
    facts = case.fact_graph
    answers_text = " ".join(f"{question}: {answer}" for question, answer in payload.answers.items())
    return legal_orchestrator.process_intake(
        IntakeRequest(
            user_narrative=f"{facts.incident_narrative}\n\nAdditional clarifications: {answers_text}",
            case_id=case.id,
            user_name=facts.complainant_data.get("name", ""),
            user_city=facts.complainant_data.get("city", ""),
            user_state=facts.complainant_data.get("state", ""),
            user_phone=facts.complainant_data.get("phone"),
            user_email=facts.complainant_data.get("email"),
        ),
        db,
        current_user.id,
    )


@app.get("/api/v1/cases", response_model=List[CaseResponse])
def list_cases(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    results = []
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.user_id == current_user.id)
        .order_by(CaseModel.updated_at.desc())
        .all()
    )
    for case in cases:
        facts = case.fact_graph
        request = IntakeRequest(
            user_narrative=facts.incident_narrative if facts else case.title,
            case_id=case.id,
            user_name=facts.complainant_data.get("name", "") if facts else "",
            user_city=facts.complainant_data.get("city", "") if facts else "",
            user_state=facts.complainant_data.get("state", "") if facts else "",
        )
        results.append(legal_orchestrator.process_intake(request, db, current_user.id))
    return results


@app.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
def get_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    case = _get_owned_case(db, case_id, current_user)
    facts = case.fact_graph
    return legal_orchestrator.process_intake(
        IntakeRequest(
            user_narrative=facts.incident_narrative if facts else case.title,
            case_id=case.id,
            user_name=facts.complainant_data.get("name", "") if facts else "",
            user_city=facts.complainant_data.get("city", "") if facts else "",
            user_state=facts.complainant_data.get("state", "") if facts else "",
        ),
        db,
        current_user.id,
    )


@app.post("/api/v1/cases/{case_id}/timeline/{event_id}/toggle")
def toggle_timeline_event(
    case_id: str,
    event_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    _get_owned_case(db, case_id, current_user)
    event = db.query(CaseTimelineEventModel).filter(
        CaseTimelineEventModel.case_id == case_id,
        CaseTimelineEventModel.id == event_id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    if event.status == "COMPLETED":
        event.status, event.completed_at = "PENDING", None
    else:
        event.status, event.completed_at = "COMPLETED", datetime.utcnow()
    db.commit()
    return {"status": "success", "new_event_status": event.status}


def _document_validation_data(facts: FactGraphSchema, overrides: dict, user: UserModel | None = None) -> dict:
    def personal(key: str, case_value: str | None, profile_value: str | None):
        if key in overrides:
            return overrides[key]
        if case_value and case_value.strip().casefold() not in {"complainant", "city", "state"}:
            return case_value
        return profile_value

    return {
        "complainant_name": personal("complainant_name", facts.complainant.name, user.full_name if user else None),
        "recipient_name": overrides.get("recipient_name") or facts.opposite_party.name,
        "opposite_party_name": overrides.get("opposite_party_name") or facts.opposite_party.name,
        "complainant_city": personal("complainant_city", facts.complainant.city, user.city if user else None),
        "complainant_address": personal("complainant_address", facts.complainant.address, user.full_address if user else None),
        "complainant_state": personal("complainant_state", facts.complainant.state, user.state if user else None),
        "complainant_pin_code": personal("complainant_pin_code", None, user.pin_code if user else None),
        "complainant_phone": personal("complainant_phone", facts.complainant.phone, user.phone if user else None),
        "property_address": overrides.get("property_address") or facts.opposite_party.address,
        "disputed_amount": overrides.get("disputed_amount", facts.financials.amount_paid),
        "vacating_date": overrides.get("vacating_date") or facts.incident_date,
        "incident_date": overrides.get("incident_date") or facts.incident_date,
        "incident_narrative": overrides.get("incident_narrative") or facts.incident_narrative,
        "police_station_name": overrides.get("police_station_name") or facts.opposite_party.name,
        "bank_name": overrides.get("bank_name") or facts.opposite_party.name,
        "transaction_id": overrides.get("transaction_id"),
        "opposite_party_address": overrides.get("opposite_party_address") or facts.opposite_party.address,
        "recipient_address": overrides.get("recipient_address") or facts.opposite_party.address,
        "landlord_address": overrides.get("landlord_address") or facts.opposite_party.address,
        "employee_role": overrides.get("employee_role"),
        "unpaid_months": overrides.get("unpaid_months"),
        "order_number": overrides.get("order_number"),
        "reference_number": overrides.get("reference_number"),
        "agreement_date": overrides.get("agreement_date"),
        "previous_requests": overrides.get("previous_requests"),
        "accused_name": overrides.get("accused_name"),
        "witnesses": overrides.get("witnesses"),
        "fraudster_phone": overrides.get("fraudster_phone"),
        "fraudster_upi_id": overrides.get("fraudster_upi_id"),
        "cybercrime_acknowledgement": overrides.get("cybercrime_acknowledgement"),
        "public_authority_address": overrides.get("public_authority_address"),
        "period_of_information": overrides.get("period_of_information"),
    }


def _document_context(db: Session, case_id: str, current_user: UserModel):
    case = _get_owned_case(db, case_id, current_user)
    chat_record = db.query(ChatCaseSessionModel).filter(
        ChatCaseSessionModel.case_id == case_id,
        ChatCaseSessionModel.user_id == current_user.id,
        ChatCaseSessionModel.is_demo.is_(False),
    ).first()
    profile = _load_chat_profile(chat_record) if chat_record else None
    if profile:
        messages = _messages_for_session(chat_record)
        narrative = "\n".join(message.text for message in messages if message.sender == "user") or profile.title
        facts = _profile_to_fact_graph(profile, narrative)
    elif case.fact_graph:
        stored = case.fact_graph
        facts = IntakeFactExtractor.extract_facts(stored.incident_narrative, {
            "user_name": stored.complainant_data.get("name"),
            "user_city": stored.complainant_data.get("city"),
            "user_state": stored.complainant_data.get("state"),
            "user_phone": stored.complainant_data.get("phone"),
            "user_email": stored.complainant_data.get("email"),
            "opposite_party_name": stored.opposite_party_data.get("name"),
            "amount_paid": stored.financial_breakdown.get("amount_paid", 0),
            "incident_date": case.cause_of_action_date,
            "category": case.category,
        })
    else:
        raise HTTPException(status_code=404, detail="Case facts not found")
    return case, chat_record, profile, facts


def _assess_case_document(profile, facts, doc_type, overrides, user=None):
    values = _document_validation_data(facts, overrides, user)
    if profile:
        for field in ("employee_role", "unpaid_months", "order_number", "employer_address", "public_authority_address", "witnesses", "previous_requests"):
            values[field] = overrides.get(field) or profile.key_facts.get(field) or getattr(profile, field, None)
        values["order_number"] = values["order_number"] or profile.key_facts.get("order_reference_id")
        values["employer_address"] = values["employer_address"] or profile.opposite_party_address
        values["public_authority_address"] = values["public_authority_address"] or profile.opposite_party_address
        values["transaction_id"] = overrides.get("transaction_id") or profile.transaction_id
    safety = profile.safety_status if profile else None
    safety_blocked = bool(safety and (
        safety.get("immediate_danger") is True
        or (not safety.get("triage_complete") and safety.get("immediate_danger") is not False)
    ))
    return assess_document_generation(doc_type, facts.category, values, safety_blocked=safety_blocked)


@app.get("/api/v1/documents/assessment", response_model=DocumentAssessment)
def document_assessment(
    case_id: str, doc_type: str, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    _, _, profile, facts = _document_context(db, case_id, current_user)
    return _assess_case_document(profile, facts, doc_type, {}, current_user)


@app.post("/api/v1/documents/generate", response_model=DocumentResponse)
def generate_document(
    req: DocumentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    case, chat_record, profile, fact_graph = _document_context(db, req.case_id, current_user)
    overrides = {key: value for key, value in (req.override_data or {}).items() if key in {
        field for definition in list_document_definitions()
        for field in (*definition.required_fields, *definition.optional_fields)
    }}
    assessment = _assess_case_document(profile, fact_graph, req.doc_type, overrides, current_user)
    if not assessment.ready_to_generate:
        raise HTTPException(
            status_code=422,
            detail={"message": "; ".join(assessment.blockers) or "Confirm the required facts before generating this document.", "missing_fields": assessment.missing_required_fields},
        )
    response = doc_generator.generate_document(
        case_id=case.id,
        doc_type=req.doc_type,
        fact_graph=fact_graph,
        appropriate_forum=case.appropriate_forum or "Competent authority",
        custom_data={field.key: field.value for field in assessment.fields
                     if field.value not in (None, "", []) or field.key in {
                         "complainant_address", "complainant_state", "complainant_pin_code", "complainant_phone"
                     }},
    )
    db.add(
        GeneratedDocumentModel(
            id=response.id,
            case_id=case.id,
            doc_type=response.doc_type,
            title=response.title,
            content_html=response.content_html,
            pdf_filename=os.path.basename(response.pdf_download_url) if response.pdf_download_url else None,
            pdf_download_url=response.pdf_download_url,
            statutory_citations=response.statutory_citations,
        )
    )
    if profile:
        if profile.document_request and profile.document_request.get("document_type") == req.doc_type:
            profile.document_request["status"] = "GENERATED"
        confirmed_override_fields = {
            "complainant_name": "user_name",
            "complainant_city": "user_city",
            "opposite_party_name": "opposite_party_name",
            "property_address": "property_address",
            "disputed_amount": "disputed_amount",
            "vacating_date": "vacating_date",
            "incident_date": "incident_date",
            "bank_name": "bank_name",
            "transaction_id": "transaction_id",
            "police_station_name": "police_station_name",
        }
        for override_key, profile_field in confirmed_override_fields.items():
            value = overrides.get(override_key)
            if value is not None and value != "":
                setattr(profile, profile_field, value)
                profile.fact_metadata[profile_field] = {
                    "value": value,
                    "source": "document_confirmation",
                    "confidence": 1.0,
                    "confirmed": True,
                }
        profile.documents.append(
            {
                "id": response.id,
                "type": response.doc_type,
                "title": response.title,
                "status": "DRAFT",
                "created_at": response.created_at,
                "pdf_download_url": response.pdf_download_url,
                "docx_download_url": response.docx_download_url,
            }
        )
        if not any(action.get("type") == "document_prepared" for action in profile.actions_completed):
            conversational_agent._add_action(profile, "document_prepared", f"Prepared {response.title}")
        _save_chat_session(
            profile,
            _messages_for_session(chat_record),
            db,
            current_user.id,
        )
    else:
        db.commit()
    logger.info("case_id=%s document_type=%s event=document_generated", case.id, req.doc_type)
    return response


@app.get("/api/v1/documents/download/{filename}")
def download_document(
    filename: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    if filename != os.path.basename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    records = (
        db.query(GeneratedDocumentModel)
        .join(CaseModel, CaseModel.id == GeneratedDocumentModel.case_id)
        .filter(CaseModel.user_id == current_user.id)
        .all()
    )
    is_owned = False
    for record in records:
        pdf_name = os.path.basename(record.pdf_download_url or record.pdf_filename or "")
        docx_name = f"{os.path.splitext(pdf_name)[0]}.docx" if pdf_name else ""
        if filename in {pdf_name, docx_name}:
            is_owned = True
            break
    if not is_owned:
        raise HTTPException(status_code=404, detail="File not found")
    file_path = os.path.join(settings.STORAGE_DIR, "documents", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@app.get("/api/v1/documents")
def list_documents(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    records = (
        db.query(GeneratedDocumentModel, CaseModel)
        .join(CaseModel, CaseModel.id == GeneratedDocumentModel.case_id)
        .filter(CaseModel.user_id == current_user.id)
        .order_by(GeneratedDocumentModel.created_at.desc())
        .all()
    )
    result = []
    for record, case in records:
        docx_url = None
        if record.pdf_download_url:
            candidate = record.pdf_download_url.rsplit(".", 1)[0] + ".docx"
            candidate_path = os.path.join(settings.STORAGE_DIR, "documents", os.path.basename(candidate))
            if os.path.exists(candidate_path):
                docx_url = candidate
        result.append(
            {
                "id": record.id,
                "case_id": record.case_id,
                "case_title": case.title if case else "Case",
                "doc_type": record.doc_type,
                "title": record.title,
                "status": "DRAFT",
                "pdf_download_url": record.pdf_download_url,
                "docx_download_url": docx_url,
                "created_at": record.created_at.isoformat(),
            }
        )
    return result


@app.get("/api/v1/document-definitions", response_model=List[DocumentDefinitionSchema])
def document_definitions():
    return list_document_definitions()


@app.get("/api/v1/cases/{case_id}/dossier", response_model=PortalFilingDossier)
def get_filing_dossier(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    case = _get_owned_case(db, case_id, current_user)
    if not case.fact_graph:
        raise HTTPException(status_code=404, detail="Case facts not found")
    facts = case.fact_graph
    schema = IntakeFactExtractor.extract_facts(
        facts.incident_narrative,
        {
            "user_name": facts.complainant_data.get("name"),
            "user_city": facts.complainant_data.get("city"),
            "user_state": facts.complainant_data.get("state"),
            "opposite_party_name": facts.opposite_party_data.get("name"),
            "amount_paid": facts.financial_breakdown.get("amount_paid", 0),
            "incident_date": case.cause_of_action_date,
            "category": case.category,
        },
    )
    return DossierGenerator.generate_dossier(
        case_id=case.id,
        category=case.category,
        fact_graph=schema,
        appropriate_forum=case.appropriate_forum or "Competent authority",
    )


@app.get("/api/v1/statutes")
def get_all_statutes():
    return statutory_rag.corpus


@app.get("/api/v1/llm/status", response_model=ProviderStatus)
def get_llm_status():
    return get_llm_provider().status


@app.post("/api/v1/chat/message", response_model=ChatTurnResponse)
async def handle_chat_message(
    req: ChatTurnRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    record = _get_owned_chat_record(db, req.case_id, current_user) if req.case_id else None
    existing_profile = _load_chat_profile(record) if record else None
    recent_messages = _messages_for_session(record) if record else []
    user_context = chat_user_context(db, current_user, req.message, req.case_id)
    # Evidence is case data: only the owned current case's evidence is ever included.
    evidence_context = evidence_chat_context(db, record.case_id, req.message) if record else {}
    response = await gemini_conversation_service.process_turn(
        req, existing_profile, recent_messages, user_context=user_context, evidence_context=evidence_context,
    )
    if user_context["intent"] == CASE_HISTORY_LOOKUP:
        response.reply_text = case_history_lookup_reply(user_context, response.case_profile.language_style)
    memory_text = explicit_memory_text(req.message)
    if memory_text and not (response.case_profile.safety_status or {}).get("is_safety_case"):
        if db.query(UserMemoryModel).filter(UserMemoryModel.user_id == current_user.id).count() < 50:
            db.add(UserMemoryModel(user_id=current_user.id, category="explicit", text=memory_text))
            memory_note = {
                "hindi": "मैंने इसे आपकी दीर्घकालीन स्मृति में सहेज लिया है। आप इसे प्रोफ़ाइल में हटा सकते हैं।",
                "hinglish": "Maine ise long-term memory mein save kar liya hai. Aap Profile mein ise delete kar sakte hain.",
            }.get(response.case_profile.language_style, "I've saved that to your long-term memory. You can delete it in Profile.")
        else:
            memory_note = "Your long-term memory is full. Delete an old memory in Profile before saving another."
        response.reply_text = memory_note
    stored_messages = list(recent_messages)
    stored_messages.extend(
        [
            ChatMessage(id=str(uuid.uuid4()), sender="user", text=req.message),
            ChatMessage(
                id=response.message_id,
                sender="bot",
                text=response.reply_text,
                quick_replies=response.quick_replies,
                suggested_action=response.suggested_action,
            ),
        ]
    )
    _save_chat_session(response.case_profile, stored_messages, db, current_user.id)
    logger.info(
        "case_id=%s classification=%s workflow_stage=%s provider=%s model=%s mode=%s event=chat_processed",
        response.case_profile.case_id,
        response.case_profile.category,
        response.case_profile.current_stage_key,
        response.llm_provider,
        response.llm_model,
        response.llm_mode,
    )
    return response


@app.get("/api/v1/chat/cases", response_model=List[StructuredCaseProfile])
def list_chat_cases(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    records = (
        db.query(ChatCaseSessionModel)
        .options(selectinload(ChatCaseSessionModel.case).selectinload(CaseModel.evidence_files))
        .filter(
            ChatCaseSessionModel.user_id == current_user.id,
            ChatCaseSessionModel.is_demo.is_(False),
        )
        .order_by(ChatCaseSessionModel.updated_at.desc())
        .all()
    )
    return [_safe_profile(record) for record in records]


@app.get("/api/v1/chat/cases/{case_id}", response_model=ChatSessionResponse)
def get_chat_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    record = _get_owned_chat_record(db, case_id, current_user)
    return ChatSessionResponse(
        case_profile=_load_chat_profile(record),
        messages=_messages_for_session(record),
    )


@app.post("/api/v1/chat/cases/{case_id}/summary", response_model=CaseSummaryResponse)
async def generate_chat_case_summary(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Generate only on explicit request; reuse a brief until case content changes."""
    record = _get_owned_chat_record(db, case_id, current_user)
    profile = _load_chat_profile(record)
    if not summary_ready(profile):
        raise HTTPException(status_code=422, detail="Add some case facts before generating a summary.")
    fingerprint = summary_fingerprint(profile)
    cached = profile.ai_summary_cache or {}
    if cached.get("fingerprint") == fingerprint and cached.get("text"):
        return CaseSummaryResponse(text=cached["text"], cached=True)

    provider = get_llm_provider()
    if not provider.status.configured:
        raise HTTPException(status_code=503, detail="AI summary is unavailable until an AI provider is configured.")
    try:
        brief = await generate_case_summary(profile, provider)
    except LLMProviderError as exc:
        logger.warning("case_id=%s event=case_summary_provider_failed", case_id)
        raise HTTPException(status_code=503, detail="AI summary is temporarily unavailable. Please try again.") from exc
    if not brief:
        raise HTTPException(status_code=503, detail="AI summary is temporarily unavailable. Please try again.")
    profile.ai_summary_cache = {"fingerprint": fingerprint, "text": brief}
    record.profile_data = profile.model_dump(mode="json")
    db.commit()
    return CaseSummaryResponse(text=brief, cached=False)


@app.post("/api/v1/chat/cases/{case_id}/resolve", response_model=StructuredCaseProfile)
def resolve_chat_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    record = _get_owned_chat_record(db, case_id, current_user)
    profile = _load_chat_profile(record)
    if profile.current_stage_key != "RESOLVED":
        profile.current_stage_key = "RESOLVED"
        profile.current_stage_label = "Resolved"
        conversational_agent._add_action(profile, "case_resolved", "Case marked resolved")
    _save_chat_session(profile, _messages_for_session(record), db, current_user.id)
    return profile


@app.post("/api/v1/chat/upload-document", response_model=ChatTurnResponse)
async def handle_document_upload_extraction(
    req: DocumentUploadExtractionRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    if not req.case_id:
        raise HTTPException(status_code=400, detail="Start a case before uploading a document")
    record = _get_owned_chat_record(db, req.case_id, current_user)
    profile = _load_chat_profile(record)
    response = await gemini_conversation_service.process_document_upload(req, profile)
    messages = _messages_for_session(record)
    messages.append(
        ChatMessage(
            id=response.message_id,
            sender="bot",
            text=response.reply_text,
            quick_replies=response.quick_replies,
        )
    )
    _save_chat_session(response.case_profile, messages, db, current_user.id)
    logger.info("case_id=%s document_type=%s event=evidence_metadata_processed", profile.case_id, req.doc_type)
    return response


@app.post("/api/v1/chat/upload-file", response_model=ChatTurnResponse)
async def handle_evidence_file_upload(
    background_tasks: BackgroundTasks,
    case_id: str = Form(...),
    doc_type: str = Form(...),
    upload: UploadFile = File(...),
    excerpt: str = Form(""),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Store the original, then extract and analyze it in the background."""
    record = _get_owned_chat_record(db, case_id, current_user)
    profile = _load_chat_profile(record)

    original_name = sanitize_file_name(upload.filename)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    try:
        extension = validate_upload(original_name, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    evidence_id = str(uuid.uuid4())
    stored_name = f"{case_id[:8]}_{evidence_id}{extension}"
    storage_path = os.path.join(settings.STORAGE_DIR, "evidence", stored_name)
    try:
        with open(storage_path, "wb") as handle:
            handle.write(content)
    except OSError as exc:
        logger.error("case_id=%s event=evidence_storage_failed error_type=%s", case_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="The file could not be stored. Nothing was attached; please try again.") from exc

    evidence_map = {
        "RENTAL_AGREEMENT": "rental_agreement",
        "SALARY_SLIP": "salary_slips",
        "INVOICE": "invoice",
        "REJECTION_REPLY": "seller_rejection",
    }
    checklist_item = next(
        (item for item in profile.evidence_checklist if item.id == evidence_map.get(doc_type)),
        None,
    )
    evidence = EvidenceFileModel(
        id=evidence_id,
        case_id=case_id,
        file_name=original_name,
        file_type=upload.content_type or extension.lstrip("."),
        file_path=storage_path,
        annexure_label=checklist_item.annexure_label if checklist_item else None,
        uploaded_at=datetime.utcnow(),
        file_size=len(content),
        doc_type_hint=doc_type[:40],
        user_excerpt=excerpt.strip()[:12000] or None,
        processing_status="UPLOADED",
    )
    db.add(evidence)
    profile.provided_documents.append(evidence_summary(evidence))
    profile.key_facts["last_upload"] = {
        "evidence_id": evidence_id,
        "file_name": original_name,
        "stored_name": stored_name,
        "extraction_mode": "background",
    }
    # Existing metadata step: evidence checklist and timeline only. No content is
    # passed, so no facts are extracted here; findings arrive after processing.
    response = conversational_agent.process_document_upload(
        DocumentUploadExtractionRequest(case_id=case_id, doc_type=doc_type, file_name=original_name),
        profile,
    )
    response.reply_text = (
        f"I've attached {original_name} to your case and I'm reading it now. When it's processed you'll see "
        "what I found under Provided by you. Nothing from the document is added to your case until you confirm it."
    )
    response.quick_replies = ["Continue my case"]
    response = gemini_conversation_service._tag_response(response, gemini_conversation_service._provider_mode)
    messages = _messages_for_session(record)
    messages.append(
        ChatMessage(
            id=response.message_id,
            sender="bot",
            text=response.reply_text,
            quick_replies=response.quick_replies,
        )
    )
    _save_chat_session(response.case_profile, messages, db, current_user.id)
    db.commit()
    background_tasks.add_task(
        process_evidence,
        evidence_id,
        provider=gemini_conversation_service.provider,
        workflow_agent=conversational_agent,
    )
    logger.info(
        "case_id=%s evidence_id=%s document_type=%s file_type=%s event=evidence_uploaded",
        case_id,
        evidence_id,
        doc_type,
        extension.lstrip("."),
    )
    return response


def _owned_evidence(db: Session, evidence_id: str, current_user: UserModel) -> EvidenceFileModel:
    record = (
        db.query(EvidenceFileModel)
        .join(CaseModel, CaseModel.id == EvidenceFileModel.case_id)
        .filter(
            EvidenceFileModel.id == evidence_id,
            CaseModel.user_id == current_user.id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return record


@app.get("/api/v1/evidence/{evidence_id}")
def get_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Processing status, page-level extracted text and evidence-derived findings."""
    return evidence_detail(_owned_evidence(db, evidence_id, current_user))


@app.post("/api/v1/evidence/{evidence_id}/retry")
def retry_evidence_processing(
    evidence_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Re-process the stored original; never creates a new evidence record."""
    record = _owned_evidence(db, evidence_id, current_user)
    stage = retry_stage(record)
    if stage is None:
        raise HTTPException(status_code=409, detail="This file is not in a state that can be retried.")
    record.processing_status = "UPLOADED"
    record.processing_error_code = None
    record.processing_started_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(
        process_evidence,
        record.id,
        provider=gemini_conversation_service.provider,
        workflow_agent=conversational_agent,
        stage=stage,
    )
    logger.info("evidence_id=%s stage=%s event=evidence_retry_requested", record.id, stage)
    return evidence_summary(record)


@app.post("/api/v1/evidence/{evidence_id}/review", response_model=ChatTurnResponse)
def review_evidence_findings(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Hand evidence candidates to the existing chat confirmation flow; nothing is applied here."""
    evidence = _owned_evidence(db, evidence_id, current_user)
    chat_record = _get_owned_chat_record(db, evidence.case_id, current_user)
    analysis = evidence.analysis_data or {}
    if evidence.processing_status != "COMPLETED" or not (analysis.get("candidate_facts") or analysis.get("conflicts")):
        raise HTTPException(status_code=409, detail="There are no extracted details to review for this file.")
    profile = _load_chat_profile(chat_record)
    reply_text, quick_replies, pending = build_review_turn(evidence)
    if pending:
        profile.key_facts["pending_document_extraction"] = pending
    response = ChatTurnResponse(
        reply_text=reply_text,
        case_profile=profile,
        quick_replies=quick_replies,
        message_id=str(uuid.uuid4()),
    )
    response = gemini_conversation_service._tag_response(response, gemini_conversation_service._provider_mode)
    messages = _messages_for_session(chat_record)
    messages.append(ChatMessage(id=response.message_id, sender="bot", text=reply_text, quick_replies=quick_replies))
    evidence.analysis_data = {**analysis, "review": {"status": "OFFERED", "offered_at": datetime.utcnow().isoformat()}}
    _save_chat_session(profile, messages, db, current_user.id)
    db.commit()
    return response


@app.get("/api/v1/evidence/{evidence_id}/download")
def download_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    record = (
        db.query(EvidenceFileModel)
        .join(CaseModel, CaseModel.id == EvidenceFileModel.case_id)
        .filter(
            EvidenceFileModel.id == evidence_id,
            CaseModel.user_id == current_user.id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    evidence_root = os.path.abspath(os.path.join(settings.STORAGE_DIR, "evidence"))
    file_path = os.path.abspath(record.file_path)
    if os.path.commonpath([evidence_root, file_path]) != evidence_root or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Evidence file not found")
    media_type = mimetypes.guess_type(record.file_name)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=record.file_name)


@app.get("/api/v1/dashboard")
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    case_ids = db.query(CaseModel.id).filter(CaseModel.user_id == current_user.id)
    total_cases = case_ids.count()
    active_cases = (
        db.query(func.count(CaseModel.id))
        .filter(
            CaseModel.user_id == current_user.id,
            CaseModel.status.notin_(["Resolved", "RESOLVED"]),
        )
        .scalar()
        or 0
    )
    document_count = (
        db.query(func.count(GeneratedDocumentModel.id))
        .join(CaseModel, CaseModel.id == GeneratedDocumentModel.case_id)
        .filter(CaseModel.user_id == current_user.id)
        .scalar()
        or 0
    )
    evidence_count = (
        db.query(func.count(EvidenceFileModel.id))
        .join(CaseModel, CaseModel.id == EvidenceFileModel.case_id)
        .filter(CaseModel.user_id == current_user.id)
        .scalar()
        or 0
    )
    return {
        "total_cases": total_cases,
        "active_cases": active_cases,
        "documents": document_count,
        "evidence_files": evidence_count,
    }
