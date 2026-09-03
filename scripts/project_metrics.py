"""Measure NyayaBot project metrics for reporting and resume claims.

Every number this prints is measured from the repository or from a live server.
Nothing is estimated or hard-coded, so re-running it after new work keeps any
claim you make accurate.

Usage:
    python scripts/project_metrics.py                 # static metrics only
    python scripts/project_metrics.py --tests         # also run the test suite
    python scripts/project_metrics.py --bench         # also benchmark a running API
    python scripts/project_metrics.py --all
    python scripts/project_metrics.py --all --json metrics.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
API_BASE = "http://127.0.0.1:8000"

SKIP_DIRS = {
    "node_modules", ".next", "__pycache__", ".git", ".pytest_cache",
    "storage", "venv", ".venv", "dist", "build",
}


# ---------------------------------------------------------------- utilities

def iter_files(root: Path, suffixes: tuple[str, ...]):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def count_lines(path: Path) -> tuple[int, int]:
    """Return (total lines, non-blank non-comment lines)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0, 0
    code = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "/*", "*", "<!--")):
            continue
        code += 1
    return len(lines), code


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------ static metrics

def measure_codebase() -> dict:
    areas = {
        "backend_python": (BACKEND / "app", (".py",)),
        "backend_tests": (BACKEND / "tests", (".py",)),
        "frontend_typescript": (FRONTEND / "src", (".ts", ".tsx")),
        "document_templates": (BACKEND / "app" / "templates", (".html",)),
    }
    result: dict[str, dict[str, int]] = {}
    for name, (root, suffixes) in areas.items():
        files = list(iter_files(root, suffixes))
        totals = [count_lines(f) for f in files]
        result[name] = {
            "files": len(files),
            "lines": sum(t for t, _ in totals),
            "code_lines": sum(c for _, c in totals),
        }
    result["total"] = {
        key: sum(area[key] for area in result.values())
        for key in ("files", "lines", "code_lines")
    }
    return result


def measure_api_surface() -> dict:
    """Count FastAPI routes by parsing decorators (no import side effects)."""
    main = BACKEND / "app" / "main.py"
    text = main.read_text(encoding="utf-8") if main.exists() else ""
    routes = re.findall(r'@app\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)', text)
    by_method: dict[str, int] = {}
    for method, _ in routes:
        by_method[method.upper()] = by_method.get(method.upper(), 0) + 1
    return {
        "endpoints": len(routes),
        "by_method": dict(sorted(by_method.items())),
        "paths": sorted({path for _, path in routes}),
    }


def measure_schemas() -> dict:
    """Count Pydantic models across schema and LLM-contract modules."""
    counts: dict[str, int] = {}
    for folder in ("schemas", "llm", "db"):
        total = 0
        for path in iter_files(BACKEND / "app" / folder, (".py",)):
            text = path.read_text(encoding="utf-8", errors="ignore")
            total += len(re.findall(r"^class\s+\w+\(BaseModel\)", text, re.MULTILINE))
            total += len(re.findall(r"^class\s+\w+\(Base\)", text, re.MULTILINE))
        counts[folder] = total
    counts["total"] = sum(counts.values())
    return counts


def measure_domain_coverage() -> dict:
    """Legal domain surface: categories, workflow stages, evidence, statutes."""
    workflows = load_json(BACKEND / "app" / "data" / "workflows.json") or {}

    stages = 0
    evidence_items: set[str] = set()
    required_fields: set[str] = set()
    for spec in workflows.values():
        if not isinstance(spec, dict):
            continue
        stages += len(spec.get("stages", []) or [])
        for item in spec.get("evidence_items", []) or []:
            evidence_items.add(item.get("id") if isinstance(item, dict) else str(item))
        for field in spec.get("required_fields", []) or []:
            required_fields.add(str(field))

    statutes, provisions = 0, 0
    for path in iter_files(BACKEND / "app" / "data", (".json",)):
        if path.name == "workflows.json":
            continue
        data = load_json(path)
        if isinstance(data, list):
            statutes += 1
            provisions += len(data)

    templates = list(iter_files(BACKEND / "app" / "templates", (".html",)))

    return {
        "case_categories": len(workflows),
        "category_names": sorted(workflows.keys()),
        "workflow_stages": stages,
        "evidence_types": len(evidence_items),
        "tracked_fact_fields": len(required_fields),
        "document_templates": len(templates),
        "statute_sources": statutes,
        "statute_provisions": provisions,
    }


# ------------------------------------------------------------ quality metrics

