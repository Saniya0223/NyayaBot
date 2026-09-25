"""Typed contracts for NyayaBot legal domains.

Domain definitions describe product structure, never substantive legal advice.
Workflow state, safety decisions, retrieval content, and document rendering stay
owned by their existing engines.
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.professional_help import ProfessionalHelpPolicy


DomainId = Literal[
    "CONSUMER", "EMPLOYMENT", "HOUSING_TENANT", "CYBER_FRAUD",
    "POLICE_COMPLAINT", "GENERAL",
]

ActionId = Literal[
    "informal_request_made", "formal_demand_sent", "response_rejected",
    "response_accepted", "case_resolved", "bank_reported",
    "cybercrime_reported", "police_complaint_submitted",
]

EvidenceId = Literal[
    "rental_agreement", "deposit_payment_proof", "move_out_photos",
    "landlord_chat", "invoice", "defect_photos", "support_tickets",
    "seller_rejection", "offer_letter", "salary_slips", "hr_emails", "relieving_letter",
    "upi_receipt", "scammer_chat", "bank_complaint_ack", "incident_proof",
    "complaint_copy", "speed_post_receipt",
]


class FactValueType(str, Enum):
    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"
    MONEY = "MONEY"
    DATE = "DATE"
    TEXT_LIST = "TEXT_LIST"
    IDENTIFIER = "IDENTIFIER"


class QuestionPriority(IntEnum):
    """Semantic reason a fact should be requested, in conversational order."""

    SAFETY_OR_URGENCY = 10
    ISSUE_IDENTIFICATION = 20
    CORE_EVENT_FACTS = 30
    JURISDICTION_WHEN_LEGALLY_RELEVANT = 40
    ACTIONS_ALREADY_TAKEN = 50
    EVIDENCE = 60
    DESIRED_OUTCOME = 70
    ADMINISTRATIVE_IDENTIFIERS = 80
    DOCUMENT_ONLY_FIELDS = 90


class FactStage(IntEnum):
    ISSUE_IDENTIFICATION = 10
    CASE_UNDERSTANDING = 20
    LEGAL_GUIDANCE = 30
    DOCUMENT = 40


class JurisdictionRequirement(str, Enum):
    REQUIRED_EARLY = "REQUIRED_EARLY"
    REQUIRED_LATER = "REQUIRED_LATER"
    OPTIONAL = "OPTIONAL"
    NOT_CURRENTLY_NEEDED = "NOT_CURRENTLY_NEEDED"


class FactState(str, Enum):
    UNKNOWN = "UNKNOWN"
    KNOWN = "KNOWN"
    FALSE = "FALSE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FactPurpose(str, Enum):
    CORE_CONTEXT = "core_context"
    ACTION_CONTEXT = "action_context"
    EVIDENCE_CONTEXT = "evidence_context"
    JURISDICTION_CONTEXT = "jurisdiction_context"
    DESIRED_OUTCOME = "desired_outcome"
    ADMINISTRATIVE_IDENTIFIER = "administrative_identifier"
    DOCUMENT_ONLY = "document_only"


class FactSourceKind(str, Enum):
    USER_PROVIDED = "USER_PROVIDED"
    CONVERSATION_EXTRACTION = "CONVERSATION_EXTRACTION"
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"


class VerificationState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    DOCUMENT_SUPPORTED = "DOCUMENT_SUPPORTED"
    CONFLICTED = "CONFLICTED"


class FactProvenance(BaseModel):
    """Future-ready provenance view; compatible with current fact_metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: FactSourceKind
    verification: VerificationState = VerificationState.UNVERIFIED
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    source_reference: Optional[str] = None


class FactDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value_type: FactValueType
    meaning: str = Field(min_length=3)
    priority: QuestionPriority
    stage: FactStage = FactStage.CASE_UNDERSTANDING
    required_for_understanding: bool = True
    jurisdiction_related: bool = False
    evidence_related: bool = False
    document_only: bool = False
    false_is_known: bool = True
    not_applicable_allowed: bool = False
    sensitive: bool = False
    zero_is_unknown: bool = False
    aliases: tuple[str, ...] = ()
    issue_type_ids: tuple[str, ...] = ()
    evidence_type_id: Optional[str] = None
    normalization: Optional[str] = None
    ask_when_fact: Optional[str] = None
    ask_when_value: Optional[bool] = None
    conversation_alternatives: tuple[str, ...] = ()

    @property
    def purpose(self) -> FactPurpose:
        """Derive conversational purpose from the domain's existing priority."""
        if self.document_only:
            return FactPurpose.DOCUMENT_ONLY
        return {
            QuestionPriority.ACTIONS_ALREADY_TAKEN: FactPurpose.ACTION_CONTEXT,
            QuestionPriority.EVIDENCE: FactPurpose.EVIDENCE_CONTEXT,
            QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT: FactPurpose.JURISDICTION_CONTEXT,
            QuestionPriority.DESIRED_OUTCOME: FactPurpose.DESIRED_OUTCOME,
            QuestionPriority.ADMINISTRATIVE_IDENTIFIERS: FactPurpose.ADMINISTRATIVE_IDENTIFIER,
        }.get(self.priority, FactPurpose.CORE_CONTEXT)

    @model_validator(mode="after")
    def validate_semantics(self) -> "FactDefinition":
        if self.document_only and self.required_for_understanding:
            raise ValueError(f"document-only fact {self.key} cannot be required for understanding")
        if self.document_only and self.stage != FactStage.DOCUMENT:
            raise ValueError(f"document-only fact {self.key} must use DOCUMENT stage")
        if self.jurisdiction_related and self.priority != QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT:
            raise ValueError(f"jurisdiction fact {self.key} must use jurisdiction priority")
        if self.evidence_type_id and not self.evidence_related:
            raise ValueError(f"fact {self.key} has evidence binding but is not evidence-related")
        if self.ask_when_value is not None and not self.ask_when_fact:
            raise ValueError(f"fact {self.key} has a condition without a condition fact")
        return self


class IssueTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str = Field(min_length=3)
    aliases: tuple[str, ...] = ()


class EvidenceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: EvidenceId
    purpose: str = Field(min_length=3)


class ActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ActionId
    meaning: str = Field(min_length=3)
    target_workflow_stage: Optional[str] = None
    fact_updates: tuple[tuple[str, Any], ...] = ()


class DocumentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    minimum_readiness: str = "READY_FOR_ACTION"
    workflow_stages: tuple[str, ...] = ()
    is_default: bool = False


class OfficialSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    authority: str
    url: str


class RagRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_ids: tuple[str, ...] = ()
    requires_state: bool = False
    include_workflow_stage: bool = True
    include_party_role: bool = False
    official_sources: tuple[OfficialSourceReference, ...] = ()


class JurisdictionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: JurisdictionRequirement
    fact_key: Optional[str] = None
    rationale: str = Field(min_length=3)

    @model_validator(mode="after")
    def validate_fact_key(self) -> "JurisdictionPolicy":
        needs_fact = self.requirement in {
            JurisdictionRequirement.REQUIRED_EARLY,
            JurisdictionRequirement.REQUIRED_LATER,
        }
        if needs_fact and not self.fact_key:
            raise ValueError("required jurisdiction policy needs a fact_key")
        return self


class SafetyIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    global_triage_precedes_domain: bool = True
    cross_domain_escalation_supported: bool = True
    notes: Optional[str] = None


class DomainDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str = Field(min_length=3)
    case_title: str = Field(min_length=3)
    version: str = Field(pattern=r"^\d+\.\d+$")
    description: str = Field(min_length=3)
    aliases: tuple[str, ...] = ()
    classification_terms: tuple[str, ...] = ()
    fallback_priority: int = Field(default=100, ge=0)
    issue_types: tuple[IssueTypeDefinition, ...]
    default_issue_type_id: str
    facts: tuple[FactDefinition, ...]
    minimum_context_any_of: tuple[str, ...] = ()
    actions: tuple[ActionDefinition, ...] = ()
    evidence: tuple[EvidenceDefinition, ...] = ()
    workflow_binding: str
    documents: tuple[DocumentBinding, ...] = ()
    rag: RagRoutingPolicy = RagRoutingPolicy()
    jurisdiction: JurisdictionPolicy
    safety: SafetyIntegration = SafetyIntegration()
    professional_help: ProfessionalHelpPolicy = ProfessionalHelpPolicy()

    @model_validator(mode="after")
    def validate_definition(self) -> "DomainDefinition":
        def unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} in domain {self.id}")

        issue_ids = [item.id for item in self.issue_types]
        fact_keys = [item.key for item in self.facts]
        unique(issue_ids, "issue type IDs")
        unique(fact_keys, "fact keys")
        if set(self.minimum_context_any_of) - set(fact_keys):
            raise ValueError(f"minimum context references unknown facts in {self.id}")
        unique([item.id for item in self.actions], "action IDs")
        unique([item.id for item in self.evidence], "evidence IDs")
        unique([item.document_type for item in self.documents], "document bindings")
        if self.default_issue_type_id not in issue_ids:
            raise ValueError(f"default issue type is not registered for {self.id}")
        for fact in self.facts:
            unknown = set(fact.issue_type_ids) - set(issue_ids)
            if unknown:
                raise ValueError(f"fact {fact.key} references unknown issue types: {sorted(unknown)}")
            if fact.ask_when_fact and fact.ask_when_fact not in fact_keys:
                raise ValueError(f"fact {fact.key} references unknown condition {fact.ask_when_fact}")
            if set(fact.conversation_alternatives) - set(fact_keys):
                raise ValueError(f"fact {fact.key} references unknown conversation alternatives")
        evidence_ids = {item.id for item in self.evidence}
        for fact in self.facts:
            if fact.evidence_type_id and fact.evidence_type_id not in evidence_ids:
                raise ValueError(f"fact {fact.key} references unknown evidence {fact.evidence_type_id}")
        if self.jurisdiction.fact_key and self.jurisdiction.fact_key not in fact_keys:
            raise ValueError(f"jurisdiction fact {self.jurisdiction.fact_key} is not defined")
        return self

    def normalize_issue_type(self, value: Optional[str]) -> str:
        normalized = (value or "").strip().casefold()
        for issue in self.issue_types:
            candidates = {issue.id.casefold(), issue.display_name.casefold(), *(item.casefold() for item in issue.aliases)}
            if normalized in candidates:
                return issue.id
        return self.default_issue_type_id

    def ordered_facts(self, issue_type: Optional[str], *, include_document: bool = False) -> tuple[FactDefinition, ...]:
        issue_id = self.normalize_issue_type(issue_type)
        values = [
            fact for fact in self.facts
            if (not fact.issue_type_ids or issue_id in fact.issue_type_ids)
            and (include_document or not fact.document_only)
        ]
        return tuple(sorted(values, key=lambda fact: (fact.stage, fact.priority, fact.key)))

    def action(self, action_id: str) -> Optional[ActionDefinition]:
        return next((item for item in self.actions if item.id == action_id), None)

    def document_for_stage(self, stage: str = "") -> Optional[DocumentBinding]:
        stage_specific = next((item for item in self.documents if stage in item.workflow_stages), None)
        return stage_specific or next((item for item in self.documents if item.is_default), None)


def fact_state(value: Any, *, present: bool, definition: FactDefinition) -> FactState:
    """Distinguish absence from explicit False and explicit not-applicable."""
    if not present or value is None:
        return FactState.UNKNOWN
    if isinstance(value, str):
        stripped = value.strip()
        if definition.not_applicable_allowed and stripped.casefold() in {"not_applicable", "not applicable", "n/a"}:
            return FactState.NOT_APPLICABLE
        if not stripped:
            return FactState.UNKNOWN
    if value is False:
        return FactState.FALSE if definition.false_is_known else FactState.UNKNOWN
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return FactState.UNKNOWN
    if definition.zero_is_unknown and isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return FactState.UNKNOWN
    return FactState.KNOWN
