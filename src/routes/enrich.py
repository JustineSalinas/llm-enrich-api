"""
POST /enrich

Stage 1: route, input validation (via EnrichInput), output schema (via
EnrichOutput as response_model), and stub mode. The model call itself
(src/llm/pipeline.py) is wired in during Stage 2/3 -- this file stays the
same shape throughout, which is the point of writing the contract first.
"""

import os

from fastapi import APIRouter, HTTPException

from src.llm.schema import Category, EnrichInput, EnrichOutput, QualityFlag

router = APIRouter()

STUB_RESPONSE = EnrichOutput(
    category=Category.other,
    summary="Stub response: no model was called (LLM_STUB=1).",
    quality_flags=[QualityFlag.none],
    confidence=0.0,
)


@router.post("/enrich", response_model=EnrichOutput)
async def enrich(payload: EnrichInput) -> EnrichOutput:
    if os.environ.get("LLM_ENABLED", "true").strip().lower() == "false":
        raise HTTPException(status_code=503, detail="LLM disabled (LLM_ENABLED=false); model call skipped.")

    if os.environ.get("LLM_STUB", "0").strip() == "1":
        return STUB_RESPONSE

    from src.llm.pipeline import run_enrich_pipeline  # local import: keeps stub mode import-light

    return await run_enrich_pipeline(payload)
