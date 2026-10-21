"""Rule-based navigation toward the complaint form, and the Python-first driver.

Fake pages only. No playwright, no browser_use, no network, no LLM call, no Chromium.
"""

import asyncio
import logging

import pytest

from app.agents import browser_agent as ba
from app.services import form_filler as ff
from app.services import form_navigator as nav
from app.services.form_navigator import NavCandidate, choose_navigation, score_candidate

HOST = "portal.example.gov.in"


def C(text, tag="a", href="", in_dialog=False, type="", disabled=False, index=None, submits_form=False, context=""):
    return NavCandidate(index=index if index is not None else abs(hash(text)) % 1000, tag=tag, type=type,
                        text=text, href=href, in_dialog=in_dialog, disabled=disabled, submits_form=submits_form,
                        context=context)


# ── What gets chosen ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Click here to continue with this new portal", "Lodge Public Grievance", "Lodge Grievance",
    "Register a Complaint", "File a Complaint", "New Complaint", "Registration of Grievance",
    "Make a complaint", "Raise a grievance", "Complaint Registration Form", "New Application",
    "Apply Online", "File an RTI",
])
def test_clear_complaint_form_links_are_chosen(text):
    assert score_candidate(C(text)) >= nav.MIN_NAV_SCORE


@pytest.mark.parametrize("text", [
    "Login", "Department login", "Sign in", "Sign Up", "Logout", "Track Complaint Status",
    "Track your Application", "Grievance Status", "About Us", "Contact Us", "FAQ", "Help",
    "Download Complaint Form", "Print", "Search", "Submit", "Submit Complaint", "Pay Now", "Payment",
    "Cancel", "Reset", "I agree", "Accept", "Verify OTP", "Enter Captcha", "Home", "Screen Reader Access",
    "click here", "More", "",
])
def test_everything_else_is_never_clicked(text):
    assert score_candidate(C(text)) < nav.MIN_NAV_SCORE


def test_a_submit_type_control_is_never_clicked_whatever_it_says():
    assert score_candidate(C("Register a Complaint", tag="input", type="submit")) == 0
    assert score_candidate(C("Register a Complaint", tag="button", type="submit")) == 0


def test_a_button_the_page_guard_would_swallow_is_skipped_but_a_link_with_those_words_is_navigation():
    assert score_candidate(C("Register Complaint", tag="button")) == 0
    assert score_candidate(C("Lodge Complaint", tag="button")) == 0
    assert score_candidate(C("Register Complaint", tag="a")) >= nav.MIN_NAV_SCORE


def test_a_button_that_submits_a_form_is_never_chosen_but_a_plain_popup_button_is():
    assert score_candidate(C("Continue with this new portal", tag="button", submits_form=True)) == 0
    assert score_candidate(C("Click here to continue with this new portal", tag="button")) >= nav.MIN_NAV_SCORE


def test_disabled_and_overlong_text_are_skipped():
    assert score_candidate(C("Lodge Grievance", disabled=True)) == 0
    assert score_candidate(C("Lodge Grievance " + "x" * 200)) == 0


def test_a_portal_specific_hint_beats_generic_wording():
    plain = C("Click here to continue with this new portal")
    assert score_candidate(plain, (r"continue with this new portal",)) == 10
    assert score_candidate(C("Open the portal"), (r"open the portal",)) == 10


def test_an_open_popup_is_preferred_over_the_page_behind_it():
    behind, popup = C("Lodge Grievance"), C("Lodge Grievance", in_dialog=True)
    assert score_candidate(popup) > score_candidate(behind)
    assert choose_navigation([behind, popup], page_host=HOST) is popup


def test_the_best_scoring_link_wins_and_ties_go_to_the_first():
    menu, strong, other = C("Grievances"), C("Lodge Public Grievance"), C("Register a Complaint")
    assert choose_navigation([menu, strong, other], page_host=HOST) is strong
    assert choose_navigation([menu, other, strong], page_host=HOST) is other


def test_nothing_is_chosen_when_nothing_is_clear():
    assert choose_navigation([C("Login"), C("About Us"), C("Contact")], page_host=HOST) is None
    assert choose_navigation([], page_host=HOST) is None


