"""Fill on Portal (browser extension) backend: sessions, codes, plans, reports.

All local: TestClient, the test database, fake LLM providers. No browser, no
network, no live model call.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified

from app import autofill_routes
from app.config import settings
from app.db.models import AutofillSessionModel, ChatCaseSessionModel
from app.db.session import SessionLocal
from app.main import app
from app.services import portal_autofill as pa

PORTAL_URL = "https://cybercrime.gov.in/Webform/Crime_AuthoLogin.aspx"
PHONE, NAME, EMAIL = "9876543210", "Asha Verma", "asha.verma@example.com"


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    unconfigured = SimpleNamespace(status=SimpleNamespace(configured=False))
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: unconfigured)


def _client() -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/signup", json={
        "full_name": NAME, "email": f"autofill-{uuid.uuid4().hex[:8]}@example.com", "password": "StrongPass123!",
    })
    assert response.status_code == 201
    return client


def _case(client: TestClient, category="CYBER_FRAUD", **facts) -> str:
    response = client.post("/api/v1/chat/message", json={"message": "Someone took money from my bank account."})
    assert response.status_code == 200
    case_id = response.json()["case_profile"]["case_id"]
    with SessionLocal() as db:
        record = db.query(ChatCaseSessionModel).filter_by(case_id=case_id).one()
        profile = dict(record.profile_data or {})
        profile.update({"category": category, "user_name": NAME, "user_phone": PHONE, "user_email": EMAIL,
                        "bank_name": "State Bank of India", "transaction_id": "UTR123456789",
                        "disputed_amount": 25000.0, "incident_date": "2026-03-02",
                        "incident_narrative": "A caller posing as bank staff took Rs 25,000 through a UPI request.",
                        "user_state": "Rajasthan"})
        key_facts = dict(profile.get("key_facts") or {})
        key_facts.update(facts.pop("key_facts", {}))
        profile["key_facts"] = key_facts
        profile.update(facts)
        record.profile_data = profile
        flag_modified(record, "profile_data")
        db.commit()
    return case_id


def _start(client, case_id, **extra):
    return client.post("/api/v1/autofill/session", json={"case_id": case_id, **extra})


def _bearer(code):
    return {"Authorization": f"Bearer {code}"}


def F(field_id, label, tag="input", type="text", **kw):
    return {"field_id": field_id, "tag": tag, "type": tag if tag in {"select", "textarea"} else type, "label": label, **kw}


BANKS = ["Select", "Axis Bank", "HDFC Bank", "State Bank of India", "PhonePe"]
CYBER_FORM = [
    F("0:1", "Category of complaint", tag="select", options=["Select", "Online Financial Fraud"]),
    F("0:2", "Approximate date & time of Incident/receiving/viewing of content", type="datetime-local"),
    F("0:3", "Yes", type="radio", name="lostMoney"),
    F("0:4", "Bank / (Wallet / PG / PA) / Merchant", tag="select", options=BANKS, selected_text="Select"),
    F("0:5", "Account No / Wallet ID / UPI ID"),
    F("0:6", "Transaction ID / UTR Number"),
    F("0:7", "Amount", type="number"),
    F("0:8", "Transaction Date", type="date"),
    F("0:9", "First Six Digit", maxlength=6),
    F("0:10", "Provide any additional information", tag="textarea", maxlength=1500),
    F("0:11", "", neighbour="Name", name="ctl00$cName"),
    F("0:12", "", neighbour="Mobile No", type="tel", maxlength=10),
    F("0:13", "", neighbour="Email Id", type="email"),
    F("0:14", "", neighbour="Pin Code", maxlength=6),
    F("0:15", "Enter OTP", name="otp"),
    F("0:16", "Please Upload any National ID", type="file"),
]


# ── Starting a session ────────────────────────────────────────────────────────

def test_start_needs_a_signed_in_user():
    assert TestClient(app).post("/api/v1/autofill/session", json={"case_id": "x"}).status_code == 401


def test_start_refuses_someone_elses_case():
    owner, other = _client(), _client()
    case_id = _case(owner)
    assert _start(other, case_id).status_code == 404


def test_start_refuses_a_case_the_portal_does_not_serve():
    client = _client()
    # Employment cases now go to Samadhan; tenancy still has no portal.
    response = _start(client, _case(client, category="HOUSING_TENANT"))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "PORTAL_NOT_SUPPORTED"


def test_a_consumer_case_gets_the_ejagriti_portal_without_changing_cybercrime():
    client = _client()
    consumer = _start(client, _case(client, category="CONSUMER")).json()
    assert consumer["portal"]["key"] == "ejagriti"
    assert consumer["portal"]["url"] == "https://e-jagriti.gov.in/login"
    cyber = _start(client, _case(client)).json()
    assert cyber["portal"]["key"] == "cybercrime"


def test_a_cyber_fraud_case_gets_a_code_and_the_cyber_crime_portal():
    client = _client()
    body = _start(client, _case(client)).json()
    assert body["portal"]["key"] == "cybercrime" and body["portal"]["url"] == "https://cybercrime.gov.in/"
    assert len(body["code"]) >= 40
    with SessionLocal() as db:
        row = db.query(AutofillSessionModel).filter_by(id=body["session_id"]).one()
        assert row.code_hash != body["code"] and body["code"] not in row.code_hash   # only the hash is stored
        assert row.status == "active" and row.expires_at > datetime.utcnow()


def test_the_test_portal_is_only_available_when_switched_on(monkeypatch):
    client = _client()
    case_id = _case(client, category="CONSUMER")
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", False)
    assert _start(client, case_id, portal="test").status_code == 404
    assert client.get("/api/v1/autofill/test-form").status_code == 404
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", True)
    body = _start(client, case_id, portal="test").json()
    assert body["portal"]["key"] == "test" and body["portal"]["url"].endswith("/api/v1/autofill/test-form")
    page = client.get("/api/v1/autofill/test-form")
    assert page.status_code == 200 and "Transaction ID / UTR Number" in page.text


def test_ejagriti_test_page_is_dev_only_and_uses_its_own_profile(monkeypatch):
    client = _client()
    case_id = _case(client, category="CONSUMER")
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", False)
    assert _start(client, case_id, portal="test-ejagriti").status_code == 404
    assert client.get("/api/v1/autofill/test-ejagriti").status_code == 404
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", True)
    started = _start(client, case_id, portal="test-ejagriti").json()
    assert started["portal"]["key"] == "test-ejagriti"
    assert started["portal"]["url"].endswith("/api/v1/autofill/test-ejagriti")
    page = client.get("/api/v1/autofill/test-ejagriti")
    assert page.status_code == 200
    assert "Opposite Party Details" in page.text and "File New Case" in page.text
    monkeypatch.setattr(settings, "AUTOFILL_TEST_FORM", False)
    assert client.get("/api/v1/autofill/session", headers=_bearer(started["code"])).status_code == 401


def test_starting_again_retires_the_previous_code():
    client = _client()
    case_id = _case(client)
    first = _start(client, case_id).json()["code"]
    second = _start(client, case_id).json()["code"]
    assert client.get("/api/v1/autofill/session", headers=_bearer(first)).status_code == 401
    assert client.get("/api/v1/autofill/session", headers=_bearer(second)).status_code == 200


# ── Using the code ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("header", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer not-a-real-code"},
                                    {"Authorization": "Basic abc"}])
def test_calls_without_a_valid_code_are_refused(header):
    assert TestClient(app).get("/api/v1/autofill/session", headers=header).status_code == 401


def test_an_expired_code_is_refused():
    client = _client()
    body = _start(client, _case(client)).json()
    with SessionLocal() as db:
        row = db.query(AutofillSessionModel).filter_by(id=body["session_id"]).one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert TestClient(app).get("/api/v1/autofill/session", headers=_bearer(body["code"])).status_code == 401


def test_the_code_works_without_the_nyayabot_login_cookie_but_only_for_its_own_case():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    extension = TestClient(app)            # no cookies, like the extension
    body = extension.get("/api/v1/autofill/session", headers=_bearer(code)).json()
    assert body["portal"]["key"] == "cybercrime"
    statuses = {v["key"]: v["status"] for v in body["values"]}
    assert statuses["transaction_id"] == "ready" and statuses["complainant_pin"] == "missing"


def test_session_lists_which_values_exist_but_never_the_values_themselves():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    text = TestClient(app).get("/api/v1/autofill/session", headers=_bearer(code)).text
    for private in (PHONE, EMAIL, "UTR123456789", "25000", "UPI request"):
        assert private not in text


def test_cancel_ends_the_code():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    extension = TestClient(app)
    assert extension.delete("/api/v1/autofill/session", headers=_bearer(code)).status_code == 200
    assert extension.get("/api/v1/autofill/session", headers=_bearer(code)).status_code == 401


# ── Plans ─────────────────────────────────────────────────────────────────────

def _plan(code, fields=CYBER_FORM, url=PORTAL_URL):
    return TestClient(app).post("/api/v1/autofill/plan", headers=_bearer(code), json={"page_url": url, "fields": fields})


@pytest.mark.parametrize("url", [
    "https://evil.example.com/cybercrime.gov.in", "https://cybercrime.gov.in.evil.com/", "file:///c:/form.html",
    "javascript:alert(1)", "", "https://localhost:8000/api/v1/autofill/test-form",
])
def test_a_plan_is_refused_for_any_page_that_is_not_the_portal(url):
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    response = _plan(code, url=url)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "WRONG_SITE"


def test_subdomains_of_the_portal_are_accepted():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    assert _plan(code, url="https://www.cybercrime.gov.in/Webform/x.aspx").status_code == 200


def test_the_cyber_crime_form_plan_fills_known_fields_and_leaves_the_rest():
    client = _client()
    plan = _plan(_start(client, _case(client)).json()["code"]).json()
    actions = {a["field_id"]: (a["key"], a["value"], a["kind"], a["source"]) for a in plan["actions"]}
    assert actions["0:4"] == ("bank_name", "State Bank of India", "select", "portal")
    assert actions["0:6"] == ("transaction_id", "UTR123456789", "text", "portal")
    assert actions["0:7"] == ("disputed_amount", "25000", "text", "portal")
    assert actions["0:8"] == ("incident_date", "2026-03-02", "date", "portal")
    assert actions["0:10"][0] == "incident_narrative" and actions["0:10"][3] == "portal"
    assert actions["0:11"][:2] == ("complainant_name", NAME)
    assert actions["0:12"][:2] == ("complainant_phone", PHONE)
    assert actions["0:13"][:2] == ("complainant_email", EMAIL)
    assert all(not a["needs_approval"] for a in plan["actions"])
    assert {n["field_id"] for n in plan["sensitive"]} == {"0:9", "0:15", "0:16"}   # card digits, OTP, national ID
    manual = {n["field_id"] for n in plan["manual"]}
    assert {"0:1", "0:2", "0:5"} <= manual and "0:3" not in manual
    assert [n["key"] for n in plan["missing"]] == ["complainant_pin"]
    filled_ids = set(actions)
    assert not filled_ids & {"0:1", "0:2", "0:3", "0:5", "0:9", "0:15", "0:16"}


def test_an_unconfirmed_value_in_the_chat_is_not_filled():
    client = _client()
    case_id = _case(client, key_facts={"pending_conflict": {"field": "disputed_amount", "candidate": 52000}})
    plan = _plan(_start(client, case_id).json()["code"]).json()
    assert "disputed_amount" not in {a["key"] for a in plan["actions"]}
    assert [n["key"] for n in plan["conflict"]] == ["disputed_amount"]


def test_a_prefilled_field_is_left_alone():
    client = _client()
    fields = [dict(f) for f in CYBER_FORM]
    fields[10]["filled"] = True                       # the portal already shows the user's name
    plan = _plan(_start(client, _case(client)).json()["code"], fields=fields).json()
    assert "complainant_name" not in {a["key"] for a in plan["actions"]}
    assert "0:11" in {n["field_id"] for n in plan["already_filled"]}


def test_a_page_that_is_not_the_complaint_form_gets_no_actions():
    client = _client()
    plan = _plan(_start(client, _case(client)).json()["code"], fields=[F("0:1", "Search"), F("0:2", "Email")]).json()
    assert plan["actions"] == [] and plan["reason"] == "not_a_complaint_form"


def test_scanned_fields_are_validated_strictly():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    for bad in ([{**F("0:1", "Amount"), "value": "secret"}], [F("not-an-id", "Amount")], [F("0:1", "Amount", tag="div")]):
        assert _plan(code, fields=bad).status_code == 422


# ── AI fallback (fake provider) ───────────────────────────────────────────────

def _fake_provider(result=None, error=None):
    calls = []

    async def generate(prompt, system_prompt, schema):
        calls.append((prompt, system_prompt))
        if error:
            raise error
        return schema.model_validate(result)

    provider = SimpleNamespace(status=SimpleNamespace(configured=True), _generate_structured=generate)
    return provider, calls


UNUSUAL = [F("0:20", "Naam (Shikayatkarta)"), F("0:21", "Sampark sankhya", type="text"),
           F("0:22", "Kuch bhi"), F("0:23", "Bank / (Wallet / PG / PA) / Merchant", tag="select", options=BANKS)]


def test_ai_is_asked_only_about_unknown_fields_and_never_sees_case_values(monkeypatch):
    provider, calls = _fake_provider({"mappings": [
        {"field_id": "0:20", "key": "complainant_name"}, {"field_id": "0:21", "key": "complainant_phone"},
        {"field_id": "0:22", "key": None},
    ]})
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    plan = _plan(_start(client, _case(client)).json()["code"], fields=UNUSUAL).json()
    prompt = calls[0][0]
    for private in (NAME, PHONE, EMAIL, "UTR123456789", "25000", "UPI request"):
        assert private not in prompt
    assert "0:23" not in prompt                      # the portal mapping already knew this one
    by_key = {a["key"]: a for a in plan["actions"]}
    assert by_key["complainant_name"]["source"] == "ai" and by_key["complainant_name"]["needs_approval"]
    assert by_key["complainant_phone"]["value"] == PHONE
    assert by_key["bank_name"]["source"] == "portal" and not by_key["bank_name"]["needs_approval"]
    assert plan["ai_used"] is True
    assert "0:22" in {n["field_id"] for n in plan["unmatched"]}


def test_ai_answers_outside_the_allowed_keys_or_fields_are_ignored(monkeypatch):
    provider, _ = _fake_provider({"mappings": [
        {"field_id": "0:20", "key": "password"}, {"field_id": "9:99", "key": "complainant_name"},
        {"field_id": "0:21", "key": "opposite_party_name"},
    ]})
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    plan = _plan(_start(client, _case(client)).json()["code"], fields=UNUSUAL).json()
    assert {a["source"] for a in plan["actions"]} == {"portal"}


def test_a_failing_ai_call_still_returns_the_rule_based_plan(monkeypatch):
    provider, _ = _fake_provider(error=RuntimeError("rate limited"))
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    plan = _plan(_start(client, _case(client)).json()["code"], fields=CYBER_FORM + UNUSUAL[:2]).json()
    assert plan["ai_used"] is False and len(plan["actions"]) >= 5


def test_ai_is_never_asked_about_a_sensitive_field():
    provider, calls = _fake_provider({"mappings": []})
    fields = [pa.ScannedField(**F("0:1", "Enter OTP")), pa.ScannedField(**F("0:2", "Kuch bhi"))]
    asyncio.run(pa._ai_candidates([(0, fields[1])], pa.CYBERCRIME, provider))
    plan = asyncio.run(pa.build_plan(pa.PlanRequest(page_url=PORTAL_URL, fields=fields), pa.CYBERCRIME, {}, provider=provider))
    assert all("Enter OTP" not in prompt for prompt, _ in calls)
    assert "0:1" in {n.field_id for n in plan.sensitive}


# ── Reports ───────────────────────────────────────────────────────────────────

def test_reports_count_fills_and_record_review_but_never_a_submission():
    client = _client()
    body = _start(client, _case(client)).json()
    extension = TestClient(app)
    r1 = extension.post("/api/v1/autofill/report", headers=_bearer(body["code"]), json={"event": "page_filled", "filled": 6, "failed": 1})
    r2 = extension.post("/api/v1/autofill/report", headers=_bearer(body["code"]), json={"event": "review_confirmed"})
    assert r1.json()["filled_total"] == 6 and r2.json()["status"] == "reviewed"
    assert extension.post("/api/v1/autofill/report", headers=_bearer(body["code"]), json={"event": "submitted"}).status_code == 422
    with SessionLocal() as db:
        row = db.query(AutofillSessionModel).filter_by(id=body["session_id"]).one()
        assert (row.filled_count, row.failed_count, row.status) == (6, 1, "reviewed") and row.reviewed_at


# ── Pure helpers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [("2026-03-02", "2026-03-02"), ("02/03/2026", "2026-03-02"),
                                           ("2-3-2026", "2026-03-02"), ("last Tuesday", None), ("", None)])
def test_dates_are_converted_for_date_inputs_or_left_alone(raw, expected):
    assert pa.iso_date(raw) == expected


def test_case_values_fall_back_to_the_signed_in_profile():
    user = SimpleNamespace(full_name="Profile Name", phone="9000000000", email="p@example.com", city="Pune",
                           state="Maharashtra", pin_code="411001", full_address="1 Main St")
    values = pa.case_values({"user_name": "Case Name", "disputed_amount": 0}, user)
    assert values["complainant_name"] == "Case Name" and values["complainant_pin"] == "411001"
    assert values["disputed_amount"] == ""


def test_every_cyber_crime_mapping_points_at_a_key_the_portal_uses():
    assert all(key in pa.CYBERCRIME.value_keys for _, key in pa.CYBERCRIME.field_map)
    assert all(key in pa.VALUE_LABELS for key in pa.CYBERCRIME.value_keys)


def test_district_dropdown_is_matched_from_the_city_once_it_is_enabled():
    user = SimpleNamespace(full_name="A", phone="", email="", city="Jaipur", state="Rajasthan", pin_code="", full_address="")
    fields = [pa.ScannedField(**f) for f in CYBER_FORM[3:8]] + [
        pa.ScannedField(**F("0:30", "", tag="select", neighbour="District", options=["--Select District--", "Jaipur", "Jodhpur"]))]
    plan = asyncio.run(pa.build_plan(pa.PlanRequest(page_url=PORTAL_URL, fields=fields), pa.CYBERCRIME,
                                     pa.case_values({"bank_name": "HDFC Bank"}, user)))
    district = next(a for a in plan.actions if a.field_id == "0:30")
    assert (district.key, district.value, district.kind) == ("complainant_city", "Jaipur", "select")


def test_portal_specific_instructions_beat_the_generic_one_and_radios_are_not_listed():
    fields = [pa.ScannedField(**f) for f in CYBER_FORM]
    plan = asyncio.run(pa.build_plan(pa.PlanRequest(page_url=PORTAL_URL, fields=fields), pa.CYBERCRIME, {}))
    reasons = {n.field_id: n.reason for n in plan.manual}
    assert reasons["0:2"] == "Pick the date and time in the portal's calendar."
    assert "0:3" not in reasons


# ── Navigation choice ─────────────────────────────────────────────────────────

def _nav(code, links, url="https://cybercrime.gov.in/", visited=()):
    return TestClient(app).post("/api/v1/autofill/navigate", headers=_bearer(code),
                                json={"page_url": url, "links": links, "visited": list(visited)})


HOME_LINKS = [
    {"index": 1, "tag": "a", "text": "Home"},
    {"index": 2, "tag": "a", "text": "Report Cyber Crime"},
    {"index": 3, "tag": "button", "text": "Register a Complaint",
     "context": "Women & Children Related Crime Cybercrime affects women the most"},
    {"index": 4, "tag": "button", "text": "Register a Complaint",
     "context": "Cyber Crime Related Complaint Cybercrime refers to any unlawful activity"},
    {"index": 5, "tag": "a", "text": "Track your Complaint"},
    {"index": 6, "tag": "button", "type": "submit", "text": "Submit", "submits_form": True},
]


def test_navigation_picks_the_cyber_card_and_never_the_women_children_card():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    assert _nav(code, HOME_LINKS).json() == {"index": 4, "text": "Register a Complaint"}


def test_ejagriti_navigation_clicks_only_file_new_case_from_dashboard():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    links = [
        {"index": 1, "tag": "a", "text": "File Complaint"},
        {"index": 2, "tag": "a", "text": "File New Case"},
        {"index": 3, "tag": "a", "text": "Payment History"},
    ]
    assert _nav(code, links, url="https://e-jagriti.gov.in/dashboard").json() == {"index": 2, "text": "File New Case"}
    without_entry = [link for link in links if link["index"] != 2]
    assert _nav(code, without_entry, url="https://e-jagriti.gov.in/dashboard").json()["index"] is None
    assert _nav(code, links, url="https://e-jagriti.gov.in/case/new").json()["index"] is None


def test_ejagriti_opens_collapsed_dashboard_menu_once_before_filing():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    url = "https://e-jagriti.gov.in/login"
    menu = [{"index": 1, "tag": "button", "text": "Open menu"},
            {"index": 2, "tag": "a", "text": "Success Stories"}]
    assert _nav(code, menu, url=url).json() == {"index": 1, "text": "Open menu"}
    assert _nav(code, menu, url=url, visited=["Open menu"]).json()["index"] is None
    expanded = menu + [{"index": 3, "tag": "a", "text": "File New Case"}]
    assert _nav(code, expanded, url=url, visited=["Open menu"]).json() == {
        "index": 3, "text": "File New Case",
    }


def test_ejagriti_navigation_selects_consumer_complaint_only_after_file_new_case():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    case_types = [{"index": 1, "tag": "div", "text": "Case Type"},
                  {"index": 2, "tag": "div", "text": "Success Stories"}]
    url = "https://e-jagriti.gov.in/case/new"
    assert _nav(code, case_types, url=url).json()["index"] is None
    assert _nav(code, case_types, url=url, visited=["File New Case"]).json() == {
        "index": 1, "text": "Case Type",
    }
    options = case_types + [{"index": 3, "tag": "div", "text": "Consumer Complaint"},
                            {"index": 4, "tag": "div", "text": "Other Case Type"}]
    assert _nav(code, options, url=url, visited=["File New Case", "Case Type"]).json() == {
        "index": 3, "text": "Consumer Complaint",
    }
    assert _nav(code, options, url=url, visited=["File New Case", "Case Type", "Consumer Complaint"]).json()["index"] is None
    assert _nav(code, [{"index": 9, "tag": "button", "text": "Final Submit"}], url=url,
                visited=["File New Case"]).json()["index"] is None


def test_navigation_does_not_repeat_a_visited_link_and_never_picks_submit_or_track():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    body = _nav(code, HOME_LINKS, visited=["Register a Complaint", "Report Cyber Crime"]).json()
    assert body["index"] is None


def test_navigation_is_refused_off_the_portal():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    assert _nav(code, HOME_LINKS, url="https://example.com/").status_code == 403


# ── Origin check: the extension calls from chrome-extension:// ────────────────

EXT_ORIGIN = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}


def test_extension_origin_can_use_the_code_endpoints():
    """Regression: the write-origin check returned 403 "Untrusted request origin"
    for the extension's first POST, because its origin is chrome-extension://."""
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    extension = TestClient(app)
    headers = {**_bearer(code), **EXT_ORIGIN}
    assert extension.post("/api/v1/autofill/plan", headers=headers, json={"page_url": PORTAL_URL, "fields": CYBER_FORM}).status_code == 200
    assert extension.post("/api/v1/autofill/navigate", headers=headers, json={"page_url": PORTAL_URL, "links": HOME_LINKS}).status_code == 200
    assert extension.post("/api/v1/autofill/report", headers=headers, json={"event": "page_filled", "filled": 1}).status_code == 200
    assert extension.delete("/api/v1/autofill/session", headers=headers).status_code == 200


