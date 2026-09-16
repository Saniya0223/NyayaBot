from app.domains.contracts import (
    ActionDefinition, DocumentBinding, DomainDefinition, EvidenceDefinition,
    FactDefinition, FactStage, FactValueType, IssueTypeDefinition,
    JurisdictionPolicy, JurisdictionRequirement, OfficialSourceReference, QuestionPriority,
    RagRoutingPolicy,
)


EMPLOYMENT_DOMAIN = DomainDefinition(
    id="EMPLOYMENT",
    display_name="Employment & Labour Dispute",
    case_title="Employment Dispute",
    version="1.0",
    description="Employment disputes concerning salary, termination, dues, and workplace grievances.",
    classification_terms=("salary", "employer", "wages", "boss", "vetan", "unpaid salary", "termination", "तनख्वाह", "वेतन"),
    fallback_priority=20,
    issue_types=(
        IssueTypeDefinition(id="UNPAID_DELAYED_SALARY", display_name="Unpaid or delayed salary", aliases=("Unpaid Salary / Delayed Wages",)),
        IssueTypeDefinition(id="TERMINATION_ISSUE", display_name="Termination-related issue"),
        IssueTypeDefinition(id="EMPLOYMENT_DUES", display_name="Employment dues"),
        IssueTypeDefinition(id="WORKPLACE_GRIEVANCE", display_name="Workplace grievance"),
    ),
    default_issue_type_id="UNPAID_DELAYED_SALARY",
    facts=(
        FactDefinition(key="opposite_party_name", value_type=FactValueType.TEXT, meaning="employer involved", priority=QuestionPriority.ISSUE_IDENTIFICATION),
        FactDefinition(key="unpaid_months", value_type=FactValueType.TEXT_LIST, meaning="months or period for which salary is unpaid", priority=QuestionPriority.CORE_EVENT_FACTS),
        FactDefinition(key="monthly_salary", value_type=FactValueType.MONEY, meaning="monthly salary relevant to the dispute", priority=QuestionPriority.CORE_EVENT_FACTS, zero_is_unknown=True),
        FactDefinition(key="hr_contacted", value_type=FactValueType.BOOLEAN, meaning="whether HR or management has been contacted", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN),
        FactDefinition(key="employment_proof_available", value_type=FactValueType.BOOLEAN, meaning="whether employment and salary records are available", priority=QuestionPriority.EVIDENCE, evidence_related=True, evidence_type_id="offer_letter"),
        FactDefinition(key="user_state", value_type=FactValueType.TEXT, meaning="State relevant to workplace and authority", priority=QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT, stage=FactStage.LEGAL_GUIDANCE, jurisdiction_related=True),
        FactDefinition(key="employee_role", value_type=FactValueType.TEXT, meaning="employee role or work classification", priority=QuestionPriority.CORE_EVENT_FACTS, required_for_understanding=False),
        FactDefinition(key="user_name", value_type=FactValueType.TEXT, meaning="employee name required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
        FactDefinition(key="opposite_party_address", value_type=FactValueType.TEXT, meaning="employer address required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
    ),
    actions=(
        ActionDefinition(id="formal_demand_sent", meaning="written salary demand sent", target_workflow_stage="AWAITING_EMPLOYER_RESPONSE"),
        ActionDefinition(id="response_rejected", meaning="employer response rejected or absent", target_workflow_stage="LABOUR_COMMISSIONER_COMPLAINT"),
    ),
    evidence=(
        EvidenceDefinition(id="offer_letter", purpose="employment terms"),
        EvidenceDefinition(id="salary_slips", purpose="salary history"),
        EvidenceDefinition(id="hr_emails", purpose="HR or management communications"),
        EvidenceDefinition(id="relieving_letter", purpose="end-of-employment communication"),
    ),
    workflow_binding="EMPLOYMENT",
    documents=(DocumentBinding(document_type="SALARY_DEMAND_NOTICE", is_default=True),),
    rag=RagRoutingPolicy(
        corpus_ids=(),
        requires_state=True,
        official_sources=(OfficialSourceReference(title="Code on Wages, 2019", authority="Ministry of Labour & Employment", url="https://labour.gov.in/sites/default/files/the_code_on_wages_2019_no._29_of_2019.pdf"),),
    ),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.REQUIRED_LATER, fact_key="user_state", rationale="Applicable authority may depend on State, role, and establishment."),
)