def test_the_same_text_is_never_clicked_twice():
    link = C("Lodge Public Grievance")
    assert choose_navigation([link], page_host=HOST, visited={"lodge public grievance"}) is None


# ── Where a link may lead ─────────────────────────────────────────────────────

@pytest.mark.parametrize("href, allowed", [
    ("", True), ("#", True), ("javascript:void(0)", True),
    (f"https://{HOST}/new", True), ("https://sub.example.gov.in/new", True),
    ("https://other.gov.in/x", False), ("https://twitter.com/x", False),
    ("mailto:help@example.gov.in", False), ("tel:1800", False),
    (f"https://{HOST}/guide.pdf", False), (f"https://{HOST}/form.DOCX", False),
])
def test_links_must_stay_on_the_portal_and_must_not_be_files_or_mail(href, allowed):
    assert nav._same_site_link(href, HOST) is allowed


def test_a_cross_site_link_is_not_chosen_even_if_the_wording_is_perfect():
    assert choose_navigation([C("Lodge Grievance", href="https://twitter.com/x")], page_host=HOST) is None


# ── The driver ────────────────────────────────────────────────────────────────

PLAIN = {"inputs": 0, "password": 0, "otp": 0, "captcha": 0}
LOGIN = {"inputs": 2, "password": 1, "otp": 0, "captcha": 0}


def link(index, text, **kw):
    return {"index": index, "tag": "a", "type": "", "text": text, "href": kw.pop("href", ""),
            "in_dialog": kw.pop("in_dialog", False), "disabled": False, **kw}


def field(index, label, tag="input", **kw):
    return {"index": index, "tag": tag, "type": tag if tag != "input" else "text", "label": label,
            "options": [], "filled": False, **kw}


FORM = [field(1, "Name *"), field(2, "Mobile No."), field(3, "E-mail"),
        field(4, "Address", tag="textarea"), field(5, "Grievance Details", tag="textarea"),
        field(6, "Password", type="password")]

VALUES = {
    "complainant_name": "Asha Verma", "complainant_phone": "9876543210", "complainant_email": "a@example.com",
    "complainant_address": "12 MG Road", "incident_narrative": "Order not delivered.",
}


class FakePortal:
    """A scripted portal: each stage is what the page shows; a nav click advances it."""

    def __init__(self, stages):
        self.stages, self.stage = stages, 0
        self.clicks, self.typed, self.tabs = [], [], 1
        self.opens_new_tab = False

    @property
    def now(self):
        return self.stages[min(self.stage, len(self.stages) - 1)]


class FakeLocator:
    def __init__(self, portal, selector):
        self.portal, self.selector, self.first = portal, selector, self

    async def click(self, timeout=None):
        if self.portal.now.get("click_fails"):
            raise RuntimeError("covered by overlay")
        self.portal.clicks.append(self.selector)
        self.portal.stage += 1
        if self.portal.opens_new_tab:
            self.portal.tabs += 1

    async def fill(self, value, timeout=None):
        self.portal.typed.append((self.selector, value))

    async def select_option(self, label=None, timeout=None):
        self.portal.typed.append((self.selector, label))

    async def input_value(self):
        return next(v for sel, v in reversed(self.portal.typed) if sel == self.selector)

    async def dispatch_event(self, name):
        pass


class FakePage:
    def __init__(self, portal):
        self.portal = portal

    @property
    def url(self):
        return self.portal.now.get("url", f"https://{HOST}/")

    async def evaluate(self, script):
        stage = self.portal.now
        if script == ff.SCAN_JS:
            return stage.get("fields", [])
        if script == nav.NAV_SCAN_JS:
            return stage.get("nav", [])
        return stage.get("signals", PLAIN)

    def locator(self, selector):
        return FakeLocator(self.portal, selector)

    async def wait_for_load_state(self, state, timeout=None):
        pass


class FakeBrowser:
    def __init__(self, portal):
        self.portal, self.page = portal, FakePage(portal)
        self.switched = []

    async def get_current_page(self):
        return self.page

    @property
    def browser_context(self):
        portal = self.portal

        class Context:
            @property
            def pages(self):          # live, like Playwright's context.pages
                return [object()] * portal.tabs

        return Context()

    async def switch_tab(self, index):
        self.switched.append(index)


