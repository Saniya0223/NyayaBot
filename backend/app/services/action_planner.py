"""Next Action Planner for NyayaBot.

Determines the best executable next action based on current validated case state,
workflow definitions, and action prerequisites. Separates possible actions from
required vs non-blocking facts, and prevents circular recommendations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.domains import domain_registry
from app.domains.compatibility import read_profile_fact
from app.schemas.chat import NextActionPlan, NextActionStatus, StructuredCaseProfile


class ActionCandidate(BaseModel):
    action_id: str
    label: str
    description: str
    target_workflow_stage: Optional[str] = None
    required_fact_keys: List[str] = Field(default_factory=list)
    doc_type: Optional[str] = None


class NextActionPlanner:
    """Plans and evaluates deterministic next executable actions for a case."""

    @staticmethod
    def _is_fact_known(profile: StructuredCaseProfile, key: str) -> bool:
        present, value = read_profile_fact(profile, key)
        if not present or value in (None, "", [], 0, 0.0):
            # Also check key_facts
            facts = profile.key_facts or {}
            val = facts.get(key)
            return val not in (None, "", [], 0, 0.0)
        return True

    @staticmethod
    def _is_fact_stated_unknown(profile: StructuredCaseProfile, key: str) -> bool:
        stated = set(profile.key_facts.get("stated_unknown_facts", []))
        meta = (profile.fact_metadata or {}).get(key, {})
        if isinstance(meta, dict) and meta.get("stated_unknown"):
            return True
        return key in stated

    @classmethod
    def _is_action_completed(
        cls,
        profile: StructuredCaseProfile,
        candidate: ActionCandidate,
    ) -> bool:
        # Check actions_completed list
        completed_types = {
            item.get("type") or item.get("id") or item.get("action")
            for item in profile.actions_completed
            if isinstance(item, dict)
        }
        if candidate.action_id in completed_types:
            return True

        # Check key_facts flags
        facts = profile.key_facts or {}
        if candidate.action_id == "bank_reported":
            if facts.get("bank_reported") or facts.get("lender_contacted"):
                return True
            if profile.current_stage_key in {"BANK_REPORTED", "CYBERCRIME_PORTAL_FILED", "POLICE_FIR_ESCALATION", "ESCALATION"}:
                return True
            if any(a.get("type") in {"bank_reported", "formal_demand_sent", "response_rejected"} for a in profile.actions_completed if isinstance(a, dict)):
                return True

        if candidate.action_id == "cybercrime_reported":
            if facts.get("cyber_reported") or profile.current_stage_key in {"CYBERCRIME_PORTAL_FILED", "POLICE_FIR_ESCALATION"}:
                return True

        if candidate.action_id == "formal_demand_sent" and (facts.get("formal_demand_sent") or facts.get("seller_contacted") or facts.get("landlord_contacted") or facts.get("hr_contacted")):
            if profile.current_stage_key in {"AWAITING_RESPONSE", "AWAITING_SELLER_RESPONSE", "AWAITING_LANDLORD_RESPONSE", "AWAITING_EMPLOYER_RESPONSE", "ESCALATION", "EDAAKHIL_COMPLAINT", "RENT_AUTHORITY_ESCALATION", "LABOUR_COMMISSIONER_COMPLAINT"}:
                return True

        if candidate.target_workflow_stage and profile.current_stage_key == candidate.target_workflow_stage:
            return True

        return False

    @classmethod
    def get_candidate_actions(
        cls,
        profile: StructuredCaseProfile,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> List[ActionCandidate]:
        category = profile.category
        candidates: List[ActionCandidate] = []

        if category == "CYBER_FRAUD":
            party = profile.opposite_party_name or profile.bank_name or "Institution / Bank"
            candidates.append(
                ActionCandidate(
                    action_id="bank_reported",
                    label=f"Dispute with {party} & Alert Fraud Helpline",
                    description=f"Send a formal written dispute to {party} denying the unauthorized transaction/loan and demanding full audit records and disbursement details.",
                    target_workflow_stage="BANK_REPORTED",
                    required_fact_keys=["opposite_party_name", "bank_name"],  # Either party or bank name
                    doc_type="FORMAL_LEGAL_NOTICE",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="cybercrime_reported",
                    label="Report to National Cyber Crime Portal & 1930",
                    description="File an incident report on cybercrime.gov.in or call 1930 with reference and transaction facts.",
                    target_workflow_stage="CYBERCRIME_PORTAL_FILED",
                    required_fact_keys=[],  # Narrative is sufficient
                    doc_type="CYBERCRIME_BANK_FREEZE",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="police_fir_escalation",
                    label="Cyber Cell Police Complaint / FIR Escalation",
                    description="Escalate to the cyber police cell if recovery or institution response is refused.",
                    target_workflow_stage="POLICE_FIR_ESCALATION",
                    required_fact_keys=["disputed_amount"],
                    doc_type="POLICE_COMPLAINT_BNSS",
                )
            )

        elif category == "HOUSING_TENANT":
            candidates.append(
                ActionCandidate(
                    action_id="formal_demand_sent",
                    label="Prepare Tenant Security Deposit Demand Notice",
                    description="Send a formal written demand letter to the landlord specifying the vacating date and deposit amount.",
                    target_workflow_stage="AWAITING_LANDLORD_RESPONSE",
                    required_fact_keys=["opposite_party_name", "user_city", "vacating_date", "deposit_payment_proof_available", "landlord_reason"],
                    doc_type="TENANT_DEMAND_NOTICE",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="response_rejected",
                    label="Rent Authority / Consumer Forum Escalation",
                    description="File a complaint before the Rent Authority or Consumer Commission if the deposit is withheld after notice.",
                    target_workflow_stage="RENT_AUTHORITY_ESCALATION",
                    required_fact_keys=["disputed_amount", "user_state"],
                    doc_type="GENERAL_COMPLAINT_LETTER",
                )
            )

        elif category == "CONSUMER":
            candidates.append(
                ActionCandidate(
                    action_id="formal_demand_sent",
                    label="Prepare Consumer Grievance / Legal Notice",
                    description="Send a formal legal notice to the seller or service provider demanding a refund, replacement, or rectification.",
                    target_workflow_stage="AWAITING_SELLER_RESPONSE",
                    required_fact_keys=["opposite_party_name"],
                    doc_type="FORMAL_LEGAL_NOTICE",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="response_rejected",
                    label="File e-Daakhil Consumer Complaint",
                    description="File a formal consumer complaint online via the e-Daakhil portal if the seller refuses relief.",
                    target_workflow_stage="EDAAKHIL_COMPLAINT",
                    required_fact_keys=["opposite_party_name", "disputed_amount"],
                    doc_type="EDAAKHIL_COMPLAINT",
                )
            )

        elif category == "EMPLOYMENT":
            candidates.append(
                ActionCandidate(
                    action_id="formal_demand_sent",
                    label="Prepare Salary Demand Notice",
                    description="Send a formal salary demand notice to the employer specifying unpaid months and dues.",
                    target_workflow_stage="AWAITING_EMPLOYER_RESPONSE",
                    required_fact_keys=["opposite_party_name"],
                    doc_type="SALARY_DEMAND_NOTICE",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="response_rejected",
                    label="Labour Commissioner Grievance",
                    description="Submit a complaint to the Labour Commissioner or conciliation officer for unpaid wages.",
                    target_workflow_stage="LABOUR_COMMISSIONER_COMPLAINT",
                    required_fact_keys=["opposite_party_name", "disputed_amount"],
                    doc_type="GENERAL_COMPLAINT_LETTER",
                )
            )

        elif category == "POLICE_COMPLAINT":
            candidates.append(
                ActionCandidate(
                    action_id="police_complaint_submitted",
                    label="Draft Police Complaint under BNSS",
                    description="Prepare a formal written complaint with facts and evidence for submission to the SHO.",
                    target_workflow_stage="FORMAL_WRITTEN_COMPLAINT",
                    required_fact_keys=[],
                    doc_type="POLICE_COMPLAINT_BNSS",
                )
            )
            candidates.append(
                ActionCandidate(
                    action_id="response_rejected",
                    label="Escalate to Superintendent of Police (SP) under BNSS",
                    description="Send a copy of the refused complaint by Speed Post to the District SP.",
                    target_workflow_stage="SP_ESCALATION",
                    required_fact_keys=[],
                    doc_type="POLICE_COMPLAINT_BNSS",
                )
            )

        else:
            candidates.append(
                ActionCandidate(
                    action_id="clarify_legal_issue",
                    label="Identify Legal Issue & Appropriate Next Step",
                    description="Understand the core grievance and determine the governing legal framework.",
                    target_workflow_stage="INTAKE",
                    required_fact_keys=[],
                    doc_type=None,
                )
            )

        return candidates

    @classmethod
    def evaluate_action(
        cls,
        profile: StructuredCaseProfile,
        candidate: ActionCandidate,
        all_unresolved_facts: List[str],
    ) -> NextActionPlan:
        if cls._is_action_completed(profile, candidate):
            return NextActionPlan(
                action_id=candidate.action_id,
                label=candidate.label,
                description=candidate.description,
                target_workflow_stage=candidate.target_workflow_stage,
                status=NextActionStatus.COMPLETED,
                doc_type=candidate.doc_type,
            )

        required = list(candidate.required_fact_keys)
        available: List[str] = []
        blocking: List[str] = []
        non_blocking: List[str] = []

        if candidate.action_id == "bank_reported" and "opposite_party_name" in required and "bank_name" in required:
            # Special disjunction: either opposite_party_name OR bank_name is sufficient
            has_party = cls._is_fact_known(profile, "opposite_party_name") or cls._is_fact_known(profile, "bank_name")
            if has_party:
                available.append("opposite_party_name" if cls._is_fact_known(profile, "opposite_party_name") else "bank_name")
            else:
                blocking.append("opposite_party_name")
        else:
            for fact_key in required:
                if cls._is_fact_known(profile, fact_key):
                    available.append(fact_key)
                elif cls._is_fact_stated_unknown(profile, fact_key):
                    non_blocking.append(fact_key)
                else:
                    blocking.append(fact_key)

        # All other unresolved domain facts that aren't strictly required for this action are non-blocking
        for f in all_unresolved_facts:
            if f not in required and f not in blocking and f not in non_blocking:
                non_blocking.append(f)

        status = NextActionStatus.READY if len(blocking) == 0 else NextActionStatus.BLOCKED

        return NextActionPlan(
            action_id=candidate.action_id,
            label=candidate.label,
            description=candidate.description,
            target_workflow_stage=candidate.target_workflow_stage,
            required_facts=required,
            available_facts=available,
            blocking_missing_facts=blocking,
            non_blocking_missing_facts=non_blocking,
            status=status,
            doc_type=candidate.doc_type,
        )

    @classmethod
    def plan_next_action(
        cls,
        profile: StructuredCaseProfile,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> NextActionPlan:
        unresolved = [fact.key for fact in domain_registry.unresolved_facts(profile)]
        candidates = cls.get_candidate_actions(profile, workflow)

        for candidate in candidates:
            plan = cls.evaluate_action(profile, candidate, unresolved)
            if plan.status == NextActionStatus.COMPLETED:
                continue
            return plan

        last_candidate = candidates[-1] if candidates else ActionCandidate(
            action_id="case_resolved",
            label="Case Records Maintained",
            description="All immediate workflow steps completed. Retain case documents and evidence.",
        )
        return NextActionPlan(
            action_id=last_candidate.action_id,
            label=last_candidate.label,
            description=last_candidate.description,
            target_workflow_stage=last_candidate.target_workflow_stage,
            status=NextActionStatus.COMPLETED,
            doc_type=last_candidate.doc_type,
        )


action_planner = NextActionPlanner()
