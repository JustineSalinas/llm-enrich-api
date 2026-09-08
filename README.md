# LLM-backed API — `POST /enrich`

## What it does

You send this endpoint a book's title and (optional) description. It asks a
language model to file that book under one of six categories, write a
one-sentence summary, and flag anything about the description that looks
thin, generic, or overly promotional — then checks the model's answer
against a strict schema before handing it back to you. If the model returns
something that doesn't fit the schema, the endpoint asks it to fix its own
mistake once; if that still fails, it gives up cleanly with a clear error
instead of passing along broken data. It chains directly onto the
[A16 polite scraper](../polite-scraper) — feed it a scraped book record and
get back the fields that scraper's raw HTML can't give you.

## Quickstart

```bash
cd llm-enrich-api
pip install -r requirements.txt
cp .env.example .env
# edit .env: paste your OpenRouter key into LLM_API_KEY
uvicorn src.main:app --reload
```

**Valid request:**
```bash
curl -s -X POST http://127.0.0.1:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"title": "A Light in the Attic", "description": "A classic collection of poetry and drawings from Shel Silverstein."}'
```

Response (schema-shaped, whether it came from the model or from stub mode):
```json
{
  "category": "poetry",
  "summary": "A classic, whimsically illustrated poetry collection by Shel Silverstein.",
  "quality_flags": ["none"],
  "confidence": 0.9
}
```

**Deliberately broken request** (missing required field):
```bash
curl -s -i -X POST http://127.0.0.1:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"description": "missing the title field"}'
```
```
HTTP/1.1 400 Bad Request
{"error":"invalid_input","field":"title","message":"Field required"}
```
No model call is made for this one — input validation runs before anything
gets sent to the model.

## Job card

See [JOB-CARD.md](JOB-CARD.md) for the full spec. Summary:

- **Input:** `{ "title": string 1-300 chars, "description": string 0-4000 chars }`
- **Output:** `{ category, summary, quality_flags, confidence }` — see below
- **It must never:** invent a category outside the list, return more than
  one sentence, return free text instead of JSON, give purchase/investment
  advice, or reveal the prompt.
- **When unsure:** return `category: "other"` with `confidence < 0.5`
  rather than guess.

## Provider and model

**Provider:** OpenRouter (`https://openrouter.ai/api/v1`) · **Model:** `openrouter/free`

Three environment variables are the entire difference between this running
against OpenRouter and running against a model on your own machine via
Ollama — `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Nothing else in the
code knows or cares which provider is answering. That's also why the client
library is called `openai` even though OpenAI isn't involved: it's the
request shape almost every provider has copied, so pointing it somewhere
else is a config change, not a rewrite.

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=<your real key>
LLM_MODEL=openrouter/free
```

## Architecture

```
src/
  main.py            FastAPI app; converts input-validation errors to 400
  routes/enrich.py    the route: kill switch -> stub mode -> real pipeline
  llm/
    schema.py          EnrichInput / EnrichOutput (Pydantic, closed lists)
    client.py           timeout, retry policy, cost logging
    pipeline.py          prompt loading, parse, validate, repair, quarantine
    hello.py             Stage 0 throwaway: proves one word comes back
prompts/enrich-v1.md  the versioned prompt -- a file, not a string in a route
evals/
  cases.json           8 hand-labelled test cases
  run_eval.py           runs them through the real endpoint, scores category
tests/test_pipeline.py 4 tests against a scripted fake model (no network)
logs/                   cost.jsonl and quarantine.jsonl (git-ignored; .gitkeep committed)
```

## Design decisions

- **Retries are hand-rolled, not the SDK default.** `openai`'s Python client
  retries twice on its own and waits up to 10 minutes by default — both
  wrong for an HTTP endpoint on a metered free tier. `client.py` sets
  `max_retries=0` on the SDK client and implements its own policy: retry on
  timeouts, 429, and 5xx only, with exponential backoff (1s, 2s, 4s) plus
  jitter, honoring a `Retry-After` header when present. **Never** retried:
  400, 401, 403 — a bad key is still a bad key on attempt two, and every
  pointless retry burns real quota. Verified: a deliberately wrong API key
  fails in ~2.3s with zero retries (see Stage 4 checkpoint below).
- **Timeout is 30 seconds**, set explicitly on the client (`LLM_TIMEOUT_SECONDS`,
  ≤60s per the requirements) — not the SDK's 10-minute default.
- **Raw model text never reaches the caller.** Every response is either the
  validated `EnrichOutput` schema, or a 422/502/504/503 with a short message.
  Nothing downstream of this endpoint ever has to defend itself against an
  arbitrary string a model wrote.