def make_session(case_data=VALUES, hints=()):
    return ba.BrowserSession(
        session_id="s-1", case_id="c-1", user_id="u-1", category="EMPLOYMENT", portal_label="Portal",
        portal_url=f"https://{HOST}/", task="private task text", case_data=case_data, entry_hints=hints,
    )


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ff, "FILL_PAUSE_SECONDS", 0)
    monkeypatch.setattr(nav, "RETRY_WAIT_SECONDS", 0)


def drive(portal, session=None, user=None):
    async def scenario():
        sess = session or make_session()
        browser = FakeBrowser(portal)
        tasks = [asyncio.create_task(user(sess))] if user else []
        result = await asyncio.wait_for(ba._drive_to_form(sess, browser, settle_seconds=0), timeout=5)
        for task in tasks:
            await asyncio.wait_for(task, timeout=5)
        return result, sess, browser

    return asyncio.run(scenario())


CONTINUE_BUTTON = link(3, "Click here to continue with this new portal", href=f"https://{HOST}/new", in_dialog=True)


def test_landing_page_then_form_is_clicked_through_and_filled_with_no_agent():
    portal = FakePortal([{"nav": [link(1, "About Us"), CONTINUE_BUTTON]}, {"fields": FORM}])
    (filled, pauses, clicks), session, _ = drive(portal)
    assert (filled, pauses, clicks) == (5, 0, 1)
    assert portal.clicks == ['[data-nyaya-nav="3"]']
    assert any("Click here to continue with this new portal" in step for step in session.steps)
    assert any(step.startswith("Filled 5 field(s)") for step in session.steps)
    assert not any(selector.endswith('"6"]') for selector, _ in portal.typed)      # password untouched


def test_a_form_that_is_already_showing_is_filled_without_any_click():
    portal = FakePortal([{"fields": FORM, "nav": [CONTINUE_BUTTON]}])
    (filled, _, clicks), _, _ = drive(portal)
    assert filled == 5 and clicks == 0 and portal.clicks == []


def test_no_clear_link_hands_over_to_the_agent_without_clicking_anything():
    portal = FakePortal([{"nav": [link(1, "About Us"), link(2, "Login"), link(3, "Contact")]}])
    result, _, _ = drive(portal)
    assert result == (0, 0, 0) and portal.clicks == [] and portal.typed == []


def test_a_menu_then_a_submenu_is_followed_in_order():
    portal = FakePortal([
        {"nav": [link(1, "Grievances")]},
        {"nav": [link(2, "Lodge Public Grievance")]},
        {"fields": FORM},
    ])
    (filled, _, clicks), _, _ = drive(portal)
    assert (filled, clicks) == (5, 2)


def test_after_a_click_lands_on_a_login_page_it_pauses_for_the_user_then_continues():
    portal = FakePortal([
        {"nav": [link(1, "Lodge Public Grievance")]},
        {"signals": LOGIN, "nav": [], "fields": []},
        {"fields": FORM},
    ])

    async def user(session):
        while not (session.status == ba.SessionStatus.PAUSED and session.pause_kind == "login"):
            await asyncio.sleep(0)
        assert "never sees or stores" in session.message
        portal.stage = 2                      # the user signs in; the form appears
        session._approve_event.set()

    (filled, pauses, clicks), session, _ = drive(portal, user=user)
    assert filled == 5 and pauses == 1 and clicks == 1


def test_the_number_of_clicks_is_capped_so_it_can_never_wander_forever():
    pages = [{"nav": [link(1, f"Lodge Grievance {n}")]} for n in range(50)]
    portal = FakePortal(pages)
    (filled, _, clicks), _, _ = drive(portal)
    assert filled == 0 and clicks == nav.MAX_NAV_STEPS == len(portal.clicks)


def test_a_click_that_fails_is_not_retried_and_the_next_best_link_is_tried():
    portal = FakePortal([
        {"nav": [link(1, "Lodge Public Grievance"), link(2, "Register a Complaint")], "click_fails": True},
    ])
    (filled, _, clicks), _, _ = drive(portal)
    assert filled == 0 and clicks == 0 and portal.clicks == []