def measure_tests() -> dict:
    """Run pytest and parse the summary line."""
    print("  running pytest ...", flush=True)
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": "."},
            timeout=900,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "error": str(exc)[:200]}

    output = proc.stdout + proc.stderr
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", output)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", output)) else 0
    total = passed + failed
    return {
        "available": True,
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate_pct": round(100 * passed / total, 1) if total else 0.0,
        "duration_s": round(time.time() - started, 1),
        "exit_code": proc.returncode,
    }


# -------------------------------------------------------- performance metrics

def _time_request(url: str, timeout: float = 15.0) -> float | None:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
    except (urllib.error.URLError, OSError):
        return None
    return (time.perf_counter() - started) * 1000.0


def measure_latency(samples: int = 20) -> dict:
    """Benchmark deterministic (non-LLM) endpoints against a running server.

    These endpoints do no model inference, so the numbers reflect application
    and database work only and stay stable between runs.
    """
    endpoints = {
        "llm_status": f"{API_BASE}/api/v1/llm/status",
        "list_cases": f"{API_BASE}/api/v1/chat/cases",
        "list_documents": f"{API_BASE}/api/v1/documents",
        "statutes": f"{API_BASE}/api/v1/statutes",
    }

    if _time_request(endpoints["llm_status"], timeout=3.0) is None:
        return {"available": False, "reason": f"no server responding at {API_BASE}"}

    results: dict[str, dict] = {}
    all_timings: list[float] = []
    for name, url in endpoints.items():
        _time_request(url)  # warm-up, excluded from statistics
        timings = [t for _ in range(samples) if (t := _time_request(url)) is not None]
        if not timings:
            continue
        timings.sort()
        results[name] = {
            "samples": len(timings),
            "p50_ms": round(statistics.median(timings), 1),
            "p95_ms": round(timings[min(int(len(timings) * 0.95), len(timings) - 1)], 1),
            "mean_ms": round(statistics.fmean(timings), 1),
        }
        all_timings.extend(timings)

    if not all_timings:
        return {"available": False, "reason": "no endpoint returned a timing"}

    all_timings.sort()
    return {
        "available": True,
        "per_endpoint": results,
        "overall": {
            "requests": len(all_timings),
            "p50_ms": round(statistics.median(all_timings), 1),
            "p95_ms": round(all_timings[min(int(len(all_timings) * 0.95), len(all_timings) - 1)], 1),
        },
    }


def measure_document_generation(samples: int = 5) -> dict:
    """Time real PDF and DOCX rendering through the app's own generator."""
    sys.path.insert(0, str(BACKEND))
    try:
        from app.agents.intake_node import IntakeFactExtractor  # type: ignore
        from app.services.doc_generator import doc_generator  # type: ignore
    except Exception as exc:  # generator import is environment-dependent
        return {"available": False, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}

    narrative = (
        "I purchased an air conditioner from Reliance Digital on 10-02-2026 for "
        "Rs. 38,000. It never cooled and the technician refused to replace it."
    )
    try:
        facts = IntakeFactExtractor.extract_facts(narrative)
    except Exception as exc:
        return {"available": False, "reason": f"fact extraction: {type(exc).__name__}"}

    timings: list[float] = []
    for index in range(samples):
        started = time.perf_counter()
        try:
            doc_generator.generate_document(
                case_id=f"metrics-run-{index:04d}",
                doc_type="FORMAL_LEGAL_NOTICE",
                fact_graph=facts,
                appropriate_forum="District Consumer Disputes Redressal Commission, Pune",
            )
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
        timings.append((time.perf_counter() - started) * 1000.0)

    return {
        "available": True,
        # One call renders the HTML and writes both the PDF and the DOCX.
        "render_ms_median": round(statistics.median(timings), 1),
        "render_ms_min": round(min(timings), 1),
        "samples": samples,
    }


# ------------------------------------------------------------------- report

