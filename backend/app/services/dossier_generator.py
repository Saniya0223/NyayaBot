from typing import Dict, Any
from app.schemas.fact_graph import FactGraphSchema
from app.schemas.document import PortalFilingDossier, PortalDossierStep

class DossierGenerator:
    """
    Generates step-by-step Government Portal Filing Dossiers and copy-paste packages.
    Bridges the gap where automated APIs do not exist (e-Daakhil, NCH, RTI Online).
    """

    @classmethod
    def generate_dossier(
        cls,
        case_id: str,
        category: str,
        fact_graph: FactGraphSchema,
        appropriate_forum: str
    ) -> PortalFilingDossier:
        cat = category.upper()

        if cat == "CONSUMER":
            return cls._build_edaakhil_dossier(case_id, fact_graph, appropriate_forum)
        elif cat == "TENANCY":
            return cls._build_tenancy_dossier(case_id, fact_graph, appropriate_forum)
        elif cat == "RTI":
            return cls._build_rti_dossier(case_id, fact_graph, appropriate_forum)
        else:
            return cls._build_edaakhil_dossier(case_id, fact_graph, appropriate_forum)

    @classmethod
    def _build_edaakhil_dossier(cls, case_id: str, fact_graph: FactGraphSchema, forum: str) -> PortalFilingDossier:
        steps = [
            PortalDossierStep(
                step_number=1,
                title="Register / Login on e-Daakhil Portal",
                description="Navigate to the official e-Daakhil portal. If you are a new citizen user, register using your Mobile Number and Aadhaar/OTP verification.",
                portal_url="https://edaakhil.nic.in",
                portal_section="Citizen Login -> New Consumer Complaint",
                fields_to_fill=[
                    {"label": "Complainant Name", "value": fact_graph.complainant.name},
                    {"label": "Complainant Mobile", "value": fact_graph.complainant.phone or "Your 10-digit mobile"},
                    {"label": "Complainant State", "value": fact_graph.complainant.state or "State"},
                    {"label": "Complainant District/City", "value": fact_graph.complainant.city or "City"}
                ],
                documents_to_upload=["Aadhaar / Voter ID (Identity Proof)"],
                pro_tip="Ensure the mobile number matches your Aadhaar-linked number to receive instant OTP verification."
            ),
            PortalDossierStep(
                step_number=2,
                title="Select Commission Jurisdiction & Case Type",
                description="Select the appropriate State and District Commission based on your residence or where the dispute occurred.",
                portal_url="https://edaakhil.nic.in",
                portal_section="Filing -> Select Commission",
                fields_to_fill=[
                    {"label": "State Commission / District", "value": fact_graph.complainant.state or "Delhi"},
                    {"label": "Selected Commission", "value": forum},
                    {"label": "Case Type", "value": "Consumer Case (CC)"},
                    {"label": "Claim Valuation", "value": f"₹{fact_graph.financials.total_claim_amount:,.2f}"}
                ],
                documents_to_upload=[],
                pro_tip="Under CPA 2019 Section 34(2)(d), you are legally entitled to file at your own home district regardless of where the company's head office is."
            ),
            PortalDossierStep(
                step_number=3,
                title="Enter Opposite Party & Transaction Details",
                description="Fill in the official registered details of the opposite seller or service provider.",
                portal_url="https://edaakhil.nic.in",
                portal_section="Opposite Party Details",
                fields_to_fill=[
                    {"label": "Opposite Party Name", "value": fact_graph.opposite_party.name},
                    {"label": "Opposite Party Address", "value": fact_graph.opposite_party.address or "Address"},
                    {"label": "Date of Cause of Action", "value": fact_graph.incident_date or "Incident Date"},
                    {"label": "Total Relief Claimed", "value": f"₹{fact_graph.financials.total_claim_amount:,.2f}"}
                ],
                documents_to_upload=[],
                pro_tip="Copy and paste the exact values provided in this card to prevent clerical errors."
            ),
            PortalDossierStep(
                step_number=4,
                title="Upload Court-Ready PDF Complaint & Annexures",
                description="Upload the generated complaint PDF and supporting proof documents in the specified sequence.",
                portal_url="https://edaakhil.nic.in",
                portal_section="Document Upload Section",
                fields_to_fill=[],
                documents_to_upload=[
                    "1. Generated Memorandum of Complaint (Signed PDF)",
                    "2. Verification Affidavit (Signed & Dated)",
                    "3. Annexure A-1: Tax Invoice / Order Receipt",
                    "4. Annexure A-2: Written Communications & Legal Notice Copy",
                    "5. Speed Post / Email Delivery Tracking Receipt"
                ],
                pro_tip="Keep all files below 5MB each in PDF format. Self-attest each page with your signature before uploading."
            ),
            PortalDossierStep(
                step_number=5,
                title="Online Fee Payment & Acknowledgement",
                description="Pay the statutory court fees via BharatKosh / Payment Gateway. Note your 16-digit e-Daakhil Acknowledgement Number.",
                portal_url="https://edaakhil.nic.in",
                portal_section="Fee Payment & Final Submission",
                fields_to_fill=[
                    {"label": "Prescribed Fee Amount", "value": "₹0 (under ₹5L) or ₹200-₹500 (up to ₹50L)"}
                ],
                documents_to_upload=[],
                pro_tip="Save the PDF receipt of payment. Add your Acknowledgement Number to NyayaSahay Case Tracker to monitor scrutiny status."
            )
        ]

        annexures = [
            {"label": "Main Pleading", "title": "Consumer Complaint Memo & Verification Affidavit"},
            {"label": "Proof of Purchase", "title": "Tax Invoice / Transaction Receipt"},
            {"label": "Prior Notice", "title": "Copy of Formal Legal Demand Notice Served"},
            {"label": "Service Proof", "title": "Speed Post Postal Receipt / Email Delivery Acknowledgment"}
        ]

        return PortalFilingDossier(
            case_id=case_id,
            portal_name="e-Daakhil (National Consumer Disputes Portal)",
            portal_url="https://edaakhil.nic.in",
            forum_name=forum,
            prescribed_fees="₹0 for claims up to ₹5 Lakhs | ₹200-₹500 up to ₹50 Lakhs",
            estimated_resolution_time="Scrutiny in 7-14 Days | First Hearing in 21-45 Days",
            steps=steps,
            annexure_checklist=annexures
        )

    @classmethod
    def _build_tenancy_dossier(cls, case_id: str, fact_graph: FactGraphSchema, forum: str) -> PortalFilingDossier:
        steps = [
            PortalDossierStep(
                step_number=1,
                title="Serve Mandatory Legal Demand Notice",
                description="Send the generated Security Deposit Demand Notice via Indian Speed Post with Acknowledgment Due (AD) to the landlord.",
                fields_to_fill=[
                    {"label": "Notice Cure Period", "value": "7 to 15 Days from receipt"},
                    {"label": "Disputed Deposit Amount", "value": f"₹{fact_graph.financials.amount_paid:,.2f}"}
                ],
                documents_to_upload=["Speed Post Receipt"],
                pro_tip="Track the Speed Post tracking number on indiapost.gov.in and download the delivery confirmation report as conclusive legal proof."
            ),
            PortalDossierStep(
                step_number=2,
                title="Approach Local Rent Authority / Rent Tribunal",
                description="If the landlord does not refund the deposit within the notice period, file an application before the Rent Authority.",
                fields_to_fill=[
                    {"label": "Jurisdiction Forum", "value": forum},
                    {"label": "Relief Claimed", "value": f"Full refund of ₹{fact_graph.financials.amount_paid:,.2f} with interest"}
                ],
                documents_to_upload=[
                    "1. Tenancy / Lease Agreement copy",
                    "2. Bank statement showing original security deposit transfer",
                    "3. Move-out communication / keys handover proof",
                    "4. Copy of Demand Notice & Speed Post Delivery Confirmation"
                ],
                pro_tip="Rent Authority proceedings are time-bound under the Model Tenancy Act, typically resolved within 60-90 days."
            )
        ]
        return PortalFilingDossier(
            case_id=case_id,
            portal_name="Local Rent Authority / District Civil Court",
            portal_url="https://services.ecourts.gov.in",
            forum_name=forum,
            prescribed_fees="Nominal application fee / Standard stamp fee",
            estimated_resolution_time="30 to 90 Days",
            steps=steps,
            annexure_checklist=[
                {"label": "Agreement", "title": "Executed Rent Agreement"},
                {"label": "Payment Proof", "title": "Bank Statement / Security Deposit Receipt"},
                {"label": "Notice Proof", "title": "Demand Notice & Speed Post Delivery Record"}
            ]
        )

    @classmethod
    def _build_rti_dossier(cls, case_id: str, fact_graph: FactGraphSchema, forum: str) -> PortalFilingDossier:
        steps = [
            PortalDossierStep(
                step_number=1,
                title="Access RTI Online Portal (Central / State)",
                description="Open the RTI Online portal. Select 'Submit Request' and accept the guidelines.",
                portal_url="https://rtionline.gov.in",
                portal_section="Submit Request",
                fields_to_fill=[
                    {"label": "Public Authority", "value": fact_graph.opposite_party.name},
                    {"label": "Applicant Name", "value": fact_graph.complainant.name},
                    {"label": "Email Address", "value": fact_graph.complainant.email or "Your email"},
                    {"label": "Mobile Number", "value": fact_graph.complainant.phone or "Your mobile"}
                ],
                documents_to_upload=[],
                pro_tip="Search the Ministry/Department dropdown for the exact public authority."
            ),
            PortalDossierStep(
                step_number=2,
                title="Paste Structured RTI Queries",
                description="Paste the exact queries generated by NyayaSahay into the 'Text for RTI Request Application' field.",
                portal_url="https://rtionline.gov.in",
                portal_section="Text for RTI Request Application",
                fields_to_fill=[
                    {"label": "Application Text", "value": "Refer to generated RTI Application Section 5"}
                ],
                documents_to_upload=["Supporting representation / document (Max 1MB PDF)"],
                pro_tip="Do not ask for opinions or 'Why' questions under RTI. Only ask for certified records, notings, orders, and timelines."
            ),
            PortalDossierStep(
                step_number=3,
                title="Pay ₹10 Fee & Note Registration Number",
                description="Pay the ₹10 statutory fee via Net Banking/UPI/Debit Card and save your unique RTI Registration Number.",
                portal_url="https://rtionline.gov.in",
                portal_section="Payment Gateway",
                fields_to_fill=[{"label": "Statutory Fee", "value": "₹10"}],
                documents_to_upload=[],
                pro_tip="The PIO has a statutory deadline of 30 days under Section 7(1). If no reply is received within 30 days, you can file a First Appeal on the same portal for ₹0 fee."
            )
        ]
        return PortalFilingDossier(
            case_id=case_id,
            portal_name="RTI Online Portal (Government of India)",
            portal_url="https://rtionline.gov.in",
            forum_name=forum,
            prescribed_fees="₹10 Application Fee",
            estimated_resolution_time="Mandatory 30 Days statutory deadline under Sec 7(1)",
            steps=steps,
            annexure_checklist=[
                {"label": "RTI Draft", "title": "Application under Section 6(1) RTI Act, 2005"},
                {"label": "Payment Acknowledgment", "title": "₹10 Online Payment / Postal Order Slip"}
            ]
        )