def test_the_exemption_needs_the_code_and_does_not_cover_cookie_endpoints():
    client = _client()
    case_id = _case(client)
    # Starting a session uses the login cookie, so a foreign origin is still refused.
    assert client.post("/api/v1/autofill/session", json={"case_id": case_id}, headers=EXT_ORIGIN).status_code == 403
    # Without a Bearer code, the extension endpoints get the normal origin check.
    assert client.post("/api/v1/autofill/plan", headers=EXT_ORIGIN, json={"page_url": PORTAL_URL, "fields": []}).status_code == 403
    # Other cookie endpoints are unaffected, even with a Bearer header attached.
    hostile = {**EXT_ORIGIN, "Authorization": "Bearer anything"}
    assert client.post("/api/v1/chat/message", json={"message": "hi"}, headers=hostile).status_code == 403


def test_a_custom_dropdown_is_listed_for_the_user_and_never_filled():
    fields = [pa.ScannedField(**f) for f in CYBER_FORM] + [pa.ScannedField(
        field_id="0:100002", tag="custom", type="custom", aria="Bank / (Wallet / PG / PA) / Merchant")]
    plan = asyncio.run(pa.build_plan(pa.PlanRequest(page_url=PORTAL_URL, fields=fields), pa.CYBERCRIME,
                                     {"bank_name": "State Bank of India"}))
    assert "0:100002" not in {a.field_id for a in plan.actions}
    assert any(n.field_id == "0:100002" and "list yourself" in n.reason for n in plan.manual)


