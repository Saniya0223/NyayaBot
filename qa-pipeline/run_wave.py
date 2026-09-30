"""Shared NyayaBot QA harness.

Usage: python run_wave.py <wave-number>
Reads  wave-N/tests.json, writes wave-N/transcripts.json

Drives the live API exactly as a real client would: one authenticated session,
one case per scenario, turns replayed in order so the bot keeps its memory.
"""
import http.cookiejar, json, sys, time, urllib.error, urllib.request
from pathlib import Path

import os
BASE = os.getenv("NYAYA_API", "http://127.0.0.1:8000") + "/api/v1"
ROOT = Path(__file__).parent
QA_PASSWORD = "NyayaQA!2026pipeline"
TURN_PAUSE = float(os.getenv("NYAYA_TURN_PAUSE", "5"))   # stay under Groq TPM
DEGRADED_PAUSE = float(os.getenv("NYAYA_DEGRADED_PAUSE", "45"))
DEGRADED_MARK = "temporarily unavailable"

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def call(path, payload=None, method=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data else "GET"),
    )
    try:
        with _opener.open(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:500]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def authenticate(wave):
    """Fresh isolated user per wave so scenarios never cross-contaminate."""
    email = f"qa.wave{wave}.{int(time.time())}@nyayabot-qa.example.com"
    status, body = call("/auth/signup", {
        "full_name": f"QA Wave {wave}", "email": email, "password": QA_PASSWORD,
    })
    if status not in (200, 201):
        raise SystemExit(f"signup failed: {status} {body}")
    return email


def digest(profile):
    """The deterministic state we grade against."""
    if not isinstance(profile, dict):
        return None
    keys = ("title", "category", "category_display_name", "issue_type",
            "current_stage_key", "current_stage_label", "readiness",
            "user_city", "user_state", "disputed_amount", "opposite_party_name",
            "risk_level", "safety_notice",
            # Added after cycle 4. Wave 1 graded M8 ("no-corpus is never
            # disclosed") against a top-level `sources` key that the API has
            # never had, so every turn recorded null and the finding was
            # unfalsifiable. The real provenance lives here.
            "legal_sources", "legal_sources_status")
    return {k: profile.get(k) for k in keys if k in profile}


def run_case(case):
    case_id, turns = None, []
    for i, msg in enumerate(case["turns"], 1):
        t0 = time.time()
        status, body = call("/chat/message", {
            "message": msg, "case_id": case_id, "history": [],
        })
        elapsed = round(time.time() - t0, 2)
        if status != 200:
            turns.append({"turn": i, "user": msg, "http_status": status,
                          "error": body, "seconds": elapsed})
            break
        profile = body.get("case_profile") or body.get("profile") or {}
        case_id = (profile.get("case_id") if isinstance(profile, dict) else None) or body.get("case_id") or case_id
        msgs = body.get("messages") or []
        reply = body.get("reply") or body.get("reply_text") or ""
        if not reply and msgs:
            bot = [m for m in msgs if isinstance(m, dict) and m.get("sender") == "bot"]
            reply = bot[-1].get("text", "") if bot else ""
        # `llm_mode` is the real field on ChatTurnResponse (schemas/chat.py:153).
        # `mode` / `response_mode` / `tag` do not exist and always read null.
        llm_mode = body.get("llm_mode")
        degraded = (DEGRADED_MARK in reply) or (llm_mode == "limited_demo")
        turns.append({
            "turn": i, "user": msg, "seconds": elapsed,
            "degraded_quota": degraded,
            "mode": llm_mode,
            "llm_model": body.get("llm_model"),
            "reply": reply,
            "quick_replies": [m.get("quick_replies") for m in msgs if isinstance(m, dict) and m.get("quick_replies")],
            "suggested_action": [m.get("suggested_action") for m in msgs if isinstance(m, dict) and m.get("suggested_action")],
            "profile": digest(profile),
            "next_action": body.get("next_action") or (profile.get("next_action_plan") if isinstance(profile, dict) else None),
            # Provenance is on the profile, not the envelope. Split so a wave can
            # tell "no provision was retrieved" from "no portal link was offered":
            # only the act+section entries are verified statute.
            "sources": (profile.get("legal_sources") if isinstance(profile, dict) else None),
            "verified_provisions": [
                s for s in ((profile.get("legal_sources") or []) if isinstance(profile, dict) else [])
                if isinstance(s, dict) and s.get("act") and s.get("section")
            ],
        })
        time.sleep(DEGRADED_PAUSE if degraded else TURN_PAUSE)
    return {"id": case["id"], "title": case.get("title"), "domain": case.get("domain"),
            "difficulty": case.get("difficulty"), "intent": case.get("intent"),
            "watch_for": case.get("watch_for"), "case_id": case_id, "turns": turns}


def main():
    wave = sys.argv[1] if len(sys.argv) > 1 else "1"
    wdir = ROOT / f"wave-{wave}"
    tests = json.loads((wdir / "tests.json").read_text(encoding="utf-8"))
    email = authenticate(wave)
    print(f"[auth] {email}", flush=True)

    results, t0 = [], time.time()
    for case in tests["cases"]:
        print(f"[run ] {case['id']} ({case.get('domain')}) ...", end="", flush=True)
        r = run_case(case)
        bad = [t for t in r["turns"] if t.get("error") or t.get("degraded_quota")]
        print(f" {len(r['turns'])} turns{' !! ' + str(len(bad)) + ' degraded' if bad else ''}", flush=True)
        results.append(r)

    out = {"wave": wave, "qa_user": email,
           "elapsed_seconds": round(time.time() - t0, 1), "results": results}
    (wdir / "transcripts.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[done] {len(results)} cases in {out['elapsed_seconds']}s -> {wdir / 'transcripts.json'}")


if __name__ == "__main__":
    main()
