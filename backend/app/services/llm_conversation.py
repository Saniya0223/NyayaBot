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
from app.services.case_naming import apply_dynamic_title
from app.services.date_facts import same_date_ignoring_year
from app.services.fact_extraction import (
    DATE_FACT_FIELDS,
    backfill_facts,
    backfill_jurisdiction,
)
from app.services.case_readiness import (
    READY_FOR_ACTION,
    READY_FOR_DOCUMENT,
    compute_blocking_missing_facts,
    compute_intake_missing_facts,
    compute_readiness,
    crisis_document_block_active,
    document_routing_allowed,
)
from app.services.helplines import model_context as helpline_model_context
from app.services.response_guard import guard_reply
from app.services.safety_triage import (
    AMBER,
    CRISIS_SUPPORT,
    EMERGENCY_NUMBER,
    EXTERNAL_HARM_CONTEXTS,
    RED,
    SafetyAssessment,
    assess_safety,
    extract_safety_facts,
    is_crisis_only_case,
    safety_triage_resolved,
    simple_yes_no,
)
from app.config import settings
from app.llm.contracts import (
    CaseExtraction,
    DetectedAction,
    LLMExtractionContext,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitedError,
    LLMResponseContext,
)
from app.llm.factory import get_llm_provider
from app.schemas.chat import (
    ChatMessage,
    ChatRetryAction,
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

# legal_sources provenance tags (M8). Applied to the context list and to
# profile.legal_sources, which are the same dict objects, so
# test_llm_conversation.py:516's identity-of-dicts membership assertion still holds.
VERIFIED_PROVISION = "verified_provision"
OFFICIAL_SOURCE_LINK = "official_source_link"


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

# Safety intake is a bounded conversation, not an unconditional loop. The same
# question group is never put a third time, and triage closes itself after this
# many safety turns so a case can never be swallowed by the questionnaire.
SAFETY_MAX_ASKS_PER_GROUP = 2
SAFETY_INTAKE_MAX_TURNS = 4

# Which safety facts each question group is responsible for, so an unanswered
# group can be recorded as unknown instead of being asked again.
SAFETY_QUESTION_GROUP_FACTS: dict[str, tuple[str, ...]] = {
    "incident": ("threat_details", "incident_date"),
    "repeated": ("repeated_incidents",),
    "violence_weapon": ("physical_violence_or_weapon",),
    "dependants": ("dependants_at_risk",),
    "evidence": ("evidence_available",),
    "police": ("police_contacted",),
}

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
        extraction_context_override: Optional[LLMExtractionContext] = None,
    ) -> ChatTurnResponse:
        history = self._recent_history(recent_messages)

        # Deterministic language/script and safety routing run before provider
        # availability, extraction/classification, workflow, RAG, or documents.
        # An unresolved safety case returns from this branch without an LLM call.
        prior_language = existing_profile.language_style if existing_profile else None
        prior_script = existing_profile.script_style if existing_profile else None
        prior_safety = existing_profile.safety_status if existing_profile else None
        if existing_profile and existing_profile.key_facts.get("crisis_support_offered"):
            # key_facts, not safety_status, is the durable record: safety_status
            # is rewritten from the current assessment on every turn.
            prior_safety = {**(prior_safety or {}), "crisis_support_offered": True}
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

        if self._safety_route_required(safety, existing_profile, req.message):
            if existing_profile:
                existing_profile.pending_interaction = None
            return self._process_safety_turn(req, existing_profile, safety, style)
        self._release_safety_intake(existing_profile, safety)
        self._advance_crisis_cooldown(existing_profile)

        if (existing_profile and existing_profile.document_request
                and existing_profile.document_request.get("document_type")
                and existing_profile.document_request.get("status") in {"OPTIONAL_FIELDS_AVAILABLE", "NEEDS_REQUIRED_FIELDS", "READY_TO_GENERATE"}
                and self._is_optional_skip(req.message)):
            doc_type = existing_profile.document_request["document_type"]
            blocked = self._document_block_reason(existing_profile, safety)
            if blocked:
                self.workflow_agent._touch(existing_profile)
                return self._tag_response(
                    self._document_gate_response(
                        existing_profile, req.message, style, blocked, hint=doc_type,
                    ),
                    self._provider_mode,
                )
            response = self._document_request_response(existing_profile, req.message, style, hint=doc_type)
            if existing_profile.document_request:
                existing_profile.document_request["optional_skipped"] = True
            self.workflow_agent._touch(existing_profile)
            return self._tag_response(response, self._provider_mode)

        # A request parked for safety (S5a) or for missing case facts (S5b)
        # resumes by itself on the turn it becomes available, so the user does
        # not have to ask a second time.
        pending_document_request = bool(
            existing_profile and existing_profile.document_request
            and existing_profile.document_request.get("status") in {"SAFETY_PAUSED", "NEEDS_CASE_FACTS"}
        )
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
                blocked = self._document_block_reason(fallback.case_profile, safety)
                if blocked:
                    gated = self._document_gate_response(
                        fallback.case_profile, req.message, style, blocked,
                        hint=(existing_profile.document_request.get("document_type") if pending_document_request else None),
                    )
                    fallback.reply_text = gated.reply_text
                    fallback.suggested_action = None
                    fallback.quick_replies = []
                else:
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
            fallback.reply_text = prefix + self._emergency_reminder(
                fallback.case_profile, style, safety.safety_level, fallback.reply_text,
            ) + fallback.reply_text
            return self._tag_response(fallback, "limited_demo")

        profile = existing_profile
        profile_before_extraction = profile.model_copy(deep=True) if profile else None
        extraction_succeeded = False
        extraction_context = None
        response_context = None
        declined_offer = False
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

            extraction_context = extraction_context_override or LLMExtractionContext(
                    user_message=req.message,
                    recent_messages=history,
                    case_summary=self._compact_case(profile) if profile else None,
                    pending_interaction=pending,
                    language_style=style.language,
                    script_style=style.script,
                    domain_catalog=domain_registry.extraction_catalog(),
                    document_catalog=[{"id": item.id, "name": item.name} for item in DOCUMENT_DEFINITIONS.values()],
            )
            extraction = await self.provider.extract_case_updates(extraction_context)
            extraction_succeeded = True

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

            previous_category = profile.category
            previous_issue_type = profile.issue_type
            self._maybe_reclassify(profile, extraction, req.message)
            conflict = self._apply_extraction(profile, extraction)
            # Deterministic backfill runs on the live path too, not only on the
            # offline demo path. It fills only what the model left empty, and it
            # enforces the date year invariant over what the model did supply.
            backfill_facts(req.message, profile, prior_text=prior_text)
            self._apply_actions(profile, extraction.actions_detected)
            self.workflow_agent._mark_evidence(profile, extraction.evidence_detected)
            self.workflow_agent._assess_risk(req.message, profile)
            self._refresh_workflow(profile, safety, req.message)
            renamed = apply_dynamic_title(profile, previous_category, previous_issue_type)
            if renamed:
                logger.info("case_id=%s event=case_retitled", profile.case_id)
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
                blocked = self._document_block_reason(profile, safety)
                if blocked:
                    return self._tag_response(
                        self._document_gate_response(profile, req.message, style, blocked, hint=hint),
                        self._provider_mode,
                    )
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
            # Lets the workspace panel distinguish "not retrieved yet" from "no corpus
            # exists for this domain" instead of showing one placeholder for both (M8).
            profile.key_facts["verified_sources_available"] = bool(profile.legal_sources)
            # Ordinary follow-ups come only from the ranked domain context.
            # Document fields are supplied separately after the user selects a document.
            missing_for_response: list[str] = []
            if profile.missing_document_fields and profile.key_facts.get("document_intake_active"):
                missing_for_response.extend(
                    f"document:{field}" for field in profile.missing_document_fields
                )
            response_context = LLMResponseContext(
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
                    legal_sources_status=self._legal_sources_status(profile, legal_sources),
                    helpline_registry=helpline_model_context(),
            )
            declined_offer = bool(
                pending and pending.type == "DOCUMENT_CONFIRMATION"
                and resolution.status == "RESOLVED" and not resolution.document_type
            )
            reply = await self.provider.chat(response_context)
            return self._finish_chat_response(profile, reply, response_context, declined_offer)
        except LLMRateLimitedError:
            logger.warning(
                "provider=%s model=%s reason=RATE_LIMITED event=turn_fell_back_to_limited_demo",
                self.provider.status.provider, self.provider.status.model,
            )
            if not extraction_succeeded:
                # Undo any staged pending-confirmation changes. Failed extraction
                # must not change facts or consume the case's pending interaction.
                if existing_profile is not None and profile_before_extraction is not None:
                    for field, value in profile_before_extraction:
                        setattr(existing_profile, field, value)
                profile = existing_profile
            # Terminal for this turn: no second extraction, chat, summary, or
            # evidence/document provider operation is invoked by this fallback.
            response = self._rate_limit_fallback(req, profile, style, prior_text=prior_text, safety=safety)
            response.retry_action = ChatRetryAction(
                message_id=response.message_id, label=f"Try {self._provider_title} again",
            )
            response._retry_state = {
                "stage": "FINAL_CHAT" if extraction_succeeded else "EXTRACTION",
                "original_message": req.message,
                "history": history,
                "extraction_context": extraction_context.model_dump(mode="json") if extraction_context else None,
                "response_context": response_context.model_dump(mode="json") if response_context else None,
                "declined_offer": declined_offer,
                "user_context": user_context or {},
                "evidence_context": evidence_context or {},
            }
            return response
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

    def _finish_chat_response(
        self, profile: StructuredCaseProfile, reply: str,
        context: LLMResponseContext, declined_offer: bool,
    ) -> ChatTurnResponse:
        if (context.pending_resolution or {}).get("status") != "AMBIGUOUS":
            offer = document_offer(profile)
            doc_label = profile.recommended_doc_label or ""
            # Preserve the same post-response pending-question/offer behavior.
            document_question = bool(re.search(
                r"\b(?:prepare|draft|create|make)\b.*\b(?:notice|letter|complaint|document)\b",
                reply.casefold(),
            ))
            if offer and not declined_offer and (
                "?" not in reply or (doc_label and doc_label.casefold() in reply.casefold())
                or document_question
            ):
                profile.pending_interaction = offer
            elif "?" in reply and context.domain_context["next_fact_candidates"]:
                profile.pending_interaction = fact_candidate(profile, context.domain_context["next_fact_candidates"][0])
            else:
                profile.pending_interaction = None
        self.workflow_agent._touch(profile)
        reply = self._emergency_reminder(
            profile,
            LanguageScript(context.language_style, context.script_style),
            (context.safety or {}).get("safety_level"),
            reply,
        ) + reply
        response = ChatTurnResponse(
            reply_text=reply, case_profile=profile,
            quick_replies=self._quick_replies(profile, context.conflict),
            suggested_action=profile.recommended_next_action, message_id=str(uuid.uuid4()),
        )
        return self._tag_response(response, self._provider_mode, context.user_message)

    async def resume_response(
        self, profile: StructuredCaseProfile, checkpoint: dict[str, Any],
    ) -> ChatTurnResponse:
        """Resume only final prose; extraction and deterministic updates already ran."""
        context = LLMResponseContext.model_validate(checkpoint["response_context"])
        try:
            reply = await self.provider.chat(context)
        except LLMProviderError:
            response = self._rate_limit_fallback(
                ChatTurnRequest(message=checkpoint["original_message"], case_id=profile.case_id),
                profile, LanguageScript(context.language_style, context.script_style),
            )
            response.retry_action = ChatRetryAction(
                message_id=response.message_id, label=f"Try {self._provider_title} again",
            )
            response._retry_state = dict(checkpoint)
            return response
        return self._finish_chat_response(profile, reply, context, checkpoint["declined_offer"])

    def _rate_limit_fallback(
        self,
        req: ChatTurnRequest,
        profile: Optional[StructuredCaseProfile],
        style: LanguageScript,
        prior_text: str = "",
        safety: Optional[SafetyAssessment] = None,
    ) -> ChatTurnResponse:
        if profile is not None:
            # A fact stated during an outage used to be lost forever: this
            # branch performed no extraction at all, and the extraction prompt
            # only ever reads the newest turn, so no later turn re-read it.
            backfill_facts(req.message, profile, prior_text=prior_text)
            self._refresh_workflow(profile, safety, req.message)
            profile.professional_help = evaluate_professional_help(profile)
        if profile is None:
            # A new session needs an envelope, not facts inferred from a turn
            # whose extraction failed. The retry can perform normal intake later.
            profile = self.workflow_agent._init_case_profile("", req.case_id, category_override="GENERAL")
            profile.language_style = style.language
            profile.script_style = style.script
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
        return self._tag_response(response, "limited_demo", req.message)

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
        message: str = "",
    ) -> bool:
        """Keep unresolved or newly urgent safety turns out of the legal flow.

        Safety routing has to be able to *end*. Previously this returned True on
        every turn until every safety fact was known, which let triage swallow
        questions it was never going to answer. The turn is handed back once the
        emergency question is settled and the user has moved on, and reclaimed
        the moment a new harm signal appears.
        """
        if not safety.is_safety_case:
            return False
        key_facts = (profile.key_facts if profile else None) or {}

        # A self-harm disclosure, or one not yet answered with crisis support.
        if safety.stage == CRISIS_SUPPORT:
            return True

        if key_facts.get("safety_triage_complete"):
            # A closed triage only reopens on a genuinely new harm signal. This
            # check sits ABOVE the active-danger short-circuit deliberately:
            # `immediate_danger` is sticky, so with the old ordering an urgent
            # turn could never be released even after the ask cap had closed
            # triage, and the same 112 sentence came back forever.
            return bool(safety.fresh_harm_signal)

        if safety.immediate_danger is True:
            return True

        if key_facts.get("crisis_support_offered") and not safety.fresh_harm_signal:
            # Crisis support has been given. Do not then drop the person into
            # the attacker-proximity ladder for a danger nobody described.
            if safety.immediate_danger is False or not any(
                context in safety.contexts for context in EXTERNAL_HARM_CONTEXTS
            ):
                return False

        if safety.immediate_danger is None:
            return True

        # Danger answered "no": release the turn when it has moved on.
        return not GeminiConversationService._safety_turn_moved_on(safety, message)

    @staticmethod
    def _safety_turn_moved_on(safety: SafetyAssessment, message: str) -> bool:
        """True when this turn asks something new rather than answering triage.

        Deliberately strict - an explicit question. A long sentence is far more
        likely to be the answer to "what exactly happened, and when?" than a
        change of subject, and releasing it would lose the deterministic safety
        fact it carries. Runaway intake is bounded separately, by the per-group
        ask cap in `_paced_safety_reply`.
        """
        if safety.fresh_harm_signal or safety.danger_signal:
            return False
        if simple_yes_no(message) is not None:
            return False
        text = (message or "").strip()
        return "?" in text and len(text.split()) > LOW_CONTEXT_MAX_WORDS

    @staticmethod
    def _release_safety_intake(
        profile: Optional[StructuredCaseProfile],
        safety: SafetyAssessment,
    ) -> None:
        """Close an open safety intake when the turn goes back to legal flow.

        Only the emergency question has to be settled. The remaining intake
        questions are useful, not blocking, so they must not pin the readiness
        ladder once the user has moved on. A crisis case is deliberately left
        open, which keeps `document_routing_allowed` shut for it.
        """
        if not profile or not safety.is_safety_case:
            return
        key_facts = profile.key_facts
        if key_facts.get("safety_triage_complete") or safety.immediate_danger is not False:
            return
        key_facts["safety_triage_complete"] = True
        key_facts.pop("last_safety_question_group", None)

    def _process_safety_turn(
        self,
        req: ChatTurnRequest,
        existing_profile: Optional[StructuredCaseProfile],
        safety: SafetyAssessment,
        style: LanguageScript,
    ) -> ChatTurnResponse:
        """Handle safety deterministically before any provider or RAG call."""
        sanitized_text, _ = PIIMasker.mask_text(req.message.strip())
        crisis = safety.stage == CRISIS_SUPPORT
        if existing_profile:
            profile = existing_profile
        else:
            # A crisis turn is not a police complaint. Let the ordinary
            # classifier decide, and mark a triage-assigned category so a later
            # confident classification can correct it.
            profile = self.workflow_agent._init_case_profile(
                sanitized_text,
                req.case_id,
                category_override=None if crisis else "POLICE_COMPLAINT",
            )
            if not crisis:
                profile.key_facts["category_source"] = "safety_triage"
        profile.language_style = style.language
        profile.script_style = style.script
        profile.risk_level = RED if safety.safety_level == RED else AMBER
        profile.safety_notice = safety.guidance
        if self._is_document_handoff_request(req.message) and not crisis:
            profile.document_request = {"intent": "USER_REQUESTED", "status": "SAFETY_PAUSED", "message": req.message}
        profile.key_facts["safety_context"] = safety.safety_context
        profile.key_facts["safety_contexts"] = list(safety.contexts)
        if crisis:
            # Recorded before `_refresh_workflow` below, not after the reply is
            # composed, so readiness is computed against the crisis-scoped
            # intake contract on the crisis turn itself.
            self._mark_crisis_support_offered(profile)

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

        # M4. A safety turn returns before extraction, so W1-05's
        # "मैं लखनऊ में रहता हूँ" - stated in the same breath as the threat -
        # was never recorded, and the case had no jurisdiction for the rest of
        # the conversation. Only the jurisdiction lookup runs here, not the full
        # backfill: a threat narrative is the worst possible place to guess a
        # counterparty, an amount or an incident date, and this turn already
        # nulls every document affordance below.
        backfill_jurisdiction(sanitized_text, profile)

        self._refresh_workflow(profile, safety, req.message)
        reply, quick_replies = self._paced_safety_reply(profile, safety, style)
        safety_state = safety.to_dict()
        safety_state["triage_complete"] = bool(profile.key_facts.get("safety_triage_complete"))
        safety_state["crisis_support_offered"] = bool(profile.key_facts.get("crisis_support_offered"))
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
        key_facts = profile.key_facts

        if safety.stage == CRISIS_SUPPORT:
            # Crisis support short-circuits everything: no evidence checklist,
            # no CCTV question, no document offer, no intake ladder.
            self._mark_crisis_support_offered(profile)
            parts = [safety.guidance, safety.triage_question]
            return "\n\n".join(part for part in parts if part), []

        # N7: `safety_intake_turns` is a per-case total that never reset, so once
        # the cap was spent a genuinely new threat closed triage on the same turn
        # and produced the self-contradictory "if you are in danger now, call
        # 112 ... the immediate-safety check is complete". A fresh harm signal
        # after a closed triage reopens the budget. This runs before the counter
        # below, so it can never fire on the turn the cap itself closes triage.
        if key_facts.get("safety_triage_complete") and safety.fresh_harm_signal:
            key_facts["safety_intake_turns"] = 0
            key_facts.pop("safety_question_asks", None)
            key_facts.pop("safety_reassurance_given", None)
            key_facts.pop("safety_triage_complete", None)
            key_facts.pop("last_safety_question_group", None)

        turns = int(key_facts.get("safety_intake_turns") or 0) + 1
        key_facts["safety_intake_turns"] = turns

        if safety.immediate_danger is None:
            if self._safety_group_exhausted(key_facts, "immediate_danger") or turns > SAFETY_INTAKE_MAX_TURNS:
                # Asked twice with no answer. Keep the emergency guidance, stop
                # asking, and let the legal conversation resume.
                key_facts["safety_triage_complete"] = True
                key_facts.pop("last_safety_question_group", None)
                parts = [safety.guidance, self._safety_text(style, "complete")]
                return "\n\n".join(part for part in parts if part), []
            self._record_safety_ask(key_facts, "immediate_danger")
            parts = [safety.triage_question, safety.guidance]
            return "\n\n".join(part for part in parts if part), self._safety_quick_replies(safety)

        if safety.immediate_danger is True:
            # The urgent branch used to write `last_safety_question_group`
            # directly instead of going through `_record_safety_ask`, so it had
            # no counter and was exempt from both caps - an unbounded loop with
            # no exit but a regex. It is now bounded exactly like the
            # check-danger branch above.
            key_facts["urgent_guidance_given"] = True
            if self._safety_group_exhausted(key_facts, "urgent_safety") or turns > SAFETY_INTAKE_MAX_TURNS:
                key_facts["safety_triage_complete"] = True
                key_facts.pop("last_safety_question_group", None)
                parts = [safety.guidance, self._safety_text(style, "complete")]
                return "\n\n".join(part for part in parts if part), []
            self._record_safety_ask(key_facts, "urgent_safety")
            # Urgent instructions precede the follow-up question.
            parts = [safety.guidance, safety.triage_question]
            return "\n\n".join(part for part in parts if part), self._safety_quick_replies(safety)

        # The reassurance line belongs to the turn on which danger resolves.
        # Repeating it every turn is what made the script look stuck.
        prefix = ""
        if not key_facts.get("safety_reassurance_given"):
            prefix = safety.guidance or ""
            key_facts["safety_reassurance_given"] = True

        missing = set(profile.intake_missing_facts)
        candidates = (
            ("incident", "threat_details" in missing or "incident_date" in missing),
            ("repeated", "repeated_incidents" in missing),
            ("violence_weapon", "physical_violence_or_weapon" in missing),
            ("dependants", safety.dependants_present and key_facts.get("dependants_at_risk") is None),
            ("evidence", "evidence_available" in missing),
            ("police", "police_contacted" in missing),
        )

        question = None
        if turns <= SAFETY_INTAKE_MAX_TURNS:
            for group, needed in candidates:
                if not needed:
                    continue
                if self._safety_group_exhausted(key_facts, group):
                    # Two unanswered asks: record it as unknown and move on.
                    self._mark_safety_group_unknown(profile, group)
                    continue
                self._record_safety_ask(key_facts, group)
                question = self._safety_text(style, group)
                break

        if question is None:
            for group, needed in candidates:
                if needed:
                    self._mark_safety_group_unknown(profile, group)
            key_facts["safety_triage_complete"] = True
            key_facts.pop("last_safety_question_group", None)
            question = self._safety_text(style, "complete")
        return "\n\n".join(part for part in (prefix, question) if part), []

    @staticmethod
    def _emergency_reminder(
        profile: Optional[StructuredCaseProfile],
        style: LanguageScript,
        safety_level: Optional[str],
        reply: str = "",
    ) -> str:
        """One-line emergency instruction carried by every post-release reply.

        Bounding the urgent branch (CF-1) means a case that was in active danger
        now gets ordinary legal answers again. It must not silently lose the 112
        instruction while the danger assessment still says RED. One short line,
        no question, no second helpline block, never doubled.
        """
        key_facts = (profile.key_facts if profile else None) or {}
        if not key_facts.get("urgent_guidance_given") or safety_level != RED:
            return ""
        if style.script == "devanagari":
            line = f"अगर अभी तुरंत ख़तरा हो, तो पहले {EMERGENCY_NUMBER} पर कॉल कीजिए।"
        elif style.language == "hinglish":
            line = f"Agar abhi turant khatra ho, to pehle {EMERGENCY_NUMBER} par call kijiye."
        else:
            line = f"If you are in immediate danger, call {EMERGENCY_NUMBER} first."
        return "" if line in (reply or "") else line + "\n\n"

    @staticmethod
    def _mark_crisis_support_offered(profile: StructuredCaseProfile) -> None:
        """Record that crisis support was given, and open the document cooldown.

        For a crisis-only case this also closes safety triage. There is no
        attacker to ask about, so the immediate-danger question will never be
        answered, and leaving triage open pinned the underlying legal matter at
        UNDERSTANDING_CASE for the life of the case (CF-4). Documents are held
        back by the explicit cooling-off window instead (CF-5), not by an
        unanswerable question. A *mixed* case - self-harm plus a real attacker -
        still owes the danger answer and is deliberately excluded.
        """
        key_facts = profile.key_facts
        key_facts["crisis_support_offered"] = True
        key_facts.pop("last_safety_question_group", None)
        # A later disclosure restarts the window rather than inheriting a spent one.
        key_facts["turns_since_crisis_support"] = 0
        if is_crisis_only_case(key_facts):
            key_facts["safety_triage_complete"] = True

    @staticmethod
    def _advance_crisis_cooldown(profile: Optional[StructuredCaseProfile]) -> None:
        """Count one turn of the post-crisis document cooling-off window.

        Incremented only on turns that reach the ordinary legal flow, so the
        window measures conversation that has moved on, not turns spent inside
        crisis support.
        """
        if profile is None:
            return
        key_facts = profile.key_facts
        if not key_facts.get("crisis_support_offered"):
            return
        key_facts["turns_since_crisis_support"] = int(
            key_facts.get("turns_since_crisis_support") or 0
        ) + 1

    @staticmethod
    def _safety_group_exhausted(key_facts: dict[str, Any], group: str) -> bool:
        asks = key_facts.get("safety_question_asks") or {}
        return int(asks.get(group, 0)) >= SAFETY_MAX_ASKS_PER_GROUP

    @staticmethod
    def _record_safety_ask(key_facts: dict[str, Any], group: str) -> None:
        asks = dict(key_facts.get("safety_question_asks") or {})
        asks[group] = int(asks.get(group, 0)) + 1
        key_facts["safety_question_asks"] = asks
        key_facts["last_safety_question_group"] = group

    @staticmethod
    def _mark_safety_group_unknown(profile: StructuredCaseProfile, group: str) -> None:
        """Record an unanswered safety fact as stated-unknown, not as blocking."""
        stated = list(profile.key_facts.get("stated_unknown_facts") or [])
        for fact in SAFETY_QUESTION_GROUP_FACTS.get(group, ()):
            if fact not in stated:
                stated.append(fact)
        profile.key_facts["stated_unknown_facts"] = stated

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
            if (
                not existing_is_empty
                and not self._same_value(existing, candidate)
                and (
                    (definition is not None and definition.value_type == FactValueType.DATE)
                    or field in DATE_FACT_FIELDS
                )
                and same_date_ignoring_year(existing, candidate)
            ):
                # H8. The year invariant strips a year the user never wrote,
                # while the extraction prompt re-asserts the full date on every
                # later turn. Stored "10 August" vs model "10 August 2026" then
                # looked like a factual conflict and hijacked every subsequent
                # turn. It is not a conflict: it is the same date, and the year
                # has already been ruled on. Let the candidate through and let
                # `backfill_facts` -> `enforce_date_year_invariant`, which runs
                # immediately after, re-apply the rule against what the user has
                # actually written. A year stated later is recovered that way;
                # one still unstated is stripped again.
                existing_is_empty = True
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

    # Fields that belong to the case itself rather than to its legal domain, so
    # they survive a re-classification intact.
    _PRESERVED_ON_RECLASSIFY = (
        "case_id", "case_number", "created_at", "key_facts", "fact_metadata",
        "user_name", "user_city", "user_state", "user_phone",
        "opposite_party_name", "opposite_party_address", "property_address",
        "disputed_amount", "incident_date", "vacating_date", "unpaid_months",
        "transaction_id", "bank_name", "police_station_name",
        "timeline", "actions_completed", "documents", "deadlines",
        "language_style", "script_style", "risk_level", "safety_notice",
    )

    def _maybe_reclassify(
        self,
        profile: StructuredCaseProfile,
        extraction: Any,
        message: str,
    ) -> bool:
        """Upgrade a case that was created before its domain was knowable.

        A conversation that opens with "hi" creates a GENERAL case, and the
        category was previously fixed at creation - so every later turn's
        classification was discarded and the case stayed GENERAL forever, with
        no workflow, no statutes and a placeholder title. Re-classify once the
        user actually describes the problem.
        """
        proposed = domain_registry.normalize_id(extraction.classification.category)
        if proposed == profile.category or proposed == "GENERAL":
            return False

        # Only an unclassified case is migrated. Re-routing an established case
        # would silently discard a workflow the user has already progressed.
        # A category assigned by safety triage is the exception: nobody chose
        # POLICE_COMPLAINT, triage imposed it, so it must remain correctable.
        triage_assigned = profile.key_facts.get("category_source") == "safety_triage"
        if profile.category != "GENERAL" and not triage_assigned:
            return False

        # A confident read of a substantive turn, not a stray word in a greeting.
        if extraction.classification.confidence < 0.5 or len(message.split()) < 3:
            return False

        fresh = self.workflow_agent._init_case_profile(
            message, profile.case_id, category_override=proposed
        )
        preserved = {field: getattr(profile, field) for field in self._PRESERVED_ON_RECLASSIFY}

        # Adopt the new domain's scaffolding, then restore what the case knows.
        for field in (
            "category", "category_display_name", "issue_type", "current_stage_key",
            "current_stage_label", "evidence_checklist", "legal_journey", "rights_summary",
        ):
            setattr(profile, field, getattr(fresh, field))
        for field, value in preserved.items():
            setattr(profile, field, value)

        domain = domain_registry.resolve(proposed)
        profile.issue_type = domain.normalize_issue_type(extraction.classification.issue_type)

        previous_category = "safety_triage" if triage_assigned else "GENERAL"
        profile.key_facts.pop("category_source", None)

        logger.info(
            "case_id=%s from=%s to=%s issue_type=%s event=case_reclassified",
            profile.case_id, previous_category, proposed, profile.issue_type,
        )
        return True

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
            if profile.key_facts.get("crisis_support_offered"):
                safety_state["crisis_support_offered"] = True
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

    def _document_block_reason(
        self,
        profile: Optional[StructuredCaseProfile],
        safety: Optional[SafetyAssessment],
    ) -> Optional[str]:
        """The whole document gate, for every affordance branch.

        `document_routing_allowed` calls itself "the single gate every document
        recommendation must pass", and was consulted at exactly one site - inside
        `_refresh_workflow`, and only to suppress the *proactive* recommendation.
        A user-*requested* document never passed through it, which is C3: W1-03
        asked for a legal notice on turn 1 and was told "I can prepare this
        document" with `readiness=UNDERSTANDING_CASE`, no employer name and no
        amount. Every affordance branch now delegates here.

        Returns `None` (allowed), `"SAFETY"` (not now - crisis or danger) or
        `"READINESS"` (not yet - the case has not earned it). The two are
        different refusals and must not share copy: the crisis deferral
        deliberately names no paperwork at all, which is right after a self-harm
        disclosure and wrong for a user who simply has not told us enough yet.
        """
        if profile is None:
            return None
        key_facts = profile.key_facts or {}
        # Ordered first so the crisis deferral copy keeps winning.
        if crisis_document_block_active(key_facts):
            return "SAFETY"
        if safety is not None and safety.blocks_document_routing and not safety_triage_resolved(key_facts):
            return "SAFETY"
        if profile.risk_level == RED or (safety is not None and safety.safety_level == RED):
            return "SAFETY"
        if safety is not None and safety.is_safety_case and not key_facts.get("safety_triage_complete"):
            return "SAFETY"
        if not document_routing_allowed(profile, safety or SafetyAssessment(), profile.readiness):
            return "READINESS"
        return None

    def _document_affordance_blocked(
        self,
        profile: Optional[StructuredCaseProfile],
        safety: Optional[SafetyAssessment],
    ) -> bool:
        return self._document_block_reason(profile, safety) is not None

    @staticmethod
    def _document_deferral_reply(style: LanguageScript) -> str:
        """Script-matched deferral that names no template and no paperwork."""
        if style.script == "devanagari":
            return (
                "अभी मैं आपकी सुरक्षा और हाल-चाल को पहले रखना चाहता हूँ। "
                "जब आप थोड़ा बेहतर महसूस करें, हम आपके मामले के औपचारिक क़दमों पर लौट सकते हैं।"
            )
        if style.language == "hinglish":
            return (
                "Abhi main aapki safety aur haal-chaal ko pehle rakhna chahta hoon. "
                "Jab aap thoda behtar mehsoos karein, hum aapke maamle ke formal steps par wapas aa sakte hain."
            )
        return (
            "Right now I would rather stay with how you are doing. "
            "Once things feel a little steadier, we can come back to the formal steps in your case."
        )

    @staticmethod
    def _blocking_fact_phrases(profile: StructuredCaseProfile, limit: int = 2) -> list[str]:
        """Plain-language names for the facts a document is still waiting on.

        Reads `FactDefinition.meaning`, which is the product's own human phrasing
        ("employer involved", "months or period for which salary is unpaid").
        The raw key never leaves this function - W1-02 t2 told a user verbatim
        that it needed "landlord ya property manager ka naam (opposite_party_name)".
        """
        definitions = {fact.key: fact for fact in domain_registry.resolve(profile.category).facts}
        phrases: list[str] = []
        for key in (profile.intake_missing_facts or []):
            definition = definitions.get(key)
            if definition is None or definition.document_only:
                continue
            meaning = (definition.meaning or "").strip()
            if meaning and meaning not in phrases:
                phrases.append(meaning)
            if len(phrases) >= limit:
                break
        return phrases

    def _document_not_ready_reply(
        self, profile: StructuredCaseProfile, style: LanguageScript,
    ) -> str:
        """"Not yet", which is a different answer from the crisis "not now".

        The crisis deferral names no paperwork on purpose. Here the document is
        the right idea and the user should be told what it is waiting for, so
        they can supply it on the next turn instead of being stonewalled.
        """
        phrases = self._blocking_fact_phrases(profile)
        label = profile.recommended_doc_label or profile.document_request and profile.document_request.get("document_type")
        if style.script == "devanagari":
            need = " और ".join(phrases)
            base = "मैं यह दस्तावेज़ तैयार कर सकता हूँ, लेकिन पहले कुछ जानकारी चाहिए।"
            return f"{base} मुझे {need} बताइए।" if need else f"{base} कृपया अपने मामले के बारे में थोड़ा और बताइए।"
        if style.language == "hinglish":
            need = " aur ".join(phrases)
            base = "Main yeh document taiyar kar sakta hoon, lekin pehle thodi jankari chahiye."
            return f"{base} Mujhe {need} bata dijiye." if need else f"{base} Apne case ke baare mein thoda aur batayein."
        need = " and ".join(phrases)
        base = "I can prepare this document once I know a little more about your case."
        return f"{base} Tell me {need}, and I will take it from there." if need else (
            f"{base} Tell me a bit more about what happened, and I will take it from there."
        )

    def _document_not_ready_response(
        self, profile: StructuredCaseProfile, message: str, style: LanguageScript,
        hint: Optional[str] = None,
    ) -> ChatTurnResponse:
        """Refuse the affordance, but remember the request so it can resume.

        A user who asked for a notice on turn 1 should get it on the turn it
        becomes available, without having to ask a second time.
        """
        doc_type, _ = resolve_requested_document(message, profile.category, hint)
        if not doc_type:
            # "There is no such document for this case" is a more precise and
            # more useful refusal than "not yet", and it is already implemented.
            # Readiness has nothing to add to it, so it keeps precedence.
            return self._document_request_response(profile, message, style, hint=hint)
        profile.document_request = {
            "intent": "USER_REQUESTED",
            "status": "NEEDS_CASE_FACTS",
            "document_type": doc_type,
            "blocking_facts": list(profile.intake_missing_facts or [])[:4],
            "message": message,
        }
        return ChatTurnResponse(
            reply_text=self._document_not_ready_reply(profile, style),
            case_profile=profile,
            quick_replies=[],
            suggested_action=None,
            message_id=str(uuid.uuid4()),
        )

    def _document_gate_response(
        self, profile: StructuredCaseProfile, message: str, style: LanguageScript,
        reason: str, hint: Optional[str] = None,
    ) -> ChatTurnResponse:
        if reason == "READINESS":
            return self._document_not_ready_response(profile, message, style, hint=hint)
        return self._document_deferral_response(profile, style)

    def _document_deferral_response(
        self, profile: StructuredCaseProfile, style: LanguageScript,
    ) -> ChatTurnResponse:
        """Refuse a document affordance without parking a request or an action."""
        return ChatTurnResponse(
            reply_text=self._document_deferral_reply(style),
            case_profile=profile,
            quick_replies=[],
            suggested_action=None,
            message_id=str(uuid.uuid4()),
        )

    def _document_request_response(
        self, profile: StructuredCaseProfile, message: str, style: LanguageScript,
        hint: Optional[str] = None,
    ) -> ChatTurnResponse:
        original_message = profile.document_request.get("message", message) if profile.document_request and profile.document_request.get("status") in {"SAFETY_PAUSED", "NEEDS_CASE_FACTS"} else message
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
        # M8: "kind" distinguishes a retrieved statutory provision from a bare Act-name
        # portal link. Without it the two were formally indistinguishable inside a list
        # called legal_sources, which is what handed the model the Act name
        # "Code on Wages, 2019" with nothing to quote from it (finding C2).
        citations = [
            {**citation.model_dump(mode="json"), "kind": VERIFIED_PROVISION}
            for citation in statutory_rag.retrieve_for_context(query, limit=4)
        ]
        seen_urls = {item.get("source_url") for item in citations}
        domain = domain_registry.resolve(profile.category)
        for source_model in domain.rag.official_sources:
            source = {**source_model.model_dump(), "kind": OFFICIAL_SOURCE_LINK}
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

    @staticmethod
    def _legal_sources_status(
        profile: StructuredCaseProfile, legal_sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Tell the model, and the guard, what kind of legal backing this turn actually has.

        ``corpus_available`` is False for EMPLOYMENT, CYBER_FRAUD, POLICE_COMPLAINT and
        GENERAL, which all declare ``corpus_ids=()``: for those domains retrieval returns
        nothing and no section number in the reply can be verified.
        ``case_law_available`` is a hard False - ``app/data/`` holds three statute files and
        no case law anywhere, so every judgment citation is unverifiable by construction.
        """
        domain = domain_registry.resolve(profile.category)
        return {
            "corpus_available": bool(domain.rag.corpus_ids),
            "verified_provisions": sum(
                1 for source in legal_sources if source.get("kind") == VERIFIED_PROVISION
            ),
            "case_law_available": False,
        }

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

    def _tag_response(
        self, response: ChatTurnResponse, mode: str, user_message: str = "",
    ) -> ChatTurnResponse:
        """The single funnel every return path passes through - including the offline
        limited_demo path, the 429 rate-limit fallback, the provider-error path, the safety
        short-circuit, the document gates and the two main.py call sites. The output guard
        is wired here so it still holds on the degraded paths, where a prompt cannot.
        """
        response.llm_provider = self.provider.status.provider
        response.llm_model = self.provider.status.model
        response.llm_mode = mode
        # user_message lets the guard recognise a digit run the user supplied this turn
        # (an order number, a landlord's phone) and leave it alone. It is passed on the
        # paths that carry model text; the deterministic composers need no protection
        # because the guard is a proven no-op on all of them.
        guarded = guard_reply(response.reply_text, response.case_profile, user_message)
        if guarded.redactions:
            # Controlled vocabulary only: rule names, case id, domain. Never the reply
            # text, the user's message, party names or amounts.
            logger.warning(
                "event=response_guard_redacted case_id=%s category=%s mode=%s rules=%s",
                getattr(response.case_profile, "case_id", None),
                getattr(response.case_profile, "category", None),
                mode,
                ",".join(item.as_log() for item in guarded.redactions),
            )
            response.reply_text = guarded.text
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
                continue
            if fact_key in DATE_FACT_FIELDS and (profile.fact_metadata or {}).get(fact_key, {}).get("year_known") is False:
                # H8. "July" is a non-empty string, so a year-less date used to
                # count as present and could be rendered straight into a date
                # slot on a legal document. A date whose year the user never
                # stated is not good enough for paperwork; ask for the year.
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