# ── Date of birth (from the NyayaBot profile) and field-aware dates ───────────

_USER_WITH_DOB = SimpleNamespace(full_name="Asha Verma", phone="9876543210", email="a@example.com", city="Jaipur",
                                 state="Rajasthan", pin_code="302001", full_address="1 MG Road", date_of_birth="1995-03-02")


def _dob_plan(*fields):
    request = pa.PlanRequest(page_url=PORTAL_URL, fields=[pa.ScannedField(**f) for f in fields] + [
        pa.ScannedField(**F("0:90", "Name")), pa.ScannedField(**F("0:91", "Mobile No")),
        pa.ScannedField(**F("0:92", "Email Id"))])
    return asyncio.run(pa.build_plan(request, pa.CYBERCRIME, pa.case_values({}, _USER_WITH_DOB)))


def test_dob_comes_from_the_profile_and_fills_a_calendar_field():
    plan = _dob_plan(F("0:1", "Date of Birth", type="date"))
    dob = next(a for a in plan.actions if a.key == "complainant_dob")
    assert (dob.value, dob.kind) == ("1995-03-02", "date")


@pytest.mark.parametrize("placeholder, expected", [("DD/MM/YYYY", "02/03/1995"), ("dd-mm-yyyy", "02-03-1995"),
                                                    ("YYYY-MM-DD", "1995-03-02")])
