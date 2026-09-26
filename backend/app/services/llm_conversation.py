import logging
import re
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from app.agents.conversation_agent import ConversationalLegalAgent, conversational_agent
from app.agents.rag_node import RagQueryContext, statutory_rag
from app.domains import domain_registry
from app.domains.compatibility import PROFILE_FACT_FIELDS, read_profile_fact
from app.domains.contracts import FactState, FactValueType, fact_state
from app.services.language_style import LanguageScript
from app.services.pii_masker import PIIMasker
from app.services.case_readiness import (
    READY_FOR_DOCUMENT,
    compute_blocking_missing_facts,
    compute_intake_missing_facts,
    compute_readiness,
    document_routing_allowed,
)
from app.services.safety_triage import (
    AMBER,
    RED,
    SafetyAssessment,
    assess_safety,
    extract_safety_facts,
    simple_yes_no,
)
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
from app.services.document_registry import DOCUMENT_DEFINITIONS, select_document_for_workflow
from app.services.document_generation import assess_document_generation, resolve_requested_document
from app.services.professional_help import SIGNAL_FACT_KEYS, asks_about_legal_help, evaluate_professional_help
from app.schemas.professional_help import ProfessionalHelpLevel
from app.services.pending_interaction import (
    PendingResolution, document_offer, fact_candidate, is_bare_confirmation,
    resolve_reply, valid_for_case,
)


logger = logging.getLogger("uvicorn.error")


DIRECT_PROFILE_FIELDS = PROFILE_FACT_FIELDS

# Retrieval-relevant, non-identifying descriptors. Anything not listed here is
# excluded from the RAG query, which is why names, addresses, transaction ids,
# bank details and exact amounts can never reach the retrieval layer.
RAG_FACT_ALLOWLIST = frozenset(
    {
        item.id
        for domain in domain_registry.all()
        for item in (*domain.evidence, *domain.actions)
    }
    | {"informal_request_made", "response_accepted"}
)

# Turns that answer a question without restating the grievance.
LOW_CONTEXT_PHRASES = frozenset({
    "yes", "no", "yeah", "yep", "nope", "both", "ok", "okay", "sure", "done",
    "correct", "right", "yes both", "no not yet", "not yet", "i did", "i have",
    "i do", "yes i did", "yes i have", "yesterday", "today", "last week",
    "this week", "last month", "continue my case", "yes please",
})

DATE_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "yesterday", "today",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
})

# At or below this word count a turn is treated as confirmation, not substance.
LOW_CONTEXT_MAX_WORDS = 3

