"""e-Jagriti: the account's Complete Profile pages, unknown headings, and the filing route
kept as profile data (no portal logic in the extension). Pure local plans; no browser,
network or LLM calls."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import portal_autofill as pa
from tests.test_autofill_extension_api import _bearer, _case, _client, _nav, _start

PROFILE_URL = "https://e-jagriti.gov.in/profile"


def field(number, label, section, **extra):
    return pa.ScannedField(field_id=f"0:{number}", tag="input", label=label, section=section, **extra)


def custom(number, name, section):
    return pa.ScannedField(field_id=f"0:{number}", tag="custom", type="custom", aria=name, section=section)


def values(**changes):
    profile = {
        "user_name": "Asha Verma", "user_phone": "9876543210", "user_email": "asha@example.com",
        "user_address": "12 Rose Lane", "user_pin_code": "302001", "date_of_birth": "1990-04-12",
        "opposite_party_name": "Example Retail", "opposite_party_address": "4 Market Road",
    }
    profile.update(changes)
    return pa.case_values(profile, SimpleNamespace())


def plan(fields, vals=None, portal=pa.EJAGRITI, url=PROFILE_URL):
    request = pa.PlanRequest(page_url=url, fields=fields)
    return asyncio.run(pa.build_plan(request, portal, vals or values()))


# ── Complete Profile ──────────────────────────────────────────────────────────

ADDRESS_PAGE = [
    custom(1, "Address Type", "Address Information"),
    field(2, "House No./Door No./Building/Flat", "Address Information", maxlength=51),
    field(3, "Block/Street/Mohalla/Sector", "Address Information"),
    field(4, "Landmark/Locality", "Address Information"),
    custom(5, "Country", "Address Information"),
    field(6, "Pin Code*", "Address Information", maxlength=6),
    custom(7, "State", "Address Information"),
    custom(8, "District", "Address Information"),
    custom(9, "Post Office", "Address Information"),
    field(10, "Police Station", "Address Information"),
]


def test_complete_profile_address_page_is_filled_from_the_users_own_details():
    """Regression: this page was refused as "not the Consumer Complaint filing form"."""
    result = plan(ADDRESS_PAGE)
    assert result.reason != "not_a_complaint_form"
    by_key = {action.key: action for action in result.actions}
    assert set(by_key) == {"complainant_address", "complainant_pin"}
    assert by_key["complainant_address"].value == "12 Rose Lane"
    assert by_key["complainant_pin"].value == "302001"
    left = {note.field_id for note in result.manual}
    assert {"0:3", "0:4", "0:10"} <= left, "street, locality and police station stay with the user"
    assert result.unknown_sections == []


def test_complete_profile_personal_page_fills_name_contacts_and_date_of_birth():
    result = plan([
        field(1, "Full Name", "Personal Information"),
        field(2, "Mobile Number", "Personal Information", type="tel"),
        field(3, "Email", "Personal Information", type="email"),
        field(4, "Date of Birth", "Personal Information", type="date"),
        field(5, "Gender", "Personal Information"),
    ])
    by_key = {action.key: action.value for action in result.actions}
    assert by_key == {"complainant_name": "Asha Verma", "complainant_phone": "9876543210",
                      "complainant_email": "asha@example.com", "complainant_dob": "1990-04-12"}
    assert "0:5" in {note.field_id for note in result.manual}


def test_profile_headings_never_count_as_yours_on_a_page_with_the_other_party():
    """A page that mixes "Address Information" with the seller's section fills nothing under it."""
    result = plan([
        field(1, "House No./Door No./Building/Flat", "Address Information"),
        field(2, "House No./Door No./Building/Flat", "Opposite Party Details"),
    ], url="https://e-jagriti.gov.in/case/new")
    assert [(a.field_id, a.key) for a in result.actions] == [("0:2", "opposite_party_address")]
    assert "Address Information" in result.unknown_sections


def test_unknown_headings_are_reported_and_nothing_is_guessed():
    result = plan([
        field(1, "Full Name", "Bank Account Details"),
        field(2, "Mobile Number", "Additional Opposite Party Details", type="tel"),
    ])
    assert result.actions == []
    assert result.reason == "not_a_complaint_form"
    assert result.unknown_sections == ["Bank Account Details", "Additional Opposite Party Details"]
    assert "doesn't recognise the section “Bank Account Details”" in result.manual[0].reason


def test_long_address_suggests_only_whole_leading_parts_for_the_51_character_box():
    long_address = "Flat 4B, Shanti Apartments, 12 Rose Lane, Malviya Nagar, Jaipur, Rajasthan 302017"
    result = plan([field(1, "House No./Door No./Building/Flat", "Address Information")],
                  values(user_address=long_address))
    [action] = result.actions
    assert action.value == "Flat 4B, Shanti Apartments, 12 Rose Lane"
    assert len(action.value) <= 51
    assert action.needs_approval, "a shortened address is a please-check suggestion, never auto-ticked"
    assert "51 characters" in action.value_label


