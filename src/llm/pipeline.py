"""
Stage 2/3: build the prompt, call the model, parse + validate the answer,
repair once if it failed, then quarantine and give up cleanly if it still
doesn't fit. Nothing here ever returns raw model text to the caller and
nothing here ever crashes the process -- a bad model answer becomes a 422
or a 504, never an exception that reaches the client uncaught.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from src.llm.client import CompletionError, complete, log_cost
from src.llm.schema import EnrichInput, EnrichOutput

PROMPT_VERSION = "v1"
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / f"enrich-{PROMPT_VERSION}.md"

LOG_DIR = Path("logs")
QUARANTINE_LOG = LOG_DIR / "quarantine.jsonl"

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json(raw_text: str) -> dict | None:
    """
    Models like to wrap JSON in a code fence or add chatty text around it.
    Strip fences, grab the first {...} block, and parse it. Returns None
    (never raises) if nothing parseable is found.
    """
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _quarantine(*, input_data: dict, raw_output: str, error: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt_version": PROMPT_VERSION,
        "input": input_data,
        "raw_output": raw_output,
        "error": error,
    }
    with QUARANTINE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


async def run_enrich_pipeline(payload: EnrichInput) -> EnrichOutput:
    system_prompt = _load_system_prompt()
    user_content = json.dumps(payload.model_dump(), ensure_ascii=False)
    model = os.environ["LLM_MODEL"]

    result = complete(system_prompt, user_content, model=model)
    if isinstance(result, CompletionError):
        _raise_for_completion_error(result)

    parsed_dict = _extract_json(result.text)
    validated, validation_error = _validate(parsed_dict)

    if validated is not None:
        log_cost(
            prompt_version=PROMPT_VERSION,
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=result.duration_ms,
            repaired=False,
        )
        return validated

    # --- Repair retry: exactly one extra call, with the broken answer and the exact error. ---
    repair_message = (
        f"{user_content}\n\n"
        f"Your previous answer was rejected for this reason: {validation_error}\n"
        f"Your previous answer was:\n{result.text}\n"
        f"Return only corrected JSON matching the schema."
    )
    repair_result = complete(system_prompt, repair_message, model=model)
    if isinstance(repair_result, CompletionError):
        _raise_for_completion_error(repair_result)

    repaired_dict = _extract_json(repair_result.text)
    repaired_validated, repaired_error = _validate(repaired_dict)

    total_duration_ms = result.duration_ms + repair_result.duration_ms
    total_input_tokens = result.input_tokens + repair_result.input_tokens
    total_output_tokens = result.output_tokens + repair_result.output_tokens

    if repaired_validated is not None:
        log_cost(
            prompt_version=PROMPT_VERSION,
            model=model,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            duration_ms=total_duration_ms,
            repaired=True,
        )
        return repaired_validated

    # --- Give up cleanly: quarantine, log the cost of the wasted attempt, 422. ---
    log_cost(
        prompt_version=PROMPT_VERSION,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        duration_ms=total_duration_ms,
        repaired=True,
    )
    _quarantine(input_data=payload.model_dump(), raw_output=repair_result.text, error=repaired_error or "unknown validation error")
    raise HTTPException(status_code=422, detail="Model could not produce a valid response after one repair attempt.")


def _validate(data: dict | None) -> tuple[EnrichOutput | None, str | None]:
    if data is None:
        return None, "response was not parseable JSON"
    try:
        return EnrichOutput.model_validate(data), None
    except ValidationError as exc:
        return None, str(exc)


def _raise_for_completion_error(error: CompletionError) -> None:
    if error.kind == "timeout":
        raise HTTPException(status_code=504, detail=f"Model call timed out: {error.message}")
    raise HTTPException(status_code=502, detail=f"Model provider error: {error.message}")
