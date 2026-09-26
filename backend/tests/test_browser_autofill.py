"""Local-only Auto-Fill checks; never contact a portal or an LLM."""

import asyncio
import sys
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from app.agents import browser_agent
from app import browser_routes
from app.services.browser_runtime import browser_runtime_status
from app.services.portal_selector import build_task, select_portal
from app.auth import get_current_user
from app.main import app


MOCK_FORM = """<html><head><title>Mock Portal</title></head><body>
<form id="complaint"><label>Name <input name="name"></label>
<label>Details <textarea name="details"></textarea></label>
<label>State <select name="state"><option value="">Choose</option>
<option value="RJ">Rajasthan</option></select></label>
<label>Confirm <input type="checkbox" name="confirm"></label>
<label>Evidence <input type="file" name="evidence"></label>
<button type="submit">Submit complaint</button></form>
<script>window.submitted = false;
document.querySelector('form').addEventListener('submit', e => {
  e.preventDefault(); window.submitted = true;
});</script></body></html>"""
MOCK_URL = "data:text/html," + quote(MOCK_FORM)
TEST_REQUEST = SimpleNamespace(base_url="http://testserver/")


def test_local_chromium_can_fill_mock_form_without_submitting():
    async def check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(MOCK_URL)
                assert await page.title() == "Mock Portal"
                await page.locator('[name="name"]').fill("Test Citizen")
                await page.locator('[name="details"]').fill("Dummy local complaint")
                await page.locator('[name="state"]').select_option("RJ")
                await page.locator('[name="confirm"]').check()
                assert await page.locator('[name="name"]').input_value() == "Test Citizen"
                assert await page.locator('[name="state"]').input_value() == "RJ"
                assert not await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(check())


def test_runtime_preflight_and_missing_runtime_error(monkeypatch):
    assert browser_runtime_status() == {
        "available": True, "browser_use": True, "playwright": True, "chromium": True
    }
    monkeypatch.setattr(browser_routes, "browser_runtime_status", lambda: {
        "available": False, "browser_use": True, "playwright": True, "chromium": False
    })
    with pytest.raises(HTTPException) as error:
        asyncio.run(browser_routes.start_browser_session(
            "dummy", request=TEST_REQUEST, db=object(), current_user=SimpleNamespace(id="user-a")
        ))
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "BROWSER_RUNTIME_UNAVAILABLE"


def test_session_owner_and_missing_values():
    session = browser_agent.create_session(
        case_id="dummy", user_id="user-a", category="consumer",
        portal_label="Mock", portal_url="https://example.invalid/", task="local only",
    )
    try:
        assert browser_routes._owned_session(session.session_id, "user-a") is session
        with pytest.raises(HTTPException) as error:
            browser_routes._owned_session(session.session_id, "user-b")
        assert error.value.status_code == 404
    finally:
        browser_agent._sessions.pop(session.session_id, None)
    task = build_task(select_portal("consumer"), {"complainant_name": "Test Citizen"})
    assert "[not provided; leave this field blank for the user]" in task
    assert "Never invent" in task


def test_http_session_routes_reject_other_user():
    session = browser_agent.create_session(
        case_id="dummy", user_id="user-a", category="consumer",
        portal_label="Mock", portal_url="https://example.invalid/", task="local only",
    )
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-b")
    try:
        client = TestClient(app)
        assert client.get(f"/api/v1/browser/status/{session.session_id}").status_code == 404
        assert client.post(f"/api/v1/browser/approve/{session.session_id}").status_code == 404
        assert client.post(f"/api/v1/browser/cancel/{session.session_id}").status_code == 404
        assert client.get(f"/api/v1/browser/cdp-info/{session.session_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        browser_agent._sessions.pop(session.session_id, None)


def test_start_does_not_fall_back_to_unowned_case(monkeypatch):
    monkeypatch.setattr(browser_routes, "browser_runtime_status", lambda: {
        "available": True, "browser_use": True, "playwright": True, "chromium": True
    })

    class EmptyDb:
        calls = 0

        def query(self, model):
            self.calls += 1
            return self

        def filter(self, *args):
            return self

        def first(self):
            return None

    db = EmptyDb()
    with pytest.raises(HTTPException) as error:
        asyncio.run(browser_routes.start_browser_session(
            "unowned", request=TEST_REQUEST, db=db, current_user=SimpleNamespace(id="user-b")
        ))
    assert error.value.status_code == 404
    assert db.calls == 1


def _use_fake_agent(monkeypatch):
    import browser_use

    class FakeHistory:
        def screenshots(self):
            return []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser = kwargs["browser"]

        async def run(self, max_steps):
            page = await self.browser.get_current_page()
            await page.goto(MOCK_URL)
            await page.locator('[name="name"]').fill("Test Citizen")
            await page.locator('[name="details"]').fill("Dummy local complaint")
            await page.locator('[name="state"]').select_option("RJ")
            return FakeHistory()

    monkeypatch.setattr(browser_use, "Agent", FakeAgent)
    monkeypatch.setattr(browser_agent, "_resolve_llm", lambda: (object(), False))


@pytest.mark.skipif(sys.platform != "win32", reason="uvicorn --reload uses SelectorEventLoop on Windows")
def test_session_launches_from_selector_loop_like_uvicorn_reload(monkeypatch):
    _use_fake_agent(monkeypatch)

    async def check():
        session = browser_agent.create_session(
            case_id="dummy", user_id="user-a", category="consumer",
            portal_label="Local Mock", portal_url=MOCK_URL, task="local dummy data only",
        )
        try:
            browser_agent.start_browser_session_task(session)

            async def wait_until(predicate, label):
                for _ in range(120):
                    if predicate():
                        return
                    if session.status == browser_agent.SessionStatus.ERROR:
                        raise AssertionError(f"Browser task stopped: {session.error}")
                    await asyncio.sleep(0.25)
                raise AssertionError(f"Timed out waiting for {label}: {session.error}")

            await wait_until(lambda: session.status == browser_agent.SessionStatus.PAUSED, "review")
            assert browser_agent.approve_session(session.session_id)
            await wait_until(lambda: session.status == browser_agent.SessionStatus.APPROVED, "approval")
            assert browser_agent.cancel_session(session.session_id)
            await wait_until(lambda: session._loop.is_closed(), "browser shutdown")
            assert session.status == browser_agent.SessionStatus.CANCELLED
        finally:
            browser_agent._sessions.pop(session.session_id, None)

    loop = asyncio.SelectorEventLoop()
    try:
        loop.run_until_complete(check())
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="interactive Chromium smoke is for Windows development")
def test_browser_use_session_opens_local_form_and_waits_for_review(monkeypatch):
    _use_fake_agent(monkeypatch)

    async def check():
        session = browser_agent.create_session(
            case_id="dummy", user_id="user-a", category="consumer",
            portal_label="Local Mock", portal_url=MOCK_URL, task="local dummy data only",
        )
        task = asyncio.create_task(browser_agent.run_browser_session(session))
        session._task = task
        try:
            async def wait_for(status):
                for _ in range(120):
                    if session.status == status:
                        return
                    if task.done():
                        raise AssertionError(f"Browser task stopped: {session.error}")
                    await asyncio.sleep(0.25)
                raise AssertionError(f"Timed out waiting for {status}: {session.error}")

            await wait_for(browser_agent.SessionStatus.PAUSED)
            assert session.cdp_ready
            assert any(field["label"].strip() == "Name" and field["status"] == "filled"
                       for field in session.review_fields)
            assert all("Test Citizen" not in str(field) for field in session.review_fields)
            assert browser_agent.approve_session(session.session_id)
            await wait_for(browser_agent.SessionStatus.APPROVED)
            assert "manually" in session.message
            assert browser_agent.cancel_session(session.session_id)
            await asyncio.wait_for(task, 20)
            assert session.status == browser_agent.SessionStatus.CANCELLED
            assert session.cdp_port is None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            browser_agent._sessions.pop(session.session_id, None)

    asyncio.run(check())


