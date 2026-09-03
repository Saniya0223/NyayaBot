import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.db.models import CaseModel, FactGraphModel, CaseTimelineEventModel, UserModel
from app.schemas.case import IntakeRequest, CaseResponse, StatutoryCitation, CaseTimelineEventSchema
from app.agents.classifier_node import IssueClassifier
from app.agents.intake_node import IntakeFactExtractor
from app.agents.calculator_node import LegalCalculator
from app.agents.rag_node import statutory_rag
from app.schemas.fact_graph import FactGraphSchema

class LegalOrchestrator:
    """
    Main Orchestration Agent connecting Intake, Classification, RAG, Calculation,
    and Long-term Case Memory.
    """

    @classmethod
    def process_intake(cls, req: IntakeRequest, db: Session) -> CaseResponse:
        # 1. Classify Issue & Evaluate Severity / Escalation Guardrails
        classification = IssueClassifier.classify_and_evaluate(req.user_narrative)
        category = classification["category"]
        severity = classification["severity_level"]
        escalation_reason = classification.get("escalation_reason")

        # 2. Extract Facts & Missing Questions
        initial_context = {
            "user_name": req.user_name,
            "user_city": req.user_city,
            "user_state": req.user_state,
            "user_phone": req.user_phone,
            "user_email": req.user_email,
            "category": category
        }
        fact_graph_schema = IntakeFactExtractor.extract_facts(req.user_narrative, initial_context)

        # 3. Calculate Jurisdiction Forum
        claim_amt = fact_graph_schema.financials.total_claim_amount
        user_city = req.user_city or "Local District"

        if category == "CONSUMER":
            jurisdiction_info = LegalCalculator.calculate_consumer_jurisdiction(claim_amt, user_city)
        elif category == "TENANCY":
            jurisdiction_info = LegalCalculator.calculate_tenancy_jurisdiction(user_city)
        elif category == "RTI":
            jurisdiction_info = LegalCalculator.calculate_rti_jurisdiction(fact_graph_schema.opposite_party.name)
        else:
            jurisdiction_info = LegalCalculator.calculate_consumer_jurisdiction(claim_amt, user_city)

        forum_name = jurisdiction_info["appropriate_forum"]

        # 4. Calculate Statutory Limitation
        cause_date = fact_graph_schema.incident_date
        limitation_deadline, days_left, limit_status = LegalCalculator.calculate_limitation_period(category, cause_date)

        # 5. Retrieve Curated Statutory Grounds (RAG)
        citations = statutory_rag.retrieve_applicable_sections(category, req.user_narrative)

        # 6. Check / Create Case in Database
        case_id = req.case_id or str(uuid.uuid4())
        existing_case = db.query(CaseModel).filter(CaseModel.id == case_id).first() if req.case_id else None

        if not existing_case:
            case_number = f"NYA-{datetime.now().year}-{str(uuid.uuid4())[:4].upper()}"
            title = f"{category.capitalize()} Dispute - {fact_graph_schema.opposite_party.name}"
            
            case_obj = CaseModel(
                id=case_id,
                case_number=case_number,
                title=title,
                category=category,
                status="FACTS_EXTRACTED" if fact_graph_schema.is_complete else "INTAKE_IN_PROGRESS",
                severity_level=severity,
                cause_of_action_date=cause_date,
                limitation_deadline=limitation_deadline,
                pecuniary_value=claim_amt,
                appropriate_forum=forum_name
            )
            db.add(case_obj)
            db.flush()

            # Create Fact Graph record
            fact_graph_db = FactGraphModel(
                case_id=case_id,
                complainant_data=fact_graph_schema.complainant.model_dump(),
                opposite_party_data=fact_graph_schema.opposite_party.model_dump(),
                incident_narrative=fact_graph_schema.incident_narrative,
                structured_timeline=[t.model_dump() for t in fact_graph_schema.timeline],
                financial_breakdown=fact_graph_schema.financials.model_dump(),
                evidence_inventory=[e.model_dump() for e in fact_graph_schema.evidence_inventory],
                missing_facts=fact_graph_schema.missing_facts,
                is_complete=fact_graph_schema.is_complete,
                completion_score=fact_graph_schema.completion_score
            )
            db.add(fact_graph_db)

            # Seed Default Case Milestones
            cls._seed_timeline_milestones(case_id, category, cause_date, limitation_deadline, db)
            db.commit()
            db.refresh(case_obj)
        else:
            case_obj = existing_case
            case_obj.category = category
            case_obj.severity_level = severity
            case_obj.cause_of_action_date = cause_date
            case_obj.limitation_deadline = limitation_deadline
            case_obj.pecuniary_value = claim_amt
            case_obj.appropriate_forum = forum_name
            
            # Update fact graph
            if case_obj.fact_graph:
                case_obj.fact_graph.incident_narrative = fact_graph_schema.incident_narrative
                case_obj.fact_graph.complainant_data = fact_graph_schema.complainant.model_dump()
                case_obj.fact_graph.opposite_party_data = fact_graph_schema.opposite_party.model_dump()
                case_obj.fact_graph.financial_breakdown = fact_graph_schema.financials.model_dump()
                case_obj.fact_graph.evidence_inventory = [e.model_dump() for e in fact_graph_schema.evidence_inventory]
                case_obj.fact_graph.missing_facts = fact_graph_schema.missing_facts
                case_obj.fact_graph.is_complete = fact_graph_schema.is_complete
                case_obj.fact_graph.completion_score = fact_graph_schema.completion_score
            
            db.commit()
            db.refresh(case_obj)

        # 7. Fetch Timeline Events
        timeline_events_db = db.query(CaseTimelineEventModel).filter(CaseTimelineEventModel.case_id == case_id).all()
        timeline_schemas = [
            CaseTimelineEventSchema(
                id=ev.id,
                title=ev.title,
                description=ev.description,
                event_type=ev.event_type,
                target_date=ev.target_date,
                completed_at=ev.completed_at,
                is_mandatory=ev.is_mandatory,
                status=ev.status
            )
            for ev in timeline_events_db
        ]

        # 8. Suggested Actions
        suggested_actions = [
            "Review extracted fact graph and answer missing clarifications",
            "Generate formal legal demand notice to opposite party",
            "Send legal notice via Speed Post and retain tracking slip",
            f"If unresolved after cure period, file online complaint on {jurisdiction_info['appropriate_forum']}"
        ]

        return CaseResponse(
            id=case_obj.id,
            case_number=case_obj.case_number,
            title=case_obj.title,
            category=case_obj.category,
            status=case_obj.status,
            severity_level=case_obj.severity_level,
            cause_of_action_date=case_obj.cause_of_action_date,
            limitation_deadline=case_obj.limitation_deadline,
            limitation_days_remaining=days_left,
            pecuniary_value=case_obj.pecuniary_value,
            appropriate_forum=case_obj.appropriate_forum,
            fact_graph=fact_graph_schema,
            timeline_events=timeline_schemas,
            applicable_statutes=citations,
            suggested_actions=suggested_actions,
            escalation_reason=escalation_reason,
            created_at=case_obj.created_at,
            updated_at=case_obj.updated_at
        )

    @classmethod
    def _seed_timeline_milestones(cls, case_id: str, category: str, cause_date: Optional[str], limitation_deadline: Optional[str], db: Session):
        now = datetime.now()
        
        events = [
            CaseTimelineEventModel(
                case_id=case_id,
                title="Grievance / Cause of Action Occurred",
                description="The primary incident, non-delivery, defective supply, or wrongful withholding took place.",
                event_type="INCIDENT",
                target_date=cause_date or now.strftime("%d-%m-%Y"),
                completed_at=datetime.utcnow(),
                status="COMPLETED"
            ),
            CaseTimelineEventModel(
                case_id=case_id,
                title="Serve Formal Legal Demand Notice",
                description="Issue the pre-vetted legal notice giving 15 days statutory cure period via Registered Speed Post / Email.",
                event_type="NOTICE_SENT",
                target_date=(now + timedelta(days=2)).strftime("%d-%m-%Y"),
                status="PENDING"
            ),
            CaseTimelineEventModel(
                case_id=case_id,
                title="Expiry of 15-Day Cure Window",
                description="Wait for opposite party to refund or remedy. If they refuse or ignore, cause of action for court filing matures.",
                event_type="DEADLINE",
                target_date=(now + timedelta(days=17)).strftime("%d-%m-%Y"),
                status="PENDING"
            ),
            CaseTimelineEventModel(
                case_id=case_id,
                title="Institute Complaint on Portal (e-Daakhil / RTI / Tribunal)",
                description="Submit verified complaint and annexures using NyayaSahay guided filing dossier.",
                event_type="PORTAL_FILED",
                target_date=(now + timedelta(days=20)).strftime("%d-%m-%Y"),
                status="PENDING"
            ),
            CaseTimelineEventModel(
                case_id=case_id,
                title="Statutory Limitation Final Expiry",
                description="Final statutory date before which legal complaint must be registered.",
                event_type="DEADLINE",
                target_date=limitation_deadline or (now + timedelta(days=730)).strftime("%d-%m-%Y"),
                status="PENDING"
            )
        ]
        db.add_all(events)

legal_orchestrator = LegalOrchestrator()
