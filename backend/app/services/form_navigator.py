"""Rule-based navigation toward a portal's complaint form: no LLM, no tokens.

When the current page has no complaint form (a landing page, a pop-up, a menu),
this finds the one link or button that clearly leads to it - "Click here to
continue with this new portal", "Lodge Grievance", "Register Complaint" - and
clicks it, one step at a time. The caller then re-checks for a form.

It is deliberately conservative: it only clicks text that clearly means "go to
the complaint form", never anything that submits, pays, logs in or leaves the
portal's own site, and never the same text twice.

Layout:
  * `choose_navigation(candidates, ...)` - pure choice; unit-tested directly.
  * `navigate_once(page, ...)` - thin Playwright driver.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger("uvicorn.error")

MAX_NAV_STEPS = 5
MIN_NAV_SCORE = 3
# A client-rendered page may have nothing to click for a second or two after it loads.
SCAN_RETRIES = 2
RETRY_WAIT_SECONDS = 1.5
_MAX_TEXT = 100

# Reads clickable elements only: visible text, tag, type and link target. No form
# values. Tags each so it can be clicked again by index.
NAV_SCAN_JS = """
(() => {
  const clip = (s, n = 120) => String(s || '').replace(/\\s+/g, ' ').trim().slice(0, n);
  const visible = el => el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  // Text of the smallest enclosing block with real content (a card, a menu group),
  // so two identical buttons in different cards can be told apart.
  const blockText = el => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const raw = String(node.textContent || '').replace(/\\s+/g, ' ').trim();
      if (raw.length >= 60) return raw.length <= 700 ? raw.slice(0, 120) : '';
    }
    return '';
  };
  const out = [];
  const nodes = Array.from(document.querySelectorAll(
    'a, button, [role=button], [role=link], [role=menuitem], input[type=button], input[type=submit]'));
  nodes.forEach((el, i) => {
    if (!visible(el)) return;
    const tag = el.tagName.toLowerCase();
    const text = clip(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || el.title);
    if (!text) return;
    el.setAttribute('data-nyaya-nav', String(i));
    out.push({
      index: i, tag, type: (el.getAttribute('type') || '').toLowerCase(), text,
      href: tag === 'a' ? String(el.href || '') : '',
      context: blockText(el),
      in_dialog: Boolean(el.closest('[role=dialog], [aria-modal=true], dialog, .modal')),
      disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
      // A submit control inside a form: pressing it sends that form.
      submits_form: (el.type === 'submit' || el.type === 'image') && Boolean(el.form),
    });
  });
  return out;
})()
"""


@dataclass
class NavCandidate:
    index: int
    tag: str
    type: str
    text: str
    href: str = ""
    in_dialog: bool = False
    disabled: bool = False
    submits_form: bool = False
    context: str = ""


def candidate_from_scan(raw: dict) -> NavCandidate:
    return NavCandidate(
        index=int(raw.get("index", 0)), tag=str(raw.get("tag", "a")), type=str(raw.get("type") or ""),
        text=str(raw.get("text") or ""), href=str(raw.get("href") or ""),
        in_dialog=bool(raw.get("in_dialog")), disabled=bool(raw.get("disabled")),
        submits_form=bool(raw.get("submits_form")), context=str(raw.get("context") or ""),
    )


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


# ── What may be clicked ───────────────────────────────────────────────────────

# (pattern on normalised text, weight). A link must reach MIN_NAV_SCORE.
_INTENTS: tuple[tuple[str, int], ...] = (
    (r"\b(continue|proceed|enter|go) (with|to|into)\b.*\b(portal|site|website)\b", 6),
    (r"\b(register|registration|lodge|file|raise|make|launch|start|new)\b.*\b(complaint|complaints|grievance|grievances)\b", 6),
    (r"\b(complaint|grievance)\b.*\b(registration|form)\b", 5),
    (r"\breport\b.*\b(cyber ?crimes?|crimes?|fraud|incident|complaint|grievance)\b", 6),
    (r"\b(file|new) (an? )?(rti|application)\b|\bapply (now|online)\b|\bnew application\b", 4),
    (r"^(complaint|complaints|grievance|grievances)$", 3),
)
# Anything here is never clicked, whatever else it says.
_FORBIDDEN = re.compile(
    r"\b(login|log in|sign in|signin|sign up|signup|logout|log out|download|print|track|status|about|"
    r"contact|faq|help|feedback|language|skip|screen reader|sitemap|privacy|policy|terms|disclaimer|"
    r"archive|search|cancel|reset|clear|delete|pay|payment|submit|send|confirm|agree|accept|otp|captcha|"
    r"aadhaar|aadhar|password)\b"
)
# The page's own guard swallows clicks on buttons with these labels, because they
# may be the real final submit. Links with these words are navigation and are fine.
_FINAL_LOOKING = re.compile(r"\b(submit|register complaint|file complaint|lodge complaint|pay now)\b", re.IGNORECASE)
_SPECIFIC_WORDS = re.compile(r"\b(cyber|crime|crimes|fraud|report|grievance|grievances|incident)\b")
_DOWNLOAD_SUFFIXES =(".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".png")


def _site(host: str) -> str:
    labels = [part for part in (host or "").lower().split(".") if part]
    if len(labels) >= 3 and labels[-2] in {"gov", "nic", "co", "ac", "org", "res"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _same_site_link(href: str, page_host: str) -> bool:
    """No target, a hash, javascript: and same-site http(s) links are navigation inside the portal."""
    if not href:
        return True
    parsed = urlparse(href)
    if parsed.scheme == "javascript":
        return True
    if parsed.scheme not in {"", "http", "https"}:
        return False                     # mailto:, tel:, data:, ...
    if parsed.path.lower().endswith(_DOWNLOAD_SUFFIXES):
        return False
    if not parsed.scheme or not parsed.hostname:
        return True                      # relative link or in-page anchor: same portal
    return not page_host or _site(parsed.hostname) == _site(page_host)


def score_candidate(
    candidate: NavCandidate, hints: tuple[str, ...] = (), avoid: tuple[str, ...] = (),
) -> int:
    text = _norm(candidate.text)
    if not text or len(candidate.text) > _MAX_TEXT or candidate.disabled or candidate.submits_form:
        return 0
    if candidate.type in {"submit", "reset"} or _FORBIDDEN.search(text):
        return 0
    if candidate.tag != "a" and _FINAL_LOOKING.search(candidate.text):
        return 0                         # the guard would eat this click; it may be a real submit
    # A generic button ("Register a Complaint") is judged by the card it sits in. A
    # specific one ("Report a Crime") is judged by its own text only, because a menu
    # group can mention an unrelated entry next to it.
    specific = bool(_SPECIFIC_WORDS.search(text))
    surrounding = text if specific else _norm(f"{candidate.text} {candidate.context}")
    if any(re.search(pattern, surrounding) for pattern in avoid):
        return 0                         # e.g. the women/children card on a cyber-crime portal
    score = 0
    for pattern in hints:
        if re.search(pattern, text):
            score = max(score, 10)
    for pattern, weight in _INTENTS:
        if re.search(pattern, text):
            score = max(score, weight)
    if score and candidate.in_dialog:
        score += 2                       # an open pop-up blocks everything behind it
    return score


def choose_navigation(
    candidates: list[NavCandidate], *, page_host: str = "", hints: tuple[str, ...] = (),
    avoid: tuple[str, ...] = (), visited: frozenset[str] | set[str] = frozenset(),
) -> NavCandidate | None:
    """The single best link/button to click toward the form, or None."""
    best: tuple[int, int, NavCandidate] | None = None
    for order, candidate in enumerate(candidates):
        if _norm(candidate.text) in visited or not _same_site_link(candidate.href, page_host):
            continue
        score = score_candidate(candidate, hints, avoid)
        if score < MIN_NAV_SCORE:
            continue
        if best is None or (score, -order) > (best[0], -best[1]):
            best = (score, order, candidate)
    return best[2] if best else None


# ── Page driver ───────────────────────────────────────────────────────────────


@dataclass
class NavResult:
    clicked: bool = False
    text: str = ""
    reason: str = ""
    skipped: list[str] = field(default_factory=list)


async def settle_page(page, *, seconds: float = 0.8) -> None:
    """Give a client-rendered page (Angular, React) a moment to draw. Never raises."""
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001 - some pages never go idle; carry on
        pass
    if seconds:
        await asyncio.sleep(seconds)


async def navigate_once(
    page, *, hints: tuple[str, ...] = (), avoid: tuple[str, ...] = (), visited: set[str] | None = None,
    session_id: str = "-", settle_seconds: float = 1.0, scan_retries: int | None = None,
    retry_wait: float | None = None,
) -> NavResult:
    """Click the best navigation link on the page, if there is a clear one. Never raises.

    A single-page app often has nothing to click for the first second or two, so an
    empty result is retried `scan_retries` times after letting the page settle.
    """
    visited = visited if visited is not None else set()
    scan_retries = SCAN_RETRIES if scan_retries is None else scan_retries
    retry_wait = RETRY_WAIT_SECONDS if retry_wait is None else retry_wait
    host = urlparse(str(getattr(page, "url", "") or "")).hostname or ""
    candidates: list[NavCandidate] = []
    choice: NavCandidate | None = None
    for attempt in range(scan_retries + 1):
        try:
            raw = await page.evaluate(NAV_SCAN_JS)
        except Exception as exc:  # noqa: BLE001 - an unreadable page just means no click
            logger.info("session=%s event=nav_scan_failed type=%s", session_id, type(exc).__name__)
            return NavResult(reason="scan_failed")
        candidates = [candidate_from_scan(item) for item in raw or []]
        choice = choose_navigation(candidates, page_host=host, hints=hints, avoid=avoid, visited=visited)
        if choice is not None or attempt == scan_retries:
            break
        await settle_page(page, seconds=retry_wait)
    if choice is None:
        # Link labels are the portal's public wording, never case data; they show what
        # the page offered so the rules can be tuned from a real run.
        seen = [_norm(c.text)[:40] for c in candidates[:15]]
        logger.info("session=%s event=nav_no_candidate links_seen=%s first_links=%s",
                    session_id, len(candidates), seen)
        return NavResult(reason="no_candidate")
    visited.add(_norm(choice.text))
    try:
        await page.locator(f'[data-nyaya-nav="{choice.index}"]').first.click(timeout=5000)
    except Exception as exc:  # noqa: BLE001 - covered, detached or moved: leave it and report
        logger.info("session=%s event=nav_click_failed text=%r type=%s", session_id, choice.text, type(exc).__name__)
        return NavResult(text=choice.text, reason="click_failed")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001 - a same-page change has no load to wait for
        pass
    if settle_seconds:
        await asyncio.sleep(settle_seconds)
    logger.info("session=%s event=nav_clicked text=%r score=%s",
                session_id, choice.text, score_candidate(choice, hints, avoid))
    return NavResult(clicked=True, text=choice.text)
