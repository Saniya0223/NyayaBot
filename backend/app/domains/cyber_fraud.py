from app.domains.contracts import (
    ActionDefinition, DocumentBinding, DomainDefinition, EvidenceDefinition,
    FactDefinition, FactStage, FactValueType, IssueTypeDefinition, JurisdictionPolicy,
    JurisdictionRequirement, OfficialSourceReference, QuestionPriority, RagRoutingPolicy,
)


CYBER_FRAUD_DOMAIN = DomainDefinition(
    id="CYBER_FRAUD",
    display_name="Cybercrime & Online Fraud",
    case_title="Cyber Financial Fraud",
    version="1.0",
    description="Online scams, account compromise, and electronic payment fraud.",
    aliases=("CYBER",),
    classification_terms=("upi", "cyber", "phishing", "otp", "bank fraud", "online fraud", "scam", "hacked", "card fraud"),
    fallback_priority=30,
    issue_types=(
        IssueTypeDefinition(id="UPI_BANK_TRANSFER_FRAUD", display_name="UPI or bank-transfer fraud", aliases=("UPI / Banking Scam / Financial Fraud",)),
        IssueTypeDefinition(id="CARD_PAYMENT_FRAUD", display_name="Card or payment fraud"),
        IssueTypeDefinition(id="ACCOUNT_TAKEOVER", display_name="Account takeover"),
        IssueTypeDefinition(id="ONLINE_SCAM", display_name="Online scam or fraud"),
    ),
    default_issue_type_id="ONLINE_SCAM",
    facts=(
        FactDefinition(key="incident_date", value_type=FactValueType.DATE, meaning="date or time of the fraudulent event", priority=QuestionPriority.SAFETY_OR_URGENCY),
        FactDefinition(key="bank_name", value_type=FactValueType.TEXT, meaning="bank or payment service involved", priority=QuestionPriority.CORE_EVENT_FACTS),
        FactDefinition(key="bank_reported", value_type=FactValueType.BOOLEAN, meaning="whether the bank or payment provider has been alerted", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN),
        FactDefinition(key="cyber_reported", value_type=FactValueType.BOOLEAN, meaning="whether the cyber-fraud reporting channel has been contacted", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN),
        FactDefinition(key="transaction_id", value_type=FactValueType.IDENTIFIER, meaning="payment transaction, UTR, or bank reference identifier", priority=QuestionPriority.ADMINISTRATIVE_IDENTIFIERS, sensitive=True, aliases=("transaction_ref", "payment_transaction_id")),
        FactDefinition(key="scam_method", value_type=FactValueType.TEXT, meaning="how the scam or account compromise occurred", priority=QuestionPriority.CORE_EVENT_FACTS, required_for_understanding=False),
        FactDefinition(key="user_state", value_type=FactValueType.TEXT, meaning="State relevant to local escalation", priority=QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT, required_for_understanding=False, jurisdiction_related=True),
        FactDefinition(key="user_name", value_type=FactValueType.TEXT, meaning="complainant name required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
    ),
    actions=(
        ActionDefinition(id="bank_reported", meaning="bank or payment provider alerted", target_workflow_stage="BANK_REPORTED", fact_updates=(("bank_reported", True),)),
        ActionDefinition(id="cybercrime_reported", meaning="cybercrime portal report made", target_workflow_stage="CYBERCRIME_PORTAL_FILED", fact_updates=(("cyber_reported", True),)),
        ActionDefinition(id="response_rejected", meaning="recovery response rejected or absent", target_workflow_stage="POLICE_FIR_ESCALATION"),
    ),
    evidence=(
        EvidenceDefinition(id="upi_receipt", purpose="payment transaction record"),
        EvidenceDefinition(id="scammer_chat", purpose="fraudster communications"),
        EvidenceDefinition(id="bank_complaint_ack", purpose="bank reporting acknowledgement"),
    ),
    workflow_binding="CYBER_FRAUD",
    documents=(DocumentBinding(document_type="CYBERCRIME_BANK_FREEZE", is_default=True),),
    rag=RagRoutingPolicy(
        corpus_ids=(),
        requires_state=False,
        official_sources=(
            OfficialSourceReference(title="National Cyber Crime Reporting Portal", authority="Indian Cyber Crime Coordination Centre", url="https://www.cybercrime.gov.in/"),
            OfficialSourceReference(title="Customer protection for unauthorised electronic transactions", authority="Reserve Bank of India", url="https://www.rbi.org.in/commonman/Upload/English/Notification/PDFs/NOTI1506072017.PDF"),
        ),
    ),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.OPTIONAL, fact_key="user_state", rationale="State can support later local escalation but is not required for urgent initial reporting."),
)
