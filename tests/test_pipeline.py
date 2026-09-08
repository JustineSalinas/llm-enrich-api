"""
Proves the parse -> validate -> repair-once -> quarantine path without
spending a single real model call: src.llm.client.complete is monkeypatched
to return scripted text, standing in for the model.

Run: python -m pytest tests/test_pipeline.py -v
(or python tests/test_pipeline.py to run without pytest)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("LLM_MODEL", "openrouter/free")
os.environ.setdefault("LLM_BASE_URL", "https://openrouter.ai/api/v1")
os.environ.setdefault("LLM_API_KEY", "unused-in-tests")

from src.llm.client import CompletionResult
from src.llm.pipeline import QUARANTINE_LOG, run_enrich_pipeline
from src.llm.schema import EnrichInput


def _fake_result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=10, output_tokens=10, duration_ms=5)


def test_happy_path_first_try():
    good_json = json.dumps({"category": "poetry", "summary": "A poetry book.", "quality_flags": ["none"], "confidence": 0.9})
    with patch("src.llm.pipeline.complete", return_value=_fake_result(good_json)) as mock_complete:
        result = asyncio.run(run_enrich_pipeline(EnrichInput(title="A Light in the Attic", description="Poetry.")))
    assert mock_complete.call_count == 1, "a valid first answer must not trigger a repair call"
    assert result.category.value == "poetry"


def test_code_fence_is_stripped():
    fenced = "```json\n" + json.dumps({"category": "fiction", "summary": "A novel.", "quality_flags": [], "confidence": 0.7}) + "\n```"
    with patch("src.llm.pipeline.complete", return_value=_fake_result(fenced)):
        result = asyncio.run(run_enrich_pipeline(EnrichInput(title="Some Novel", description="A story.")))
    assert result.category.value == "fiction"


def test_invalid_category_triggers_repair_then_succeeds():
    bad = json.dumps({"category": "not-a-real-category", "summary": "x", "quality_flags": [], "confidence": 0.5})
    good = json.dumps({"category": "other", "summary": "Fixed answer.", "quality_flags": ["none"], "confidence": 0.3})
    with patch("src.llm.pipeline.complete", side_effect=[_fake_result(bad), _fake_result(good)]) as mock_complete:
        result = asyncio.run(run_enrich_pipeline(EnrichInput(title="Mystery Book", description="Unclear genre.")))
    assert mock_complete.call_count == 2, "an invalid first answer must trigger exactly one repair call"
    assert result.category.value == "other"


def test_invalid_category_twice_gives_up_and_quarantines():
    bad = json.dumps({"category": "not-a-real-category", "summary": "x", "quality_flags": [], "confidence": 0.5})
    before_lines = QUARANTINE_LOG.read_text(encoding="utf-8").splitlines() if QUARANTINE_LOG.exists() else []

    from fastapi import HTTPException

    with patch("src.llm.pipeline.complete", side_effect=[_fake_result(bad), _fake_result(bad)]) as mock_complete:
        try:
            asyncio.run(run_enrich_pipeline(EnrichInput(title="Mystery Book", description="Still unclear.")))
            raised = None
        except HTTPException as exc:
            raised = exc

    assert mock_complete.call_count == 2, "must give up after exactly one repair attempt, not loop forever"
    assert raised is not None and raised.status_code == 422

    after_lines = QUARANTINE_LOG.read_text(encoding="utf-8").splitlines()
    assert len(after_lines) == len(before_lines) + 1, "a permanently-bad answer must add exactly one quarantine line"
    logged = json.loads(after_lines[-1])
    assert logged["error"]


if __name__ == "__main__":
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    raise SystemExit(1 if failed else 0)
