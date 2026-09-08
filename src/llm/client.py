"""
The one place that talks to the model. Everything the assignment calls
"the properties of an LLM call" lives here: an explicit timeout (never the
SDK's 10-minute default), retries on only the right errors with backoff +
jitter, and a structured cost-log line per call.

Route code and the repair loop (pipeline.py) never touch the openai client
directly -- they call complete() and get back either text or a typed error.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

import openai
from openai import OpenAI

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 2  # up to 3 attempts total
BACKOFF_BASE_SECONDS = 1.0
JITTER_SECONDS = 0.5

LOG_DIR = Path("logs")
COST_LOG = LOG_DIR / "cost.jsonl"

RETRYABLE_EXCEPTIONS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,  # 5xx
)
# Deliberately NOT retried: AuthenticationError (401), BadRequestError (400),
# PermissionDeniedError (403) -- a bad key or bad request is still bad on
# retry #2, and every pointless retry burns real quota.


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


@dataclass
class CompletionError:
    kind: str  # "timeout" | "provider_error"
    message: str


def _client() -> OpenAI:
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    return OpenAI(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        timeout=timeout,
        max_retries=0,  # we do our own retries below, deliberately, not the SDK's default 2
    )


def _sleep_for_attempt(attempt: int, retry_after_header: str | None) -> None:
    if retry_after_header:
        try:
            time.sleep(float(retry_after_header))
            return
        except ValueError:
            pass  # not a plain seconds value; fall through to our own backoff
    wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, JITTER_SECONDS)
    time.sleep(wait)


def complete(system_prompt: str, user_content: str, model: str | None = None) -> CompletionResult | CompletionError:
    """
    One chat completion call, with our retry policy applied. Returns either
    a CompletionResult (text + token usage + timing) or a CompletionError
    describing why every attempt failed.
    """
    client = _client()
    model = model or os.environ["LLM_MODEL"]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    last_error: Exception | None = None
    start = time.monotonic()

    for attempt in range(1, MAX_RETRIES + 2):  # 1 initial + MAX_RETRIES retries
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=0.2)
            duration_ms = int((time.monotonic() - start) * 1000)
            usage = resp.usage
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                duration_ms=duration_ms,
            )

        except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError) as exc:
            # Not retryable: a bad key or bad request is still bad in four seconds.
            return CompletionError(kind="provider_error", message=f"{type(exc).__name__}: {exc}")

        except RETRYABLE_EXCEPTIONS as exc:
            last_error = exc
            if attempt > MAX_RETRIES:
                break
            retry_after = None
            response = getattr(exc, "response", None)
            if response is not None:
                retry_after = response.headers.get("retry-after")
            _sleep_for_attempt(attempt, retry_after)
            continue

    duration_ms = int((time.monotonic() - start) * 1000)
    kind = "timeout" if isinstance(last_error, openai.APITimeoutError) else "provider_error"
    return CompletionError(kind=kind, message=f"failed after {MAX_RETRIES + 1} attempts: {last_error!r}")


def log_cost(*, prompt_version: str, model: str, input_tokens: int, output_tokens: int, duration_ms: int, repaired: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt_version": prompt_version,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "repaired": repaired,
    }
    with COST_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
