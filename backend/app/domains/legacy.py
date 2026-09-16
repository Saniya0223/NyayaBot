"""Compatibility definitions that preserve existing Police and General cases.

These are intentionally shallow in Phase 1. Safety remains global and no new
police/family legal corpus is introduced here.
"""

from app.domains.contracts import (
    ActionDefinition, DocumentBinding, DomainDefinition, EvidenceDefinition,
    FactDefinition, FactStage, FactValueType, IssueTypeDefinition,
    JurisdictionPolicy, JurisdictionRequirement, OfficialSourceReference, QuestionPriority,
    RagRoutingPolicy, SafetyIntegration,
)


POLICE_COMPLAINT_DOMAIN = DomainDefinition(
    id="POLICE_COMPLAINT",
    display_name="Police Complaint & FIR Assistance",
    case_title="Police Complaint Assistance",
    version="1.0",
    description="Compatibility binding for the existing police-complaint workflow.",
    classification_terms=("police", "fir", "thana", "sho", "complaint refusal", "threat", "threaten", "intimidat", "harass", "stalk", "abuse", "violence", "assault", "weapon", "knife", "blackmail", "extort", "चौकी", "पुलिस", "थाना", "धमकी", "मारपीट", "हिंसा"),
    fallback_priority=40,
    issue_types=(IssueTypeDefinition(id="POLICE_COMPLAINT_ASSISTANCE", display_name="Police complaint assistance", aliases=("Police Refusal to Register FIR / Formal Written Complaint",)),),
    default_issue_type_id="POLICE_COMPLAINT_ASSISTANCE",
    facts=(
        FactDefinition(key="incident_date", value_type=FactValueType.DATE, meaning="date of the reported incident", priority=QuestionPriority.CORE_EVENT_FACTS),
        FactDefinition(key="threat_details", value_type=FactValueType.TEXT, meaning="facts of the threat or incident", priority=QuestionPriority.CORE_EVENT_FACTS),
        FactDefinition(key="police_contacted", value_type=FactValueType.BOOLEAN, meaning="whether police have already been contacted", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN, aliases=("police_approached",)),
        FactDefinition(key="evidence_available", value_type=FactValueType.BOOLEAN, meaning="whether incident evidence is available", priority=QuestionPriority.EVIDENCE, evidence_related=True, evidence_type_id="incident_proof"),
        FactDefinition(key="user_state", value_type=FactValueType.TEXT, meaning="State relevant to the police or authority", priority=QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT, stage=FactStage.LEGAL_GUIDANCE, jurisdiction_related=True),
        FactDefinition(key="police_station_name", value_type=FactValueType.TEXT, meaning="police station name required for a complaint document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True),
    ),
    actions=(
        ActionDefinition(id="police_complaint_submitted", meaning="written complaint submitted", target_workflow_stage="FORMAL_WRITTEN_COMPLAINT", fact_updates=(("written_complaint_available", True),)),
        ActionDefinition(id="response_rejected", meaning="local complaint refused or unresolved", target_workflow_stage="SP_ESCALATION"),
    ),
    evidence=(
        EvidenceDefinition(id="incident_proof", purpose="record supporting the incident report"),
        EvidenceDefinition(id="complaint_copy", purpose="copy of an earlier written complaint"),
        EvidenceDefinition(id="speed_post_receipt", purpose="proof of written escalation delivery"),
    ),
    workflow_binding="POLICE_COMPLAINT",
    documents=(DocumentBinding(document_type="POLICE_COMPLAINT_BNSS", is_default=True),),
    rag=RagRoutingPolicy(
        corpus_ids=(),
        requires_state=False,
        official_sources=(OfficialSourceReference(title="Bharatiya Nagarik Suraksha Sanhita, 2023", authority="India Code", url="https://www.indiacode.nic.in/handle/123456789/21419"),),
    ),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.REQUIRED_LATER, fact_key="user_state", rationale="Local authority and procedure may depend on location after immediate safety is addressed."),
    safety=SafetyIntegration(global_triage_precedes_domain=True, cross_domain_escalation_supported=True, notes="Threat and domestic-partner detection remains in the global safety engine."),
)


GENERAL_DOMAIN = DomainDefinition(
    id="GENERAL",
    display_name="General Legal Information",
    case_title="Legal Information Request",
    version="1.0",
    description="Safe fallback for an issue that is not yet classified.",
    fallback_priority=1000,
    issue_types=(IssueTypeDefinition(id="UNCLASSIFIED", display_name="Unclassified legal issue", aliases=("Grievance",)),),
    default_issue_type_id="UNCLASSIFIED",
    facts=(FactDefinition(key="issue_description", value_type=FactValueType.TEXT, meaning="description of what happened", priority=QuestionPriority.ISSUE_IDENTIFICATION),),
    workflow_binding="GENERAL",
    rag=RagRoutingPolicy(corpus_ids=()),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.NOT_CURRENTLY_NEEDED, rationale="Jurisdiction is deferred until the issue is identified."),
)