def test_dob_in_a_text_field_follows_the_format_the_field_shows(placeholder, expected):
    plan = _dob_plan(F("0:1", "DOB", placeholder=placeholder))
    assert next(a for a in plan.actions if a.key == "complainant_dob").value == expected


def test_dob_without_a_known_format_is_shown_for_the_user_not_guessed():
    plan = _dob_plan(F("0:1", "Date of Birth"))
    assert "complainant_dob" not in {a.key for a in plan.actions}
    note = next(n for n in plan.manual if n.key == "complainant_dob")
    assert "02/03/1995" in note.reason


def test_a_locked_date_picker_is_listed_for_the_user():
    plan = _dob_plan(F("0:1", "Date of Birth", readonly=True))
    assert any(n.key == "complainant_dob" and "picker" in n.reason for n in plan.manual)


def test_a_dob_calendar_field_is_never_taken_as_the_incident_date():
    plan = _dob_plan(F("0:1", "Date of Birth", type="date"), F("0:2", "Transaction Date", type="date"))
    keys = {a.field_id: a.key for a in plan.actions}
    keys.update({n.field_id: n.key for n in plan.missing})   # no incident date in this case
    assert keys["0:1"] == "complainant_dob" and keys["0:2"] == "incident_date"


def test_father_or_suspect_dob_is_not_the_users():
    plan = _dob_plan(F("0:1", "Father's Date of Birth", type="date"), F("0:2", "Suspect DOB", type="date"))
    assert "complainant_dob" not in {a.key for a in plan.actions}