def test_mock_portal_route_is_disabled_unless_flagged(monkeypatch):
    monkeypatch.delenv("AUTOFILL_MOCK_PORTAL", raising=False)
    client = TestClient(app)
    assert client.get("/api/v1/browser/mock-portal").status_code == 404
    monkeypatch.setenv("AUTOFILL_MOCK_PORTAL", "1")
    page = client.get("/api/v1/browser/mock-portal")
    assert page.status_code == 200
    assert "LOCAL MOCK PORTAL — TEST ONLY — NO REAL SUBMISSION" in page.text


def test_mock_portal_values_use_profile_and_current_case_only():
    user = SimpleNamespace(full_name="Test Citizen", email="t@example.com", phone=None, state="Rajasthan",
                           city="Jaipur", pin_code="302001", full_address="12 Test Lane")
    record = SimpleNamespace(profile_data={
        "category": "cyber_fraud", "disputed_amount": 0, "transaction_id": "UTR-1",
        "key_facts": {"incident_narrative": "Dummy narrative"},
    })
    values = browser_routes._mock_portal_values(record, user)
    assert values["full_name"] == "Test Citizen" and values["phone"] == ""
    assert values["complaint_category"] == "CYBER_FRAUD"
    assert values["amount"] == ""  # zero means unknown, never "0"
    assert values["description"] == "Dummy narrative" and values["transaction_ref"] == "UTR-1"


def test_mock_portal_is_filled_by_playwright_without_llm(monkeypatch):
    from pathlib import Path

    html = (Path(browser_routes.__file__).parent / "static" / "autofill_mock_portal.html").read_text(encoding="utf-8")
    url = "data:text/html;charset=utf-8," + quote(html)

    def no_llm():
        raise AssertionError("mock portal must not resolve an LLM")

    monkeypatch.setattr(browser_agent, "_resolve_llm", no_llm)

    async def check():
        session = browser_agent.create_session(
            case_id="dummy", user_id="user-a", category="CYBER_FRAUD",
            portal_label="Local Mock Portal", portal_url=url, task="local only",
            fill_values={"full_name": "Test Citizen", "state": "Rajasthan",
                         "complaint_category": "CYBER_FRAUD", "phone": ""},
        )
        task = asyncio.create_task(browser_agent.run_browser_session(session))
        session._task = task
        try:
            for _ in range(160):
                if session.status in (browser_agent.SessionStatus.PAUSED, browser_agent.SessionStatus.ERROR):
                    break
                await asyncio.sleep(0.25)
            assert session.status == browser_agent.SessionStatus.PAUSED, session.error
            assert session.pause_kind == "review" and session.screenshot_b64
            # A <select>'s label text also contains its options; keep the first line.
            status = {f["label"].strip().split("\n")[0]: f["status"] for f in session.review_fields}
            assert status["Full Name"] == "filled" and status["State"] == "filled"
            assert status["Complaint Category"] == "filled" and status["Phone"] == "missing"
            assert browser_agent.cancel_session(session.session_id)
            await asyncio.wait_for(task, 20)
            assert session.status == browser_agent.SessionStatus.CANCELLED
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            browser_agent._sessions.pop(session.session_id, None)

    asyncio.run(check())
