import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from app.agents.conversation_agent import OFFICIAL_SOURCES, ConversationalLegalAgent, conversational_agent
from app.agents.rag_node import statutory_rag
from app.config import settings
from app.llm.contracts import (
    CaseExtraction,
    DetectedAction,
    LLMExtractionContext,
    LLMProvider,
    LLMProviderError,
    LLMResponseContext,
)
from app.llm.factory import get_llm_provider
from app.schemas.chat import (
    ChatMessage,
    ChatTurnRequest,
    ChatTurnResponse,
    DocumentUploadExtractionRequest,
    StructuredCaseProfile,
)
from app.services.document_registry import select_document_for_workflow


logger = logging.getLogger("uvicorn.error")


DIRECT_PROFILE_FIELDS = {
    "user_name",
    "user_city",
    "user_state",
    "opposite_party_name",
    "opposite_party_address",
    "property_address",
    "disputed_amount",
    "incident_date",
    "vacating_date",
    "unpaid_months",
    "transaction_id",
    "bank_name",
    "police_station_name",
}

EVIDENCE_FROM_FACT = {
    "rental_agreement_available": "rental_agreement",
    "deposit_payment_proof_available": "deposit_payment_proof",
    "invoice_available": "invoice",
    "employment_proof_available": "offer_letter",
    "written_complaint_available": "complaint_copy",
}

ESCALATION_STAGE = {
    "HOUSING_TENANT": "RENT_AUTHORITY_ESCALATION",
    "EMPLOYMENT": "LABOUR_COMMISSIONER_COMPLAINT",
    "CONSUMER": "EDAAKHIL_COMPLAINT",
    "CYBER_FRAUD": "POLICE_FIR_ESCALATION",
    "POLICE_COMPLAINT": "SP_ESCALATION",
}

WAITING_STAGE = {
    "HOUSING_TENANT": "AWAITING_LANDLORD_RESPONSE",
    "EMPLOYMENT": "AWAITING_EMPLOYER_RESPONSE",
    "CONSUMER": "AWAITING_SELLER_RESPONSE",
}