def test_a_link_that_opens_a_new_tab_moves_the_browser_to_that_tab():
    portal = FakePortal([{"nav": [CONTINUE_BUTTON]}, {"fields": FORM}])
    portal.opens_new_tab = True
    (filled, _, _), _, browser = drive(portal)
    assert filled == 5 and browser.switched == [1]


def test_without_case_data_nothing_is_clicked_or_filled():
    portal = FakePortal([{"nav": [CONTINUE_BUTTON]}, {"fields": FORM}])
    result, _, _ = drive(portal, session=make_session(case_data=None))
    assert result == (0, 0, 0) and portal.clicks == [] and portal.typed == []


def test_a_crash_while_filling_falls_back_instead_of_failing_the_session(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("page closed")

    monkeypatch.setattr(ba, "fill_page", boom)
    portal = FakePortal([{"fields": FORM}])
    result, _, _ = drive(portal)
    assert result == (0, 0, 0)


def test_portal_hints_from_the_session_are_used():
    portal = FakePortal([{"nav": [link(1, "Open the citizens window")]}, {"fields": FORM}])
    (filled, _, clicks), _, _ = drive(portal, session=make_session(hints=(r"citizens window",)))
    assert filled == 5 and clicks == 1


def test_logs_and_steps_never_contain_case_values_or_the_task(caplog):
    portal = FakePortal([{"nav": [CONTINUE_BUTTON]}, {"fields": FORM}])
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        _, session, _ = drive(portal)
    everything = caplog.text + " ".join(session.steps)
    for private in ("Asha Verma", "9876543210", "a@example.com", "12 MG Road", "Order not delivered", "private task text"):
        assert private not in everything
    assert "event=nav_clicked" in caplog.text


def test_cancel_during_a_sign_in_pause_stops_the_whole_drive():
    portal = FakePortal([{"signals": LOGIN}])

    async def user(session):
        while session.status != ba.SessionStatus.PAUSED:
            await asyncio.sleep(0)
        session._cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        drive(portal, user=user)


def test_the_samadhan_portal_hint_is_registered_from_the_real_landing_page():
    from app.services.portal_selector import select_portal
    assert select_portal("EMPLOYMENT").entry_hints == (r"continue with this new portal",)
    assert select_portal("CONSUMER").entry_hints == ()


# ── navigate_once on its own ──────────────────────────────────────────────────

def test_navigate_once_reports_each_outcome_without_raising():
    async def scenario():
        portal = FakePortal([{"nav": [CONTINUE_BUTTON]}])
        page = FakePage(portal)
        visited = set()
        first = await nav.navigate_once(page, visited=visited, settle_seconds=0)
        second = await nav.navigate_once(page, visited=visited, settle_seconds=0)
        return first, second, visited

    first, second, visited = asyncio.run(scenario())
    assert first.clicked and first.text.startswith("Click here")
    assert second.reason == "no_candidate" and not second.clicked
    assert visited == {"click here to continue with this new portal"}


def test_navigate_once_survives_an_unreadable_page():
    class Broken:
        url = f"https://{HOST}/"

        async def evaluate(self, script):
            raise RuntimeError("closed")

    result = asyncio.run(nav.navigate_once(Broken(), settle_seconds=0))
    assert result.reason == "scan_failed" and not result.clicked


def test_submit_guard_only_blocks_form_submits_and_final_labels_not_every_default_type_button():
    """Regression: a <button> with no type attribute reports type 'submit', so the old
    guard swallowed every such click - including the Samadhan pop-up button - even
    though it submits nothing. Behaviour was checked in a simulated page (jsdom)."""
    guard = ba._SUBMISSION_GUARD
    assert "submitsForm" in guard and "Boolean(node.form)" in guard
    assert "button.type === 'submit' ||" not in guard
    for final_label in ("submit", "pay now", "payment", "register complaint", "lodge complaint"):
        assert final_label in guard


def test_scan_script_reads_text_and_targets_only_and_never_submits():
    script = nav.NAV_SCAN_JS
    assert ".value" not in script.replace("el.value ||", "")
    assert "submit(" not in script and ".click(" not in script


# ── Cyber-crime portal behaviour (wording and cards taken from the portal's own page text) ──

CYBER_HINTS = (r"^register a complaint$",)
CYBER_AVOID = (r"\bwomen\b", r"\bchild(ren)?\b", r"\banonymous(ly)?\b", r"\bsocial media\b")


@pytest.mark.parametrize("text", ["Report Cyber Crime", "Report a Crime", "Report Financial Fraud", "Report an Incident"])
def test_report_wording_leads_toward_the_form(text):
    assert score_candidate(C(text)) >= nav.MIN_NAV_SCORE


@pytest.mark.parametrize("text", ["Track your Complaint", "Report Abuse", "Women/Children Related Crime", "Suspect Data", "Help & Feedback"])
def test_other_menu_entries_are_not_clicked(text):
    assert score_candidate(C(text)) < nav.MIN_NAV_SCORE


def test_a_generic_button_is_judged_by_its_card_so_the_women_children_card_is_skipped():
    women = C("Register a Complaint", tag="button", context="Women & Children Related Crime Cybercrime affects women the most")
    cyber = C("Register a Complaint", tag="button", context="Cyber Crime Related Complaint Cybercrime refers to any unlawful activity")
    assert score_candidate(women, CYBER_HINTS, CYBER_AVOID) == 0
    assert score_candidate(cyber, CYBER_HINTS, CYBER_AVOID) == 10
    assert choose_navigation([women, cyber], page_host="cybercrime.gov.in", hints=CYBER_HINTS, avoid=CYBER_AVOID) is cyber
    assert choose_navigation([cyber, women], page_host="cybercrime.gov.in", hints=CYBER_HINTS, avoid=CYBER_AVOID) is cyber


def test_a_specific_menu_link_is_not_blocked_by_an_unrelated_neighbour_in_its_menu_group():
    menu_group = "Report a Crime Track your Complaint Women/Children Related Crime"
    assert score_candidate(C("Report a Crime", context=menu_group), CYBER_HINTS, CYBER_AVOID) >= nav.MIN_NAV_SCORE
    assert score_candidate(C("Report Cyber Crime", context=menu_group), CYBER_HINTS, CYBER_AVOID) >= nav.MIN_NAV_SCORE


def test_report_abuse_to_social_media_is_avoided_even_by_its_own_text():
    assert score_candidate(C("Report Abuse to Social Media"), CYBER_HINTS, CYBER_AVOID) == 0


def test_the_cyber_crime_portal_carries_the_card_hint_and_the_avoid_list():
    from app.services.portal_selector import select_portal
    portal = select_portal("CYBER_FRAUD")
    assert portal.entry_hints == CYBER_HINTS and portal.entry_avoid == CYBER_AVOID


def test_an_empty_first_scan_is_retried_so_a_late_rendering_page_is_still_found():
    class Late(FakePage):
        scans = 0

        async def evaluate(self, script):
            if script == nav.NAV_SCAN_JS:
                Late.scans += 1
                return [] if Late.scans < 3 else [link(1, "Report Cyber Crime")]
            return await super().evaluate(script)

    async def scenario():
        portal = FakePortal([{"nav": []}, {"fields": FORM}])
        return await nav.navigate_once(Late(portal), settle_seconds=0), Late.scans

    result, scans = asyncio.run(scenario())
    assert result.clicked and result.text == "Report Cyber Crime" and scans == 3


def test_a_page_that_never_offers_a_link_gives_up_after_the_retries_and_logs_what_it_saw(caplog):
    async def scenario():
        portal = FakePortal([{"nav": [link(1, "About Us"), link(2, "Contact")]}])
        return await nav.navigate_once(FakePage(portal), settle_seconds=0)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = asyncio.run(scenario())
    assert result.reason == "no_candidate"
    assert "event=nav_no_candidate links_seen=2 first_links=['about us', 'contact']" in caplog.text


def test_a_menu_then_its_submenu_then_the_form_is_followed_on_the_cyber_crime_portal():
    portal = FakePortal([
        {"nav": [link(1, "Home"), link(2, "Report Cyber Crime")]},
        {"nav": [link(3, "Report a Crime"), link(4, "Track your Complaint")]},
        {"fields": FORM},
    ])
    (filled, _, clicks), session, _ = drive(portal)
    assert (filled, clicks) == (5, 2)
    assert portal.clicks == ['[data-nyaya-nav="2"]', '[data-nyaya-nav="3"]']