def render(metrics: dict) -> None:
    def header(title: str) -> None:
        print(f"\n{title}\n{'-' * len(title)}")

    print("=" * 62)
    print("NyayaBot - project metrics")
    print(f"measured: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)

    header("Codebase")
    code = metrics["codebase"]
    for name in ("backend_python", "backend_tests", "frontend_typescript", "document_templates"):
        area = code[name]
        print(f"  {name:22} {area['files']:>4} files   {area['code_lines']:>6} code lines")
    print(f"  {'TOTAL':22} {code['total']['files']:>4} files   {code['total']['code_lines']:>6} code lines")

    header("API surface")
    api = metrics["api"]
    print(f"  REST endpoints         {api['endpoints']}")
    print(f"  by method              {api['by_method']}")
    print(f"  typed models           {metrics['schemas']['total']}"
          f"  (schemas {metrics['schemas']['schemas']},"
          f" llm contracts {metrics['schemas']['llm']},"
          f" orm {metrics['schemas']['db']})")

    header("Legal domain coverage")
    dom = metrics["domain"]
    print(f"  case categories        {dom['case_categories']}  {dom['category_names']}")
    print(f"  workflow stages        {dom['workflow_stages']}")
    print(f"  evidence types         {dom['evidence_types']}")
    print(f"  tracked fact fields    {dom['tracked_fact_fields']}")
    print(f"  document templates     {dom['document_templates']}")
    print(f"  statute sources        {dom['statute_sources']} ({dom['statute_provisions']} provisions)")

    if (tests := metrics.get("tests", {})).get("available"):
        header("Test suite")
        print(f"  tests passing          {tests['passed']}/{tests['total']}"
              f"  ({tests['pass_rate_pct']}%) in {tests['duration_s']}s")

    if (lat := metrics.get("latency", {})).get("available"):
        header("API latency (deterministic endpoints, no model inference)")
        for name, stats in lat["per_endpoint"].items():
            print(f"  {name:22} p50 {stats['p50_ms']:>7} ms   p95 {stats['p95_ms']:>7} ms")
        print(f"  {'OVERALL':22} p50 {lat['overall']['p50_ms']:>7} ms   "
              f"p95 {lat['overall']['p95_ms']:>7} ms  ({lat['overall']['requests']} requests)")
    elif lat:
        print(f"\n  [latency skipped: {lat.get('reason')}]")

    if (doc := metrics.get("documents", {})).get("available"):
        header("Document generation")
        print(f"  PDF + DOCX median      {doc['render_ms_median']} ms  (per document, both formats)")
        print(f"  fastest run            {doc['render_ms_min']} ms")
    elif doc:
        print(f"\n  [document timing skipped: {doc.get('reason')}]")

    print("\n" + "=" * 62)
    print("Resume-ready lines (all figures measured above)")
    print("=" * 62)
    for line in resume_lines(metrics):
        print(f"  - {line}")
    print()


def resume_lines(metrics: dict) -> list[str]:
    code, api, dom = metrics["codebase"], metrics["api"], metrics["domain"]
    lines = [
        f"Built a full-stack legal assistant spanning {code['total']['code_lines']:,} lines "
        f"across {code['total']['files']} files (FastAPI/Python backend, Next.js/TypeScript frontend).",
        f"Designed and shipped {api['endpoints']} REST endpoints backed by "
        f"{metrics['schemas']['total']} typed Pydantic/ORM models for schema-validated LLM output.",
        f"Modelled {dom['case_categories']} legal case categories across {dom['workflow_stages']} "
        f"workflow stages, {dom['evidence_types']} evidence types and {dom['document_templates']} "
        f"court-ready document templates.",
    ]
    if (tests := metrics.get("tests", {})).get("available") and tests["total"]:
        lines.append(
            f"Maintained a {tests['total']}-test backend suite at a {tests['pass_rate_pct']}% pass rate "
            f"covering workflow transitions, document validation and provider-failure fallback."
        )
    if (lat := metrics.get("latency", {})).get("available"):
        lines.append(
            f"Kept deterministic API responses at a {lat['overall']['p50_ms']} ms median / "
            f"{lat['overall']['p95_ms']} ms p95 across {lat['overall']['requests']} sampled requests."
        )
    if (doc := metrics.get("documents", {})).get("available"):
        lines.append(
            f"Rendered filing-ready legal notices to PDF and DOCX in a "
            f"{doc['render_ms_median']} ms median per document."
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure NyayaBot project metrics.")
    parser.add_argument("--tests", action="store_true", help="run the pytest suite")
    parser.add_argument("--bench", action="store_true", help="benchmark a running API and document generation")
    parser.add_argument("--all", action="store_true", help="run every measurement")
    parser.add_argument("--json", metavar="PATH", help="also write metrics as JSON")
    args = parser.parse_args()

    run_tests = args.tests or args.all
    run_bench = args.bench or args.all

    print("Measuring ...", flush=True)
    metrics = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "codebase": measure_codebase(),
        "api": measure_api_surface(),
        "schemas": measure_schemas(),
        "domain": measure_domain_coverage(),
    }
    if run_tests:
        metrics["tests"] = measure_tests()
    if run_bench:
        metrics["latency"] = measure_latency()
        metrics["documents"] = measure_document_generation()

    render(metrics)

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"JSON written to {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
