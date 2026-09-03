import re
from typing import Dict, Any, List, Tuple
from app.schemas.fact_graph import (
    FactGraphSchema, PartyInfo, TimelineEvent, FinancialBreakdown, EvidenceItem
)
from app.services.pii_masker import PIIMasker

class IntakeFactExtractor:
    """
    Extracts structured facts from unstructured natural language grievances,
    evaluates completeness, and generates context-aware follow-up clarification questions.
    """

    CURRENCY_REGEX = re.compile(r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{2})?)|([\d,]+)\s*(?:rupees|rs|inr)', re.IGNORECASE)
    DATE_REGEX = re.compile(r'\b(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})\b', re.IGNORECASE)

    @classmethod
    def extract_facts(cls, narrative: str, initial_data: Dict[str, Any] = None) -> FactGraphSchema:
        initial_data = initial_data or {}
        sanitized_narrative, _ = PIIMasker.mask_text(narrative)

        # 1. Extract Amounts
        amounts = []
        for match in cls.CURRENCY_REGEX.finditer(sanitized_narrative):
            val_str = match.group(1) or match.group(2)
            if val_str:
                clean_val = val_str.replace(',', '')
                try:
                    amounts.append(float(clean_val))
                except ValueError:
                    pass

        amount_paid = amounts[0] if amounts else initial_data.get("amount_paid", 0.0)
        # By default in Indian consumer complaints, compensation claimed is 20-30% or minimum 10k-25k for harassment + refund
        refund_claimed = amount_paid
        compensation_claimed = 15000.0 if amount_paid > 0 else 0.0
        total_claim = refund_claimed + compensation_claimed

        # 2. Extract Dates
        dates = cls.DATE_REGEX.findall(sanitized_narrative)
        primary_date = dates[0] if dates else initial_data.get("incident_date", None)

        # 3. Identify Opposing Party & Entities
        opposite_name = cls._extract_entity_name(sanitized_narrative) or initial_data.get("opposite_party_name", "Opposite Party / Service Provider")

        # 4. Extract Evidence mentioned
        evidence_list = cls._extract_evidence_items(sanitized_narrative)

        # 5. Build Structured Timeline
        timeline = []
        if primary_date:
            timeline.append(TimelineEvent(
                date=primary_date,
                event_description="Occurrence of primary grievance / transaction",
                evidence_reference="Invoice / Transaction record"
            ))

        # 6. Assess Missing Facts & Questions
        missing_facts = []
        clarifications = []

        if not primary_date:
            missing_facts.append("Exact date of transaction or grievance occurrence")
            clarifications.append("What was the exact date when this transaction, payment, or incident took place?")

        if amount_paid == 0.0 and initial_data.get("category", "") != "RTI":
            missing_facts.append("Total monetary amount involved / paid")
            clarifications.append("What was the exact amount (in ₹) paid or disputed in this matter?")

        if opposite_name in ["Opposite Party / Service Provider", ""]:
            missing_facts.append("Full official name and address of the opposite company or landlord")
            clarifications.append("What is the registered company name, vendor name, or landlord's name and city?")

        if not any(e.doc_type == "INVOICE" for e in evidence_list) and initial_data.get("category", "") == "CONSUMER":
            missing_facts.append("Proof of purchase (Tax invoice or order receipt)")
            clarifications.append("Do you possess an invoice, order ID, or payment receipt for this purchase?")

        # Completeness Score Calculation (0.0 to 1.0)
        total_checks = 4
        passed_checks = total_checks - len(missing_facts)
        completion_score = max(0.2, round(passed_checks / total_checks, 2))
        is_complete = completion_score >= 0.75

        complainant = PartyInfo(
            name=initial_data.get("user_name", "Complainant"),
            city=initial_data.get("user_city", "New Delhi"),
            state=initial_data.get("user_state", "Delhi"),
            phone=initial_data.get("user_phone"),
            email=initial_data.get("user_email")
        )

        opposite_party = PartyInfo(
            name=opposite_name,
            city=initial_data.get("opposite_city", "Opponent City / Registered Office"),
            state=initial_data.get("opposite_state", "State"),
            address=initial_data.get("opposite_address", "Registered Office / Branch Address")
        )

        return FactGraphSchema(
            complainant=complainant,
            opposite_party=opposite_party,
            incident_narrative=sanitized_narrative,
            incident_date=primary_date,
            category=initial_data.get("category", "CONSUMER"),
            sub_category="GENERAL_DISPUTE",
            timeline=timeline,
            financials=FinancialBreakdown(
                amount_paid=amount_paid,
                refund_claimed=refund_claimed,
                compensation_claimed=compensation_claimed,
                litigation_costs_claimed=5000.0 if amount_paid > 0 else 0.0,
                total_claim_amount=total_claim + (5000.0 if amount_paid > 0 else 0.0)
            ),
            evidence_inventory=evidence_list,
            missing_facts=missing_facts,
            clarification_questions=clarifications,
            is_complete=is_complete,
            completion_score=completion_score
        )

    @classmethod
    def _extract_entity_name(cls, text: str) -> str:
        # Common known brands or regex match for company names
        known_companies = ["Amazon", "Flipkart", "Swiggy", "Zomato", "Myntra", "Tata Neu", "Reliance Digital", "Croma", "MakeMyTrip", "Ola", "Uber", "Urban Company", "Samsung", "Apple", "LG", "Sony", "Airtel", "Jio"]
        for brand in known_companies:
            if re.search(r'\b' + re.escape(brand) + r'\b', text, re.IGNORECASE):
                return f"{brand} India / Authorized Seller"

        # Regex for Landlord / Vendor patterns
        match = re.search(r'(?:landlord|owner|vendor|company|dealer|builder|agency)\s+(?:named|called|is)?\s*([A-Z][a-zA-Z0-9\s]+?)(?:[\.,]|$)', text)
        if match:
            return match.group(1).strip()

        return "Opposite Party / Service Provider"

    @classmethod
    def _extract_evidence_items(cls, text: str) -> List[EvidenceItem]:
        evidence = []
        text_lower = text.lower()

        if any(w in text_lower for w in ["invoice", "bill", "receipt", "order", "ordered", "purchased", "bought", "payment", "paid", "transaction"]):
            evidence.append(EvidenceItem(
                doc_type="INVOICE",
                doc_name="Original Tax Invoice / Order Receipt",
                annexure_label="Annexure A-1"
            ))

        if any(w in text_lower for w in ["email", "chat", "whatsapp", "ticket", "support", "complained", "rejected", "refused", "technician", "customer care"]):
            evidence.append(EvidenceItem(
                doc_type="EMAIL_THREAD",
                doc_name="Written Communications & Support Grievance Records",
                annexure_label="Annexure A-2"
            ))

        if any(w in text_lower for w in ["photo", "picture", "video", "damage", "broken", "leak", "broke", "defective"]):
            evidence.append(EvidenceItem(
                doc_type="PHOTO",
                doc_name="Photographic Evidence of Defect / Malfunction",
                annexure_label="Annexure A-3"
            ))

        if any(w in text_lower for w in ["agreement", "lease", "contract", "rent agreement", "terms"]):
            evidence.append(EvidenceItem(
                doc_type="CONTRACT",
                doc_name="Executed Agreement / Terms of Contract",
                annexure_label="Annexure A-4"
            ))

        return evidence
