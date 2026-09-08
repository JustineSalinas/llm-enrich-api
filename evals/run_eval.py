"""
Runs evals/cases.json through the real /enrich endpoint (in-process, via
FastAPI's TestClient -- no separate server needed) and reports how many
matched on the key field (category). Requires a real LLM_API_KEY in .env
and LLM_STUB unset/0 -- this spends real model calls (8 of them, plus one
extra per repair).

Run: python evals/run_eval.py
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from fastapi.testclient import TestClient

from src.main import app

CASES_PATH = Path(__file__).parent / "cases.json"


def main():
    if os.environ.get("LLM_STUB", "0") == "1":
        print("LLM_STUB=1 is set -- this would only test the stub, not the model. Unset it and rerun.")
        sys.exit(1)

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    client = TestClient(app)

    matched = 0
    failures = []

    for case in cases:
        resp = client.post("/enrich", json=case["input"])
        if resp.status_code != 200:
            failures.append({"id": case["id"], "reason": f"HTTP {resp.status_code}: {resp.text}"})
            continue

        body = resp.json()
        got = body.get("category")
        expected = case["expected_category"]
        if got == expected:
            matched += 1
        else:
            failures.append({"id": case["id"], "reason": f"expected category={expected!r}, got={got!r}", "full_response": body})

    total = len(cases)
    print(f"\n=== Eval result: {matched}/{total} matched on category ===")
    print(f"Date: {date.today().isoformat()}  Prompt version: v1\n")

    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f['id']}: {f['reason']}")
    else:
        print("All cases matched.")

    print(f"\nRECORD THIS LINE IN README.md: {matched}/{total} on {date.today().isoformat()}, prompt v1")


if __name__ == "__main__":
    main()