# ── Multiple-choice "what happened" questions ─────────────────────────────────

LEVEL_1 = {"group": 0, "question": "What type of complaint is this?", "options": [
    {"index": 10, "text": "I lost money, or someone is trying to take money from me"},
    {"index": 11, "text": "Someone is harassing or blackmailing me online, or sharing obscene / explicit material"},
    {"index": 12, "text": "My phone, computer, email, social media or device was hacked, locked or taken over"},
]}
LOAN = {"group": 1, "question": "A Loan / Lending Agent", "options": [
    {"index": 20, "text": "Pay a processing / legal / registration fee before giving the loan"},
    {"index": 21, "text": "Pay extra charges or fines after the loan was given"},
    {"index": 22, "text": "Something else"},
]}


def _choose(code, groups, url=PORTAL_URL):
    return TestClient(app).post("/api/v1/autofill/choose", headers=_bearer(code), json={"page_url": url, "groups": groups})


def test_ejagriti_search_filters_and_success_stories_are_not_question_proposals(monkeypatch):
    def unexpected_provider():
        raise AssertionError("e-Jagriti public page controls must never reach an LLM")

    monkeypatch.setattr(autofill_routes, "get_llm_provider", unexpected_provider)
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    group = {"group": 0, "question": "Citizens Share Their Success Stories", "options": [
        {"index": 0, "text": "DC/44/CC/285/2026"},
        {"index": 1, "text": "DC/72/CC/88/2026"},
    ]}
    assert _choose(code, [group], url="https://e-jagriti.gov.in/").json() == {"proposals": [], "ai_used": False}


