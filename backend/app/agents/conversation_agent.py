import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.schemas.chat import (
    ChatTurnRequest,
    ChatTurnResponse,
    DocumentUploadExtractionRequest,
    EvidenceStatusItem,
    LegalStageMilestone,
    StructuredCaseProfile,
)
from app.services.document_registry import select_document_for_workflow
from app.services.pii_masker import PIIMasker


MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

OFFICIAL_SOURCES = {
    "CONSUMER": [
        {
            "title": "Consumer Protection Act, 2019",
            "authority": "India Code",
            "url": "https://www.indiacode.nic.in/handle/123456789/21423",
        }
    ],
    "CYBER_FRAUD": [
        {
            "title": "National Cyber Crime Reporting Portal",
            "authority": "Indian Cyber Crime Coordination Centre",
            "url": "https://www.cybercrime.gov.in/",
        },
        {
            "title": "Customer protection for unauthorised electronic transactions",
            "authority": "Reserve Bank of India",
            "url": "https://www.rbi.org.in/commonman/Upload/English/Notification/PDFs/NOTI1506072017.PDF",
        },
    ],
    "POLICE_COMPLAINT": [
        {
            "title": "Bharatiya Nagarik Suraksha Sanhita, 2023",
            "authority": "India Code",
            "url": "https://www.indiacode.nic.in/handle/123456789/21419",
        }
    ],
    "EMPLOYMENT": [
        {
            "title": "Code on Wages, 2019",
            "authority": "Ministry of Labour & Employment",
            "url": "https://labour.gov.in/sites/default/files/the_code_on_wages_2019_no._29_of_2019.pdf",
        }
    ],
    "HOUSING_TENANT": [
        {
            "title": "Model Tenancy Act, 2021 (state adoption must be checked)",
            "authority": "Ministry of Housing and Urban Affairs",
            "url": "https://mohua.gov.in/upload/uploadfiles/files/Model-Tenancy-Act-English-02_06_2021.pdf",
        }
    ],
}


