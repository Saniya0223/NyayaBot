"""e-Jagriti routing and safety checks. Pure local plans; no browser or LLM calls."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from app.services import form_navigator as nav
from app.services import portal_autofill as pa


def field(number, label, section, **extra):
    return pa.ScannedField(field_id=f"0:{number}", tag="input", label=label, section=section, **extra)


def plan(fields, values, provider=None):
    request = pa.PlanRequest(page_url="https://e-jagriti.gov.in/case/new", fields=fields)
    return asyncio.run(pa.build_plan(request, pa.EJAGRITI, values, provider=provider))


def values(**changes):
    profile = {
        "user_name": "Asha Verma", "user_phone": "9876543210", "user_email": "asha@example.com",
        "user_address": "12 Rose Lane", "user_pin_code": "302001",
        "opposite_party_name": "Example Retail", "opposite_party_address": "4 Market Road",
        "opposite_party_phone": "9123456780", "opposite_party_email": "seller@example.com",
        "disputed_amount": 25000, "incident_date": "2026-03-02",
    }
    profile.update(changes)
    return pa.case_values(profile, SimpleNamespace())


def test_exact_section_routes_repeated_names_contacts_and_addresses_to_the_right_party():
    fields = [
        field(1, "Full Name", "Complainant Details"),
        field(2, "Mobile Number", "Complainant Details", type="tel"),
        field(3, "Email Address", "Complainant Details", type="email"),
        field(4, "House No./Door No./Building/Flat", "Complainant Details", maxlength=51),
        field(5, "Full Name", "Opposite Party Details"),
        field(6, "Mobile Number", "Opposite Party Details", type="tel"),
        field(7, "Email Address", "Opposite Party Details", type="email"),
        field(8, "House No./Door No./Building/Flat", "Opposite Party Details", maxlength=51),
    ]
    actions = {a.field_id: (a.key, a.value) for a in plan(fields, values()).actions}
    assert actions == {
        "0:1": ("complainant_name", "Asha Verma"),
        "0:2": ("complainant_phone", "9876543210"),
        "0:3": ("complainant_email", "asha@example.com"),
        "0:4": ("complainant_address", "12 Rose Lane"),
        "0:5": ("opposite_party_name", "Example Retail"),
        "0:6": ("opposite_party_phone", "9123456780"),
        "0:7": ("opposite_party_email", "seller@example.com"),
        "0:8": ("opposite_party_address", "4 Market Road"),
    }


def test_missing_ambiguous_and_additional_party_sections_never_receive_case_values():
    fields = [
        field(1, "Full Name", ""),
        field(2, "Mobile Number", "Complainant / Opposite Party"),
        field(3, "Full Name", "Additional Complainant Details"),
        field(4, "Full Name", "Proforma Opposite Party Details"),
    ]
    result = plan(fields, values())
    assert result.actions == []
    assert {note.field_id for note in result.manual} == {"0:1", "0:2", "0:3", "0:4"}
    assert all("section" in note.reason.lower() for note in result.manual)


def test_unavailable_opposite_mobile_never_uses_the_complainants_mobile():
    profile_values = values(opposite_party_phone="")
    result = plan([
        field(1, "Mobile Number", "Complainant Details", type="tel"),
        field(2, "Mobile Number", "Respondent Details", type="tel"),
    ], profile_values)
    assert [(action.field_id, action.key) for action in result.actions] == [("0:1", "complainant_phone")]
    assert [(note.field_id, note.key) for note in result.missing] == [("0:2", "opposite_party_phone")]


def test_payment_bank_is_not_misidentified_as_consumer_opposite_party():
    profile_values = values(opposite_party_name="", bank_name="Payment Bank")
    assert profile_values["opposite_party_name"] == ""
    result = plan([field(1, "Full Name", "Opposite Party Details")], profile_values)
    assert result.actions == []
    assert [(note.field_id, note.key) for note in result.missing] == [("0:1", "opposite_party_name")]


def test_paid_consideration_is_unticked_suggestion_and_claim_is_an_unticked_refund_suggestion():
    # Changed on the owner's request: the claim is suggested (a refund of what was paid)
    # but never ticked, since it decides the Commission and the fee.
    result = plan([
        field(1, "Paid as consideration", "Case Details", type="number"),
        field(2, "Claim Consideration", "Case Details", type="number"),
        field(3, "Date of Cause of Action", "Case Details", type="date"),
    ], values())
    by_id = {action.field_id: action for action in result.actions}
    assert by_id["0:1"].key == "disputed_amount"
    assert by_id["0:1"].value == "25000" and by_id["0:1"].needs_approval
    assert "price paid" in by_id["0:1"].value_label
    assert by_id["0:3"].value == "2026-03-02"
    assert by_id["0:2"].key == "claim_amount" and by_id["0:2"].value == "25000"
    assert by_id["0:2"].needs_approval and "compensation" in by_id["0:2"].value_label


def test_long_house_address_is_flagged_instead_of_truncated():
    # The live portal may enforce the limit in React validation without maxlength.
    result = plan([field(1, "House No./Door No./Building/Flat", "Complainant Details")],
                  values(user_address="A" * 52))
    assert result.actions == []
    assert [(note.field_id, note.key) for note in result.manual] == [("0:1", "complainant_address")]
    assert "51-character" in result.manual[0].reason


def test_location_selects_uploads_advocate_declaration_and_picker_stay_manual():
    fields = [
        field(1, "Pin Code", "Complainant Details", maxlength=6),
        field(2, "State", "Complainant Details"),
        field(3, "District", "Complainant Details"),
        field(4, "Post Office", "Complainant Details"),
        field(5, "Date of Cause of Action", "Case Details", readonly=True),
        pa.ScannedField(field_id="0:6", tag="custom", type="custom", aria="Case Category", section="Case Details"),
        field(7, "Advocate Name", "Complainant Details"),
        field(8, "Document Upload", "Case Details", type="file"),
        field(9, "Commission Name", "Case Details"),
        field(10, "Declaration", "Case Details", type="checkbox"),
        field(11, "Pin Code", "Opposite Party Details", maxlength=6),
    ]
    result = plan(fields, values())
    assert [(action.field_id, action.key) for action in result.actions] == [("0:1", "complainant_pin")]
    assert {note.field_id for note in result.manual} == {f"0:{i}" for i in range(2, 12)}


def test_wrong_party_ai_guess_is_rejected_even_when_section_is_known():
    class WrongGuess:
        status = SimpleNamespace(configured=True)

        async def _generate_structured(self, *_args):
            return pa.AIFieldMappingResult(mappings=[pa.AIFieldMapping(field_id="0:1", key="complainant_phone")])

    result = plan([field(1, "Contact", "Opposite Party Details", type="tel")], values(), provider=WrongGuess())
    assert result.actions == []


def test_navigation_only_selects_file_new_case_on_signed_in_entry_page():
    portal = pa.EJAGRITI
    assert portal.is_entry_page("https://e-jagriti.gov.in/dashboard")
    assert not portal.is_entry_page("https://e-jagriti.gov.in/case/new")
    assert portal.allows_url("https://www.e-jagriti.gov.in/dashboard")
    assert not portal.allows_url("https://e-jagriti.gov.in.evil.example/dashboard")
    links = [
        nav.NavCandidate(index=1, tag="a", type="", text="Dashboard"),
        nav.NavCandidate(index=2, tag="a", type="", text="File New Case"),
        nav.NavCandidate(index=3, tag="a", type="", text="Payment History"),
        nav.NavCandidate(index=4, tag="a", type="", text="Draft Cases"),
    ]
    chosen = nav.choose_navigation(links, page_host="e-jagriti.gov.in", hints=portal.entry_hints, avoid=portal.entry_avoid)
    assert chosen and chosen.index == 2


def test_cybercrime_remains_a_sectionless_profile():
    assert pa.CYBERCRIME.section_required is False
    assert pa.portal_for_category("CYBER_FRAUD") is pa.CYBERCRIME
    assert pa.portal_for_category("CONSUMER") is pa.EJAGRITI


def test_mutation_check_label_only_mapping_loses_the_opposite_party_value():
    """Putting the old label-only matcher back would break the party-routing test."""
    unsafe = replace(
        pa.EJAGRITI,
        field_map=tuple((label, key) for _, label, key in pa.EJAGRITI.field_map),
        section_required=False,
    )
    fields = [
        field(1, "Mobile Number", "Complainant Details", type="tel"),
        field(2, "Mobile Number", "Opposite Party Details", type="tel"),
    ]
    request = pa.PlanRequest(page_url="https://e-jagriti.gov.in/case/new", fields=fields)
    result = asyncio.run(pa.build_plan(request, unsafe, values()))
    assert {action.key for action in result.actions} != {"complainant_phone", "opposite_party_phone"}