def test_cybercrime_plans_are_unchanged_by_sections():
    assert pa.CYBERCRIME.sections == () and pa.CYBERCRIME.route == ()
    result = plan([field(1, "Transaction ID / UTR Number", "")], portal=pa.CYBERCRIME,
                  url="https://cybercrime.gov.in/form", vals={"transaction_id": "UTR1"})
    assert [a.key for a in result.actions] == ["transaction_id"]
    assert result.unknown_sections == []


# ── The filing route as data ─────────────────────────────────────────────────

def link(index, text, kind="link", value=""):
    return {"index": index, "text": text, "kind": kind, "value": value}


ROUTE = pa.EJAGRITI


def test_route_follows_its_steps_in_order_from_the_entry_page_only():
    page = [link(1, "Payment History"), link(2, "File New Case")]
    assert pa.route_action(ROUTE, page, [], on_entry_page=True) == {"index": 2, "text": "File New Case"}
    assert pa.route_action(ROUTE, page, [], on_entry_page=False) is None
    case_type = [link(5, "Case Type", "dropdown", "Select"), link(6, "Success Stories")]
    # A "Case Type" control is never touched before File New Case (it could be a search filter).
    assert pa.route_action(ROUTE, case_type, [], on_entry_page=True) is None
    assert pa.route_action(ROUTE, case_type, ["File New Case"], on_entry_page=False) == {"index": 5, "text": "Case Type"}
    options = case_type + [link(7, "Consumer Complaint CC", "option"), link(8, "Other Case Type", "option")]
    assert pa.route_action(ROUTE, options, ["File New Case", "Case Type"], on_entry_page=False) == {
        "index": 7, "text": "Consumer Complaint CC", "step": "Consumer Complaint"}


def test_route_presses_next_only_when_the_dropdown_shows_the_chosen_value():
    chosen = [link(5, "Case Type", "dropdown", "Consumer Complaint")]
    visited = ["File New Case", "Case Type", "Consumer Complaint"]
    assert pa.route_action(ROUTE, chosen, visited, on_entry_page=False) == {
        "index": None, "text": "Next", "action": "next", "step": "Case Type Next"}
    not_chosen = [link(5, "Case Type", "dropdown", "Other Case Type")]
    assert pa.route_action(ROUTE, not_chosen, visited, on_entry_page=False) is None
    # Chosen already (e.g. by the user): opening the list and picking again are skipped.
    assert pa.route_action(ROUTE, chosen, ["File New Case"], on_entry_page=False)["action"] == "next"
    assert pa.route_action(ROUTE, chosen, visited + ["Case Type Next"], on_entry_page=False) is None


def test_route_opens_a_collapsed_menu_once_and_never_clicks_submit_like_controls():
    menu = [link(1, "Open menu"), link(2, "Success Stories")]
    assert pa.route_action(ROUTE, menu, [], on_entry_page=True) == {"index": 1, "text": "Open menu"}
    assert pa.route_action(ROUTE, menu, ["Open menu"], on_entry_page=True) is None
    submit = [{**link(9, "File New Case"), "submits_form": True}]
    assert pa.route_action(ROUTE, submit, [], on_entry_page=True) is None


def test_local_test_portal_uses_the_real_profiles_rules_and_route():
    local = pa.local_test_portal("http://localhost:8000/", kind="test-ejagriti")
    assert local.route == pa.EJAGRITI.route and local.sections == pa.EJAGRITI.sections
    assert local.choice_questions is False and local.not_found_hint == pa.EJAGRITI.not_found_hint
    assert local.url.endswith("/api/v1/autofill/test-ejagriti")


# ── Through the API ───────────────────────────────────────────────────────────

def test_navigate_api_returns_next_with_its_step_and_the_portals_own_hint():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    url = "https://e-jagriti.gov.in/case/new"
    chosen = [{"index": 5, "tag": "div", "text": "Case Type", "kind": "dropdown", "value": "Consumer Complaint"}]
    reply = _nav(code, chosen, url=url, visited=["File New Case", "Case Type", "Consumer Complaint"]).json()
    assert reply == {"index": None, "text": "Next", "action": "next", "step": "Case Type Next"}
    stuck = _nav(code, [{"index": 1, "tag": "a", "text": "Success Stories"}], url=url).json()
    assert stuck["index"] is None and stuck["offer_choices"] is False
    assert "Complete Profile" in stuck["message"]


def test_cybercrime_navigation_ignores_dropdowns_and_list_options():
    client = _client()
    code = _start(client, _case(client)).json()["code"]
    links = [{"index": 1, "tag": "div", "text": "Register a Complaint", "kind": "dropdown", "value": "Select"},
             {"index": 2, "tag": "div", "text": "Register a Complaint", "kind": "option"}]
    assert _nav(code, links).json() == {"index": None, "text": ""}


def test_choose_api_offers_nothing_on_a_portal_without_case_questions():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    body = {"page_url": "https://e-jagriti.gov.in/", "groups": [{"group": 0, "question": "Stories", "options": [
        {"index": 0, "text": "DC/44/CC/285/2026"}, {"index": 1, "text": "DC/72/CC/88/2026"}]}]}
    reply = TestClient(app).post("/api/v1/autofill/choose", headers=_bearer(code), json=body)
    assert reply.status_code == 200 and reply.json()["proposals"] == []
