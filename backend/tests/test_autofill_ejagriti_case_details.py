"""e-Jagriti Case Details: amounts, today's pre-filled date, portal dropdowns, and pop-up
guidance. Pure local plans; no browser, network or real LLM calls."""

import asyncio
from types import SimpleNamespace

from app.services import portal_autofill as pa
from tests.test_autofill_extension_api import _case, _client, _nav, _start

CASE = "Case Details"
DEMO_CASE = {  # the owner's real test case: ₹2,500 resin gift from an Instagram seller, not delivered
    "category": "CONSUMER", "issue_type": "NON_DELIVERY", "disputed_amount": 2500.0,
    "user_state": "Rajasthan", "user_city": "Jaipur",
    "key_facts": {"seller_platform": "Instagram", "incident_narrative": "Paid for a resin gift; it never arrived."},
}


def field(number, label, **extra):
    return pa.ScannedField(field_id=f"0:{number}", tag="input", label=label, section=CASE, **extra)


def dropdown(number, name, options=(), filled=False):
    return pa.ScannedField(field_id=f"0:{100000 + number}", tag="custom", type="custom", aria=name,
                           section=CASE, options=list(options), filled=filled)


def plan(fields, profile=None, provider=None):
    profile = DEMO_CASE if profile is None else profile
    request = pa.PlanRequest(page_url="https://e-jagriti.gov.in/case/new", fields=fields)
    return asyncio.run(pa.build_plan(request, pa.EJAGRITI, pa.case_values(profile, SimpleNamespace()),
                                     profile=profile, provider=provider))


def by_label(result):
    return {a.field_label: a for a in result.actions}


def test_paid_amount_is_ticked_for_non_delivery_and_the_claim_is_an_unticked_suggestion():
    result = plan([field(1, "Paid as consideration*", type="number"), field(2, "Claim Consideration", type="number")])
    actions = by_label(result)
    paid, claim = actions["Paid as consideration*"], actions["Claim Consideration"]
    assert (paid.value, paid.needs_approval, paid.value_label) == ("2500", False, "Amount you paid (from your case)")
    assert (claim.value, claim.needs_approval) == ("2500", True)
    assert "compensation" in claim.value_label


def test_paid_amount_stays_a_please_check_item_when_the_issue_is_not_about_the_price():
    result = plan([field(1, "Paid as consideration*", type="number")], {**DEMO_CASE, "issue_type": "SERVICE_DEFICIENCY"})
    [paid] = result.actions
    assert paid.needs_approval and "may not equal price paid" in paid.value_label


def test_todays_pre_filled_date_is_offered_for_replacement_only_with_a_case_date():
    with_date = plan([field(1, "Date of Cause of Action", type="date", filled=True, today_date=True)],
                     {**DEMO_CASE, "incident_date": "2026-09-20"})
    [action] = with_date.actions
    assert (action.value, action.replace_today, action.needs_approval) == ("2026-09-20", True, True)
    assert "today's date" in action.value_label
    no_date = plan([field(1, "Date of Cause of Action", type="date", filled=True, today_date=True)])
    assert no_date.actions == []
    [note] = no_date.manual
    assert "today's date" in note.reason and "delivery was due" in note.reason
    typed = plan([field(1, "Date of Cause of Action", type="date", filled=True)], {**DEMO_CASE, "incident_date": "2026-09-20"})
    assert typed.actions == [] and typed.already_filled, "a date the user typed is never replaced"


def test_cause_of_action_dropdowns_ask_for_their_options_then_pick_exactly_and_unticked():
    first = plan([dropdown(1, "State of Cause of Action*"), dropdown(2, "District of Cause of Action*")])
    assert first.peek == ["0:100001", "0:100002"] and first.actions == []
    second = plan([dropdown(1, "State of Cause of Action*", ["Delhi", "RAJASTHAN"]),
                   dropdown(2, "District of Cause of Action*", ["Jodhpur", "Udaipur"])])
    assert second.peek == []
    [state] = second.actions
    assert (state.value, state.kind, state.needs_approval) == ("RAJASTHAN", "pick", True)
    assert "0:100002" in {n.field_id for n in second.manual}, "no exact option (Jaipur) means the user picks"


