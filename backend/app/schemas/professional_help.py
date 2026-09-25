"""Structured, advisory professional-help assessment contracts."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfessionalHelpLevel(str, Enum):
    SELF_HELP_REASONABLE = "SELF_HELP_REASONABLE"
    CONSIDER_LEGAL_HELP = "CONSIDER_LEGAL_HELP"
    LEGAL_HELP_RECOMMENDED = "LEGAL_HELP_RECOMMENDED"
    URGENT_LEGAL_HELP = "URGENT_LEGAL_HELP"


class ProfessionalType(str, Enum):
    ADVOCATE = "ADVOCATE"
    LEGAL_AID = "LEGAL_AID"
    SPECIALIST_LEGAL_COUNSEL = "SPECIALIST_LEGAL_COUNSEL"


class ProfessionalHelpReason(str, Enum):
    CURRENT_INFORMATION_LIMITED = "CURRENT_INFORMATION_LIMITED"
    EARLY_STAGE = "EARLY_STAGE"
    STANDARD_SELF_HELP_AVAILABLE = "STANDARD_SELF_HELP_AVAILABLE"
    FORMAL_LEGAL_NOTICE_RECEIVED = "FORMAL_LEGAL_NOTICE_RECEIVED"
    COURT_OR_TRIBUNAL_NOTICE_RECEIVED = "COURT_OR_TRIBUNAL_NOTICE_RECEIVED"
    FORMAL_PROCEEDING_STARTED = "FORMAL_PROCEEDING_STARTED"
    CRIMINAL_ALLEGATION = "CRIMINAL_ALLEGATION"
    ARREST_OR_POLICE_RISK = "ARREST_OR_POLICE_RISK"
    COMPLEX_FACTUAL_DISPUTE = "COMPLEX_FACTUAL_DISPUTE"
    MULTIPLE_PARTIES = "MULTIPLE_PARTIES"
    CROSS_JURISDICTION_COMPLEXITY = "CROSS_JURISDICTION_COMPLEXITY"
    COUNTERPARTY_REPRESENTED = "COUNTERPARTY_REPRESENTED"
    GRIEVANCE_FAILED = "GRIEVANCE_FAILED"
    REPEATED_ESCALATION_FAILED = "REPEATED_ESCALATION_FAILED"
    SELLER_DISPUTES_TRANSACTION = "SELLER_DISPUTES_TRANSACTION"
    FRAUD_OR_FORGERY_ALLEGED = "FRAUD_OR_FORGERY_ALLEGED"
    EVICTION_PROCEEDING_STARTED = "EVICTION_PROCEEDING_STARTED"
    PROPERTY_POSSESSION_AT_RISK = "PROPERTY_POSSESSION_AT_RISK"
    OWNERSHIP_DISPUTE = "OWNERSHIP_DISPUTE"
    TERMINATION_OCCURRED = "TERMINATION_OCCURRED"
    RETALIATION_ALLEGED = "RETALIATION_ALLEGED"
    COMPLEX_CONTRACT_DISPUTE = "COMPLEX_CONTRACT_DISPUTE"
    IDENTITY_THEFT = "IDENTITY_THEFT"
    ACCOUNT_FREEZE_COMPLICATION = "ACCOUNT_FREEZE_COMPLICATION"
    BANK_CLAIM_REJECTED = "BANK_CLAIM_REJECTED"
    POLICE_CASE_COMPLICATION = "POLICE_CASE_COMPLICATION"
    SERIOUS_SAFETY_CONTEXT = "SERIOUS_SAFETY_CONTEXT"


class ReassessTrigger(str, Enum):
    FORMAL_NOTICE_RECEIVED = "FORMAL_NOTICE_RECEIVED"
    FORMAL_PROCEEDING_STARTED = "FORMAL_PROCEEDING_STARTED"
    GRIEVANCE_FAILED = "GRIEVANCE_FAILED"
    BANK_REJECTED_CLAIM = "BANK_REJECTED_CLAIM"
    EVICTION_PROCEEDING_STARTED = "EVICTION_PROCEEDING_STARTED"
    TERMINATION_OCCURRED = "TERMINATION_OCCURRED"
    COUNTERPARTY_LAWYER_INVOLVED = "COUNTERPARTY_LAWYER_INVOLVED"
    VERIFIED_DEADLINE_IDENTIFIED = "VERIFIED_DEADLINE_IDENTIFIED"
    CASE_FACTS_BECAME_DISPUTED = "CASE_FACTS_BECAME_DISPUTED"


class ProfessionalHelpRule(BaseModel):
    """A declared signal, not a legal rule or representation requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["fact_true", "action_completed"]
    key: str = Field(min_length=2)
    reason: ProfessionalHelpReason
    level: ProfessionalHelpLevel


class ProfessionalHelpPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rules: tuple[ProfessionalHelpRule, ...] = ()
    reassess_on: tuple[ReassessTrigger, ...] = ()
    professional_types: tuple[ProfessionalType, ...] = (
        ProfessionalType.ADVOCATE, ProfessionalType.LEGAL_AID,
    )


class ProfessionalHelpAssessment(BaseModel):
    level: ProfessionalHelpLevel
    reason_codes: list[ProfessionalHelpReason] = Field(default_factory=list)
    professional_types: list[ProfessionalType] = Field(default_factory=list)
    urgency: Literal["ROUTINE", "PROMPT"] = "ROUTINE"
    reassess_on: list[ReassessTrigger] = Field(default_factory=list)
    relevant_known_signals: list[str] = Field(default_factory=list)
    unknown_relevant_signals: list[str] = Field(default_factory=list)
    assessment_version: str = "1.0"