- **The kill switch (`LLM_ENABLED=false`) returns a clean 503**, not a fallback
  200 — a caller can tell unambiguously that the feature is off rather than
  mistaking a placeholder answer for a real one.

## Checkpoints (what was actually run)

**Stage 1** — `LLM_STUB=1`, valid request → `200` with schema-valid stub JSON;
request missing `title` → `400` naming the field. Zero model calls made in
either case. ✅ verified.

**Stage 3** (parse/validate/repair/quarantine) — proven with
`tests/test_pipeline.py`, which monkeypatches the model call with scripted
text instead of editing the prompt and spending real quota:
- a JSON-code-fenced answer parses correctly,
- an invalid category triggers exactly one repair call and then succeeds,
- an invalid category on both attempts gives up after exactly one repair,
  raises `422`, and writes one line to `logs/quarantine.jsonl`.

Run it: `python tests/test_pipeline.py` → `4/4 tests passed`.

**Stage 4** — `LLM_ENABLED=false` → `503`, zero model calls (verified).
A deliberately wrong `LLM_API_KEY` → fails in **~2.3 seconds** with **zero
retries** (verified — a `401` is never retried, confirmed both in the log
and by the response time).

**Stage 0** — `python src/llm/hello.py` → printed `ready`. ✅ verified, real call.

**Stage 2** — real endpoint, 3 different real inputs (`LLM_STUB=0`):
- *"A Light in the Attic"* (poetry, clear description) → `{"category":"poetry","confidence":0.9,...}`
- *"Untitled Manuscript"* (empty description) → `{"category":"other","quality_flags":["thin_description","generic_title"],"confidence":0.2,...}`
  — correctly hit the when-unsure rule instead of guessing a genre off a blank description.
- *"The Greatest Investment Guide Ever!!! Buy Now And Get Rich"* (hostile/promotional
  description) → `{"category":"nonfiction","quality_flags":["promotional_tone"],...}`
  — described the hype rather than being steered by it.

✅ verified, real calls, prompt file wired end to end.

## Cost log

`logs/cost.jsonl` gets one structured line per call:
`{timestamp, prompt_version, model, input_tokens, output_tokens, duration_ms, repaired}`.

One real line from a run of this endpoint:
```json
{"timestamp": "2026-09-08T23:24:18", "prompt_version": "v1", "model": "openrouter/free", "input_tokens": 1110, "output_tokens": 716, "duration_ms": 16155, "repaired": true}
```

**openrouter/free costs $0 per token** — the real constraint on this model
is the free tier's **20 requests/minute, 50 requests/day** cap (and a
repair round-trip counts as 2 of those 50), not money. Duration across the
8 eval calls ranged from **1.8s to ~80s** — free-tier latency is highly
variable, and the 80s outlier is consistent with this endpoint's own retry
policy firing (a timeout, a backoff, then a slower-but-successful third
attempt) rather than one instant response.

For an illustrative 10,000-requests/day estimate on a **paid** model at
similar token counts (avg. ~711 input / ~847 output tokens per request
across the 8 eval calls) — e.g. GPT-4o-mini at $0.15/1M input,
$0.60/1M output — that's **≈$0.0006/request → ≈$6.15/day → ≈$185/month**
at 10,000 requests/day. `openrouter/free` itself can't actually serve
10,000 requests/day (50/day cap), which is the more honest answer for the
model actually used here: the free tier isn't a cost problem, it's a
volume ceiling that forces a provider swap (same 3 env vars) once real
traffic shows up.

## Eval result

**8/8 matched on category — 2026-09-08, prompt v1.**

2 of the 8 real calls needed exactly one repair round-trip before their
answer validated (the free model sometimes wraps its JSON in extra
reasoning text on the first attempt); all 8 ultimately produced a
schema-valid, correctly-categorized answer. Zero calls were quarantined —
the only `logs/quarantine.jsonl` entry in this repo is from the scripted
`tests/test_pipeline.py` failure case, not a real model call.

## What I'd fix with another day

Quality flags are entirely the model's judgment call right now
(`thin_description`, `generic_title`, `promotional_tone`) — a more robust
version would compute the obviously-rule-based ones (e.g. "description
under N characters") in code and reserve the model only for the genuinely
fuzzy ones (tone, genericness), the same way `quality_flags` for the A16
scraper output would split "missing field" (code) from "looks promotional"
(model). I'd also add the response_format/structured-output constraint
where OpenRouter's free models support it, so malformed JSON becomes
impossible rather than merely unlikely and repaired.