def test_the_first_question_is_answered_by_a_fixed_rule_for_a_money_fraud_case():
    client = _client()
    code = _start(client, _case(client, issue_type="UPI_BANK_TRANSFER_FRAUD")).json()["code"]
    body = _choose(code, [LEVEL_1]).json()
    assert body["proposals"] == [{"group": 0, "option_index": 10, "source": "rules",
                                  "option_text": LEVEL_1["options"][0]["text"],
                                  "reason": "Your case is about money lost to a fraud."}]
    assert body["ai_used"] is False


def test_an_account_takeover_case_gets_the_hacked_option():
    client = _client()
    code = _start(client, _case(client, issue_type="ACCOUNT_TAKEOVER")).json()["code"]
    assert _choose(code, [LEVEL_1]).json()["proposals"][0]["option_index"] == 12


def test_a_question_already_answered_on_the_page_is_never_overridden():
    client = _client()
    code = _start(client, _case(client, issue_type="UPI_BANK_TRANSFER_FRAUD")).json()["code"]
    answered = {**LEVEL_1, "options": [{**LEVEL_1["options"][0]}, {**LEVEL_1["options"][1], "selected": True}, LEVEL_1["options"][2]]}
    assert _choose(code, [answered]).json()["proposals"] == []


def test_without_ai_a_deeper_question_gets_no_suggestion_and_the_user_chooses():
    client = _client()
    code = _start(client, _case(client, issue_type="ONLINE_SCAM")).json()["code"]
    assert _choose(code, [LOAN]).json() == {"proposals": [], "ai_used": False}


