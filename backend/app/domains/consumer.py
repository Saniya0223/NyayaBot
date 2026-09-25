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


CONSUMER_DOMAIN = DomainDefinition(
    id="CONSUMER",
    display_name="Consumer Rights & Defective Goods",
    case_title="Consumer Dispute",
    version="1.0",
    description="Consumer goods, services, delivery, refund, and seller/platform disputes.",
    aliases=("CONSUMER_DISPUTE",),
    classification_terms=("refund", "seller", "product", "defective", "warranty", "purchase", "bought", "order", "delivery", "amazon", "flipkart", "service", "shop", "merchant", "consumer", "सामान", "रिफंड"),
    fallback_priority=50,
    issue_types=(
        IssueTypeDefinition(id="NON_DELIVERY", display_name="Non-delivery or delayed delivery", aliases=("delayed delivery",)),
        IssueTypeDefinition(id="REFUND_NOT_RECEIVED", display_name="Refund not received", aliases=("refund refusal",)),
        IssueTypeDefinition(id="DEFECTIVE_PRODUCT", display_name="Defective product", aliases=("Defective Product / Refund Refusal",)),
        IssueTypeDefinition(id="SERVICE_DEFICIENCY", display_name="Service deficiency"),
        IssueTypeDefinition(id="SELLER_PLATFORM_DISPUTE", display_name="Seller or platform dispute"),
    ),
    default_issue_type_id="SELLER_PLATFORM_DISPUTE",
    minimum_context_any_of=("opposite_party_name", "seller_platform", "product_name", "purchase_timing"),
    facts=(
        FactDefinition(key="opposite_party_name", value_type=FactValueType.TEXT, meaning="seller, service provider, or platform involved", priority=QuestionPriority.ISSUE_IDENTIFICATION, aliases=("seller_name",)),
        FactDefinition(key="seller_platform", value_type=FactValueType.TEXT, meaning="marketplace or social platform where the seller was found", priority=QuestionPriority.ISSUE_IDENTIFICATION, required_for_understanding=False, aliases=("platform",)),
        FactDefinition(key="product_name", value_type=FactValueType.TEXT, meaning="product or service involved", priority=QuestionPriority.ISSUE_IDENTIFICATION),
        FactDefinition(key="incident_date", value_type=FactValueType.DATE, meaning="purchase, delivery, failure, or refund date relevant to the issue", priority=QuestionPriority.CORE_EVENT_FACTS, conversation_alternatives=("purchase_timing",)),
        FactDefinition(key="purchase_timing", value_type=FactValueType.TEXT, meaning="approximate time of purchase when an exact date is not known", priority=QuestionPriority.CORE_EVENT_FACTS, required_for_understanding=False),
        FactDefinition(key="advance_payment_made", value_type=FactValueType.BOOLEAN, meaning="whether advance payment was made", priority=QuestionPriority.CORE_EVENT_FACTS, required_for_understanding=False),
        FactDefinition(key="seller_contacted", value_type=FactValueType.BOOLEAN, meaning="whether the seller or platform has already been contacted", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN),
        FactDefinition(key="seller_response_received", value_type=FactValueType.BOOLEAN, meaning="whether the seller or platform replied after contact", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN, ask_when_fact="seller_contacted", ask_when_value=True, conversation_alternatives=("seller_response",)),
        FactDefinition(key="seller_response", value_type=FactValueType.TEXT, meaning="summary of the seller or platform reply", priority=QuestionPriority.ACTIONS_ALREADY_TAKEN, not_applicable_allowed=True, ask_when_fact="seller_response_received", ask_when_value=True, aliases=("seller_response_summary",)),
        FactDefinition(key="invoice_available", value_type=FactValueType.BOOLEAN, meaning="whether purchase or payment evidence is available", priority=QuestionPriority.EVIDENCE, evidence_related=True, evidence_type_id="invoice"),
        FactDefinition(key="desired_outcome", value_type=FactValueType.TEXT, meaning="outcome the user wants", priority=QuestionPriority.DESIRED_OUTCOME, required_for_understanding=False, aliases=("desired_resolution",)),
        FactDefinition(key="user_state", value_type=FactValueType.TEXT, meaning="State relevant to applicable forum and law", priority=QuestionPriority.JURISDICTION_WHEN_LEGALLY_RELEVANT, stage=FactStage.LEGAL_GUIDANCE, jurisdiction_related=True),
        FactDefinition(key="order_reference_id", value_type=FactValueType.IDENTIFIER, meaning="seller or platform order/reference identifier", priority=QuestionPriority.ADMINISTRATIVE_IDENTIFIERS, required_for_understanding=False, sensitive=True, aliases=("order_number",)),
        FactDefinition(key="payment_transaction_id", value_type=FactValueType.IDENTIFIER, meaning="payment transaction or bank reference identifier", priority=QuestionPriority.ADMINISTRATIVE_IDENTIFIERS, required_for_understanding=False, sensitive=True),
        FactDefinition(key="shipment_tracking_id", value_type=FactValueType.IDENTIFIER, meaning="shipment tracking or AWB identifier", priority=QuestionPriority.ADMINISTRATIVE_IDENTIFIERS, required_for_understanding=False, sensitive=True, aliases=("awb_id",)),
        FactDefinition(key="user_name", value_type=FactValueType.TEXT, meaning="complainant name required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
        FactDefinition(key="opposite_party_address", value_type=FactValueType.TEXT, meaning="recipient address required by a document", priority=QuestionPriority.DOCUMENT_ONLY_FIELDS, stage=FactStage.DOCUMENT, required_for_understanding=False, document_only=True, sensitive=True),
    ),
    actions=(
        ActionDefinition(id="formal_demand_sent", meaning="formal consumer grievance sent", target_workflow_stage="AWAITING_SELLER_RESPONSE"),
        ActionDefinition(id="response_rejected", meaning="seller response rejected or absent", target_workflow_stage="EDAAKHIL_COMPLAINT"),
    ),
    evidence=(
        EvidenceDefinition(id="invoice", purpose="purchase or payment record"),
        EvidenceDefinition(id="defect_photos", purpose="record of product condition or service problem"),
        EvidenceDefinition(id="support_tickets", purpose="seller or platform communications"),
        EvidenceDefinition(id="seller_rejection", purpose="written seller or platform response"),
    ),
    workflow_binding="CONSUMER",
    documents=(
        DocumentBinding(document_type="FORMAL_LEGAL_NOTICE", is_default=True),
        DocumentBinding(document_type="EDAAKHIL_COMPLAINT", workflow_stages=("EDAAKHIL_COMPLAINT",)),
    ),
    rag=RagRoutingPolicy(
        corpus_ids=("CONSUMER",),
        requires_state=False,
        official_sources=(OfficialSourceReference(title="Consumer Protection Act, 2019", authority="India Code", url="https://www.indiacode.nic.in/handle/123456789/21423"),),
    ),
    jurisdiction=JurisdictionPolicy(requirement=JurisdictionRequirement.REQUIRED_LATER, fact_key="user_state", rationale="State may affect forum and procedure after the dispute is understood."),
    professional_help=ProfessionalHelpPolicy(
        rules=(
            ProfessionalHelpRule(source="fact_true", key="seller_disputes_transaction", reason=Reason.SELLER_DISPUTES_TRANSACTION, level=HelpLevel.CONSIDER_LEGAL_HELP),
            ProfessionalHelpRule(source="fact_true", key="fraud_or_forgery_alleged", reason=Reason.FRAUD_OR_FORGERY_ALLEGED, level=HelpLevel.CONSIDER_LEGAL_HELP),
        ),
        reassess_on=(Trigger.GRIEVANCE_FAILED, Trigger.FORMAL_NOTICE_RECEIVED, Trigger.FORMAL_PROCEEDING_STARTED, Trigger.CASE_FACTS_BECAME_DISPUTED),
    ),
)
