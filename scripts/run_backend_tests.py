#!/usr/bin/env python
"""Repeatable backend full-test entry (P2-2).

Why this script exists
----------------------
`python -m unittest discover` inside a single process is order-dependent:
a test module that stubs a public package name in ``sys.modules`` (e.g.
``server``, ``server.utils``) can break every later module that needs the
real package. The review required a repeatable entry point plus an accurate
product-failure / env-missing / skip categorisation.

This runner executes *each test module in its own subprocess*, so a module
can never poison another module's import state regardless of discovery order.
Per-module output is parsed and grouped into:

- PASS      -- module ran with zero failures/errors
- FAIL      -- assertions failed (real product bugs, need investigation)
- ERROR     -- an exception escaped; sub-classified as:
    * env-missing : external dependency unavailable on this host
                    (Milvus / Neo4j / MySQL / missing optional module)
    * product     : anything else -- a real defect surfaced by the test
- SKIP      -- tests that skipped (platform limits, missing env opt-in)

Usage
-----
    python scripts/run_backend_tests.py [--pattern test_*.py] [--quiet]
    python scripts/run_backend_tests.py --baseline baseline.json --report report.json

Exit code is 0 when there are no product failures/errors; 1 otherwise.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"

# Error text markers that identify "the environment is missing X" rather than
# "the product code is wrong". Order matters: check Milvus markers first.
_ENV_MISSING_MARKERS = (
    ("Failed to connect to Milvus", "milvus"),
    ("Fail connecting to server on milvus", "milvus"),
    ("MilvusException", "milvus"),
    ("No module named 'pymilvus'", "milvus"),
    ("NEO4J_URI not set", "neo4j"),
    ("Failed to connect to Neo4j", "neo4j"),
    ("No module named 'neo4j'", "neo4j"),
    ("MySQL server", "mysql"),
    ("Can't connect to MySQL", "mysql"),
    ("No module named 'graphrag_api'", "graphrag_api"),
)


def _categorise_error(text: str) -> str:
    """Return 'env-missing:<label>' or 'product' for an error traceback."""
    for marker, label in _ENV_MISSING_MARKERS:
        if marker in text:
            return f"env-missing:{label}"
    return "product"


def _parse_summary(output: str) -> tuple[str, dict]:
    """Return (outcome, {testsRun, failures, errors, skips}) from unittest -v text."""
    counts = {"testsRun": 0, "failures": 0, "errors": 0, "skips": 0}
    outcome = "PASS"

    ran_match = re.search(r"Ran (\d+) tests?", output)
    if ran_match:
        counts["testsRun"] = int(ran_match.group(1))
    for line in output.splitlines():
        if line.strip().endswith("... skipped") or " ... skipped " in line:
            counts["skips"] += 1

    failed_match = re.search(
        r"FAILED \((.*?)\)", output
    )
    if failed_match:
        outcome = "FAIL"
        summary = failed_match.group(1)
        m = re.search(r"failures=(\d+)", summary)
        if m:
            counts["failures"] = int(m.group(1))
        m = re.search(r"errors=(\d+)", summary)
        if m:
            counts["errors"] = int(m.group(1))
    elif re.search(r"^OK", output, re.MULTILINE):
        outcome = "PASS"

    # An unexpected crash (import error, subprocess failure) shows as 1 error
    # but "Ran 0 tests" -- keep it visible as an ERROR module.
    if counts["testsRun"] == 0 and counts["errors"] == 0 and outcome == "PASS":
        outcome = "ERROR"
    return outcome, counts


def _run_module(name: str) -> dict:
    """Run one test module in a subprocess; return a result dict.

    ``TEST_DIR`` is added to PYTHONPATH so modules that import sibling test
    files by bare name (e.g. test_local_feature_access imports
    ``test_role_routes``) resolve correctly in the isolated subprocess.
    """
    cmd = [sys.executable, "-m", "unittest", f"test.{name}", "-v"]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(TEST_DIR) + (os.pathsep + existing if existing else "")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    combined = proc.stdout + "\n" + proc.stderr
    outcome, counts = _parse_summary(combined)

    classification = None
    if outcome == "ERROR":
        classification = _categorise_error(combined)
    elif outcome == "FAIL":
        classification = _categorise_error(combined)
        if not classification.startswith("env-missing:"):
            classification = "product"
        else:
            # An env-driven error reported as a "failure" -- re-label as ERROR.
            outcome = "ERROR"

    return {
        "module": name,
        "outcome": outcome,
        "classification": classification,
        "testsRun": counts["testsRun"],
        "failures": counts["failures"],
        "errors": counts["errors"],
        "skips": counts["skips"],
        "returncode": proc.returncode,
        "errorText": combined[-2000:] if outcome in ("ERROR", "FAIL") else "",
    }


def _discover_modules(pattern: str) -> list[str]:
    files = sorted(glob.glob(str(TEST_DIR / pattern)))
    return [
        os.path.basename(path)[:-3]
        for path in files
        if not os.path.basename(path).startswith("__")
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pattern", default="test_*.py")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--baseline", help="path to a previous results.json to diff against")
    ap.add_argument("--report", help="write a JSON report to this path")
    args = ap.parse_args()

    modules = _discover_modules(args.pattern)
    results = []
    t0 = time.time()

    for i, name in enumerate(modules, 1):
        if not args.quiet:
            print(f"[{i}/{len(modules)}] {name} ...", flush=True)
        res = _run_module(name)
        if not args.quiet:
            tag = res["outcome"]
            if res["classification"]:
                tag += f" ({res['classification']})"
            print(f"    -> {tag}  (run={res['testsRun']} fail={res['failures']} "
                  f"err={res['errors']} skip={res['skips']})", flush=True)
        results.append(res)

    elapsed = time.time() - t0

    total_run = sum(r["testsRun"] for r in results)
    env_error_modules = [
        r for r in results
        if r["outcome"] == "ERROR"
        and r["classification"] and r["classification"].startswith("env-missing:")
    ]
    product_modules = [
        r for r in results
        if r["outcome"] in ("FAIL", "ERROR") and r not in env_error_modules
    ]

    summary = {
        "modules": len(modules),
        "testsRun": total_run,
        "envErrorModules": len(env_error_modules),
        "productFailModules": len(product_modules),
        "envErrors": {r["module"]: r["classification"] for r in env_error_modules},
        "productFails": [r["module"] for r in product_modules],
        "elapsedSeconds": round(elapsed, 1),
        "results": results,
    }

    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        summary["baseline"] = {
            "testsRun": baseline.get("testsRun"),
            "productFailModules": baseline.get("productFailModules"),
            "envErrors": baseline.get("envErrors"),
        }

    report_path = Path(args.report) if args.report else (ROOT / "saves" / "backend_test_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport written to {report_path}")

    print("\n=================== SUMMARY ===================")
    print(f"modules run        : {len(modules)}")
    print(f"tests executed     : {total_run}")
    print(f"env-missing ERROR  : {len(env_error_modules)} module(s)")
    for r in env_error_modules:
        print(f"    {r['module']}: {r['classification']}")
    print(f"product FAIL/ERROR : {len(product_modules)} module(s)")
    for r in product_modules:
        print(f"    {r['module']}")
    if args.baseline:
        b = summary["baseline"]
        print("\nvs baseline:")
        print(f"    tests   {b['testsRun']} -> {total_run}")
        print(f"    product {b.get('productFailModules', 0)} -> {len(product_modules)}")
    print(f"elapsed            : {elapsed:.1f}s")
    print("===============================================")

    return 0 if len(product_modules) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