def test_ai_suggestion_uses_what_happened_but_never_who_the_person_is(monkeypatch):
    provider, calls = _fake_provider({"choices": [{"group": 1, "option_index": 20, "reason": "They asked for a fee before the loan."}]})
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    case_id = _case(client, issue_type="ONLINE_SCAM",
                    incident_narrative="A loan app agent took Rs 4,999 as processing fee via asha.verma@okaxis, call me on 9876543210.",
                    key_facts={"scam_method": "fake instant loan app"})
    body = _choose(_start(client, case_id).json()["code"], [LOAN]).json()
    assert body["proposals"][0]["option_index"] == 20 and body["proposals"][0]["source"] == "ai"
    prompt = calls[0][0]
    assert "fake instant loan app" in prompt and "processing fee" in prompt
    for private in (NAME, PHONE, EMAIL, "asha.verma@okaxis", "UTR123456789"):
        assert private not in prompt


def test_ai_answers_are_validated(monkeypatch):
    provider, _ = _fake_provider({"choices": [
        {"group": 1, "option_index": 99, "reason": "not an option"},
        {"group": 7, "option_index": 20, "reason": "not a question on the page"},
    ]})
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    code = _start(client, _case(client, issue_type="ONLINE_SCAM")).json()["code"]
    assert _choose(code, [LOAN]).json()["proposals"] == []


