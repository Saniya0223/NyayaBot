"""Curated statutory citations the product already prints inside generated documents.

Moved out of ``doc_generator.generate_document`` (where it was a local variable) so
there is exactly one copy and ``response_guard`` can read it. It is an *input* to the
citation allowlist, never a target: several of these provisions are in no RAG corpus
(Indian Contract Act s.73, BNSS s.173, IT Act s.66D, RTI s.6(1), RBI/2017-18/15), so
without them the guard would generate a notice citing IT Act s.66D and then delete
"s.66D" from the chat message that explains the same notice.

Human-curated content. Nothing here may be added by a model or by a fix loop.
"""

from __future__ import annotations

from typing import Any

# document_type -> citations printed on that document
DOCUMENT_CITATIONS: dict[str, list[dict[str, Any]]] = {
    "FORMAL_LEGAL_NOTICE": [
        {"act": "Consumer Protection Act, 2019", "section": "Section 2(11)", "title": "Deficiency in service"},
        {"act": "Consumer Protection Act, 2019", "section": "Section 2(47)", "title": "Unfair trade practice"},
    ],
    "EDAAKHIL_COMPLAINT": [
        {"act": "Consumer Protection Act, 2019", "section": "Section 35", "title": "Manner in which complaint shall be made"},
    ],
    "TENANT_DEMAND_NOTICE": [
        {"act": "Indian Contract Act, 1872", "section": "Section 73", "title": "Compensation for breach of contract"},
    ],
    "SALARY_DEMAND_NOTICE": [
        {"act": "Applicable employment and wage law", "section": "State/fact specific", "title": "Payment of earned wages"},
    ],
    "POLICE_COMPLAINT_BNSS": [
        {"act": "Bharatiya Nagarik Suraksha Sanhita, 2023", "section": "Section 173", "title": "Information in cognizable cases"},
    ],
    "CYBERCRIME_BANK_FREEZE": [
        {"act": "Information Technology Act, 2000", "section": "Section 66D", "title": "Cheating by personation using computer resource"},
        {"act": "RBI/2017-18/15", "section": "Paragraphs 6-10", "title": "Customer liability for unauthorised electronic transactions"},
    ],
    "RTI_SEC6": [
        {"act": "Right to Information Act, 2005", "section": "Section 6(1)", "title": "Request for obtaining information"},
    ],
}


def citations_for_document(doc_type: str) -> list[dict[str, Any]]:
    return DOCUMENT_CITATIONS.get(doc_type, [])
