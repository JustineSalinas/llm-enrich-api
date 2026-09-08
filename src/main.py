"""
The API. `python -m src.main` or `uvicorn src.main:app --reload` to run it.
"""

from dotenv import load_dotenv

load_dotenv()  # before anything reads os.environ, including route imports below

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.routes.enrich import router as enrich_router

app = FastAPI(title="LLM-backed API", version="1.0")


@app.exception_handler(RequestValidationError)
async def on_invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Turns FastAPI/Pydantic's default 422 into the 400-naming-the-field this
    assignment asks for. Fires before any route body runs, so a malformed
    request never reaches (and never pays for) a model call.
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = [str(p) for p in first.get("loc", []) if p != "body"]
    field = ".".join(loc) if loc else "(unknown)"
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_input", "field": field, "message": first.get("msg", "validation failed")},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(enrich_router)
