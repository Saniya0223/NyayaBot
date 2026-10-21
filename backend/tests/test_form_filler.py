"""Rule-based Auto-Fill: matching, planning and the page driver.

Fake fields and fake pages only. No playwright, no browser_use, no network, no
LLM call, no Chromium.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.services import form_filler as ff
from app.services.form_filler import FieldInfo, plan_fills

VALUES = {
    "complainant_name": "Asha Verma",
    "complainant_email": "asha.verma@example.com",
    "complainant_phone": "+91 98765 43210",
    "complainant_address": "12 MG Road, Jaipur",
    "complainant_city": "Jaipur",
    "complainant_state": "Rajasthan",
    "complainant_pin": "302001",
    "opposite_party_name": "Acme Appliances Pvt Ltd",
    "opposite_party_address": "5 Industrial Area, Noida",
    "property_address": "Flat 4, Sector 9",
    "bank_name": "State Bank of India",
    "transaction_id": "UTR123456789",
    "reference_number": "RJJP010012342024",
    "disputed_amount": "32000.0",
    "incident_narrative": "The washing machine ordered on 2 March was never delivered.",
}

_next_index = iter(range(1, 10_000))


def F(label="", type="text", tag="input", **kw):
    if tag in {"textarea", "select"}:
        type = tag
    return FieldInfo(frame=kw.pop("frame", 0), index=next(_next_index), tag=tag, type=type, label=label, **kw)


def keys_for(*fields, values=VALUES):
    plan = plan_fills(list(fields), values)
    return {a.info.label or a.info.name or a.info.neighbour: a.key for a in plan.actions}, plan


def complaint_form():
    return [
        F("Name *"), F("Mobile No."), F("E-mail"), F("Address", tag="textarea"),
        F("Complaint Details", tag="textarea"),
    ]


# ── Matching ──────────────────────────────────────────────────────────────────

def test_a_normal_complaint_form_is_matched_field_by_field():
    mapping, plan = keys_for(*complaint_form())
    assert mapping == {
        "Name *": "complainant_name", "Mobile No.": "complainant_phone", "E-mail": "complainant_email",
        "Address": "complainant_address", "Complaint Details": "incident_narrative",
    }
    assert plan.reason == ""


@pytest.mark.parametrize("label, expected", [
    ("Full Name", "complainant_name"), ("NAME OF THE COMPLAINANT", "complainant_name"),
    ("Applicant Name:", "complainant_name"), ("Email ID", "complainant_email"),
    ("Contact Number", "complainant_phone"), ("Mobile", "complainant_phone"),
    ("Pincode", "complainant_pin"), ("Postal Code", "complainant_pin"),
    ("Opposite Party Name", "opposite_party_name"), ("Name of the Company", "opposite_party_name"),
    ("Opposite Party Address", "opposite_party_address"), ("Property Address", "property_address"),
    ("Bank Name", "bank_name"), ("Transaction ID", "transaction_id"), ("UTR Number", "transaction_id"),
    ("CNR Number", "reference_number"), ("Amount Lost (Rs.)", "disputed_amount"),
    ("Disputed Amount", "disputed_amount"), ("Description", "incident_narrative"),
    ("Grievance Description", "incident_narrative"), ("City", "complainant_city"),
])
def test_label_wording_variants_map_to_the_right_value(label, expected):
    key, score = ff.match_field(F(label))
    assert key == expected and score >= ff.MIN_SCORE


@pytest.mark.parametrize("label", [
    "Father's Name", "Mother Name", "User Name", "Username", "First Name", "Last Name",
    "Alternate Mobile", "Office Phone", "Landline", "Confirm Email", "Transaction Date",
    "Bank Account Number", "IFSC Code", "Search", "Subject", "Date of Incident", "Upload Document",
    "नाम", "मोबाइल नंबर",
])
def test_ambiguous_or_unsupported_labels_are_left_for_the_user(label):
    assert ff.match_field(F(label))[0] is None


def test_opposite_party_and_property_addresses_never_take_the_complainants_address():
    assert ff.match_field(F("Opposite Party Address"))[0] == "opposite_party_address"
    assert ff.match_field(F("Company Address"))[0] == "opposite_party_address"
    assert ff.match_field(F("Property Address"))[0] == "property_address"
    assert ff.match_field(F("Address"))[0] == "complainant_address"
    assert ff.match_field(F("Email Address"))[0] == "complainant_email"


def test_two_equally_good_readings_means_no_guess():
    assert ff.match_field(F("Mobile / Email"))[0] is None


def test_name_and_id_attributes_work_when_there_is_no_label():
    assert ff.match_field(F("", name="ctl00$ContentPlaceHolder1$txtMobileNo", ident="txtMobileNo"))[0] == "complainant_phone"
    assert ff.match_field(F("", name="applicantName", ident="applicantName"))[0] == "complainant_name"


def test_a_neighbouring_cell_text_is_used_when_there_is_no_label():
    assert ff.match_field(F("", neighbour="Mobile Number"))[0] == "complainant_phone"


def test_a_name_attribute_alone_is_too_weak_to_act_on():
    assert ff.match_field(F("", name="mobile"))[0] is None


# ── Never touched ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("info", [
    F("Password", type="password"), F("Enter OTP"), F("Mobile OTP"), F("Captcha"),
    F("Enter the code shown in the image", name="txtCaptcha"), F("Aadhaar Number"), F("Aadhar No."),
    F("PAN"), F("UPI PIN"), F("CVV"), F("Card Number"), F("", name="txtOTP", ident="txtOTP"),
])
def test_sensitive_fields_are_never_filled_or_matched(info):
    assert ff.is_sensitive(info)
    assert ff.match_field(info) == (None, 0)
    plan = plan_fills(complaint_form() + [info], VALUES)
    assert info in plan.sensitive and all(a.info is not info for a in plan.actions)


def test_pin_code_is_postal_not_a_secret_pin():
    assert not ff.is_sensitive(F("Pin Code"))
    assert not ff.is_sensitive(F("Company"))   # contains "pan" inside a word


@pytest.mark.parametrize("kind", ["checkbox", "radio", "file", "submit", "button", "image", "reset", "date", "hidden"])
def test_non_text_controls_are_ignored(kind):
    info = F("Name", type=kind)
    assert ff.match_field(info) == (None, 0)
    assert all(a.info is not info for a in plan_fills(complaint_form() + [info], VALUES).actions)


def test_disabled_and_read_only_fields_are_not_touched():
    locked = [F("Name", disabled=True), F("Mobile", readonly=True)]
    plan = plan_fills(locked + [F("E-mail"), F("Address"), F("Complaint Details", tag="textarea")], VALUES)
    assert {a.key for a in plan.actions} == {"complainant_email", "complainant_address", "incident_narrative"}


# ── Is this a complaint form at all? ──────────────────────────────────────────

def test_a_newsletter_box_is_not_mistaken_for_the_complaint_form():
    plan = plan_fills([F("Name"), F("Email"), F("Search")], VALUES)
    assert plan.actions == [] and plan.reason == "too_few_matches"


def test_a_lone_search_box_or_empty_page_fills_nothing():
    assert plan_fills([F("Search")], VALUES).actions == []
    assert plan_fills([], VALUES).reason == "too_few_matches"


# ── Already filled / missing values ───────────────────────────────────────────

def test_a_field_the_user_or_portal_already_filled_is_never_overwritten():
    prefilled = F("Name", filled=True)
    plan = plan_fills([prefilled, F("Mobile"), F("E-mail"), F("Address")], VALUES)
    assert prefilled in plan.already_filled
    assert "complainant_name" not in {a.key for a in plan.actions}


def test_a_prefilled_field_also_keeps_a_second_matching_field_from_taking_its_value():
    plan = plan_fills([F("Name", filled=True), F("Full Name"), F("Mobile"), F("E-mail")], VALUES)
    assert "complainant_name" not in {a.key for a in plan.actions}


def test_no_value_means_the_field_is_left_blank_not_invented():
    values = {k: v for k, v in VALUES.items() if k != "complainant_email"}
    plan = plan_fills(complaint_form(), values)
    assert "complainant_email" not in {a.key for a in plan.actions}
    assert [key for _, key in plan.missing_value] == ["complainant_email"]


def test_blank_and_whitespace_values_are_treated_as_missing():
    plan = plan_fills(complaint_form(), {**VALUES, "complainant_email": "   ", "complainant_name": ""})
    assert {k for _, k in plan.missing_value} == {"complainant_email", "complainant_name"}


def test_duplicate_fields_for_one_value_fill_the_first_only():
    first, second = F("Address"), F("Address")
    plan = plan_fills([first, second, F("Name"), F("Mobile")], VALUES)
    assert [a.info for a in plan.actions if a.key == "complainant_address"] == [first]
    assert (second, "complainant_address") in plan.duplicates


# ── Value formatting ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("+91 98765 43210", "9876543210"), ("098765-43210"[1:], "9876543210"), ("9876543210", "9876543210"),
    ("919876543210", "9876543210"), ("12345", None), ("", None),
])
def test_phone_numbers_are_normalised_to_ten_digits_or_skipped(raw, expected):
    assert ff.format_value("complainant_phone", raw, F("Mobile")) == expected


@pytest.mark.parametrize("raw, expected", [("302001", "302001"), ("302 001", "302001"), ("3020", None), ("abcdef", None)])
def test_pin_codes_must_be_six_digits(raw, expected):
    assert ff.format_value("complainant_pin", raw, F("Pin Code")) == expected


@pytest.mark.parametrize("raw, expected", [
    ("32000.0", "32000"), ("32,000", "32000"), ("Rs. 5000", "5000"), ("1250.5", "1250.50"),
    ("0", None), ("-5", None), ("abc", None), ("inf", None), ("nan", None),
])
def test_amounts_are_cleaned_or_skipped(raw, expected):
    assert ff.format_value("disputed_amount", raw, F("Amount")) == expected


def test_an_email_without_an_at_sign_is_not_typed():
    assert ff.format_value("complainant_email", "not-an-email", F("Email")) is None


def test_maxlength_truncates_the_narrative_but_never_chops_a_name():
    assert len(ff.format_value("incident_narrative", "word " * 100, F("Details", maxlength=40))) <= 40
    assert ff.format_value("complainant_name", "Asha Verma", F("Name", maxlength=5)) is None


def test_the_email_field_type_only_ever_receives_the_email():
    assert ff.match_field(F("Email", type="email"))[0] == "complainant_email"
    assert ff.match_field(F("Complainant Name", type="email"))[0] is None
    assert ff.match_field(F("Mobile", type="email"))[0] is None


def test_tel_and_number_fields_only_get_numeric_values():
    assert ff.match_field(F("Mobile", type="tel"))[0] == "complainant_phone"
    assert ff.match_field(F("Complainant Name", type="number"))[0] is None
    assert ff.match_field(F("Pin Code", type="number"))[0] == "complainant_pin"


# ── Dropdowns ─────────────────────────────────────────────────────────────────

STATES = ["--Select State--", "Delhi", "Karnataka", "Rajasthan", "Uttar Pradesh"]


def test_state_dropdown_picks_the_exact_option():
    select = F("State", tag="select", options=STATES, selected_text="--Select State--", selected_value="0")
    plan = plan_fills([select, F("Name"), F("Mobile"), F("E-mail")], VALUES)
    assert [(a.key, a.value) for a in plan.actions if a.info is select] == [("complainant_state", "Rajasthan")]


def test_a_dropdown_still_on_its_placeholder_counts_as_empty():
    for text, value in [("--Select State--", "0"), ("Select", ""), ("Please choose", "-1"), ("", "")]:
        assert not ff.select_is_filled(F("State", tag="select", selected_text=text, selected_value=value))


def test_a_dropdown_with_a_real_choice_is_left_alone():
    chosen = F("State", tag="select", options=STATES, selected_text="Karnataka", selected_value="2")
    assert ff.select_is_filled(chosen)
    plan = plan_fills([chosen, F("Name"), F("Mobile"), F("E-mail")], VALUES)
    assert chosen in plan.already_filled


@pytest.mark.parametrize("options, wanted, expected", [
    (["Select", "NCT of Delhi", "Haryana"], "Delhi", "NCT of Delhi"),
    (["Select", "Delhi", "New Delhi"], "Delhi", "Delhi"),
    (["Select", "NCT of Delhi", "New Delhi"], "Delhi", None),        # two loose matches: do not guess
    (["Select", "Jammu & Kashmir"], "jammu kashmir", "Jammu & Kashmir"),
    (["Select", "Goa"], "Rajasthan", None),
    (["Select"], "Rajasthan", None),
])
def test_dropdown_options_match_exactly_or_uniquely_or_not_at_all(options, wanted, expected):
    assert ff.pick_option(options, wanted) == expected


def test_only_state_and_city_can_fill_a_dropdown():
    select = F("Mobile", tag="select", options=["A", "B", "Select"])
    assert ff.match_field(select) == (None, 0)
    assert ff.match_field(F("City", tag="select", options=["Jaipur"]))[0] == "complainant_city"


def test_a_state_with_no_matching_option_is_left_for_the_user():
    select = F("State", tag="select", options=["Select", "Goa"], selected_text="Select", selected_value="")
    plan = plan_fills([select, F("Name"), F("Mobile"), F("E-mail")], VALUES)
    assert (select, "complainant_state") in plan.unfit_value


# ── Page driver ───────────────────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, store, frame_no, selector, fail=None, shown=None):
        self.store, self.frame_no, self.selector, self.fail, self.shown = store, frame_no, selector, fail, shown
        self.first = self

    async def fill(self, value, timeout=None):
        if self.fail == "fill":
            raise RuntimeError("detached")
        self.store.append(("fill", self.frame_no, self.selector, value))

    async def select_option(self, label=None, timeout=None):
        if self.fail == "select":
            raise RuntimeError("no such option")
        self.store.append(("select", self.frame_no, self.selector, label))

    async def input_value(self):
        if self.shown is not None:
            return self.shown
        return next(v for kind, _, sel, v in reversed(self.store) if sel == self.selector)

    async def dispatch_event(self, name):
        self.store.append(("event", self.frame_no, self.selector, name))


class FakeFrame:
    def __init__(self, store, number, raw_fields, evaluate_fails=False, behaviour=None):
        self.store, self.number, self.raw, self.fails, self.behaviour = store, number, raw_fields, evaluate_fails, behaviour or {}

    async def evaluate(self, script):
        if self.fails:
            raise RuntimeError("cross-origin")
        return self.raw

    def locator(self, selector):
        index = int(selector.split('"')[1])
        return FakeLocator(self.store, self.number, selector, **self.behaviour.get(index, {}))


def raw(index, label, tag="input", type="text", **kw):
    base = {"index": index, "tag": tag, "type": tag if tag != "input" else type, "label": label, "options": [], "filled": False}
    base.update(kw)
    return base


def page_with(*frames, store=None):
    return SimpleNamespace(frames=list(frames))


def run_fill(page, values=VALUES, **kw):
    return asyncio.run(ff.fill_page(page, values, pause=0, session_id="s-1", **kw))


COMPLAINT_RAW = [
    raw(1, "Name *"), raw(2, "Mobile No."), raw(3, "E-mail"),
    raw(4, "State", tag="select", options=["--Select--", "Delhi", "Rajasthan"], selected_text="--Select--", selected_value="0"),
    raw(5, "Address", tag="textarea"), raw(6, "Grievance Details", tag="textarea"),
    raw(7, "Password", type="password"), raw(8, "Captcha"), raw(9, "Submit", type="submit"),
]


def test_driver_fills_each_matched_field_with_the_formatted_value():
    store = []
    report = run_fill(page_with(FakeFrame(store, 0, COMPLAINT_RAW)))
    fills = {sel: val for kind, _, sel, val in store if kind in {"fill", "select"}}
    assert fills['[data-nyaya-fill="1"]'] == "Asha Verma"
    assert fills['[data-nyaya-fill="2"]'] == "9876543210"
    assert fills['[data-nyaya-fill="3"]'] == "asha.verma@example.com"
    assert fills['[data-nyaya-fill="4"]'] == "Rajasthan"
    assert fills['[data-nyaya-fill="5"]'] == "12 MG Road, Jaipur"
    assert fills['[data-nyaya-fill="6"]'] == "The washing machine ordered on 2 March was never delivered."
    assert report.filled_count == 6 and report.failed == []
    assert report.sensitive == ["Password", "Captcha"]


def test_driver_never_touches_sensitive_or_unmatched_fields_and_never_clicks():
    store = []
    run_fill(page_with(FakeFrame(store, 0, COMPLAINT_RAW)))
    touched = {sel for _, _, sel, _ in store}
    assert not touched & {'[data-nyaya-fill="7"]', '[data-nyaya-fill="8"]', '[data-nyaya-fill="9"]'}
    assert all(kind in {"fill", "select", "event"} for kind, *_ in store)


def test_driver_uses_the_right_frame_for_each_field():
    store = []
    outer = FakeFrame(store, 0, [raw(1, "Name"), raw(2, "Mobile")])
    inner = FakeFrame(store, 1, [raw(1, "E-mail"), raw(2, "Address", tag="textarea")])
    report = run_fill(page_with(outer, inner))
    where = {(frame_no, sel): val for kind, frame_no, sel, val in store if kind == "fill"}
    assert where[(0, '[data-nyaya-fill="1"]')] == "Asha Verma"
    assert where[(1, '[data-nyaya-fill="1"]')] == "asha.verma@example.com"
    assert report.filled_count == 4


def test_one_stubborn_field_does_not_stop_the_rest():
    store = []
    frame = FakeFrame(store, 0, COMPLAINT_RAW, behaviour={2: {"fail": "fill"}})
    report = run_fill(page_with(frame))
    assert report.failed == ["Mobile No."]
    assert report.filled_count == 5
    assert "Mobile No." in report.left_for_user


def test_a_field_that_stays_empty_after_filling_is_reported_as_failed():
    store = []
    frame = FakeFrame(store, 0, COMPLAINT_RAW, behaviour={3: {"shown": ""}})
    report = run_fill(page_with(frame))
    assert "E-mail" in report.failed and "E-mail" not in report.filled


def test_an_unreadable_frame_is_skipped_not_fatal():
    store = []
    report = run_fill(page_with(FakeFrame(store, 0, COMPLAINT_RAW), FakeFrame(store, 1, [], evaluate_fails=True)))
    assert report.filled_count == 6


def test_a_page_with_no_frames_attribute_still_works():
    store = []
    frame = FakeFrame(store, 0, COMPLAINT_RAW)
    report = run_fill(frame)          # a bare frame/page without .frames
    assert report.filled_count == 6


def test_a_page_that_is_not_a_complaint_form_is_left_completely_alone():
    store = []
    report = run_fill(page_with(FakeFrame(store, 0, [raw(1, "Name"), raw(2, "Email"), raw(3, "Search")])))
    assert report.filled_count == 0 and report.reason == "too_few_matches" and store == []


def test_report_and_logs_carry_labels_only_never_the_values_typed(caplog):
    store = []
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        report = run_fill(page_with(FakeFrame(store, 0, COMPLAINT_RAW)))
    visible = " ".join(report.filled + report.failed + report.left_for_user) + caplog.text
    for private in ("Asha Verma", "9876543210", "asha.verma@example.com", "12 MG Road", "washing machine"):
        assert private not in visible
    assert "event=rule_fill_report" in caplog.text and "matched_values=" in caplog.text


def test_scan_script_reads_structure_not_field_values():
    script = ff.SCAN_JS.replace("el.value.length", "")
    assert "el.value" not in script and "selected_value" in script
    assert "click(" not in ff.SCAN_JS and "submit(" not in ff.SCAN_JS


# ── Case data handed to the filler ────────────────────────────────────────────

def test_case_data_includes_email_and_pin_for_the_filler():
    from app.browser_routes import _extract_case_data
    record = SimpleNamespace(profile_data={"user_name": "Asha Verma", "key_facts": {}})
    user = SimpleNamespace(full_name="Asha", full_address="A", city="Jaipur", state="Rajasthan",
                           phone="9876543210", email="asha@example.com", pin_code="302001")
    data = _extract_case_data(record, user)
    assert data["complainant_email"] == "asha@example.com" and data["complainant_pin"] == "302001"
    assert data["complainant_name"] == "Asha Verma"
