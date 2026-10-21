"""Phase 2 Auto-Fill: pause for the user at a login wall, before any agent tokens.

Fake pages and a simulated user only. No playwright, no browser_use.Agent, no
network, no LLM call, no Chromium.
"""

import asyncio

import pytest

from app.agents import browser_agent as ba

FORM = {"inputs": 9, "password": 0, "otp": 0, "captcha": 0}
LOGIN = {"inputs": 2, "password": 1, "otp": 0, "captcha": 0}
OTP = {"inputs": 1, "password": 0, "otp": 1, "captcha": 0}


# ── The wall rule ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("signals, expected", [
    ({"inputs": 9, "password": 0, "otp": 0, "captcha": 0}, False),                       # plain form
    ({"inputs": 2, "password": 1, "otp": 0, "captcha": 0}, True),                        # password
    ({"inputs": 12, "password": 1, "otp": 0, "captcha": 0}, True),                       # sign-up form with password
    ({"inputs": 1, "password": 0, "otp": 1, "captcha": 0}, True),                        # OTP
    ({"inputs": 3, "password": 0, "otp": 0, "captcha": 1}, True),                        # username + captcha page
    ({"inputs": 11, "password": 0, "otp": 0, "captcha": 1}, False),                      # real form ending in a captcha
    ({"inputs": 0, "password": 0, "otp": 0, "captcha": 0, "url_hint": True}, True),      # login URL, empty page
    ({"inputs": 1, "password": 0, "otp": 0, "captcha": 0, "url_hint": True}, True),
    ({"inputs": 8, "password": 0, "otp": 0, "captcha": 0, "url_hint": True}, False),     # ".../Register" form
    ({}, False),
])
def test_login_wall_rule(signals, expected):
    assert ba._is_login_wall(signals) is expected


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakePage:
    """Reports whatever `state["signals"]` is when evaluated; no screenshot support."""

    def __init__(self, state, url="https://portal.example.gov.in/home"):
        self.state, self.url = state, url

    async def evaluate(self, script):
        if self.state.get("raise"):
            raise RuntimeError("page closed")
        return self.state["signals"]


def make_session():
    return ba.BrowserSession(
        session_id="s-1", case_id="c-1", user_id="u-1", category="CYBER_FRAUD",
        portal_label="Portal", portal_url="https://portal.example.gov.in/", task="private task text",
    )


async def run_hold(state, user_steps, max_rounds=3):
    """Run _hold_for_login while a simulated user acts each time the session pauses.

    `user_steps` is a list of callables, one per expected pause. Each receives the
    session while it is PAUSED (so the test can assert on it), then the user
    "clicks Continue". Returns (rounds, session, observed_pauses).
    """
    session = make_session()
    page = FakePage(state)

    async def get_page():
        return page

    observed = []

    async def user():
        # Count pauses rather than watching `status`: the fakes never yield to the
        # event loop, so a second pause can begin before this task sees the first end.
        for number, step in enumerate(user_steps, start=1):
            while not (
                session.status == ba.SessionStatus.PAUSED
                and session.steps.count("Waiting for you to sign in on the portal.") == number
            ):
                await asyncio.sleep(0)
            observed.append((session.pause_kind, session.message))
            step(session)
            session._approve_event.set()

    user_task = asyncio.create_task(user())
    rounds = await asyncio.wait_for(ba._hold_for_login(session, get_page, max_rounds), timeout=5)
    await asyncio.wait_for(user_task, timeout=5)
    return rounds, session, observed


def run(coro):
    return asyncio.run(coro)


# ── Behaviour ─────────────────────────────────────────────────────────────────

def test_a_plain_form_never_pauses():
    state = {"signals": FORM}
    rounds, session, observed = run(run_hold(state, []))
    assert rounds == 0 and observed == []
    assert session.status == ba.SessionStatus.PENDING and session.pause_kind is None


def test_login_wall_pauses_with_a_login_kind_and_resumes_after_continue():
    state = {"signals": LOGIN}

    def user_logs_in(session):
        state["signals"] = FORM   # the form is showing once they have signed in

    rounds, session, observed = run(run_hold(state, [user_logs_in]))
    assert rounds == 1
    assert observed[0][0] == "login"
    assert "Continue" in observed[0][1] and "never sees or stores" in observed[0][1]
    assert session.status == ba.SessionStatus.RUNNING and session.pause_kind is None
    assert "Sign-in step completed by you." in session.steps


def test_a_second_wall_after_login_pauses_again_for_the_otp_step():
    state = {"signals": LOGIN}
    stages = iter([OTP, FORM])

    rounds, session, observed = run(run_hold(
        state, [lambda s: state.update(signals=next(stages)), lambda s: state.update(signals=next(stages))],
    ))
    assert rounds == 2
    assert [kind for kind, _ in observed] == ["login", "login"]
    assert session.status == ba.SessionStatus.RUNNING


def test_it_gives_up_after_the_round_limit_instead_of_asking_forever():
    state = {"signals": LOGIN}      # never clears
    rounds, session, observed = run(run_hold(state, [lambda s: None] * 3, max_rounds=3))
    assert rounds == 3 and len(observed) == 3
    assert session.status == ba.SessionStatus.RUNNING
    assert "Continuing after several sign-in pauses." in session.steps


def test_limit_note_is_not_added_when_the_last_login_succeeded():
    state = {"signals": LOGIN}
    rounds, session, _ = run(run_hold(state, [lambda s: state.update(signals=FORM)], max_rounds=1))
    assert rounds == 1
    assert "Continuing after several sign-in pauses." not in session.steps


def test_an_uninspectable_page_does_not_block_the_run():
    state = {"signals": LOGIN, "raise": True}
    rounds, session, observed = run(run_hold(state, []))
    assert rounds == 0 and observed == []
    assert session.status == ba.SessionStatus.PENDING


def test_no_page_does_not_block_the_run():
    async def scenario():
        session = make_session()

        async def get_page():
            return None

        return await ba._hold_for_login(session, get_page), session

    rounds, session = run(scenario())
    assert rounds == 0 and session.pause_kind is None


def test_cancel_during_the_login_pause_stops_the_session():
    async def scenario():
        session = make_session()
        state = {"signals": LOGIN}

        async def get_page():
            return FakePage(state)

        async def user_cancels():
            while session.status != ba.SessionStatus.PAUSED:
                await asyncio.sleep(0)
            session._cancel_event.set()

        asyncio.create_task(user_cancels())
        await asyncio.wait_for(ba._hold_for_login(session, get_page), timeout=5)

    with pytest.raises(asyncio.CancelledError):
        run(scenario())


def test_pause_never_records_page_values_or_task_text():
    state = {"signals": LOGIN}
    _, session, observed = run(run_hold(state, [lambda s: state.update(signals=FORM)]))
    everything = " ".join(session.steps) + " ".join(message for _, message in observed)
    assert "private task text" not in everything
