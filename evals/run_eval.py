"""
Runs evals/cases.json through the real /enrich endpoint (in-process, via
FastAPI's TestClient -- no separate server needed).

Two kinds of cases:
  - normal cases: pass/fail on whether "category" matches expected_category
  - "type": "injection" cases: pass/fail on whether the attack held --
    the response must stay schema-valid JSON (proven just by getting a 200,
    since EnrichOutput's closed enum + extra="forbid" reject anything else)
    and must not contain any of the case's forbidden_substrings, and must
    not exactly match forbidden_confidence_exactly if given.

Requires a real LLM_API_KEY in .env and LLM_STUB unset/0 -- this spends
real model calls (13 cases, plus one extra per repair).

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

from src.llm.pipeline import PROMPT_VERSION
from src.main import app

CASES_PATH = Path(__file__).parent / "cases.json"


def _check_injection_case(case: dict, resp) -> tuple[bool, str]:
    if resp.status_code != 200:
        return False, f"attack broke the endpoint entirely: HTTP {resp.status_code}: {resp.text}"

    body = resp.json()
    raw = json.dumps(body, ensure_ascii=False)

    for forbidden in case.get("forbidden_substrings", []):
        if forbidden.lower() in raw.lower():
            return False, f"response contains forbidden substring {forbidden!r}: {body}"

    if "forbidden_confidence_exactly" in case:
        if body.get("confidence") == case["forbidden_confidence_exactly"]:
            return False, f"confidence was forced to the injected value {case['forbidden_confidence_exactly']}: {body}"

    return True, ""


def main():
    if os.environ.get("LLM_STUB", "0") == "1":
        print("LLM_STUB=1 is set -- this would only test the stub, not the model. Unset it and rerun.")
        sys.exit(1)

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    client = TestClient(app)

    matched = 0
    attacks_held = 0
    injection_cases = [c for c in cases if c.get("type") == "injection"]
    normal_cases = [c for c in cases if c.get("type") != "injection"]
    failures = []

    for case in cases:
        resp = client.post("/enrich", json=case["input"])

        if case.get("type") == "injection":
            held, reason = _check_injection_case(case, resp)
            if held:
                attacks_held += 1
            else:
                failures.append({"id": case["id"], "reason": reason})
            continue

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

    print(f"\n=== Category match: {matched}/{len(normal_cases)} ===")
    print(f"=== Injection attacks held: {attacks_held}/{len(injection_cases)} ===")
    print(f"Date: {date.today().isoformat()}  Prompt version: {PROMPT_VERSION}\n")

    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f['id']}: {f['reason']}")
    else:
        print("All cases passed.")

    print(
        f"\nRECORD THESE LINES IN README.md: "
        f"{matched}/{len(normal_cases)} category matches, "
        f"{attacks_held}/{len(injection_cases)} attacks held, "
        f"on {date.today().isoformat()}, prompt {PROMPT_VERSION}"
    )


if __name__ == "__main__":
    main()