class GeminiConversationService:
    """Gemini understands and writes; deterministic code owns critical case state."""

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        workflow_agent: Optional[ConversationalLegalAgent] = None,
    ):
        self.provider = provider or get_llm_provider()
        self.workflow_agent = workflow_agent or conversational_agent

    async def process_turn(
        self,
        req: ChatTurnRequest,
        existing_profile: Optional[StructuredCaseProfile],
        recent_messages: Iterable[ChatMessage],
    ) -> ChatTurnResponse:
        if not self.provider.status.configured:
            fallback = self.workflow_agent.process_turn(req, existing_profile)
            fallback.reply_text = self._limited_demo_prefix() + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

        history = self._recent_history(recent_messages)
        profile = existing_profile

        try:
            if profile and self._is_document_request(req.message):
                profile.key_facts["document_intake_active"] = True
            if profile and (
                profile.key_facts.get("pending_conflict")
                or profile.key_facts.get("pending_document_extraction")
            ):
                # Explicit confirmation commands are applied by the deterministic
                # controller. Gemini still receives the resulting updated state.
                self._apply_pending_confirmation(req.message.strip(), profile)

            extraction = await self.provider.extract_case_updates(
                LLMExtractionContext(
                    user_message=req.message,
                    recent_messages=history,
                    case_summary=self._compact_case(profile) if profile else None,
                )
            )

            if profile is None:
                profile = self.workflow_agent._init_case_profile(
                    req.message,
                    req.case_id,
                    category_override=extraction.classification.category,
                )
                profile.issue_type = extraction.classification.issue_type

            conflict = self._apply_extraction(profile, extraction)
            self._apply_actions(profile, extraction.actions_detected)
            self.workflow_agent._mark_evidence(profile, extraction.evidence_detected)
            self.workflow_agent._assess_risk(req.message, profile)
            self._refresh_workflow(profile)

            legal_sources = self._verified_sources(profile, req.message)
            missing_for_response = list(profile.missing_required_fields)
            if profile.key_facts.get("document_intake_active"):
                missing_for_response.extend(
                    f"document:{field}" for field in profile.missing_document_fields
                )
            reply = await self.provider.chat(
                LLMResponseContext(
                    user_message=req.message,
                    recent_messages=history,
                    case_summary=self._compact_case(profile),
                    workflow=self._workflow_summary(profile),
                    missing_information=missing_for_response,
                    legal_sources=legal_sources,
                    language_style=extraction.language_style,
                    conflict=conflict,
                )
            )
            self.workflow_agent._touch(profile)
            suggested_action = profile.recommended_next_action
            response = ChatTurnResponse(
                reply_text=reply,
                case_profile=profile,
                quick_replies=self._quick_replies(profile, conflict),
                suggested_action=suggested_action,
                message_id=str(uuid.uuid4()),
            )
            return self._tag_response(response, "gemini")
        except LLMProviderError:
            logger.warning(
                "provider=%s model=%s event=turn_fell_back_to_limited_demo",
                self.provider.status.provider,
                self.provider.status.model,
            )
            if profile is None:
                fallback = self.workflow_agent.process_turn(req, existing_profile)
                fallback.reply_text = self._temporary_failure_prefix() + fallback.reply_text
                return self._tag_response(fallback, "limited_demo")

            self._refresh_workflow(profile)
            response = ChatTurnResponse(
                reply_text=self._temporary_failure_prefix() + self._safe_next_prompt(profile),
                case_profile=profile,
                quick_replies=["Try Gemini again"],
                suggested_action=profile.recommended_next_action,
                message_id=str(uuid.uuid4()),
            )
            return self._tag_response(response, "limited_demo")

    async def process_document_upload(
        self,
        req: DocumentUploadExtractionRequest,
        profile: StructuredCaseProfile,
    ) -> ChatTurnResponse:
        content = (req.simulated_content or "").strip()
        if not self.provider.status.configured or not content:
            fallback = self.workflow_agent.process_document_upload(req, profile)
            fallback.reply_text = self._limited_demo_prefix() + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

        try:
            analysis = await self.provider.analyze_document(content, req.doc_type)
            metadata_response = self.workflow_agent.process_document_upload(
                req.model_copy(update={"simulated_content": None}),
                profile,
            )
            candidate_facts: dict[str, Any] = {}
            confidence_by_field = {
                item.field: item.confidence for item in analysis.confidence_by_field
            }
            for field, value in analysis.facts.model_dump(exclude_none=True).items():
                confidence = confidence_by_field.get(field, analysis.confidence)
                if confidence >= 0.55:
                    candidate_facts[field] = value
            if analysis.outcome != "UNCLEAR":
                candidate_facts["response_outcome"] = analysis.outcome
            if analysis.explicit_deadlines:
                candidate_facts["response_deadline_text"] = analysis.explicit_deadlines[0]

            if candidate_facts:
                profile.key_facts["pending_document_extraction"] = {
                    "file_name": req.file_name,
                    "facts": candidate_facts,
                    "analysis_summary": analysis.summary,
                    "source": "gemini_document",
                }
                lines = "\n".join(
                    f"• {field.replace('_', ' ').title()}: {value}"
                    for field, value in candidate_facts.items()
                )
                metadata_response.reply_text = (
                    f"Gemini analyzed {req.file_name} and found these candidate details:\n{lines}\n\n"
                    "Please confirm them before I add them to the case profile. Document extraction can be wrong."
                )
                metadata_response.quick_replies = ["Details are correct", "I need to correct them"]
            else:
                metadata_response.reply_text = (
                    f"Gemini analyzed {req.file_name}, but did not find facts reliable enough to add. "
                    "The file is still attached to the evidence checklist."
                )
            self.workflow_agent._touch(profile)
            return self._tag_response(metadata_response, "gemini")
        except LLMProviderError:
            fallback = self.workflow_agent.process_document_upload(req, profile)
            fallback.reply_text = self._temporary_failure_prefix() + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

    def _apply_extraction(
        self,
        profile: StructuredCaseProfile,
        extraction: CaseExtraction,
    ) -> Optional[dict[str, Any]]:
        facts = extraction.facts.model_dump(exclude_none=True)
        confidence_by_field = {
            item.field: item.confidence for item in extraction.confidence_by_field
        }
        conflict: Optional[dict[str, Any]] = None
        for field, candidate in facts.items():
            confidence = confidence_by_field.get(field, extraction.classification.confidence)
            if confidence < 0.55:
                continue

            target = profile if field in DIRECT_PROFILE_FIELDS else profile.key_facts
            existing = getattr(profile, field) if target is profile else profile.key_facts.get(field)
            metadata = profile.fact_metadata.get(field, {})
            existing_is_empty = (
                existing is None
                or existing == ""
                or existing == []
                or (isinstance(existing, (int, float)) and not isinstance(existing, bool) and existing == 0)
            )

            if metadata.get("confirmed") and not self._same_value(existing, candidate):
                continue
            if not existing_is_empty and not self._same_value(existing, candidate):
                if conflict is None:
                    conflict = {
                        "field": field,
                        "existing": existing,
                        "candidate": candidate,
                        "source": "gemini_chat",
                    }
                    profile.key_facts["pending_conflict"] = conflict
                continue

            if target is profile:
                setattr(profile, field, candidate)
            else:
                profile.key_facts[field] = candidate
            profile.fact_metadata[field] = {
                "value": candidate,
                "source": "gemini_chat",
                "confidence": confidence,
                "confirmed": False,
            }
            evidence_id = EVIDENCE_FROM_FACT.get(field)
            if candidate is True and evidence_id:
                self.workflow_agent._mark_evidence(profile, [evidence_id])
        return conflict

    def _apply_actions(self, profile: StructuredCaseProfile, actions: list[DetectedAction]) -> None:
        for action in actions:
            if not action.completed or action.confidence < 0.75:
                continue
            action_type = action.type
            label = action_type.replace("_", " ").title()
            if action_type == "formal_demand_sent":
                stage = WAITING_STAGE.get(profile.category)
                if stage:
                    self.workflow_agent._set_journey_current(profile, stage, complete_through=True)
            elif action_type == "response_rejected":
                stage = ESCALATION_STAGE.get(profile.category)
                if stage:
                    self.workflow_agent._set_journey_current(profile, stage, complete_through=True)
            elif action_type == "case_resolved":
                profile.current_stage_key = "RESOLVED"
                profile.current_stage_label = "Resolved"
                for stage in profile.legal_journey:
                    stage.status = "COMPLETED"
                    stage.is_current = False
            elif action_type == "bank_reported" and profile.category == "CYBER_FRAUD":
                self.workflow_agent._set_journey_current(profile, "BANK_REPORTED", complete_through=True)
                profile.key_facts["bank_reported"] = True
            elif action_type == "cybercrime_reported" and profile.category == "CYBER_FRAUD":
                self.workflow_agent._set_journey_current(profile, "CYBERCRIME_PORTAL_FILED", complete_through=True)
                profile.key_facts["cyber_reported"] = True
            elif action_type == "police_complaint_submitted" and profile.category == "POLICE_COMPLAINT":
                self.workflow_agent._set_journey_current(profile, "FORMAL_WRITTEN_COMPLAINT", complete_through=True)
                profile.key_facts["written_complaint_available"] = True
            self.workflow_agent._add_action(profile, action_type, label)

    def _apply_pending_confirmation(self, text: str, profile: StructuredCaseProfile) -> None:
        lowered = text.casefold().strip()
        pending_conflict = profile.key_facts.get("pending_conflict")
        if pending_conflict and (lowered.startswith("use ") or lowered.startswith("keep ")):
            field = pending_conflict.get("field")
            if field and lowered.startswith("use "):
                candidate = pending_conflict.get("candidate")
                if field in DIRECT_PROFILE_FIELDS:
                    setattr(profile, field, candidate)
                else:
                    profile.key_facts[field] = candidate
                profile.fact_metadata[field] = {
                    "value": candidate,
                    "source": "user_conflict_confirmation",
                    "confidence": 1.0,
                    "confirmed": True,
                }
            elif field:
                profile.fact_metadata.setdefault(field, {})["confirmed"] = True
            profile.key_facts.pop("pending_conflict", None)

        if profile.key_facts.get("pending_document_extraction"):
            self.workflow_agent._check_conversation_actions(text, profile)

    def _refresh_workflow(self, profile: StructuredCaseProfile) -> None:
        workflow = self.workflow_agent.workflows.get(profile.category, {})
        profile.missing_required_fields = self.workflow_agent._compute_missing_fields(profile)
        profile.recommended_doc_type = select_document_for_workflow(
            profile.category, profile.current_stage_key
        )
        profile.recommended_doc_label = workflow.get("default_doc_label", "Prepare a factual written record")
        profile.missing_document_fields = self._missing_document_fields(profile)
        core_ready = not profile.missing_required_fields and profile.risk_level != "RED"
        profile.is_ready_for_document = core_ready and not profile.missing_document_fields
        if profile.is_ready_for_document:
            profile.key_facts.pop("document_intake_active", None)
        if core_ready:
            profile.recommended_next_action = {
                "type": "PREPARE_DOC",
                "doc_type": profile.recommended_doc_type,
                "label": profile.recommended_doc_label,
            }
        else:
            profile.recommended_next_action = None

    def _verified_sources(self, profile: StructuredCaseProfile, narrative: str) -> list[dict[str, Any]]:
        rag_category = "TENANCY" if profile.category == "HOUSING_TENANT" else profile.category
        citations = [
            citation.model_dump(mode="json")
            for citation in statutory_rag.retrieve_applicable_sections(rag_category, narrative)[:4]
        ]
        seen_urls = {item.get("source_url") for item in citations}
        for source in OFFICIAL_SOURCES.get(profile.category, []):
            if source.get("url") not in seen_urls:
                citations.append(source)
        return citations

    def _compact_case(self, profile: Optional[StructuredCaseProfile]) -> Optional[dict[str, Any]]:
        if profile is None:
            return None
        return {
            "case_id": profile.case_id,
            "category": profile.category,
            "issue_type": profile.issue_type,
            "current_stage": {
                "key": profile.current_stage_key,
                "label": profile.current_stage_label,
            },
            "facts": {
                "user_name": profile.user_name,
                "user_city": profile.user_city,
                "user_state": profile.user_state,
                "opposite_party_name": profile.opposite_party_name,
                "opposite_party_address": profile.opposite_party_address,
                "property_address": profile.property_address,
                "disputed_amount": profile.disputed_amount or None,
                "incident_date": profile.incident_date,
                "vacating_date": profile.vacating_date,
                "unpaid_months": profile.unpaid_months,
                "transaction_id": profile.transaction_id,
                "bank_name": profile.bank_name,
                "police_station_name": profile.police_station_name,
                **{
                    key: value
                    for key, value in profile.key_facts.items()
                    if key not in {"last_question_group", "last_upload"}
                },
            },
            "evidence_available": [item.id for item in profile.evidence_checklist if item.is_available],
            "actions_completed": profile.actions_completed,
            "risk_level": profile.risk_level,
            "safety_notice": profile.safety_notice,
            "missing_document_fields": profile.missing_document_fields,
            "document_intake_active": bool(profile.key_facts.get("document_intake_active")),
        }

    @staticmethod
    def _workflow_summary(profile: StructuredCaseProfile) -> dict[str, Any]:
        return {
            "current_stage_key": profile.current_stage_key,
            "current_stage_label": profile.current_stage_label,
            "journey": [
                {"id": stage.id, "title": stage.title, "status": stage.status}
                for stage in profile.legal_journey
            ],
            "ready_for_document": profile.is_ready_for_document,
            "missing_document_fields": profile.missing_document_fields,
            "recommended_document": profile.recommended_doc_type,
            "recommended_next_action": profile.recommended_next_action,
        }

    @staticmethod
    def _same_value(first: Any, second: Any) -> bool:
        if isinstance(first, str) and isinstance(second, str):
            return first.strip().casefold() == second.strip().casefold()
        return first == second

    @staticmethod
    def _quick_replies(
        profile: StructuredCaseProfile,
        conflict: Optional[dict[str, Any]],
    ) -> list[str]:
        if conflict:
            existing = conflict["existing"]
            candidate = conflict["candidate"]
            return [f"Keep {existing}", f"Use {candidate}"]
        if profile.current_stage_key == "RESOLVED":
            return []
        if profile.is_ready_for_document:
            return [profile.recommended_doc_label or "Prepare document", "Review my evidence"]
        return []

    @staticmethod
    def _safe_next_prompt(profile: StructuredCaseProfile) -> str:
        missing = profile.missing_required_fields or profile.missing_document_fields
        if missing:
            labels = ", ".join(field.replace("_", " ") for field in missing[:4])
            return f"I kept your case progress. You can continue by sharing: {labels}."
        return "I kept your case progress. Please try the Gemini response again in a moment."

    def _recent_history(self, messages: Iterable[ChatMessage]) -> list[dict[str, str]]:
        values = list(messages)[-settings.LLM_RECENT_MESSAGE_LIMIT :]
        return [
            {"role": "assistant" if item.sender == "bot" else item.sender, "content": item.text}
            for item in values
            if item.sender in {"user", "bot"}
        ]

    def _tag_response(self, response: ChatTurnResponse, mode: str) -> ChatTurnResponse:
        response.llm_provider = self.provider.status.provider
        response.llm_model = self.provider.status.model
        response.llm_mode = mode
        return response

    @staticmethod
    def _is_document_request(message: str) -> bool:
        lowered = message.casefold()
        return any(term in lowered for term in ["prepare", "draft", "create the letter", "make the letter"])

    @staticmethod
    def _missing_document_fields(profile: StructuredCaseProfile) -> list[str]:
        fields: list[tuple[str, Any]] = [
            ("user_name", profile.user_name),
            ("user_city", profile.user_city),
        ]
        if profile.category == "HOUSING_TENANT":
            fields.extend(
                [
                    ("opposite_party_name", profile.opposite_party_name),
                    ("property_address", profile.property_address),
                    ("disputed_amount", profile.disputed_amount),
                    ("vacating_date", profile.vacating_date),
                ]
            )
        elif profile.category == "EMPLOYMENT":
            fields.extend(
                [
                    ("opposite_party_name", profile.opposite_party_name),
                    ("disputed_amount", profile.disputed_amount),
                ]
            )
        elif profile.category == "CONSUMER":
            fields.extend(
                [
                    ("opposite_party_name", profile.opposite_party_name),
                    ("disputed_amount", profile.disputed_amount),
                ]
            )
        elif profile.category == "CYBER_FRAUD":
            fields.extend(
                [
                    ("bank_name", profile.bank_name),
                    ("disputed_amount", profile.disputed_amount),
                    ("incident_date", profile.incident_date),
                    ("transaction_id", profile.transaction_id),
                ]
            )
        elif profile.category == "POLICE_COMPLAINT":
            fields.append(("police_station_name", profile.police_station_name))
        return [name for name, value in fields if value in (None, "", 0, 0.0, [])]

    @staticmethod
    def _limited_demo_prefix() -> str:
        return "Limited demo mode — Gemini is not configured, so this reply uses local workflow rules only.\n\n"

    @staticmethod
    def _temporary_failure_prefix() -> str:
        return "Gemini is temporarily unavailable. I have switched this turn to limited demo mode and preserved your case progress.\n\n"


gemini_conversation_service = GeminiConversationService()
