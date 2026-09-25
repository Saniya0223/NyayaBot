from app.domains.contracts import (
    ActionDefinition, DocumentBinding, DomainDefinition, EvidenceDefinition,
    FactDefinition, FactStage, FactValueType, IssueTypeDefinition,
    JurisdictionPolicy, JurisdictionRequirement, OfficialSourceReference, QuestionPriority,
    RagRoutingPolicy,
)
from app.schemas.professional_help import (
    ProfessionalHelpLevel as HelpLevel, ProfessionalHelpPolicy, ProfessionalHelpReason as Reason,
    ProfessionalHelpRule, ReassessTrigger as Trigger,
)


TENANCY_DOMAIN = DomainDefinition(
    id="HOUSING_TENANT",
    display_name="Housing & Tenancy Dispute",
    case_title="Housing & Tenancy Dispute",
    version="1.0",
    description="Tenant-landlord disputes concerning deposits, rent, notices, and property condition.",
    aliases=("TENANCY", "HOUSING"),
    classification_terms=("landlord", "tenant", "deposit", "rent", "flat", "kiraya", "makan malik", "security deposit", "eviction", "lease", "मकान", "किराया"),
    fallback_priority=10,
    issue_types=(
        IssueTypeDefinition(id="SECURITY_DEPOSIT_DISPUTE", display_name="Security deposit dispute", aliases=("Security Deposit Withholding",)),
        IssueTypeDefinition(id="RENT_PAYMENT_DISPUTE", display_name="Rent or payment dispute"),
        IssueTypeDefinition(id="EVICTION_NOTICE", display_name="Eviction or notice issue"),
        IssueTypeDefinition(id="PROPERTY_CONDITION", display_name="Repair or property-condition dispute"),
    ),
    default_issue_type_id="SECURITY_DEPOSIT_DISPUTE",
    minimum_context_any_of=("vacating_date", "landlord_reason", "opposite_party_name"),
    facts=(
        FactDefinition(key="opposite_party_name", value_type=FactValueType.TEXT, meaning="landlord or property manager involved", priority=QuestionPriority.ISSUE_IDENTIFICATION, required_for_understanding=False),
        FactDefinition(key="vacating_date", value_type=FactValueType.DATE, meaning="date the tenant left or expects to leave", priority=QuestionPriority.CORE_EVENT_FACTS),
        FactDefinition(key="landlord_reason", value_type=FactValueType.TEXT, meaning="reason given by the landlord", priority=QuestionPriority.CORE_EVENT_FACTS, not_applicable_allowed=True),
        FactDefinition(key="landlord_contacted", value_type=FactValueType.BOOLEAN, meaning="whether the landlord has been asked to resolve the issue", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN),
        FactDefinition(key="rental_agreement_available", value_type=FactValueType.BOOLEAN, meaning="whether the rental or lease agreement is available", priority=QuestionPriority.EVIDENCE, evidence_related=True, evidence_type_id="rental_agreement", aliases=("rental_agreement_exists",)),
        FactDefinition(key="deposit_payment_proof_available", value_type=FactValueType.BOOLEAN, meaning="whether proof of deposit payment is available", priority=QuestionPriority.EVIDENCE, evidence_related=True, evidence_type_id="deposit_payment_proof"),
        FactDefinition(key="user_state", value_type=FactValueType.TEXT, meaning="State where the property is located", priority=QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT, stage=FactStage.LEGAL_GUIDANCE, jurisdiction_related=True),
        FactDefinition(key="property_address", value_type=FactValueType.TEXT, meaning="property address required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
        FactDefinition(key="user_name", value_type=FactValueType.TEXT, meaning="tenant name required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
    ),
    actions=(
        ActionDefinition(id="formal_demand_sent", meaning="formal tenancy demand sent", target_workflow_stage="AWAITING_LANDLORD_RESPONSE"),
        ActionDefinition(id="response_rejected", meaning="landlord response rejected or absent", target_workflow_stage="RENT_AUTHORITY_ESCALATION"),
    ),
    evidence=(
        EvidenceDefinition(id="rental_agreement", purpose="tenancy terms"),
        EvidenceDefinition(id="deposit_payment_proof", purpose="record of deposit payment"),
        EvidenceDefinition(id="move_out_photos", purpose="record of property condition"),
        EvidenceDefinition(id="landlord_chat", purpose="landlord communications"),
    ),
    workflow_binding="HOUSING_TENANT",
    documents=(DocumentBinding(document_type="TENANT_DEMAND_NOTICE", is_default=True),),
    rag=RagRoutingPolicy(
        corpus_ids=("TENANCY",),
        requires_state=True,
        official_sources=(OfficialSourceReference(title="Model Tenancy Act, 2021 (state adoption must be checked)", authority="Ministry of Housing and Urban Affairs", url="https://mohua.gov.in/upload/uploadfiles/files/Model-Tenancy-Act-English-02_06_2021.pdf"),),
    ),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.REQUIRED_LATER, fact_key="user_state", rationale="Applicable tenancy rules and forum depend on the property State."),
    professional_help=ProfessionalHelpPolicy(
        rules=(
            ProfessionalHelpRule(source="fact_true", key="eviction_proceeding_started", reason=Reason.EVICTION_PROCEEDING_STARTED, level=HelpLevel.LEGAL_HELP_RECOMMENDED),
            ProfessionalHelpRule(source="fact_true", key="possession_at_risk", reason=Reason.PROPERTY_POSSESSION_AT_RISK, level=HelpLevel.LEGAL_HELP_RECOMMENDED),
            ProfessionalHelpRule(source="fact_true", key="ownership_dispute", reason=Reason.OWNERSHIP_DISPUTE, level=HelpLevel.LEGAL_HELP_RECOMMENDED),
        ),
        reassess_on=(Trigger.EVICTION_PROCEEDING_STARTED, Trigger.FORMAL_NOTICE_RECEIVED, Trigger.FORMAL_PROCEEDING_STARTED, Trigger.GRIEVANCE_FAILED),
    ),
)
