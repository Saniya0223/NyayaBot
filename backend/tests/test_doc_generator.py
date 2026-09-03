import os
import pytest
from app.services.doc_generator import doc_generator
from app.agents.intake_node import IntakeFactExtractor

def test_document_generation_notice():
    narrative = "I purchased an air conditioner from Reliance Digital on 10-02-2026 for Rs. 38,000. It never cooled and service technician refused to replace it."
    facts = IntakeFactExtractor.extract_facts(narrative)
    
    doc = doc_generator.generate_document(
        case_id="test-case-12345",
        doc_type="FORMAL_LEGAL_NOTICE",
        fact_graph=facts,
        appropriate_forum="District Consumer Disputes Redressal Commission, Pune"
    )
    
    assert doc.title is not None
    assert "Reliance Digital" in doc.content_html
    assert "38,000" in doc.content_html
    assert "Section 2(11)" in doc.content_html
    assert doc.pdf_download_url is not None