class ConversationalLegalAgent:
    """Deterministic conversational intake and legal-journey engine."""

    def __init__(self):
        self.workflows: Dict[str, Any] = {}
        workflow_path = os.path.join(settings.DATA_DIR, "workflows.json")
        if os.path.exists(workflow_path):
            with open(workflow_path, "r", encoding="utf-8") as handle:
                self.workflows = json.load(handle)

    def process_turn(
        self,
        req: ChatTurnRequest,
        existing_profile: Optional[StructuredCaseProfile] = None,
    ) -> ChatTurnResponse:
        user_text = req.message.strip()
        sanitized_text, _ = PIIMasker.mask_text(user_text)
        profile = existing_profile or self._init_case_profile(sanitized_text, req.case_id)

        action_response = self._check_conversation_actions(sanitized_text, profile)
        if action_response:
            self._touch(profile)
            return action_response

        conflict_response = self._detect_conflicts(sanitized_text, profile)
        if conflict_response:
            self._touch(profile)
            return conflict_response

        self._extract_entities_into_profile(sanitized_text, profile)
        self._assess_risk(sanitized_text, profile)

        workflow = self.workflows.get(profile.category, self.workflows.get("CONSUMER", {}))
        missing_fields = self._compute_missing_fields(profile)
        profile.missing_required_fields = missing_fields
        profile.recommended_doc_type = select_document_for_workflow(profile.category, profile.current_stage_key)
        profile.recommended_doc_label = workflow.get("default_doc_label", "Prepare Complaint Letter")
        profile.is_ready_for_document = len(missing_fields) == 0 and profile.risk_level != "RED"

        reply_text, quick_replies, suggested_action = self._formulate_response(
            sanitized_text, profile, missing_fields, workflow
        )
        profile.recommended_next_action = suggested_action
        self._touch(profile)
        return ChatTurnResponse(
            reply_text=reply_text,
            case_profile=profile,
            quick_replies=quick_replies,
            suggested_action=suggested_action,
            message_id=str(uuid.uuid4()),
        )

    def _init_case_profile(
        self,
        text: str,
        case_id: Optional[str] = None,
        category_override: Optional[str] = None,
    ) -> StructuredCaseProfile:
        case_identifier = case_id if case_id and case_id not in {"new", "default-new"} else str(uuid.uuid4())
        category = category_override or self._classify_category(text)
        if category not in self.workflows and category != "GENERAL":
            category = "GENERAL"
        workflow = self.workflows.get(category, {})
        stages = [
            LegalStageMilestone(
                id=stage["id"],
                title=stage["title"],
                description=stage["description"],
                status="CURRENT" if index == 0 else "FUTURE",
                is_current=index == 0,
            )
            for index, stage in enumerate(workflow.get("stages", []))
        ]
        evidence = [
            EvidenceStatusItem(
                id=item["id"],
                name=item["name"],
                why_needed=item["why_needed"],
                annexure_label=item.get("annexure_label"),
            )
            for item in workflow.get("evidence_items", [])
        ]
        now = datetime.now().isoformat()
        case_token = re.sub(r"[^A-Za-z0-9]", "", case_identifier).upper()[:8]
        title_map = {
            "HOUSING_TENANT": "Tenant Security Deposit Dispute",
            "EMPLOYMENT": "Unpaid Salary Dispute",
            "CONSUMER": "Consumer Refund Dispute",
            "CYBER_FRAUD": "Cyber Financial Fraud",
            "POLICE_COMPLAINT": "Police Complaint Assistance",
            "GENERAL": "Legal Information Request",
        }
        rights_summary = dict(workflow.get("rights_summary") or {})
        rights_summary["sources"] = OFFICIAL_SOURCES.get(category, [])
        profile = StructuredCaseProfile(
            case_id=case_identifier,
            case_number=f"NYA-{datetime.now().year}-{case_token}",
            title=title_map.get(category, "Legal Issue"),
            category=category,
            category_display_name=workflow.get("category_display_name", "General Legal Information"),
            issue_type=workflow.get("issue_type", "Grievance"),
            current_stage_key=stages[0].id if stages else "INTAKE",
            current_stage_label=stages[0].title if stages else "Understanding your situation",
            evidence_checklist=evidence,
            legal_journey=stages,
            rights_summary=rights_summary,
            recommended_doc_type=select_document_for_workflow(category),
            recommended_doc_label=workflow.get("default_doc_label", "Prepare Complaint Letter"),
            timeline=[
                {
                    "id": str(uuid.uuid4()),
                    "type": "case_started",
                    "date": now,
                    "label": "Case conversation started",
                    "source": "chat",
                }
            ],
            created_at=now,
            updated_at=now,
        )
        self._assess_risk(text, profile)
        return profile

    def _classify_category(self, text: str) -> str:
        value = text.lower()
        if any(word in value for word in ["landlord", "tenant", "deposit", "rent", "flat", "kiraya", "makan malik", "security deposit", "मकान", "किराया"]):
            return "HOUSING_TENANT"
        if any(word in value for word in ["salary", "employer", "wages", "boss", "vetan", "unpaid salary", "तनख्वाह", "वेतन"]):
            return "EMPLOYMENT"
        if any(word in value for word in ["upi", "cyber", "phishing", "otp", "bank fraud", "online fraud", "scam", "hacked"]):
            return "CYBER_FRAUD"
        if any(word in value for word in ["police", "fir", "thana", "sho", "complaint refusal", "चौकी", "पुलिस", "थाना"]):
            return "POLICE_COMPLAINT"
        return "CONSUMER"

    def _assess_risk(self, text: str, profile: StructuredCaseProfile) -> None:
        value = text.lower()
        red_terms = [
            "suicide", "kill me", "life threat", "immediate danger", "domestic violence",
            "sexual assault", "rape", "kidnap", "in custody", "arrested", "child abuse",
        ]
        amber_terms = ["ongoing court case", "summons", "eviction", "property title", "terminated", "large fraud"]
        if any(term in value for term in red_terms):
            profile.risk_level = "RED"
            profile.safety_notice = (
                "This may involve immediate safety, liberty, or serious criminal consequences. "
                "Contact emergency services or a qualified advocate now. Free legal-aid help is available through NALSA/DLSA at 15100."
            )
        elif profile.category == "CYBER_FRAUD" or any(term in value for term in amber_terms) or profile.disputed_amount >= 500000:
            profile.risk_level = "AMBER"
            profile.safety_notice = (
                "Time or fact-sensitive matter: act promptly and verify any deadline or forum before relying on it."
            )
        else:
            profile.risk_level = "GREEN"
            profile.safety_notice = None

    def _amount_from_text(self, text: str) -> Optional[float]:
        match = re.search(
            r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:rupees|rs|inr|rupaye|रुपये|k\b)",
            text,
            re.IGNORECASE,
        )
        if match:
            raw = (match.group(1) or match.group(2)).replace(",", "")
            amount = float(raw)
            if match.group(0).lower().strip().endswith("k"):
                amount *= 1000
            return amount
        short = re.search(r"\b(\d+(?:\.\d+)?)\s*k\b", text, re.IGNORECASE)
        if short:
            return float(short.group(1)) * 1000
        large_number = re.search(r"\b(\d{4,8})\b", text)
        if large_number:
            value = float(large_number.group(1))
            if 1900 <= value <= 2100:
                return None
            return value
        return None

    def _set_fact(self, profile: StructuredCaseProfile, field: str, value: Any, confidence: float = 0.9) -> None:
        profile.fact_metadata[field] = {
            "value": value,
            "source": "chat",
            "confidence": confidence,
            "confirmed": False,
        }

    def _extract_entities_into_profile(self, text: str, profile: StructuredCaseProfile) -> None:
        lower = text.lower()
        last_group = profile.key_facts.get("last_question_group")

        amount = self._amount_from_text(text)
        if amount and profile.disputed_amount == 0:
            profile.disputed_amount = amount
            self._set_fact(profile, "disputed_amount", amount, 0.96)

        city_states = {
            "New Delhi": "Delhi", "Delhi": "Delhi", "Noida": "Uttar Pradesh",
            "Gurugram": "Haryana", "Gurgaon": "Haryana", "Bengaluru": "Karnataka",
            "Bangalore": "Karnataka", "Pune": "Maharashtra", "Mumbai": "Maharashtra",
            "Hyderabad": "Telangana", "Chennai": "Tamil Nadu", "Kolkata": "West Bengal",
            "Ahmedabad": "Gujarat", "Jaipur": "Rajasthan", "Lucknow": "Uttar Pradesh",
            "Chandigarh": "Chandigarh",
        }
        for city, state in city_states.items():
            if re.search(r"\b" + re.escape(city) + r"\b", text, re.IGNORECASE):
                if not profile.user_city:
                    profile.user_city, profile.user_state = city, state
                    self._set_fact(profile, "jurisdiction", {"city": city, "state": state}, 0.96)
                break

        name_match = re.search(r"(?:my name is|i am|mera naam|मेरा नाम)\s+([A-Z][a-zA-Z\s]{2,35}?)(?:[\.,]|\band\b|$)", text, re.IGNORECASE)
        if name_match and not profile.user_name:
            profile.user_name = name_match.group(1).strip()
            self._set_fact(profile, "user_name", profile.user_name, 0.92)

        if not profile.opposite_party_name:
            company = re.search(
                r"\b([A-Z][a-zA-Z0-9\s&.\-]{1,40}?\s+(?:Pvt\.?\s+Ltd\.?|Private\s+Limited|Ltd\.?|LLP|Limited))\b",
                text,
            )
            landlord = re.search(
                r"landlord\s+(?:name\s+is|named|is)\s+([A-Z][a-zA-Z\s.\-]{2,35}?)(?:[\.,;]|\band\b|$)",
                text,
                re.IGNORECASE,
            )
            if company:
                profile.opposite_party_name = company.group(1).strip()
            elif landlord:
                profile.opposite_party_name = landlord.group(1).strip()
            else:
                for brand in ["Amazon", "Flipkart", "Myntra", "Croma", "Swiggy", "Zomato", "Samsung", "Apple", "Paytm"]:
                    if re.search(r"\b" + brand + r"\b", text, re.IGNORECASE):
                        profile.opposite_party_name = brand
                        break
            if profile.opposite_party_name:
                self._set_fact(profile, "opposite_party_name", profile.opposite_party_name, 0.9)

        date_match = re.search(
            rf"\b(?:\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{2,4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})(?:\s+\d{{2,4}})?)\b",
            text,
            re.IGNORECASE,
        )
        if date_match:
            date_value = date_match.group(0).strip()
            if profile.category == "HOUSING_TENANT" and any(term in lower for term in ["vacat", "moved out", "move out", "left", "handover"]):
                profile.vacating_date = profile.vacating_date or date_value
                self._set_fact(profile, "vacating_date", profile.vacating_date, 0.9)
            elif not profile.incident_date:
                profile.incident_date = date_value
                self._set_fact(profile, "incident_date", profile.incident_date, 0.86)

        month_values = re.findall(rf"\b({MONTHS})\b", text, re.IGNORECASE)
        if profile.category == "EMPLOYMENT" and month_values:
            normalized = [month.title() for month in month_values]
            profile.unpaid_months = list(dict.fromkeys(profile.unpaid_months + normalized))
            self._set_fact(profile, "unpaid_months", profile.unpaid_months, 0.92)

        salary_match = re.search(r"(?:monthly salary|salary is|salary of)\s*(?:rs\.?|₹|inr)?\s*([\d,]+)", text, re.IGNORECASE)
        if salary_match:
            profile.key_facts["monthly_salary"] = float(salary_match.group(1).replace(",", ""))

        transaction_match = re.search(r"(?:transaction id|transaction ref|utr|rrn|reference number|ref no)\s*(?:is|:|-)?\s*([A-Z0-9\-]{6,30})", text, re.IGNORECASE)
        if transaction_match:
            profile.transaction_id = transaction_match.group(1)
            self._set_fact(profile, "transaction_id", profile.transaction_id, 0.96)

        for bank in ["State Bank of India", "SBI", "HDFC", "ICICI", "Axis Bank", "Kotak", "Paytm", "PhonePe", "Google Pay", "GPay"]:
            if re.search(r"\b" + re.escape(bank) + r"\b", text, re.IGNORECASE):
                profile.bank_name = profile.bank_name or bank
                break

        station_match = re.search(r"(?:police station|thana|sho at)\s+(?:is|:|-)?\s*([A-Z][a-zA-Z\s]{2,40}?)(?:[\.,]|$)", text, re.IGNORECASE)
        if station_match:
            profile.police_station_name = station_match.group(1).strip()

        address_match = re.search(r"(?:property address is|rented property at|premises at)\s+(.{5,100}?)(?:[\.;]|$)", text, re.IGNORECASE)
        if address_match:
            profile.property_address = address_match.group(1).strip()

        yes_answer = (
            lower.strip() in {"yes", "yes, both", "yes both", "haan", "haan, dono", "i have both"}
            or "yes, both" in lower
            or "i have both" in lower
        )
        if profile.category == "HOUSING_TENANT":
            if any(term in lower for term in ["isn't returning", "not returning", "return it later", "refund nahi", "asked him", "requested"]):
                profile.key_facts["landlord_contacted"] = True
            if any(term in lower for term in ["return it later", "he'll return", "he will return", "no reason", "damage", "repairs", "deduction", "keeping it"]):
                profile.key_facts["landlord_reason"] = text.strip()
            if last_group == "tenant_evidence" and yes_answer:
                profile.key_facts["rental_agreement_available"] = True
                profile.key_facts["deposit_payment_proof_available"] = True
                self._mark_evidence(profile, ["rental_agreement", "deposit_payment_proof"])
            if any(term in lower for term in ["rental agreement", "lease agreement", "agreement hai"]):
                profile.key_facts["rental_agreement_available"] = not any(term in lower for term in ["don't have", "do not have", "nahi hai"])
                if profile.key_facts["rental_agreement_available"]:
                    self._mark_evidence(profile, ["rental_agreement"])
            if any(term in lower for term in ["deposit proof", "payment proof", "bank statement", "bank transfer proof", "transfer proof", "transfer receipt"]):
                profile.key_facts["deposit_payment_proof_available"] = True
                self._mark_evidence(profile, ["deposit_payment_proof"])

        if profile.category == "CONSUMER":
            if any(term in lower for term in ["refund", "support", "seller", "customer care", "refuses", "rejected"]):
                profile.key_facts["seller_contacted"] = True
            if any(term in lower for term in ["refuses", "rejected", "won't refund", "no refund"]):
                profile.key_facts["seller_response"] = "Refund or remedy refused"
                self._mark_evidence(profile, ["seller_rejection"])
            if any(term in lower for term in ["invoice", "bill", "order receipt", "purchase proof"]):
                profile.key_facts["invoice_available"] = True
                self._mark_evidence(profile, ["invoice"])
            product = re.search(r"(?:bought|purchased|ordered)\s+(?:a|an)?\s*([a-zA-Z0-9\s\-]{2,35}?)(?:\s+from|\s+on|[\.,])", text, re.IGNORECASE)
            if product:
                profile.key_facts["product_name"] = product.group(1).strip()
            elif "defective product" in lower:
                profile.key_facts["product_name"] = "Defective product"
            if last_group == "consumer_evidence" and yes_answer:
                profile.key_facts["invoice_available"] = True
                profile.key_facts["seller_contacted"] = True
                self._mark_evidence(profile, ["invoice", "support_tickets"])

        if profile.category == "EMPLOYMENT":
            if any(term in lower for term in ["hr", "manager", "asked employer", "emailed", "followed up"]):
                profile.key_facts["hr_contacted"] = True
                self._mark_evidence(profile, ["hr_emails"])
            if any(term in lower for term in ["appointment letter", "offer letter", "employment contract", "salary slip"]):
                profile.key_facts["employment_proof_available"] = True
                self._mark_evidence(profile, ["offer_letter", "salary_slips"])
            if last_group == "employment_contact_proof" and yes_answer:
                profile.key_facts["hr_contacted"] = True
                profile.key_facts["employment_proof_available"] = True
                self._mark_evidence(profile, ["offer_letter", "hr_emails"])

        if profile.category == "CYBER_FRAUD":
            if any(term in lower for term in ["reported to bank", "called bank", "bank complaint", "blocked account"]):
                profile.key_facts["bank_reported"] = True
                self._mark_evidence(profile, ["bank_complaint_ack"])
            if any(term in lower for term in ["called 1930", "cybercrime.gov.in", "cyber portal", "cybercrime complaint"]):
                profile.key_facts["cyber_reported"] = True
            if last_group == "cyber_reports" and yes_answer:
                profile.key_facts["bank_reported"] = True
                profile.key_facts["cyber_reported"] = True

        if profile.category == "POLICE_COMPLAINT":
            profile.key_facts["police_approached"] = True
            if any(term in lower for term in ["written complaint", "complaint copy", "acknowledgement", "diary number", "gd number"]):
                profile.key_facts["written_complaint_available"] = True
                self._mark_evidence(profile, ["complaint_copy"])

        profile.key_facts.pop("last_question_group", None)

    def _mark_evidence(self, profile: StructuredCaseProfile, evidence_ids: List[str]) -> None:
        identifiers = set(evidence_ids)
        for item in profile.evidence_checklist:
            if item.id in identifiers:
                item.is_available = True

    def _compute_missing_fields(self, profile: StructuredCaseProfile) -> List[str]:
        facts = profile.key_facts
        values = {
            "user_city": profile.user_city,
            "disputed_amount": profile.disputed_amount or None,
            "opposite_party_name": profile.opposite_party_name,
            "vacating_date": profile.vacating_date,
            "incident_date": profile.incident_date,
            "unpaid_months": profile.unpaid_months or None,
            "transaction_id": profile.transaction_id,
            "bank_name": profile.bank_name,
            "police_station_name": profile.police_station_name,
            **facts,
        }
        requirements = {
            "HOUSING_TENANT": ["user_city", "vacating_date", "rental_agreement_available", "deposit_payment_proof_available", "landlord_reason"],
            "CONSUMER": ["user_city", "incident_date", "product_name", "invoice_available", "seller_contacted", "seller_response"],
            "EMPLOYMENT": ["user_city", "opposite_party_name", "unpaid_months", "monthly_salary", "hr_contacted", "employment_proof_available"],
            "CYBER_FRAUD": ["incident_date", "transaction_id", "bank_name", "bank_reported", "cyber_reported"],
            "POLICE_COMPLAINT": ["user_city", "incident_date", "police_station_name", "written_complaint_available"],
        }
        def is_missing(field: str, value: Any) -> bool:
            if value is False and field in facts:
                return False
            if value is None or value == "":
                return True
            return isinstance(value, (list, tuple, set, dict)) and len(value) == 0

        return [field for field in requirements.get(profile.category, []) if is_missing(field, values.get(field))]

    def _detect_conflicts(self, text: str, profile: StructuredCaseProfile) -> Optional[ChatTurnResponse]:
        lowered = text.lower()
        if any(term in lowered for term in ["monthly salary", "salary is", "salary of", "transaction id", "transaction ref", "utr", "rrn"]):
            return None
        new_amount = self._amount_from_text(text)
        if profile.disputed_amount and new_amount and abs(profile.disputed_amount - new_amount) > 0.01:
            profile.key_facts["pending_conflict"] = {
                "field": "disputed_amount",
                "existing": profile.disputed_amount,
                "candidate": new_amount,
            }
            return ChatTurnResponse(
                reply_text=(
                    f"I noticed two different amounts: ₹{profile.disputed_amount:,.0f} earlier and ₹{new_amount:,.0f} now. "
                    "Which amount should I use for this case? I will not replace the earlier figure without your confirmation."
                ),
                case_profile=profile,
                quick_replies=[f"Keep ₹{profile.disputed_amount:,.0f}", f"Use ₹{new_amount:,.0f}"],
                message_id=str(uuid.uuid4()),
            )
        return None

    def _check_conversation_actions(self, text: str, profile: StructuredCaseProfile) -> Optional[ChatTurnResponse]:
        lower = text.lower()
        pending_conflict = profile.key_facts.get("pending_conflict")
        if pending_conflict and (lower.startswith("use ") or lower.startswith("keep ")):
            if lower.startswith("use "):
                profile.disputed_amount = float(pending_conflict["candidate"])
            profile.key_facts.pop("pending_conflict", None)
            return ChatTurnResponse(
                reply_text=f"Confirmed. I will use ₹{profile.disputed_amount:,.0f} for this case.",
                case_profile=profile,
                quick_replies=["Continue"],
                message_id=str(uuid.uuid4()),
            )

        pending_upload = profile.key_facts.get("pending_document_extraction")
        if pending_upload and any(term in lower for term in ["details are correct", "confirm extracted", "correct details"]):
            for field, value in pending_upload.get("facts", {}).items():
                if hasattr(profile, field):
                    setattr(profile, field, value)
                    profile.fact_metadata[field] = {
                        "value": value,
                        "source": f"upload:{pending_upload.get('file_name', 'document')}",
                        "confidence": 0.78,
                        "confirmed": True,
                    }
                else:
                    profile.key_facts[field] = value
            if pending_upload.get("facts", {}).get("response_outcome") == "REJECTED":
                escalation_ids = {
                    "HOUSING_TENANT": "RENT_AUTHORITY_ESCALATION",
                    "EMPLOYMENT": "LABOUR_COMMISSIONER_COMPLAINT",
                    "CONSUMER": "EDAAKHIL_COMPLAINT",
                    "CYBER_FRAUD": "POLICE_FIR_ESCALATION",
                    "POLICE_COMPLAINT": "SP_ESCALATION",
                }
                self._set_journey_current(profile, escalation_ids.get(profile.category, "ESCALATION"), complete_through=True)
                self._add_action(profile, "response_rejected", "Uploaded response confirmed as a rejection")
            confirmed_deadline = pending_upload.get("facts", {}).get("response_deadline_text")
            if confirmed_deadline and not any(item.get("date") == confirmed_deadline for item in profile.deadlines):
                profile.deadlines.append(
                    {
                        "date": confirmed_deadline,
                        "source": f"upload:{pending_upload.get('file_name', 'document')}",
                        "reason": "Explicit response or compliance date detected in the uploaded document",
                        "confidence": 0.78,
                        "confirmed": True,
                    }
                )
            profile.key_facts.pop("pending_document_extraction", None)
            profile.missing_required_fields = self._compute_missing_fields(profile)
            profile.is_ready_for_document = len(profile.missing_required_fields) == 0 and profile.risk_level != "RED"
            return ChatTurnResponse(
                reply_text="Thanks. I recorded the confirmed details, updated the legal journey where applicable, and kept the uploaded file in your evidence checklist.",
                case_profile=profile,
                quick_replies=["Continue my case"],
                message_id=str(uuid.uuid4()),
            )

        if any(term in lower for term in ["case resolved", "matter resolved", "got my refund", "paid me", "agreed to refund"]):
            profile.current_stage_key = "RESOLVED"
            profile.current_stage_label = "Resolved"
            self._add_action(profile, "case_resolved", "Case marked resolved")
            return ChatTurnResponse(
                reply_text="That is good news. I marked this case as resolved. Keep the payment or settlement proof with your case records.",
                case_profile=profile,
                quick_replies=[],
                message_id=str(uuid.uuid4()),
            )

        sent_terms = ["i sent", "sent today", "sent yesterday", "notice bhej diya", "bhej diya", "i dispatched", "speed post"]
        if any(term in lower for term in sent_terms):
            waiting_ids = {
                "HOUSING_TENANT": "AWAITING_LANDLORD_RESPONSE",
                "EMPLOYMENT": "AWAITING_EMPLOYER_RESPONSE",
                "CONSUMER": "AWAITING_SELLER_RESPONSE",
            }
            self._set_journey_current(profile, waiting_ids.get(profile.category, "AWAITING_RESPONSE"), complete_through=True)
            self._add_action(profile, "formal_demand_sent", "Formal letter recorded as sent")
            return ChatTurnResponse(
                reply_text=(
                    f"Recorded as sent on {datetime.now().strftime('%d %B %Y')}. Your case is now awaiting response. "
                    "Keep the email delivery record or postal receipt. If a reply arrives, upload it here and I will help reassess the next step."
                ),
                case_profile=profile,
                quick_replies=["They rejected my demand", "They agreed to resolve it", "No response received"],
                message_id=str(uuid.uuid4()),
            )

        rejection_terms = ["they rejected", "landlord refused", "company refused", "seller refused", "mana kar diya", "rejected my refund", "no response received"]
        if any(term in lower for term in rejection_terms):
            escalation_ids = {
                "HOUSING_TENANT": "RENT_AUTHORITY_ESCALATION",
                "EMPLOYMENT": "LABOUR_COMMISSIONER_COMPLAINT",
                "CONSUMER": "EDAAKHIL_COMPLAINT",
                "CYBER_FRAUD": "POLICE_FIR_ESCALATION",
                "POLICE_COMPLAINT": "SP_ESCALATION",
            }
            self._set_journey_current(profile, escalation_ids.get(profile.category, "ESCALATION"), complete_through=True)
            self._add_action(profile, "response_rejected", "Response rejected or no response recorded")
            document_type = select_document_for_workflow(profile.category, profile.current_stage_key)
            action = {"type": "PREPARE_DOC", "doc_type": document_type, "label": "Prepare next complaint draft"}
            return ChatTurnResponse(
                reply_text=(
                    "I recorded the refusal or non-response. The next route depends on the forum and facts shown in your case workspace. "
                    "A formal complaint draft may now be appropriate; professional review is recommended for a court or tribunal filing."
                ),
                case_profile=profile,
                quick_replies=["Show my legal journey", "What evidence should I attach?"],
                suggested_action=action,
                message_id=str(uuid.uuid4()),
            )
        return None

    def _set_journey_current(self, profile: StructuredCaseProfile, stage_id: str, complete_through: bool = False) -> None:
        target_seen = False
        for stage in profile.legal_journey:
            if stage.id == stage_id:
                stage.status = "CURRENT"
                stage.is_current = True
                target_seen = True
                profile.current_stage_key = stage.id
                profile.current_stage_label = stage.title
            elif not target_seen and complete_through:
                stage.status = "COMPLETED"
                stage.is_current = False
            else:
                stage.is_current = False
        if not target_seen:
            profile.current_stage_key = stage_id
            profile.current_stage_label = stage_id.replace("_", " ").title()

    def _add_action(self, profile: StructuredCaseProfile, action_type: str, label: str) -> None:
        if any(action.get("type") == action_type for action in profile.actions_completed):
            return
        now = datetime.now().isoformat()
        profile.actions_completed.append({"type": action_type, "date": now, "label": label})
        profile.timeline.append(
            {"id": str(uuid.uuid4()), "type": action_type, "date": now, "label": label, "source": "chat"}
        )

    def _formulate_response(
        self,
        text: str,
        profile: StructuredCaseProfile,
        missing: List[str],
        workflow: Dict[str, Any],
    ) -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
        if profile.risk_level == "RED":
            return (
                f"Your immediate safety comes first. {profile.safety_notice}\n\n"
                "NyayaBot can preserve the facts you share, but it should not delay urgent police, medical, or legal help.",
                ["Show free legal-aid options", "Continue recording facts"],
                None,
            )

        if profile.category == "CYBER_FRAUD" and missing:
            urgent = (
                "Act now: call your bank's fraud helpline, ask it to block further transactions, and report financial fraud at 1930 or cybercrime.gov.in. "
                "Keep every acknowledgement number. Recovery is not guaranteed, but prompt reporting matters.\n\n"
            )
            if any(field in missing for field in ["incident_date", "transaction_id", "bank_name"]):
                profile.key_facts["last_question_group"] = "cyber_transaction"
                return urgent + "What were the transaction date/time, UTR or transaction ID, and bank/payment app?", [], None
            profile.key_facts["last_question_group"] = "cyber_reports"
            return urgent + "Have you already reported this to both your bank and the 1930/cybercrime portal?", ["Yes, both", "Bank only", "Not yet"], None

        if profile.category == "HOUSING_TENANT":
            if any(field in missing for field in ["user_city", "vacating_date"]):
                profile.key_facts["last_question_group"] = "tenant_location_date"
                return "I can help you work through the deposit issue. Which city and state is the property in, and when did you move out?", [], None
            if any(field in missing for field in ["rental_agreement_available", "deposit_payment_proof_available"]):
                profile.key_facts["last_question_group"] = "tenant_evidence"
                deposit_label = (
                    f"the ₹{profile.disputed_amount:,.0f} deposit"
                    if profile.disputed_amount
                    else "the security deposit"
                )
                return f"Do you have a rental agreement and proof that you paid {deposit_label}?", ["Yes, both", "Agreement only", "Payment proof only", "Neither"], None
            if "landlord_reason" in missing:
                profile.key_facts["last_question_group"] = "tenant_reason"
                return "Has the landlord given a reason for keeping the deposit, or only said it will be returned later?", ["Only says it will be returned later", "Claims property damage", "Gave no reason"], None

        if profile.category == "CONSUMER":
            if any(field in missing for field in ["user_city", "incident_date", "product_name"]):
                profile.key_facts["last_question_group"] = "consumer_purchase"
                return "What product or service was this, when did you buy it, and which city/state are you in?", [], None
            if any(field in missing for field in ["invoice_available", "seller_contacted", "seller_response"]):
                profile.key_facts["last_question_group"] = "consumer_evidence"
                return "Do you have the invoice, and have you already contacted the seller? What response did they give?", ["Yes—invoice and written rejection", "Invoice, but no written reply", "I have no invoice"], None

        if profile.category == "EMPLOYMENT":
            if any(field in missing for field in ["user_city", "opposite_party_name", "unpaid_months", "monthly_salary"]):
                profile.key_facts["last_question_group"] = "employment_basics"
                return "Which state do you work in, who is the employer, which months are unpaid, and what is your monthly salary?", [], None
            if any(field in missing for field in ["hr_contacted", "employment_proof_available"]):
                profile.key_facts["last_question_group"] = "employment_contact_proof"
                return "Have you contacted HR or management, and do you have an appointment letter, salary slips, or bank salary records?", ["Yes, both", "Contacted HR only", "Documents only", "Neither"], None

        if profile.category == "POLICE_COMPLAINT":
            if any(field in missing for field in ["user_city", "incident_date", "police_station_name"]):
                profile.key_facts["last_question_group"] = "police_basics"
                return "Which city/state is this in, when did the incident happen, and which police station refused the complaint?", [], None
            if "written_complaint_available" in missing:
                profile.key_facts["last_question_group"] = "police_written"
                return "Did you submit a written complaint or receive any diary/GD acknowledgement number?", ["Written complaint, no acknowledgement", "I have a GD/diary number", "Only spoke verbally"], None

        profile.is_ready_for_document = True
        document_type = profile.recommended_doc_type or select_document_for_workflow(profile.category)
        document_label = profile.recommended_doc_label or "Prepare Complaint Letter"
        action = {"type": "PREPARE_DOC", "doc_type": document_type, "label": document_label}
        rights = profile.rights_summary or {}
        possible_rights = rights.get("possible_rights", [])[:2]
        rights_text = "\n".join(f"• {item}" for item in possible_rights)
        reply = (
            f"I have enough information to map the next practical step.\n\n"
            f"### What this means\n{rights.get('what_this_means', 'Your case may have an actionable next step.')}\n\n"
            f"### Your possible rights\n{rights_text}\n\n"
            f"### What you should do now\nPrepare a clear written record using the facts you have confirmed.\n\n"
            f"### Current next step\n{document_label}. You will review all important names, amounts, dates, and addresses before anything is generated."
        )
        return reply, ["What evidence should I attach?", "How should I send it?"], action

    def process_document_upload(
        self,
        req: DocumentUploadExtractionRequest,
        profile: StructuredCaseProfile,
    ) -> ChatTurnResponse:
        content = (req.simulated_content or "").strip()
        candidate_facts: Dict[str, Any] = {}
        lower_content = content.lower()

        amount_text = content
        amount_patterns = {
            "RENTAL_AGREEMENT": r"(?:security\s+deposit|refundable\s+deposit)\s*(?:of|is|:|-)?\s*((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d{1,2})?)",
            "SALARY_SLIP": r"(?:net\s+(?:salary|pay)|gross\s+(?:salary|pay)|monthly\s+salary)\s*(?:of|is|:|-)?\s*((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d{1,2})?)",
            "INVOICE": r"(?:grand\s+total|amount\s+paid|invoice\s+total|total)\s*(?:is|:|-)?\s*((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d{1,2})?)",
        }
        preferred_amount = re.search(amount_patterns.get(req.doc_type, r"$^"), content, re.IGNORECASE)
        if preferred_amount:
            amount_text = preferred_amount.group(1)
        amount = self._amount_from_text(amount_text) if content else None
        if amount:
            candidate_facts["disputed_amount"] = amount

        def labelled_value(labels: str, max_length: int = 80) -> Optional[str]:
            match = re.search(
                rf"(?:{labels})\s*(?:name)?\s*(?:is|:|-)\s*([^\n\r,;]{{2,{max_length}}})",
                content,
                re.IGNORECASE,
            )
            return match.group(1).strip(" .") if match else None

        user_name = labelled_value(r"tenant|employee|customer|complainant|account\s+holder")
        other_party = labelled_value(r"landlord|employer|seller|merchant|opposite\s+party")
        property_match = re.search(
            r"(?:property\s+address|rented\s+premises|premises)\s*(?:is|:|-)\s*([^\n\r]{5,160})",
            content,
            re.IGNORECASE,
        )
        property_address = property_match.group(1).strip(" .") if property_match else None
        bank_name = labelled_value(r"bank|payment\s+app")
        transaction_id = labelled_value(r"utr|transaction\s+(?:id|reference)|rrn|reference\s+number")
        explicit_date = labelled_value(r"transaction\s+date|invoice\s+date|incident\s+date|date\s+of\s+transaction")
        if user_name:
            candidate_facts["user_name"] = user_name
        if other_party:
            candidate_facts["opposite_party_name"] = other_party
        if property_address:
            candidate_facts["property_address"] = property_address
        if bank_name:
            candidate_facts["bank_name"] = bank_name
        if transaction_id:
            candidate_facts["transaction_id"] = transaction_id
        if explicit_date:
            candidate_facts["incident_date"] = explicit_date

        if req.doc_type == "REJECTION_REPLY" and any(term in lower_content for term in ["rejected", "declined", "denied", "cannot approve", "refuse"]):
            candidate_facts["response_outcome"] = "REJECTED"
        deadline = re.search(
            rf"(?:respond|reply|appeal|comply|pay|submit).{{0,35}}?\b(?:by|before)\s+(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\s+\d{{4}})",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if deadline:
            candidate_facts["response_deadline_text"] = deadline.group(1)

        evidence_map = {
            "RENTAL_AGREEMENT": ["rental_agreement"],
            "SALARY_SLIP": ["salary_slips", "offer_letter"],
            "INVOICE": ["invoice"],
            "REJECTION_REPLY": ["seller_rejection", "landlord_chat", "hr_emails"],
        }
        self._mark_evidence(profile, evidence_map.get(req.doc_type, []))
        profile.timeline.append(
            {
                "id": str(uuid.uuid4()),
                "type": "document_uploaded",
                "date": datetime.now().isoformat(),
                "label": f"Uploaded {req.file_name}",
                "source": "upload",
            }
        )

        if candidate_facts:
            profile.key_facts["pending_document_extraction"] = {
                "file_name": req.file_name,
                "facts": candidate_facts,
            }
            lines = "\n".join(f"• {field.replace('_', ' ').title()}: {value}" for field, value in candidate_facts.items())
            reply = (
                f"I found these candidate details in {req.file_name}:\n{lines}\n\n"
                "Please confirm them before I add them to the case profile. Uploaded-document extraction can be wrong."
            )
            quick_replies = ["Details are correct", "I need to correct them"]
        else:
            reply = (
                f"I attached {req.file_name} to the evidence checklist. I could not reliably extract personal or transaction facts in fallback mode, "
                "so I did not add any invented details. You can paste a relevant text excerpt or continue in chat."
            )
            quick_replies = ["Continue my case"]
        self._touch(profile)
        return ChatTurnResponse(
            reply_text=reply,
            case_profile=profile,
            quick_replies=quick_replies,
            message_id=str(uuid.uuid4()),
        )

    def _touch(self, profile: StructuredCaseProfile) -> None:
        profile.updated_at = datetime.now().isoformat()


conversational_agent = ConversationalLegalAgent()