def test_an_already_chosen_dropdown_is_left_alone():
    result = plan([dropdown(1, "State of Cause of Action*", ["Rajasthan"], filled=True)])
    assert result.actions == [] and result.already_filled


def test_case_category_is_suggested_from_the_case_by_a_fixed_rule():
    result = plan([dropdown(3, "Case Category*", ["Banking", "E-Commerce", "Insurance", "Telecom"]),
                   field(1, "Paid as consideration*", type="number")])
    category = by_label(result)["Case Category*"]
    assert (category.value, category.kind, category.source, category.needs_approval) == ("E-Commerce", "pick", "rules", True)
    assert "online" in category.value_label


def test_without_the_online_fact_the_rule_does_not_guess_and_without_ai_the_user_chooses():
    offline = {**DEMO_CASE, "key_facts": {}}
    result = plan([dropdown(3, "Case Category*", ["Banking", "E-Commerce"]), field(1, "Paid as consideration*", type="number")],
                  offline)
    assert "Case Category*" not in by_label(result)
    assert "0:100003" in {n.field_id for n in result.manual}


def test_ai_chooses_only_among_the_real_options_and_its_choice_starts_unticked():
    class Model:
        status = SimpleNamespace(configured=True)
        prompts = []

        async def _generate_structured(self, prompt, _system, _schema):
            self.prompts.append(prompt)
            return pa.AIChoiceResult(choices=[pa.AIChoice(group=0, option_index=1, reason="The parcel never arrived.")])

    model = Model()
    result = plan([dropdown(4, "Sub Category*", ["Defective Goods", "Non Delivery", "Refund Not Given"]),
                   field(1, "Paid as consideration*", type="number")], provider=model)
    sub = by_label(result)["Sub Category*"]
    assert (sub.value, sub.source, sub.needs_approval) == ("Non Delivery", "ai", True)
    assert '"Non Delivery"' in model.prompts[0] and '"Refund Not Given"' in model.prompts[0], "the real options are offered"
    assert "Rajasthan" not in model.prompts[0] and "Jaipur" not in model.prompts[0], "no personal details go to the model"


def test_dialog_guidance_explains_the_filing_type_box_and_who_presses_submit():
    dialog = pa.DialogInfo(title="Select Filing Type", buttons=["Cancel", "Submit"], options=[
        pa.DialogOption(text="Normal Filing", selected=True),
        pa.DialogOption(text="Complaints Against Unfair Contract"),
    ])
    message = pa.dialog_guidance(pa.EJAGRITI, dialog)
    assert message.startswith("The portal opened a box: “Select Filing Type”.")
    assert "“Normal Filing” fits a complaint about a purchase" in message
    assert "only confirms the filing type" in message and "“Normal Filing” is selected." in message
    assert "press “Submit” in the box yourself" in message


def test_dialog_guidance_works_on_any_portal_without_a_note():
    message = pa.dialog_guidance(pa.CYBERCRIME, pa.DialogInfo(title="Confirm", buttons=["No", "Yes, continue"]))
    assert message == ("The portal opened a box: “Confirm”. I don't press buttons in pop-ups. Check the choice, "
                       "press “Yes, continue” in the box yourself, then press “Done, continue” here.")


def test_navigate_api_answers_an_open_dialog_with_guidance_and_no_click():
    client = _client()
    code = _start(client, _case(client, category="CONSUMER")).json()["code"]
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_autofill_extension_api import _bearer

    body = {"page_url": "https://e-jagriti.gov.in/case/new", "links": [], "visited": ["File New Case"],
            "dialogs": [{"title": "Select Filing Type", "buttons": ["Cancel", "Submit"],
                         "options": [{"text": "Normal Filing", "selected": True}]}]}
    reply = TestClient(app).post("/api/v1/autofill/navigate", headers=_bearer(code), json=body).json()
    assert reply["index"] is None and reply["action"] == "user" and reply["reason"] == "dialog"
    assert "Select Filing Type" in reply["message"]
    assert _nav(code, [{"index": 1, "tag": "a", "text": "File New Case"}],
                url="https://e-jagriti.gov.in/dashboard").json()["index"] == 1, "no dialog: the route works as before"
