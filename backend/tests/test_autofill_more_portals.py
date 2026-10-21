"""CPGRAMS, RBI CMS and Samadhan: portal choice per case type, general section
recognition, and plans for each portal's layout. Pure local plans; no browser,
network or real LLM calls."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import portal_autofill as pa
from tests.test_autofill_extension_api import _bearer, _case, _client, _start

ASHA = {"user_name": "Asha Verma", "user_phone": "9876543210", "user_email": "asha@example.com",
        "user_address": "12 Rose Lane, Malviya Nagar", "user_state": "Rajasthan", "user_city": "Jaipur",
        "user_pin_code": "302017"}
GENERAL = {**ASHA, "category": "GENERAL", "issue_type": "UNCLASSIFIED",
           "incident_narrative": "My passport application has been pending for six months without any reply."}
CYBER = {**ASHA, "category": "CYBER_FRAUD", "issue_type": "UPI_BANK_TRANSFER_FRAUD", "bank_name": "State Bank of India",
         "transaction_id": "UTR123456789", "disputed_amount": 25000, "incident_date": "2026-09-20",
         "incident_narrative": "A caller posing as bank staff took Rs 25,000 by UPI. The bank has not refunded it."}
WORK = {**ASHA, "category": "EMPLOYMENT", "issue_type": "UNPAID_DELAYED_SALARY",
        "opposite_party_name": "Example Textiles Pvt Ltd", "opposite_party_address": "Plot 4, Sitapura Industrial Area, Jaipur",
        "disputed_amount": 54000, "incident_narrative": "Salary for three months has not been paid.",
        "key_facts": {"monthly_salary": 18000, "employee_role": "Machine Operator"}}


def field(n, label, section="", **extra):
    return pa.ScannedField(field_id=f"0:{n}", tag=extra.pop("tag", "input"), label=label, section=section, **extra)


def custom(n, name, section="", options=()):
    return pa.ScannedField(field_id=f"0:{100000 + n}", tag="custom", type="custom", aria=name, section=section,
                           options=list(options))


def plan(portal, fields, profile, provider=None):
    request = pa.PlanRequest(page_url=portal.url, fields=fields)
    return asyncio.run(pa.build_plan(request, portal, pa.case_values(profile, SimpleNamespace()),
                                     profile=profile, provider=provider))


def keys(result):
    return {a.field_label: (a.key, a.value, a.needs_approval) for a in result.actions}


class PickModel:
    """A stand-in model that picks the option whose text contains `wanted`."""

    def __init__(self, wanted):
        self.wanted, self.prompts = wanted, []
        self.status = SimpleNamespace(configured=True)

    async def _generate_structured(self, prompt, _system, schema):
        import json

        self.prompts.append(prompt)
        data = json.loads(prompt.split("INPUT_DATA:\n", 1)[1])
        options = data["QUESTIONS"][0]["OPTIONS"]
        index = next((o["option_index"] for o in options if self.wanted in o["text"]), None)
        return pa.AIChoiceResult(choices=[pa.AIChoice(group=0, option_index=index, reason="Fits the case.")])


# ── Which portals fit a case ──────────────────────────────────────────────────

def test_each_case_type_gets_its_portals_in_order():
    assert [p.key for p in pa.portals_for_category("CYBER_FRAUD")] == ["cybercrime", "rbi_cms"]
    assert [p.key for p in pa.portals_for_category("EMPLOYMENT")] == ["samadhan"]
    assert [p.key for p in pa.portals_for_category("GENERAL")] == ["cpgrams"]
    assert [p.key for p in pa.portals_for_category("CONSUMER")] == ["ejagriti"]
    assert pa.portals_for_category("HOUSING_TENANT") == []
    assert all(p.choose_when for p in pa.PORTALS.values()), "every portal says when it fits"


def test_portals_api_offers_the_choice_and_start_accepts_only_a_fitting_portal():
    client = _client()
    case_id = _case(client, category="CYBER_FRAUD")
    listed = client.get(f"/api/v1/autofill/portals?case_id={case_id}").json()
    assert [p["key"] for p in listed["portals"]] == ["cybercrime", "rbi_cms"]
    assert "30 days" in listed["portals"][1]["when"]
    started = _start(client, case_id, portal="rbi_cms")
    assert started.status_code == 201 and started.json()["portal"]["url"] == "https://cms.rbi.org.in/"
    assert _start(client, case_id, portal="samadhan").status_code == 409, "a portal that doesn't fit is refused"
    assert _start(client, case_id).json()["portal"]["key"] == "cybercrime", "auto: the first fitting portal"
    assert client.get("/api/v1/autofill/portals?case_id=not-mine").status_code == 404


def test_each_test_layout_is_development_only(monkeypatch):
    client = _client()
    case_id = _case(client, category="EMPLOYMENT")
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", False)
    assert client.get("/api/v1/autofill/test-samadhan").status_code == 404
    assert _start(client, case_id, portal="test-samadhan").status_code == 404
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", True)
    assert client.get(f"/api/v1/autofill/portals?case_id={case_id}").json()["portals"][0]["test"] == "test-samadhan"
    for path, text in (("test-cpgrams", "Lodge Public Grievance"), ("test-rbi", "File A Complaint"),
                       ("test-samadhan", "Employer / Establishment Details")):
        page = client.get(f"/api/v1/autofill/{path}")
        assert page.status_code == 200 and text in page.text
    started = _start(client, case_id, portal="test-samadhan").json()
    assert started["portal"]["key"] == "test-samadhan" and started["portal"]["url"].endswith("/api/v1/autofill/test-samadhan")
    code = started["code"]
    info = TestClient(app).get("/api/v1/autofill/session", headers=_bearer(code)).json()
    assert {v["key"] for v in info["values"]} >= {"monthly_salary", "employee_role", "opposite_party_name"}


# ── Section headings recognised by their words (any portal) ────────────────────

@pytest.mark.parametrize("heading, group", [
    ("Worker Details", "complainant"),
    ("Applicant Details", "complainant"),
    ("Complainant Details", "complainant"),
    ("Employer / Establishment Details", "opposite_party"),
    ("Establishment Details", "opposite_party"),
    ("Details of the Regulated Entity", "opposite_party"),
    ("Respondent Details", "opposite_party"),
    ("Complaint Details", "case"),
    ("Application Details", "case"),
    ("Claim Details", "case"),
    ("Personal Information", "self"),
    ("Contact Details", "self"),
    ("Complainant / Opposite Party", None),
    ("Additional Complainant Details", None),
    ("Proforma Opposite Party Details", None),
    ("Bank Account Details", None),
    ("Advocate Details", None),
    ("Upload Documents", None),
])
def test_headings_are_recognised_by_their_words(heading, group):
    assert pa._heading_group(pa.ff._norm(heading)) == group


def test_a_page_without_headings_is_the_users_own_unless_another_party_appears():
    sign_up = [field(1, "Name"), field(2, "Mobile", type="tel"), field(3, "Email", type="email")]
    result = plan(pa.SAMADHAN, sign_up, WORK)
    assert {k for k, *_ in keys(result).values()} == {"complainant_name", "complainant_phone", "complainant_email"}
    mixed = sign_up + [field(4, "Name of Establishment", "Employer / Establishment Details")]
    result = plan(pa.SAMADHAN, mixed, WORK)
    assert not any(a.key.startswith("complainant_") for a in result.actions), "no guessing beside the other party"
    assert {"0:1", "0:2", "0:3"} <= {n.field_id for n in result.manual}


# ── CPGRAMS ────────────────────────────────────────────────────────────────────

def test_cpgrams_sign_up_form_fills_the_users_details_and_leaves_the_rest():
    fields = [
        field(1, "Name", maxlength=100), field(2, "Address", placeholder="Premise Number or Name", maxlength=100),
        field(3, "", aria="Sub-locality", placeholder="Sub-locality"),
        field(4, "State", tag="select", type="select", options=["--Select--", "Delhi", "Rajasthan"]),
        field(5, "Pincode", maxlength=6), field(6, "Mobile number", type="tel", maxlength=10),
        field(7, "Phone number", placeholder="Phone number with STD code. (e.g 011XXXXXXXX)"),
        field(8, "E-mail address", type="email"), field(9, "Security Code"),
    ]
    result = plan(pa.CPGRAMS, fields, GENERAL)
    assert keys(result) == {
        "Name": ("complainant_name", "Asha Verma", False),
        "Address": ("complainant_address", "12 Rose Lane, Malviya Nagar", False),
        "State": ("complainant_state", "Rajasthan", False),
        "Pincode": ("complainant_pin", "302017", False),
        "Mobile number": ("complainant_phone", "9876543210", False),
        "E-mail address": ("complainant_email", "asha@example.com", False),
    }
    assert {"0:3", "0:7"} <= {n.field_id for n in result.manual}, "locality and landline stay with the user"
    assert [n.field_id for n in result.sensitive] == ["0:9"], "the security code is never touched"


def test_cpgrams_grievance_form_fills_the_description_and_ai_suggests_the_ministry_from_a_long_list():
    ministries = [f"Ministry number {i}" for i in range(60)] + ["Ministry of External Affairs"]
    fields = [
        field(1, "Ministry/Department/State Government", tag="select", type="select", options=["--Select--"] + ministries),
        field(2, "Grievance Description", tag="textarea", type="textarea", maxlength=4000),
    ]
    model = PickModel("External Affairs")
    result = plan(pa.CPGRAMS, fields, GENERAL, provider=model)
    actions = keys(result)
    assert actions["Grievance Description"] == ("incident_narrative", GENERAL["incident_narrative"], False)
    assert actions["Ministry/Department/State Government"] == ("list_choice", "Ministry of External Affairs", True)
    assert "Asha" not in model.prompts[0] and "9876543210" not in model.prompts[0], "no personal details to the model"


# ── RBI CMS ────────────────────────────────────────────────────────────────────

def test_rbi_form_fills_the_complaint_and_never_takes_the_entity_type_for_the_bank_name():
    fields = [
        field(1, "Name", "Complainant Details"),
        field(2, "Type of Regulated Entity", "Details of the Complaint", tag="select", type="select",
              options=["Select", "Bank", "NBFC", "Payment System Participant"]),
        custom(3, "Name of the Regulated Entity", "Details of the Complaint",
               ["Axis Bank", "HDFC Bank", "State Bank of India"]),
        field(4, "Date of Transaction", "Details of the Complaint", type="date"),
        field(5, "Amount Involved", "Details of the Complaint", type="number"),
        field(6, "Facts of the complaint", "Details of the Complaint", tag="textarea", type="textarea", maxlength=2000),
        field(7, "Complaint reference number given by the Regulated Entity", "Details of the Complaint"),
        field(8, "Account Number", "Details of the Complaint"),
    ]
    actions = keys(plan(pa.RBI_CMS, fields, CYBER))
    assert actions["Type of Regulated Entity"] == ("list_choice", "Bank", True)
    assert actions["Name of the Regulated Entity"] == ("bank_name", "State Bank of India", True)
    assert actions["Date of Transaction"] == ("incident_date", "2026-09-20", False)
    assert actions["Amount Involved"] == ("disputed_amount", "25000", False)
    assert actions["Facts of the complaint"][0] == "incident_narrative"
    assert "Complaint reference number given by the Regulated Entity" not in actions
    assert "Account Number" not in actions


def test_rbi_eligibility_questions_are_never_offered_as_case_answers():
    assert pa.RBI_CMS.choice_questions is False and pa.CPGRAMS.choice_questions is False


# ── Samadhan ───────────────────────────────────────────────────────────────────

SAMADHAN_PAGE = [
    custom(1, "Select Category", "Application Details",
           ["Claim adjudication under code on wages,2019", "Industrial Dispute under Section 2(q)", "Gratuity claim"]),
    field(2, "Name", "Worker Details"), field(3, "Mobile", "Worker Details", maxlength=10),
    field(4, "Designation", "Worker Details"), field(5, "Salary", "Worker Details", type="number"),
    field(6, "Name of Establishment", "Employer / Establishment Details"),
    field(7, "Name and Designation of Employer", "Employer / Establishment Details"),
    field(8, "Address of the Establishment", "Employer / Establishment Details"),
    field(9, "Mobile", "Employer / Establishment Details", maxlength=10),
    field(10, "Claim Amount", "Claim Details", type="number"),
    field(11, "Description", "Claim Details", tag="textarea", type="textarea"),
]


def test_samadhan_fills_worker_and_employer_into_their_own_sections():
    result = plan(pa.SAMADHAN, SAMADHAN_PAGE, WORK)
    actions = keys(result)
    assert actions["Name"] == ("complainant_name", "Asha Verma", False)
    assert actions["Designation"] == ("employee_role", "Machine Operator", False)
    assert actions["Salary"] == ("monthly_salary", "18000", False)
    assert actions["Name of Establishment"] == ("opposite_party_name", "Example Textiles Pvt Ltd", False)
    assert actions["Address of the Establishment"][0] == "opposite_party_address"
    assert actions["Claim Amount"] == ("disputed_amount", "54000", False), "wages: no 'price paid' warning"
    assert actions["Select Category"] == ("list_choice", "Claim adjudication under code on wages,2019", True)
    mobiles = [a for a in result.actions if a.field_label == "Mobile"]
    assert [(a.field_id, a.value) for a in mobiles] == [("0:3", "9876543210")], "the worker's mobile only in Worker Details"
    assert [(n.field_id, n.key) for n in result.missing] == [("0:9", "opposite_party_phone")]
    assert "0:7" in {n.field_id for n in result.manual}


def test_samadhan_suggests_an_industrial_dispute_for_a_termination_case():
    result = plan(pa.SAMADHAN, SAMADHAN_PAGE, {**WORK, "issue_type": "TERMINATION_ISSUE"})
    assert keys(result)["Select Category"][1] == "Industrial Dispute under Section 2(q)"