class GeminiConversationService:
    """Gemini/Groq understands and writes; deterministic code owns critical case state."""

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        workflow_agent: Optional[ConversationalLegalAgent] = None,
    ):
        self.provider = provider or get_llm_provider()
        self.workflow_agent = workflow_agent or conversational_agent

    @property
    def _provider_title(self) -> str:
        return self.provider.status.provider.title()

    @property
    def _provider_name(self) -> str:
        return self.provider.status.provider.lower()

    @property
    def _provider_mode(self) -> str:
        return self.provider.status.mode

    async def process_turn(
        self,
        req: ChatTurnRequest,
        existing_profile: Optional[StructuredCaseProfile],
        recent_messages: Iterable[ChatMessage],
        user_context: Optional[dict[str, Any]] = None,
        evidence_context: Optional[dict[str, Any]] = None,
    ) -> ChatTurnResponse:
        history = self._recent_history(recent_messages)

        # Deterministic language/script and safety routing run before provider
        # availability, extraction/classification, workflow, RAG, or documents.
        # An unresolved safety case returns from this branch without an LLM call.
        prior_language = existing_profile.language_style if existing_profile else None
        prior_script = existing_profile.script_style if existing_profile else None
        prior_safety = existing_profile.safety_status if existing_profile else None
        prior_text = " ".join(item.get("content", "") for item in history if item.get("role") == "user")
        safety = assess_safety(
            req.message,
            prior_text,
            prior_safety=prior_safety,
            prior_language=prior_language,
            prior_script=prior_script,
            prior_question_group=(
                existing_profile.key_facts.get("last_safety_question_group")
                if existing_profile
                else None
            ),
        )
        style = LanguageScript(safety.language_style, safety.script_style)

        if self._safety_route_required(safety, existing_profile):
            if existing_profile:
                existing_profile.pending_interaction = None
            return self._process_safety_turn(req, existing_profile, safety, style)

        if (existing_profile and existing_profile.document_request
                and existing_profile.document_request.get("document_type")
                and existing_profile.document_request.get("status") in {"OPTIONAL_FIELDS_AVAILABLE", "NEEDS_REQUIRED_FIELDS", "READY_TO_GENERATE"}
                and self._is_optional_skip(req.message)):
            doc_type = existing_profile.document_request["document_type"]
            response = self._document_request_response(existing_profile, req.message, style, hint=doc_type)
            if existing_profile.document_request:
                existing_profile.document_request["optional_skipped"] = True
            self.workflow_agent._touch(existing_profile)
            return self._tag_response(response, self._provider_mode)

        pending_document_request = bool(existing_profile and existing_profile.document_request and existing_profile.document_request.get("status") == "SAFETY_PAUSED")
        previous_help_level = existing_profile.professional_help.level if existing_profile and existing_profile.professional_help else None
        if not self.provider.status.configured:
            if existing_profile:
                existing_profile.pending_interaction = None
            fallback = self.workflow_agent.process_turn(req, existing_profile)
            fallback.case_profile.language_style = style.language
            fallback.case_profile.script_style = style.script
            self._refresh_workflow(fallback.case_profile, safety, req.message)
            fallback.case_profile.professional_help = evaluate_professional_help(fallback.case_profile)
            # The legacy workflow response is composed before readiness is
            # recomputed. Keep the response envelope in sync when the refresh
            # correctly removes a premature document action.
            fallback.suggested_action = fallback.case_profile.recommended_next_action
            if fallback.suggested_action is None:
                fallback.quick_replies = []
            prefix = self._limited_demo_prefix(style)
            if self._is_document_handoff_request(req.message) or pending_document_request:
                handoff = self._document_request_response(
                    fallback.case_profile, req.message, style,
                    hint=(existing_profile.document_request.get("document_type") if pending_document_request else None),
                )
                fallback.reply_text = handoff.reply_text
                fallback.suggested_action = handoff.suggested_action
            else:
                fallback.reply_text = (
                    self._fallback_professional_help(fallback.case_profile, style)
                    if asks_about_legal_help(req.message)
                    else self._localized_fallback_reply(fallback.case_profile, fallback.reply_text, style)
                )
            fallback.reply_text = prefix + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

        profile = existing_profile
        pending = profile.pending_interaction if profile else None
        if profile and pending and (
            not valid_for_case(pending, profile)
            or (req.case_id not in {None, "new", "default-new", profile.case_id})
        ):
            profile.pending_interaction = None
            pending = None
        resolution = resolve_reply(pending, req.message) if pending else PendingResolution()

        try:
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
                    pending_interaction=pending,
                    language_style=style.language,
                    script_style=style.script,
                    domain_catalog=domain_registry.extraction_catalog(),
                    document_catalog=[{"id": item.id, "name": item.name} for item in DOCUMENT_DEFINITIONS.values()],
                )
            )

            if profile is None:
                profile = self.workflow_agent._init_case_profile(
                    req.message,
                    req.case_id,
                    category_override=extraction.classification.category,
                )
                domain = domain_registry.resolve(extraction.classification.category)
                profile.issue_type = domain.normalize_issue_type(extraction.classification.issue_type)

            profile.language_style = style.language
            profile.script_style = style.script

            # A short answer is meaningful only against its validated case-scoped
            # referent. The same extraction call still handles any other facts.
            if is_bare_confirmation(req.message):
                # An elliptical reply supplies no standalone facts. Preserve only
                # the validated referent, if one exists; never trust extra fields
                # or action/document guesses from a bare confirmation.
                anchored = resolution.values if pending and resolution.status == "RESOLVED" else {}
                extraction.facts = extraction.facts.__class__.model_validate(anchored)
                extraction.confidence_by_field = []
                extraction.actions_detected = []
                extraction.evidence_detected = []
                extraction.document_request = None
            elif pending and resolution.status == "RESOLVED" and resolution.values:
                facts = extraction.facts.model_dump(exclude_none=True)
                facts.update(resolution.values)
                extraction.facts = extraction.facts.__class__.model_validate(facts)
                extraction.confidence_by_field = [
                    item for item in extraction.confidence_by_field
                    if item.field not in resolution.values
                ]
            elif pending and resolution.status == "AMBIGUOUS":
                facts = extraction.facts.model_dump(exclude_none=True)
                for key in pending.target_keys:
                    facts.pop(key, None)
                extraction.facts = extraction.facts.__class__.model_validate(facts)
                extraction.document_request = None

            conflict = self._apply_extraction(profile, extraction)
            self._apply_actions(profile, extraction.actions_detected)
            self.workflow_agent._mark_evidence(profile, extraction.evidence_detected)
            self.workflow_agent._assess_risk(req.message, profile)
            self._refresh_workflow(profile, safety, req.message)
            profile.professional_help = evaluate_professional_help(profile)
            if pending and resolution.status == "RESOLVED":
                profile.pending_interaction = None
            elif pending and resolution.status == "UNRELATED":
                extracted_facts = extraction.facts.model_dump(exclude_none=True)
                target_answered = any(key in extracted_facts for key in pending.target_keys)
                other_update = (
                    any(key not in pending.target_keys for key in extracted_facts)
                    or bool(extraction.actions_detected or extraction.evidence_detected or extraction.document_request)
                    or (len(req.message.split()) >= 4 and extraction.classification.category != profile.category)
                )
                if target_answered or other_update or len(req.message.split()) > 4:
                    profile.pending_interaction = None
            help_question = asks_about_legal_help(req.message)
            help_level = profile.professional_help.level
            help_should_surface = help_question or (
                help_level in {ProfessionalHelpLevel.LEGAL_HELP_RECOMMENDED, ProfessionalHelpLevel.URGENT_LEGAL_HELP}
                and help_level != previous_help_level
            )

            if not conflict and (resolution.document_type or self._is_document_handoff_request(req.message) or extraction.document_request or pending_document_request):
                hint = resolution.document_type or extraction.document_request or (existing_profile.document_request.get("document_type") if pending_document_request else None)
                profile.pending_interaction = None
                self.workflow_agent._touch(profile)
                return self._tag_response(self._document_request_response(profile, req.message, style, hint=hint), self._provider_mode)

            workflow_state = self._workflow_summary(profile)
            legal_sources = self._verified_sources(profile, req.message, workflow_state)
            domain_context = domain_registry.compact_context(profile)
            # Expose at most one ordinary follow-up target to the model. This
            # makes the backend's pending referent match the question it asks.
            domain_context["next_fact_candidates"] = domain_context["next_fact_candidates"][:1]
            if resolution.status == "AMBIGUOUS":
                domain_context["next_fact_candidates"] = []
            # The workspace reuses citations already selected for this answer.
            # Generic domain source links are not case-specific provisions.
            profile.legal_sources = [
                source for source in legal_sources
                if source.get("act") and source.get("section")
            ]
            # Ordinary follow-ups come only from the ranked domain context.
            # Document fields are supplied separately after the user selects a document.
            missing_for_response: list[str] = []
            if profile.missing_document_fields and profile.key_facts.get("document_intake_active"):
                missing_for_response.extend(
                    f"document:{field}" for field in profile.missing_document_fields
                )
            reply = await self.provider.chat(
                LLMResponseContext(
                    user_message=req.message,
                    # A durable-memory/history answer must not mistake recent
                    # conversational text for a saved memory or prior case.
                    recent_messages=([] if (user_context or {}).get("intent") in {
                        "MEMORY_RECALL", "CASE_HISTORY_LOOKUP"
                    } else history),
                    case_summary=(
                        self._current_case_reference(profile)
                        if (user_context or {}).get("intent") in {"MEMORY_RECALL", "CASE_HISTORY_LOOKUP"}
                        else self._compact_case(profile)
                    ),
                    workflow=workflow_state,
                    missing_information=missing_for_response,
                    legal_sources=legal_sources,
                    language_style=style.language,
                    script_style=style.script,
                    conflict=conflict,
                    safety=safety.to_dict() if safety.is_safety_case else None,
                    readiness=profile.readiness,
                    domain_context=domain_context,
                    professional_help=profile.professional_help,
                    professional_help_should_surface=help_should_surface,
                    professional_help_question=help_question,
                    user_context=user_context or {},
                    evidence=evidence_context or {},
                    pending_resolution=(
                        {"status": resolution.status, "target_keys": pending.target_keys,
                         "allowed_choices": pending.allowed_choices}
                        if pending and resolution.status != "UNRELATED" else None
                    ),
                )
            )
            if resolution.status != "AMBIGUOUS":
                offer = document_offer(profile)
                doc_label = profile.recommended_doc_label or ""
                declined_offer = bool(
                    pending and pending.type == "DOCUMENT_CONFIRMATION"
                    and resolution.status == "RESOLVED" and not resolution.document_type
                )
                # The UI exposes the supported PREPARE_DOC action. If the reply
                # asks a question, distinguish an explicit document offer from
                # an ordinary fact question, without deriving the document ID
                # from model prose.
                document_question = bool(re.search(
                    r"\b(?:prepare|draft|create|make)\b.*\b(?:notice|letter|complaint|document)\b",
                    reply.casefold(),
                ))
                if offer and not declined_offer and (
                    "?" not in reply or (doc_label and doc_label.casefold() in reply.casefold())
                    or document_question
                ):
                    profile.pending_interaction = offer
                elif "?" in reply and domain_context["next_fact_candidates"]:
                    profile.pending_interaction = fact_candidate(profile, domain_context["next_fact_candidates"][0])
                else:
                    profile.pending_interaction = None
            self.workflow_agent._touch(profile)
            suggested_action = profile.recommended_next_action
            response = ChatTurnResponse(
                reply_text=reply,
                case_profile=profile,
                quick_replies=self._quick_replies(profile, conflict),
                suggested_action=suggested_action,
                message_id=str(uuid.uuid4()),
            )
            return self._tag_response(response, self._provider_mode)
        except LLMProviderError:
            logger.warning(
                "provider=%s model=%s event=turn_fell_back_to_limited_demo",
                self.provider.status.provider,
                self.provider.status.model,
            )
            if profile is None:
                fallback = self.workflow_agent.process_turn(req, existing_profile)
                fallback.case_profile.professional_help = evaluate_professional_help(fallback.case_profile)
                fallback.reply_text = self._temporary_failure_prefix() + (
                    self._fallback_professional_help(fallback.case_profile, style)
                    if asks_about_legal_help(req.message) else fallback.reply_text
                )
                return self._tag_response(fallback, "limited_demo")

            self._refresh_workflow(profile)
            profile.professional_help = evaluate_professional_help(profile)
            response = ChatTurnResponse(
                reply_text=self._temporary_failure_prefix() + (
                    self._fallback_professional_help(profile, style)
                    if asks_about_legal_help(req.message) else self._safe_next_prompt(profile)
                ),
                case_profile=profile,
                quick_replies=[f"Try {self._provider_title} again"],
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
                    "source": f"{self._provider_name}_document",
                }
                lines = "\n".join(
                    f"• {field.replace('_', ' ').title()}: {value}"
                    for field, value in candidate_facts.items()
                )
                metadata_response.reply_text = (
                    f"{self._provider_title} analyzed {req.file_name} and found these candidate details:\n{lines}\n\n"
                    "Please confirm them before I add them to the case profile. Document extraction can be wrong."
                )
                metadata_response.quick_replies = ["Details are correct", "I need to correct them"]
            else:
                metadata_response.reply_text = (
                    f"{self._provider_title} analyzed {req.file_name}, but did not find facts reliable enough to add. "
                    "The file is still attached to the evidence checklist."
                )
            self.workflow_agent._touch(profile)
            return self._tag_response(metadata_response, self._provider_mode)
        except LLMProviderError:
            fallback = self.workflow_agent.process_document_upload(req, profile)
            fallback.reply_text = self._temporary_failure_prefix() + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

    @staticmethod
    def _safety_route_required(
        safety: SafetyAssessment,
        profile: Optional[StructuredCaseProfile],
    ) -> bool:
        """Keep unresolved or newly urgent safety turns out of the legal flow."""
        if not safety.is_safety_case:
            return False
        complete = bool(profile and profile.key_facts.get("safety_triage_complete"))
        return not complete or safety.immediate_danger is not False

    def _process_safety_turn(
        self,
        req: ChatTurnRequest,
        existing_profile: Optional[StructuredCaseProfile],
        safety: SafetyAssessment,
        style: LanguageScript,
    ) -> ChatTurnResponse:
        """Handle safety deterministically before any provider or RAG call."""
        sanitized_text, _ = PIIMasker.mask_text(req.message.strip())
        profile = existing_profile or self.workflow_agent._init_case_profile(
            sanitized_text,
            req.case_id,
            category_override="POLICE_COMPLAINT",
        )
        profile.language_style = style.language
        profile.script_style = style.script
        profile.risk_level = RED if safety.safety_level == RED else AMBER
        profile.safety_notice = safety.guidance
        if self._is_document_handoff_request(req.message):
            profile.document_request = {"intent": "USER_REQUESTED", "status": "SAFETY_PAUSED", "message": req.message}
        profile.key_facts["safety_context"] = safety.safety_context
        profile.key_facts["safety_contexts"] = list(safety.contexts)

        extracted = extract_safety_facts(sanitized_text, safety)
        extracted.update(self._contextual_safety_facts(sanitized_text, profile))
        for field, value in extracted.items():
            if field == "incident_date":
                profile.incident_date = str(value)
            else:
                profile.key_facts[field] = value
            profile.fact_metadata[field] = {
                "value": value,
                "source": "deterministic_safety_triage",
                "confidence": 1.0,
                "confirmed": True,
            }

        self._refresh_workflow(profile, safety, req.message)
        reply, quick_replies = self._paced_safety_reply(profile, safety, style)
        safety_state = safety.to_dict()
        safety_state["triage_complete"] = bool(profile.key_facts.get("safety_triage_complete"))
        profile.safety_status = safety_state
        profile.recommended_doc_type = None
        profile.recommended_doc_label = None
        profile.recommended_next_action = None
        profile.missing_document_fields = []
        profile.is_ready_for_document = False
        self.workflow_agent._touch(profile)
        return self._tag_response(
            ChatTurnResponse(
                reply_text=reply,
                case_profile=profile,
                quick_replies=quick_replies,
                suggested_action=None,
                message_id=str(uuid.uuid4()),
            ),
            "limited_demo",
        )

    @staticmethod
    def _contextual_safety_facts(
        message: str,
        profile: StructuredCaseProfile,
    ) -> dict[str, bool]:
        """Map short yes/no replies to the single safety fact just asked."""
        answer = simple_yes_no(message)
        if answer is None:
            return {}
        group = profile.key_facts.get("last_safety_question_group")
        if group == "repeated":
            return {"repeated_incidents": answer}
        if group == "violence_weapon":
            return {"physical_violence_or_weapon": answer}
        if group == "evidence":
            return {"evidence_available": answer}
        if group == "police":
            return {"police_contacted": answer}
        if group == "dependants":
            # The question asks whether the children are safe, whereas the
            # stored fact describes whether they are at risk.
            return {"dependants_at_risk": not answer}
        return {}

    def _paced_safety_reply(
        self,
        profile: StructuredCaseProfile,
        safety: SafetyAssessment,
        style: LanguageScript,
    ) -> tuple[str, list[str]]:
        """Return safety guidance plus at most one two-part follow-up."""
        if safety.immediate_danger is None:
            profile.key_facts["last_safety_question_group"] = "immediate_danger"
            parts = [safety.triage_question, safety.guidance]
            return "\n\n".join(part for part in parts if part), self._safety_quick_replies(safety)

        if safety.immediate_danger is True:
            profile.key_facts["last_safety_question_group"] = "urgent_safety"
            # Urgent instructions precede the follow-up question.
            parts = [safety.guidance, safety.triage_question]
            return "\n\n".join(part for part in parts if part), self._safety_quick_replies(safety)

        missing = set(profile.intake_missing_facts)
        prefix = safety.guidance or ""
        if "threat_details" in missing or "incident_date" in missing:
            profile.key_facts["last_safety_question_group"] = "incident"
            question = self._safety_text(style, "incident")
        elif "repeated_incidents" in missing:
            profile.key_facts["last_safety_question_group"] = "repeated"
            question = self._safety_text(style, "repeated")
        elif "physical_violence_or_weapon" in missing:
            profile.key_facts["last_safety_question_group"] = "violence_weapon"
            question = self._safety_text(style, "violence_weapon")
        elif safety.dependants_present and profile.key_facts.get("dependants_at_risk") is None:
            profile.key_facts["last_safety_question_group"] = "dependants"
            question = self._safety_text(style, "dependants")
        elif "evidence_available" in missing:
            profile.key_facts["last_safety_question_group"] = "evidence"
            question = self._safety_text(style, "evidence")
        elif "police_contacted" in missing:
            profile.key_facts["last_safety_question_group"] = "police"
            question = self._safety_text(style, "police")
        else:
            profile.key_facts["safety_triage_complete"] = True
            profile.key_facts.pop("last_safety_question_group", None)
            question = self._safety_text(style, "complete")
        return "\n\n".join(part for part in (prefix, question) if part), []

    @staticmethod
    def _safety_text(style: LanguageScript, key: str) -> str:
        values = {
            "english": {
                "incident": "What exactly happened, and when did it happen?",
                "repeated": "Has this happened before?",
                "violence_weapon": "Was any physical violence or weapon involved?",
                "dependants": "Are the children safe right now and with you or another trusted person?",
                "evidence": "Do you still have messages, recordings, CCTV, or witnesses?",
                "police": "Have you already contacted the police?",
                "complete": "Thank you. The immediate-safety check is complete. What legal option would you like help understanding next?",
            },
            "hinglish": {
                "incident": "Exactly kya hua tha, aur yeh kab hua?",
                "repeated": "Kya yeh pehle bhi hua hai?",
                "violence_weapon": "Kya physical violence hui thi ya koi weapon involved tha?",
                "dependants": "Kya bachche abhi safe hain aur aapke ya kisi bharosemand vyakti ke saath hain?",
                "evidence": "Kya aapke paas messages, recording, CCTV ya witness hain?",
                "police": "Kya aapne police se contact kiya hai?",
                "complete": "Thank you. Immediate-safety check complete hai. Ab aap kis legal option ko samajhna chahenge?",
            },
            "hindi": {
                "incident": "ठीक-ठीक क्या हुआ था, और यह कब हुआ?",
                "repeated": "क्या यह पहले भी हुआ है?",
                "violence_weapon": "क्या कोई शारीरिक हिंसा हुई थी या हथियार शामिल था?",
                "dependants": "क्या बच्चे अभी सुरक्षित हैं और आपके या किसी भरोसेमंद व्यक्ति के साथ हैं?",
                "evidence": "क्या आपके पास संदेश, रिकॉर्डिंग, सीसीटीवी या गवाह हैं?",
                "police": "क्या आपने पुलिस से संपर्क किया है?",
                "complete": "धन्यवाद। तत्काल सुरक्षा जाँच पूरी है। अब आप किस कानूनी विकल्प को समझना चाहेंगे?",
            },
        }
        language = "hindi" if style.script == "devanagari" else style.language
        return values.get(language, values["english"])[key]

    def _localized_fallback_reply(
        self,
        profile: StructuredCaseProfile,
        original: str,
        style: LanguageScript,
    ) -> str:
        """Mirror language/script in deterministic non-provider intake replies."""
        if profile.readiness == "PRE_INTAKE":
            if style.language == "english":
                return "Hello. Please briefly describe your legal problem and what happened."
            return (
                "Namaste. Apni legal problem simple words mein batayein—kya hua?"
                if style.script == "roman"
                else "नमस्ते। अपनी कानूनी समस्या सरल शब्दों में बताइए—क्या हुआ?"
            )
        if style.language == "english":
            return original
        if profile.category == "HOUSING_TENANT":
            return (
                "Main deposit issue samajhne mein madad karunga. Property kis state mein hai, aur aap kab wahan se nikle?"
                if style.script == "roman"
                else "मैं जमा राशि का मामला समझने में मदद करूँगा। संपत्ति किस राज्य में है, और आप वहाँ से कब निकले?"
            )
        if profile.category == "EMPLOYMENT":
            return (
                "Kaun se mahine ki salary pending hai, aur employer ne kya jawab diya?"
                if style.script == "roman"
                else "किन महीनों का वेतन बाकी है, और नियोक्ता ने क्या जवाब दिया?"
            )
        if profile.category == "CYBER_FRAUD":
            return (
                "Transaction kab hua, aur kya aapne bank ya 1930 par report kiya?"
                if style.script == "roman"
                else "लेन-देन कब हुआ, और क्या आपने बैंक या 1930 पर रिपोर्ट की?"
            )
        return (
            "Jo hua use thoda aur batayein, aur yeh kab hua?"
            if style.script == "roman"
            else "जो हुआ उसे थोड़ा और बताइए, और यह कब हुआ?"
        )

    @staticmethod
    def _fallback_professional_help(profile: StructuredCaseProfile, style: LanguageScript) -> str:
        """Conservative local answer when the configured provider is unavailable."""
        level = profile.professional_help.level if profile.professional_help else ProfessionalHelpLevel.SELF_HELP_REASONABLE
        if style.language == "hindi" and style.script == "devanagari":
            return {
                ProfessionalHelpLevel.SELF_HELP_REASONABLE: "अभी ज्ञात तथ्यों के आधार पर सामान्य अगले कदम खुद उठाना उचित लग सकता है। स्थिति बदले तो कानूनी सलाह पर फिर विचार करें।",
                ProfessionalHelpLevel.CONSIDER_LEGAL_HELP: "अभी ज्ञात तथ्यों के आधार पर किसी वकील या कानूनी सहायता सेवा से बात करना उपयोगी हो सकता है।",
                ProfessionalHelpLevel.LEGAL_HELP_RECOMMENDED: "अभी ज्ञात तथ्यों के आधार पर अगला महत्वपूर्ण कदम उठाने से पहले मामले के अनुसार कानूनी सलाह लेना उचित होगा।",
                ProfessionalHelpLevel.URGENT_LEGAL_HELP: "अभी ज्ञात कानूनी जोखिम के कारण जल्द कानूनी सलाह लेना महत्वपूर्ण है। तत्काल शारीरिक सुरक्षा पहले आती है।",
            }[level]
        if style.language == "hinglish":
            return {
                ProfessionalHelpLevel.SELF_HELP_REASONABLE: "Abhi jo facts pata hain, unke hisaab se normal next step khud lena reasonable lagta hai. Situation badle to legal advice phir consider karein.",
                ProfessionalHelpLevel.CONSIDER_LEGAL_HELP: "Abhi jo facts pata hain, unke hisaab se vakil ya legal aid se baat karna useful ho sakta hai.",
                ProfessionalHelpLevel.LEGAL_HELP_RECOMMENDED: "Abhi jo facts pata hain, unke hisaab se agla important step lene se pehle case-specific legal advice lena sensible hoga.",
                ProfessionalHelpLevel.URGENT_LEGAL_HELP: "Abhi jo legal risk pata hai, uske liye jaldi legal advice lena important hai. Immediate physical safety pehle aati hai.",
            }[level]
        return {
            ProfessionalHelpLevel.SELF_HELP_REASONABLE: "Based on what is currently known, taking the ordinary next step yourself appears reasonable at this stage. Reconsider legal advice if the situation changes.",
            ProfessionalHelpLevel.CONSIDER_LEGAL_HELP: "Based on what is currently known, speaking with an advocate or legal-aid service could be useful at this stage.",
            ProfessionalHelpLevel.LEGAL_HELP_RECOMMENDED: "Based on what is currently known, case-specific legal advice would be prudent before the next significant step.",
            ProfessionalHelpLevel.URGENT_LEGAL_HELP: "The currently known legal risk makes prompt legal advice important. Immediate physical safety still comes first.",
        }[level]

    def _apply_extraction(
        self,
        profile: StructuredCaseProfile,
        extraction: CaseExtraction,
    ) -> Optional[dict[str, Any]]:
        facts = extraction.facts.model_dump(exclude_none=True)
        domain = domain_registry.resolve(profile.category)
        definitions = {fact.key: fact for fact in domain.facts}
        alias_to_key = {
            alias: fact.key for fact in domain.facts for alias in fact.aliases
        }
        unavailable: set[str] = set()
        for field in profile.key_facts.get("unavailable_fact_keys") or ():
            definition = definitions.get(field)
            if definition is None or definition.value_type == FactValueType.BOOLEAN:
                continue
            present, value = read_profile_fact(profile, field, definition.aliases)
            if fact_state(value, present=present, definition=definition) == FactState.UNKNOWN:
                unavailable.add(field)
        for extracted_field in extraction.unavailable_facts:
            field = alias_to_key.get(extracted_field, extracted_field)
            definition = definitions.get(field)
            if definition is None or definition.value_type == FactValueType.BOOLEAN:
                continue
            present, value = read_profile_fact(profile, field, definition.aliases)
            if fact_state(value, present=present, definition=definition) == FactState.UNKNOWN:
                unavailable.add(field)
        confidence_by_field = {
            item.field: item.confidence for item in extraction.confidence_by_field
        }
        conflict: Optional[dict[str, Any]] = None
        for extracted_field, candidate in facts.items():
            field = alias_to_key.get(extracted_field, extracted_field)
            confidence = confidence_by_field.get(extracted_field, extraction.classification.confidence)
            if confidence < (0.80 if field in SIGNAL_FACT_KEYS else 0.55):
                continue
            if candidate is None or candidate == "" or candidate == []:
                continue

            target = profile if field in DIRECT_PROFILE_FIELDS else profile.key_facts
            definition = definitions.get(field)
            if definition:
                present, existing = read_profile_fact(profile, field, definition.aliases)
                if not present:
                    existing = None
            else:
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
            if not existing_is_empty and self._same_value(existing, candidate):
                unavailable.discard(field)
                continue
            if not existing_is_empty and not self._same_value(existing, candidate):
                if conflict is None:
                    conflict = {
                        "field": field,
                        "existing": existing,
                        "candidate": candidate,
                        "source": f"{self._provider_name}_chat",
                    }
                    profile.key_facts["pending_conflict"] = conflict
                continue

            if target is profile:
                setattr(profile, field, candidate)
            else:
                profile.key_facts[field] = candidate
            unavailable.discard(field)
            profile.fact_metadata[field] = {
                "value": candidate,
                "source": f"{self._provider_name}_chat",
                "confidence": confidence,
                "confirmed": False,
            }
            evidence_id = definition.evidence_type_id if definition else None
            if candidate is True and evidence_id:
                self.workflow_agent._mark_evidence(profile, [evidence_id])
        if unavailable:
            profile.key_facts["unavailable_fact_keys"] = sorted(unavailable)
        else:
            profile.key_facts.pop("unavailable_fact_keys", None)
        return conflict

    def _apply_actions(self, profile: StructuredCaseProfile, actions: list[DetectedAction]) -> None:
        domain = domain_registry.resolve(profile.category)
        for action in actions:
            if not action.completed or action.confidence < 0.75:
                continue
            action_type = action.type
            label = action_type.replace("_", " ").title()
            if action_type == "case_resolved":
                profile.current_stage_key = "RESOLVED"
                profile.current_stage_label = "Resolved"
                for stage in profile.legal_journey:
                    stage.status = "COMPLETED"
                    stage.is_current = False
            else:
                action_definition = domain.action(action_type)
                if action_definition and action_definition.target_workflow_stage:
                    self.workflow_agent._set_journey_current(
                        profile,
                        action_definition.target_workflow_stage,
                        complete_through=True,
                    )
                if action_definition:
                    for field, value in action_definition.fact_updates:
                        profile.key_facts[field] = value
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

    def _refresh_workflow(
        self,
        profile: StructuredCaseProfile,
        safety: Optional[SafetyAssessment] = None,
        message: str = "",
    ) -> None:
        """Recompute intake, readiness and - only if earned - document routing.

        Order matters: understand the issue, place the case on the readiness
        ladder, and consider a document last. Previously this method assigned a
        recommended document on every turn and computed the template's fields
        alongside intake, which is why a barely-understood case immediately
        asked for the name and city a template needed.
        """
        safety = safety or SafetyAssessment()
        workflow = self.workflow_agent._workflow_for_category(profile.category)

        if safety.has_safety_relevance:
            safety_state = safety.to_dict()
            if profile.key_facts.get("safety_triage_complete"):
                safety_state["triage_complete"] = True
            profile.safety_status = safety_state
        elif not profile.key_facts.get("safety_triage_complete"):
            profile.safety_status = None
        profile.intake_missing_facts = compute_intake_missing_facts(profile, safety)
        blocking_missing = compute_blocking_missing_facts(profile, safety, profile.intake_missing_facts)
        profile.readiness = compute_readiness(profile, safety, blocking_missing, message)

        # Kept for existing callers and persisted cases; it now mirrors intake
        # only, and no longer carries document-template requirements.
        profile.missing_required_fields = list(profile.intake_missing_facts)

        if not document_routing_allowed(profile, safety, profile.readiness):
            # Neither the UI nor the chat model should see a document candidate
            # before the readiness/safety gate permits a document action.
            profile.recommended_doc_type = None
            profile.recommended_doc_label = None
            profile.missing_document_fields = []
            profile.is_ready_for_document = False
            profile.recommended_next_action = None
            profile.key_facts.pop("document_intake_active", None)
            return

        candidate_doc_type = select_document_for_workflow(
            profile.category, profile.current_stage_key
        )
        candidate_doc_label = workflow.get("default_doc_label") or (
            DOCUMENT_DEFINITIONS.get(candidate_doc_type).name if candidate_doc_type and candidate_doc_type in DOCUMENT_DEFINITIONS else None
        )
        profile.recommended_doc_type = candidate_doc_type
        profile.recommended_doc_label = candidate_doc_label
        # Template fields are computed only now, once a document is warranted.
        profile.missing_document_fields = self._missing_document_fields(profile)
        profile.is_ready_for_document = not profile.missing_document_fields
        if profile.is_ready_for_document:
            profile.readiness = READY_FOR_DOCUMENT
            profile.key_facts.pop("document_intake_active", None)
        profile.recommended_next_action = {
            "type": "PREPARE_DOC",
            "doc_type": profile.recommended_doc_type,
            "label": profile.recommended_doc_label,
            "intent": "SYSTEM_SUGGESTED",
        }

    def _document_request_response(
        self, profile: StructuredCaseProfile, message: str, style: LanguageScript,
        hint: Optional[str] = None,
    ) -> ChatTurnResponse:
        original_message = profile.document_request.get("message", message) if profile.document_request and profile.document_request.get("status") == "SAFETY_PAUSED" else message
        doc_type, error = resolve_requested_document(original_message, profile.category, hint)
        if not doc_type:
            profile.document_request = {"intent": "USER_REQUESTED", "status": "BLOCKED", "message": original_message}
            unsupported = bool(error and "not supported" in error)
            if style.script == "devanagari":
                reply = "यह दस्तावेज़ इस मामले के लिए उपलब्ध नहीं है। कृपया समर्थित दस्तावेज़ का नाम बताइए।" if unsupported else "कृपया बताइए कि उपलब्ध दस्तावेज़ों में से कौन-सा बनाना चाहते हैं।"
            elif style.language == "hinglish":
                reply = "Yeh document is case ke liye available nahi hai. Kripya supported document ka naam batayein." if unsupported else "Kaunsa supported document taiyar karna chahenge?"
            else:
                reply = error or "Please name the document you want."
            return ChatTurnResponse(reply_text=reply, case_profile=profile, suggested_action=None, message_id=str(uuid.uuid4()))
        definition = DOCUMENT_DEFINITIONS[doc_type]
        values: dict[str, Any] = {}
        aliases = {"complainant_name": "user_name", "complainant_city": "user_city", "recipient_name": "opposite_party_name"}
        for field in (*definition.required_fields, *definition.optional_fields):
            if field == "incident_narrative":
                values[field] = profile.key_facts.get("issue_description") or profile.title
            else:
                present, value = read_profile_fact(profile, aliases.get(field, field))
                if present:
                    values[field] = value
        assessment = assess_document_generation(doc_type, profile.category, values)
        profile.document_request = {
            "intent": "USER_REQUESTED", "document_type": doc_type, "status": assessment.status,
            "missing_required_fields": assessment.missing_required_fields,
            "missing_optional_fields": assessment.missing_optional_fields,
            "optional_skipped": bool(profile.document_request and profile.document_request.get("optional_skipped")),
        }
        action = {"type": "PREPARE_DOC", "doc_type": doc_type, "label": definition.name,
                  "intent": "USER_REQUESTED", "open_confirmation_modal": True}
        return ChatTurnResponse(
            reply_text=self._document_handoff_reply(style, bool(assessment.missing_required_fields)),
            case_profile=profile, quick_replies=[], suggested_action=action,
            message_id=str(uuid.uuid4()),
        )

    @staticmethod
    def _safety_first_reply(safety: SafetyAssessment) -> str:
        """Deterministic safety reply used when no provider is available.

        Leads with the triage question rather than case paperwork, so the safety
        path behaves identically whether or not the model is reachable.
        """
        parts = [safety.triage_question or "Are you safe right now?"]
        if safety.guidance:
            parts.append(safety.guidance)
        parts.append(
            "Once you tell me that, I can explain your options and help you record what happened."
        )
        return "\n\n".join(parts)

    @staticmethod
    def _safety_quick_replies(safety: SafetyAssessment) -> list[str]:
        if safety.language_style == "hindi" and safety.script_style == "devanagari":
            return ["मैं अभी सुरक्षित हूँ", "मुझे अभी खतरा है", "वह व्यक्ति चला गया"]
        if safety.language_style == "hinglish" and safety.script_style == "roman":
            return ["Abhi main safe hun", "Mujhe abhi khatra hai", "Woh vyakti chala gaya"]
        return ["I am safe right now", "I am in danger", "The person has left"]

    @staticmethod
    def _is_low_context_message(message: str) -> bool:
        """True when a turn carries confirmation rather than legal substance.

        Such turns ("Yes, both", "July and August.") are meaningful to the case
        because of the question they answer, not because of their own words, so
        they must not drive statute retrieval.
        """
        normalized = re.sub(r"[^a-z0-9\s]", " ", (message or "").lower()).strip()
        if not normalized:
            return True
        if normalized in LOW_CONTEXT_PHRASES:
            return True
        tokens = normalized.split()
        # Dates and bare quantities answer a question without naming the issue.
        if all(re.fullmatch(r"\d{1,4}", token) or token in DATE_WORDS for token in tokens):
            return True
        return len(tokens) <= LOW_CONTEXT_MAX_WORDS

    def _build_rag_query(
        self,
        profile: StructuredCaseProfile,
        latest_message: str,
        workflow_state: Optional[dict[str, Any]] = None,
    ) -> RagQueryContext:
        """Assemble PII-free retrieval context from the persisted case.

        Only RAG_FACT_ALLOWLIST-derived descriptors are included. Names,
        addresses, account and transaction identifiers, exact amounts and
        uploaded document text are deliberately excluded: they never improve
        statute matching and would leak personal data into retrieval.
        """
        stage = (workflow_state or {}).get("current_stage_key") or profile.current_stage_key

        facts: list[str] = []
        for item in profile.evidence_checklist:
            if item.is_available and item.id in RAG_FACT_ALLOWLIST:
                facts.append(item.id)
        for action in profile.actions_completed:
            action_key = (
                action.get("type") or action.get("id") or action.get("action")
                if isinstance(action, dict)
                else str(action)
            )
            if action_key and action_key in RAG_FACT_ALLOWLIST:
                facts.append(action_key)

        domain = domain_registry.resolve(profile.category)
        return RagQueryContext(
            category=domain.id,
            issue_type=profile.issue_type,
            state=profile.user_state,
            city=profile.user_city,
            workflow_stage=stage,
            facts=tuple(dict.fromkeys(facts)),  # de-duplicated, order preserved
            latest_message=latest_message or "",
            low_context=self._is_low_context_message(latest_message),
        )

    def _verified_sources(
        self,
        profile: StructuredCaseProfile,
        narrative: str,
        workflow_state: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        query = self._build_rag_query(profile, narrative, workflow_state)
        citations = [
            citation.model_dump(mode="json")
            for citation in statutory_rag.retrieve_for_context(query, limit=4)
        ]
        seen_urls = {item.get("source_url") for item in citations}
        domain = domain_registry.resolve(profile.category)
        for source_model in domain.rag.official_sources:
            source = source_model.model_dump()
            if source.get("url") not in seen_urls:
                citations.append(source)

        # Debug-only trace: controlled-vocabulary fields plus the derived query.
        # Never the message body, party names, addresses, amounts or documents.
        logger.debug(
            "event=rag_retrieval case_id=%s category=%s issue_type=%s state=%s "
            "workflow_stage=%s low_context=%s query=%r sources=%d",
            profile.case_id,
            profile.category,
            profile.issue_type,
            profile.user_state,
            query.workflow_stage,
            query.low_context,
            query.to_query_text(),
            len(citations),
        )
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
                    if key not in {"last_question_group", "last_upload", "pending_document_request"}
                },
            },
            "evidence_available": [item.id for item in profile.evidence_checklist if item.is_available],
            "actions_completed": profile.actions_completed,
            "risk_level": profile.risk_level,
            "safety_notice": profile.safety_notice,
            "safety_status": profile.safety_status,
            "language_style": profile.language_style,
            "script_style": profile.script_style,
            "readiness": profile.readiness,
            "missing_document_fields": profile.missing_document_fields,
            "document_request": profile.document_request,
            "document_intake_active": bool(profile.key_facts.get("document_intake_active")),
        }

    @staticmethod
    def _current_case_reference(profile: StructuredCaseProfile) -> dict[str, Any]:
        """A memory question needs case identity, not full current-case facts."""
        return {
            "case_id": profile.case_id,
            "title": profile.title,
            "category": profile.category,
            "current_stage": {"key": profile.current_stage_key, "label": profile.current_stage_label},
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
            "recommended_document_label": profile.recommended_doc_label,
            "recommended_next_action": profile.recommended_next_action,
            "document_request": profile.document_request,
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

    def _safe_next_prompt(self, profile: StructuredCaseProfile) -> str:
        if profile.key_facts.get("document_intake_active") and profile.missing_document_fields:
            stated_unknown = set(profile.key_facts.get("stated_unknown_facts", []))
            missing = [field for field in profile.missing_document_fields if field not in stated_unknown]
            if missing:
                labels = ", ".join(field.replace("_", " ") for field in missing)
                doc_label = profile.recommended_doc_label or "your legal document"
                return f"To prepare {doc_label}, you can continue by sharing: {labels}."
        if not domain_registry.guidance_possible(profile):
            candidates = domain_registry.compact_context(profile, max_candidates=1)["next_fact_candidates"]
            if candidates:
                return f"I kept your case progress. You can continue by sharing {candidates[0]['meaning']}."
        return f"I kept your case progress. Please try the {self._provider_title} response again in a moment."

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
    def _is_document_handoff_request(message: str) -> bool:
        lowered = message.casefold()
        if re.search(r"\b(?:don't|do not|not|never|can't)\s+(?:prepare|generate|create|draft|make|write)\b", lowered):
            return False
        action_words = re.search(
            r"\b(?:prepare|generate|create|draft|make|write|use|download|banao|bana|banado|taiyar|likho)\b"
            r"|तैयार|बनाओ|बनाइ|बनाना|लिख",
            lowered,
        )
        request_phrase = re.search(r"\b(?:give|send|share)\s+(?:me\s+)?(?:the\s+|my\s+)?", lowered)
        status_phrase = re.search(
            r"\b(?:when|where)\b.{0,80}\b(?:appear|available|ready|link|download)\b",
            lowered,
        )
        document_words = re.search(
            r"\b(?:notice|document|letter|complaint|application|pdf|docx)\b"
            r"|नोटिस|दस्तावेज|पत्र|शिकायत|आवेदन|पीडीएफ",
            lowered,
        )
        return bool(document_words and (action_words or request_phrase or status_phrase))

    @staticmethod
    def _is_optional_skip(message: str) -> bool:
        return bool(re.fullmatch(
            r"\s*(?:skip|leave it|i don't know|i do not know|i don't have that|i do not have that|generate without it|bina iske banao|nahi pata|छोड़ दो|पता नहीं)\s*[.!]?\s*",
            message.casefold(),
        ))

    @staticmethod
    def _document_handoff_reply(style: LanguageScript, has_missing_details: bool) -> str:
        if style.script == "devanagari":
            return (
                "मैं यह दस्तावेज़ तैयार कर सकता हूँ। बाकी विवरण फ़ॉर्म में पुष्टि करें, फिर PDF या DOCX बनाएँ।"
                if has_missing_details
                else "मैं यह दस्तावेज़ तैयार कर सकता हूँ। फ़ॉर्म में विवरण जाँचें, फिर PDF या DOCX बनाएँ।"
            )
        if style.language == "hinglish":
            return (
                "Main yeh document taiyar kar sakta hoon. Baaki details form mein confirm karke PDF ya DOCX banayein."
                if has_missing_details
                else "Main yeh document taiyar kar sakta hoon. Form mein details check karke PDF ya DOCX banayein."
            )
        return (
            "I can prepare this document. Please confirm the remaining details in the form, then generate the PDF or DOCX."
            if has_missing_details
            else "I can prepare this document. Please review the details in the form, then generate the PDF or DOCX."
        )

    @staticmethod
    def _missing_document_fields(profile: StructuredCaseProfile) -> list[str]:
        document_type = select_document_for_workflow(profile.category, profile.current_stage_key)
        definition = DOCUMENT_DEFINITIONS.get(document_type)
        if not definition:
            return []
        profile_keys = {
            "complainant_name": "user_name",
            "complainant_city": "user_city",
            "recipient_name": "opposite_party_name",
            "opposite_party_name": "opposite_party_name",
        }
        missing: list[str] = []
        for document_field in definition.required_fields:
            # The narrative is assembled from persisted chat messages by the
            # document endpoint; it is not an intake field on the profile.
            if document_field == "incident_narrative":
                continue
            fact_key = profile_keys.get(document_field, document_field)
            present, value = read_profile_fact(profile, fact_key)
            if not present or value in (None, "", 0, 0.0, []):
                missing.append(fact_key)
        return list(dict.fromkeys(missing))

    def _limited_demo_prefix(self, style: Optional[LanguageScript] = None) -> str:
        if style and style.language == "hindi" and style.script == "devanagari":
            return f"सीमित डेमो मोड — {self._provider_title} कॉन्फ़िगर नहीं है, इसलिए यह उत्तर स्थानीय नियमों पर आधारित है।\n\n"
        if style and style.language == "hinglish" and style.script == "roman":
            return f"Limited demo mode — {self._provider_title} configured nahi hai, isliye yeh reply local rules use karta hai.\n\n"
        return f"Limited demo mode — {self._provider_title} is not configured, so this reply uses local workflow rules only.\n\n"

    def _temporary_failure_prefix(self) -> str:
        return f"{self._provider_title} is temporarily unavailable. I have switched this turn to limited demo mode and preserved your case progress.\n\n"


gemini_conversation_service = GeminiConversationService()
llm_conversation_service = gemini_conversation_service