def test_ai_never_picks_an_action_button_but_may_pick_a_descriptive_pay_option(monkeypatch):
    group = {"group": 2, "question": "Next step", "options": [{"index": 30, "text": "I Agree"}, {"index": 31, "text": "Pay now"}]}
    provider, _ = _fake_provider({"choices": [{"group": 2, "option_index": 30, "reason": "x"}]})
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    code = _start(client, _case(client, issue_type="ONLINE_SCAM")).json()["code"]
    assert _choose(code, [group]).json()["proposals"] == []
    assert not pa.is_action_label("Pay a processing / legal / registration fee before giving the loan")
    assert pa.is_action_label("Pay now") and pa.is_action_label("I Agree") and pa.is_action_label("Submit")


def test_a_failing_ai_call_returns_the_rule_suggestions_only(monkeypatch):
    provider, _ = _fake_provider(error=RuntimeError("rate limited"))
    monkeypatch.setattr(autofill_routes, "get_llm_provider", lambda: provider)
    client = _client()
    code = _start(client, _case(client, issue_type="UPI_BANK_TRANSFER_FRAUD")).json()["code"]
    body = _choose(code, [LEVEL_1, LOAN]).json()
    assert [p["source"] for p in body["proposals"]] == ["rules"]


def test_choose_is_refused_off_the_portal_and_works_from_the_extension_origin():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    assert _choose(code, [LEVEL_1], url="https://example.com/").status_code == 403
    response = TestClient(app).post("/api/v1/autofill/choose", headers={**_bearer(code), **EXT_ORIGIN},
                                    json={"page_url": PORTAL_URL, "groups": [LEVEL_1]})
    assert response.status_code == 200


# ── Navigation only on the public landing page ────────────────────────────────

@pytest.mark.parametrize("url, entry", [
    ("https://cybercrime.gov.in/", True), ("https://cybercrime.gov.in/home", True),
    ("https://cybercrime.gov.in/Webform/Index.aspx", True),
    ("https://cybercrime.gov.in/dashboard/crimereport", False), ("https://cybercrime.gov.in/login", False),
])
def test_entry_pages_are_only_the_public_landing_pages(url, entry):
    assert pa.CYBERCRIME.is_entry_page(url) is entry


def test_inside_the_complaint_area_navigation_never_clicks_a_menu_link():
    """Regression guard: "Report Cyber Crime" in the logged-in menu would restart the questionnaire."""
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    body = _nav(code, HOME_LINKS, url="https://cybercrime.gov.in/dashboard/crimereport").json()
    assert body == {"index": None, "text": "", "reason": "not_entry_page"}


def test_local_test_form_entry_page_is_its_home_view():
    portal = pa.local_test_portal("http://localhost:8000/")
    assert portal.is_entry_page("http://localhost:8000/api/v1/autofill/test-form#home")
    assert portal.is_entry_page("http://localhost:8000/api/v1/autofill/test-form")
    assert not portal.is_entry_page("http://localhost:8000/api/v1/autofill/test-form#wizard")
