import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.domains import domain_registry
from app.schemas.chat import (
    ChatTurnRequest,
    ChatTurnResponse,
    DocumentUploadExtractionRequest,
    EvidenceStatusItem,
    LegalStageMilestone,
    StructuredCaseProfile,
)
from app.services.action_planner import NextActionPlan, NextActionStatus, action_planner
from app.services.document_registry import DOCUMENT_DEFINITIONS, select_document_for_workflow
from app.services.pii_masker import PIIMasker


MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

class ConversationalLegalAgent:
    """Deterministic conversational intake and legal-journey engine."""

    def __init__(self):
        self.workflows: Dict[str, Any] = {}
        workflow_path = os.path.join(settings.DATA_DIR, "workflows.json")
        if os.path.exists(workflow_path):
            with open(workflow_path, "r", encoding="utf-8") as handle:
                self.workflows = json.load(handle)
        domain_registry.validate_integrations(
            workflows=self.workflows,
            document_ids=set(DOCUMENT_DEFINITIONS),
        )

    def _workflow_for_category(self, category: str) -> Dict[str, Any]:
        domain = domain_registry.resolve(category)
        return self.workflows.get(domain.workflow_binding, {})

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

        workflow = self._workflow_for_category(profile.category)
        missing_fields = self._compute_missing_fields(profile)
        profile.missing_required_fields = missing_fields
        blocking_fields = self._compute_blocking_fields(profile, missing_fields)

        plan = action_planner.plan_next_action(profile, workflow)
        profile.next_action_plan = plan
        profile.recommended_doc_type = plan.doc_type or select_document_for_workflow(profile.category, profile.current_stage_key)
        profile.recommended_doc_label = plan.label
        profile.is_ready_for_document = plan.status == NextActionStatus.READY and profile.risk_level != "RED"

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
            next_action_plan=plan,
            message_id=str(uuid.uuid4()),
        )

    def _init_case_profile(
        self,
        text: str,
        case_id: Optional[str] = None,
        category_override: Optional[str] = None,
    ) -> StructuredCaseProfile:
        case_identifier = case_id if case_id and case_id not in {"new", "default-new"} else str(uuid.uuid4())
        category = domain_registry.normalize_id(category_override or self._classify_category(text))
        domain = domain_registry.resolve(category)
        workflow = self._workflow_for_category(category)
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
        rights_summary = dict(workflow.get("rights_summary") or {})
        rights_summary["sources"] = [source.model_dump() for source in domain.rag.official_sources]
        profile = StructuredCaseProfile(
            case_id=case_identifier,
            case_number=f"NYA-{datetime.now().year}-{case_token}",
            title=domain.case_title,
            category=category,
            category_display_name=domain.display_name,
            issue_type=domain.default_issue_type_id,
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
        # The LLM is the primary classifier. This deterministic path exists for
        # demo/provider-failure behavior and consumes the same domain registry.
        return domain_registry.fallback_classify(text)

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
        lakh_match = re.search(
            r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:lakh|lac|lakhs|lacs|लाख)\b",
            text,
            re.IGNORECASE,
        )
        if lakh_match:
            return float(lakh_match.group(1).replace(",", "")) * 100000.0

        crore_match = re.search(
            r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:crore|cr|crores|करोड़)\b",
            text,
            re.IGNORECASE,
        )
        if crore_match:
            return float(crore_match.group(1).replace(",", "")) * 10000000.0

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
            from_match = re.search(
                r"\bfrom\s+([A-Z][a-zA-Z0-9\s&.\-]{1,35}?)(?:[\.,;]|\band\b|\s+regarding|$)",
                text,
            )
            if company:
                profile.opposite_party_name = company.group(1).strip()
            elif landlord:
                profile.opposite_party_name = landlord.group(1).strip()
            elif from_match:
                extracted_from = from_match.group(1).strip()
                if not any(city in extracted_from for city in ["Delhi", "Noida", "Pune", "Mumbai", "Jaipur"]):
                    profile.opposite_party_name = extracted_from
            if not profile.opposite_party_name:
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

        transaction_match = re.search(
            r"(?:transaction id|transaction ref|utr|rrn|reference number|ref no|disbursement reference|loan reference|disbursement ref|loan ref|reference)\s*(?:is|:|-)?\s*([A-Z0-9\-/]{6,40})",
            text,
            re.IGNORECASE,
        )
        if transaction_match:
            profile.transaction_id = transaction_match.group(1)
            self._set_fact(profile, "transaction_id", profile.transaction_id, 0.96)
            profile.key_facts["loan_reference"] = transaction_match.group(1)

        # Detect stated unknown facts (e.g. user does not have receiving bank or UTR)
        stated_unknown = set(profile.key_facts.get("stated_unknown_facts", []))
        if re.search(r"(?:don't have|do not have|don't know|do not know|genuinely don't know|cannot provide|can't provide|nahi pata|nahi hai)\s+.*?(?:utr|transaction id|payment id|receiving bank|bank details|bank name)", lower) or re.search(r"(?:genuinely don't know|don't know|do not know)\s+.*?(?:receiving bank|utr)", lower):
            if any(term in lower for term in ["utr", "transaction", "payment"]):
                stated_unknown.add("transaction_id")
            if any(term in lower for term in ["bank", "receiving"]):
                stated_unknown.add("bank_name")
        if stated_unknown:
            profile.key_facts["stated_unknown_facts"] = sorted(list(stated_unknown))
            for f_key in stated_unknown:
                if f_key in profile.fact_metadata:
                    profile.fact_metadata[f_key]["stated_unknown"] = True
                else:
                    profile.fact_metadata[f_key] = {"stated_unknown": True, "source": "user_stated_unknown"}

        for bank in ["State Bank of India", "SBI", "HDFC", "ICICI", "Axis Bank", "Kotak", "Paytm", "PhonePe", "Google Pay", "GPay"]:
            if re.search(r"\b" + re.escape(bank) + r"\b", text, re.IGNORECASE):
                # Only set bank_name if not referring to an existing unrelated personal account
                if not any(term in lower for term in ["accounts don't end", "accounts do not end", "my hdfc and sbi"]):
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
        return [fact.key for fact in domain_registry.unresolved_facts(profile)]

    def _compute_blocking_fields(self, profile: StructuredCaseProfile, missing: List[str]) -> List[str]:
        stated_unknown = set(profile.key_facts.get("stated_unknown_facts", []))
        for key, meta in profile.fact_metadata.items():
            if isinstance(meta, dict) and meta.get("stated_unknown"):
                stated_unknown.add(key)

        if profile.category == "CYBER_FRAUD":
            has_party = bool(profile.opposite_party_name or profile.bank_name)
            has_amount_or_ref = bool(
                profile.disputed_amount
                or profile.transaction_id
                or profile.key_facts.get("loan_reference")
            )
            blocking = []
            for field in missing:
                if field in stated_unknown:
                    continue
                if field in {"bank_name", "transaction_id"} and has_party and has_amount_or_ref:
                    # Actionable against the lender/institution
                    continue
                if field in {"incident_date", "user_state", "scam_method"}:
                    continue
                blocking.append(field)
            return blocking

        return [field for field in missing if field not in stated_unknown]

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
                    evidence_source = (pending_upload.get("sources") or {}).get(field)
                    if evidence_source:
                        profile.fact_metadata[field]["evidence_source"] = evidence_source
                else:
                    profile.key_facts[field] = value
            if pending_upload.get("facts", {}).get("response_outcome") == "REJECTED":
                action = domain_registry.resolve(profile.category).action("response_rejected")
                self._set_journey_current(profile, action.target_workflow_stage if action and action.target_workflow_stage else "ESCALATION", complete_through=True)
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
            workflow = self._workflow_for_category(profile.category)
            plan = action_planner.plan_next_action(profile, workflow)
            profile.next_action_plan = plan
            profile.is_ready_for_document = plan.status == NextActionStatus.READY and profile.risk_level != "RED"
            return ChatTurnResponse(
                reply_text="Thanks. I recorded the confirmed details, updated the legal journey where applicable, and kept the uploaded file in your evidence checklist.",
                case_profile=profile,
                quick_replies=["Continue my case"],
                next_action_plan=plan,
                message_id=str(uuid.uuid4()),
            )

        if any(term in lower for term in ["case resolved", "matter resolved", "got my refund", "paid me", "agreed to refund"]):
            profile.current_stage_key = "RESOLVED"
            profile.current_stage_label = "Resolved"
            self._add_action(profile, "case_resolved", "Case marked resolved")
            plan = action_planner.plan_next_action(profile, self._workflow_for_category(profile.category))
            profile.next_action_plan = plan
            return ChatTurnResponse(
                reply_text="That is good news. I marked this case as resolved. Keep the payment or settlement proof with your case records.",
                case_profile=profile,
                quick_replies=[],
                next_action_plan=plan,
                message_id=str(uuid.uuid4()),
            )

        sent_terms = ["i sent", "sent today", "sent yesterday", "notice bhej diya", "bhej diya", "i dispatched", "speed post"]
        if any(term in lower for term in sent_terms):
            action = domain_registry.resolve(profile.category).action("formal_demand_sent")
            self._set_journey_current(profile, action.target_workflow_stage if action and action.target_workflow_stage else "AWAITING_RESPONSE", complete_through=True)
            self._add_action(profile, "formal_demand_sent", "Formal letter recorded as sent")
            plan = action_planner.plan_next_action(profile, self._workflow_for_category(profile.category))
            profile.next_action_plan = plan
            return ChatTurnResponse(
                reply_text=(
                    f"Recorded as sent on {datetime.now().strftime('%d %B %Y')}. Your case is now awaiting response. "
                    "Keep the email delivery record or postal receipt. If a reply arrives, upload it here and I will help reassess the next step."
                ),
                case_profile=profile,
                quick_replies=["They rejected my demand", "They agreed to resolve it", "No response received"],
                next_action_plan=plan,
                message_id=str(uuid.uuid4()),
            )

        rejection_terms = ["they rejected", "landlord refused", "company refused", "seller refused", "mana kar diya", "rejected my refund", "no response received"]
        if any(term in lower for term in rejection_terms):
            action_definition = domain_registry.resolve(profile.category).action("response_rejected")
            self._set_journey_current(profile, action_definition.target_workflow_stage if action_definition and action_definition.target_workflow_stage else "ESCALATION", complete_through=True)
            self._add_action(profile, "response_rejected", "Response rejected or no response recorded")
            plan = action_planner.plan_next_action(profile, self._workflow_for_category(profile.category))
            profile.next_action_plan = plan
            document_type = plan.doc_type or select_document_for_workflow(profile.category, profile.current_stage_key)
            action = {"type": "PREPARE_DOC", "doc_type": document_type, "label": plan.label or "Prepare next complaint draft"}
            return ChatTurnResponse(
                reply_text=(
                    "I recorded the refusal or non-response. The next route depends on the forum and facts shown in your case workspace. "
                    "A formal complaint draft may now be appropriate; professional review is recommended for a court or tribunal filing."
                ),
                case_profile=profile,
                quick_replies=["Show my legal journey", "What evidence should I attach?"],
                suggested_action=action,
                next_action_plan=plan,
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

        plan = action_planner.plan_next_action(profile, workflow)
        profile.next_action_plan = plan

        # 1. If the selected action is BLOCKED, ask only for the blocking facts required by this action
        if plan.status == NextActionStatus.BLOCKED:
            blocking = plan.blocking_missing_facts
            if "opposite_party_name" in blocking or "bank_name" in blocking:
                if profile.category == "CYBER_FRAUD":
                    return "Which bank, lender, or financial institution is this unauthorized transaction or dispute against?", [], None
                elif profile.category == "EMPLOYMENT":
                    return "Who is the employer or company you are working for?", [], None
                elif profile.category == "CONSUMER":
                    return "Which seller, merchant, or company did you purchase from or have a dispute with?", [], None
                elif profile.category == "HOUSING_TENANT":
                    return "Which city and state was the rental property located in, and what is your landlord's name?", [], None
                else:
                    return "Which company, lender, or financial institution is this complaint against?", [], None

            if "disputed_amount" in blocking:
                if profile.category == "HOUSING_TENANT":
                    return "What was the security deposit amount withheld by the landlord?", [], None
                return "What is the disputed amount involved in this matter?", [], None

            if "unpaid_months" in blocking:
                return "Which specific months or period has your salary been unpaid?", [], None

            if "vacating_date" in blocking:
                return "When did you vacate or move out of the rental property?", [], None

            if "deposit_payment_proof_available" in blocking:
                return "Do you have proof of paying the security deposit (such as a bank transfer or receipt)?", [], None

            if "landlord_reason" in blocking:
                return "What reason, if any, did the landlord give for withholding your deposit?", [], None

            if "user_city" in blocking:
                return "Which city and state did this occur in?", [], None

            # Fallback for other blocking facts
            if blocking:
                labels = ", ".join(field.replace("_", " ") for field in blocking[:2])
                return f"To proceed with the next step ({plan.label}), please provide: {labels}.", [], None

        # 2. If the selected action is READY, proceed with execution / document preparation
        if plan.status == NextActionStatus.READY:
            profile.is_ready_for_document = True
            doc_type = plan.doc_type or select_document_for_workflow(profile.category, profile.current_stage_key)
            doc_label = plan.label
            action = {"type": "PREPARE_DOC", "doc_type": doc_type, "label": doc_label}
            profile.recommended_doc_type = doc_type
            profile.recommended_doc_label = doc_label

            if profile.category == "CYBER_FRAUD":
                party_name = profile.opposite_party_name or profile.bank_name or "the financial institution"
                ref_num = (
                    profile.transaction_id
                    or profile.key_facts.get("loan_reference")
                    or "Disbursement Record"
                )
                amount_str = f"₹{profile.disputed_amount:,.0f}" if profile.disputed_amount else "the disputed transaction/loan"

                reply = (
                    f"I have enough details to proceed with the next practical action regarding the unauthorized {amount_str} involving {party_name} (Reference: {ref_num}).\n\n"
                    f"### What this means\n"
                    f"Because you did not apply for, authorize, or receive these funds, this constitutes unauthorized financial identity fraud. "
                    f"You do not need to know the receiving bank or disbursement UTR right now—those details are within the records of {party_name} and can be requested from them in your dispute.\n\n"
                    f"### What you should do now\n"
                    f"1. **Dispute directly with {party_name}:** Send a formal written grievance citing reference {ref_num}, explicitly denying having applied for or received the funds, and demanding a full investigation, cancellation of the loan, and copies of disbursement records (receiving bank & UTR).\n"
                    f"2. **Report to Cyber Helpline 1930 & Portal:** Register a complaint on cybercrime.gov.in using reference {ref_num} and {party_name}.\n"
                    f"3. **Credit Bureau Dispute:** If this appears on your credit report, raise an unauthorized-account dispute with credit bureaus (CIBIL / Experian) to remove the fraudulent loan entry.\n\n"
                    f"### Recommended Next Step\n"
                    f"{doc_label}. You will review and confirm all details before the draft is generated."
                )
                return reply, [f"Draft dispute letter to {party_name}", "How to report on 1930 portal", "Dispute credit report entry"], action

            rights = profile.rights_summary or {}
            possible_rights = rights.get("possible_rights", [])[:2]
            rights_text = "\n".join(f"• {item}" for item in possible_rights)
            reply = (
                f"I have enough information to proceed with the next step: {plan.label}.\n\n"
                f"### What this means\n{rights.get('what_this_means', plan.description)}\n\n"
                f"### Your possible rights\n{rights_text}\n\n"
                f"### What you should do now\n{plan.description}\n\n"
                f"### Current next step\n{doc_label}. You will review all important names, amounts, dates, and addresses before anything is generated."
            )
            return reply, ["What evidence should I attach?", "How should I send it?"], action

        # 3. If all actions in the workflow are completed
        return (
            "Your case records and completed steps are saved. Keep all receipts, acknowledgements, and correspondence.",
            ["Show my case summary", "Download case records"],
            None,
        )

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
