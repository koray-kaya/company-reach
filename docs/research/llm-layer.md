# LLM layer — research note

Date: 2026-09-19. Scope: the five LLM steps of company-reach (selection prompt,
purpose-text scoring, website choice, page choice + extraction, invitation
draft). Prices and model IDs below are Anthropic first-party API rates as of
this date; check the pricing page before quoting them elsewhere.

## 1. Recommendation in one paragraph

v1 is **Anthropic-only, through the official `anthropic` SDK, with one small
internal function** (`llm.ask(prompt, variables, output_model, model)`) that
every step calls. Structured output comes from `client.messages.parse()` with
a Pydantic model (wire parameter `output_config.format`, no beta header).
Prompts are Markdown files under `prompts/` with a version header, rendered
with `string.Template`. Every output row records `prompt@version`, a short
hash of the prompt file, the model ID and token usage. A 30-text + 15-site
golden set under `data/golden/` and one pytest module measure prompt changes.
Scoring 4,000 purpose texts costs about $1 on the cheapest model and $2–6 on
the mid/top models; a full run (scoring + 10 companies enriched + 10 drafts)
stays under $7 on any model mix, and under $4 with the Batches API.

## 2. Provider access for "bring your own key"

| Option | Version (PyPI, 2026-09) | Dependency weight | Structured output | Verdict |
|---|---|---|---|---|
| (a) `anthropic` SDK | 1.7.0, 2026-09-18 | 7 runtime deps (anyio, httpx2, jiter, pydantic, sniffio, docstring-parser, typing-extensions) | native `messages.parse(output_format=Model)`; schema enforced server-side | **use for v1** |
| (b) OpenAI-compatible endpoint (`openai` SDK) | 3.16.2 | 6 deps | depends on the backend. Anthropic's own compat layer *ignores* `response_format` and `strict`, has no prompt caching, and is documented as "not production-ready"; Ollama/OpenRouter each differ | later, if users ask |
| (c) `litellm` | 1.101.0 | ~14 core deps incl. `openai`, `tiktoken`, `tokenizers`, `boto3`, `jinja2`, `aiohttp`; its Anthropic extra still pins `anthropic<1.0` | translation layer, one abstraction per provider quirk | no: heavy, moves fast, hides the request |
| (d) `pydantic-ai` | 2.46.0 (meta-package pulls anthropic + openai + google + logfire + mcp + web); `pydantic-ai-slim[anthropic]` is lean | agent framework: `Agent`, `RunContext`, graphs | good, provider-portable `output_type=Model` | not for v1: an agent framework for five single calls; AGENTS.md says no framework until a step needs one |
| (e) `instructor` | 1.17.0 | pins `anthropic==0.93.0` exactly (pre-1.x, incompatible with the current SDK); core deps include `openai`, `rich`, `typer`, `tenacity`, `jinja2` | retry-on-validation pattern from before native structured outputs | no |

Why (a): the codebase should teach what actually goes on the wire, and every
feature the tool needs (structured output, prompt caching, Batches API,
`usage` for cost logging) is first-class there and nowhere else. The
"bring your own key" promise in v1 means "bring your Anthropic key"; the SDK
also honours `ANTHROPIC_BASE_URL`, so gateways that speak the Messages API
work without code.

Two providers behind one function is reasonable *later*, not now: it doubles
the structured-output mechanism, the caching story and the golden-set numbers,
and the OpenAI-compatible layers explicitly do not guarantee the schema. Keep
the seam (all calls go through `llm.ask`) so a second ~40-line branch using
`openai.chat.completions.parse` can be added when a real user needs it.

## 3. Structured output on the Anthropic API

Current mechanism: **`output_config.format` (JSON outputs)**, GA, no beta
header, supported on Haiku 4.5, Sonnet 5, Opus 5 and newer. The old
"call a tool to get JSON" trick is obsolete; on Claude Fable 5.1 forced
`tool_choice` even returns a 400. In the Python SDK 1.x, `messages.parse()`
takes the Pydantic class as `output_format=` and returns `.parsed_output`.

```python
# llm.py — minimal working example (anthropic 1.7.0, pydantic 2)
import anthropic
from pydantic import BaseModel, Field

class Score(BaseModel):
    uid: str
    score: int = Field(description="0 = no fit, 100 = perfect fit to the goal")
    reason: str = Field(description="One line, German")

class ScoreBatch(BaseModel):
    items: list[Score]

client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from .env / env

def score_batch(system_prompt: str, rendered_items: str, model: str) -> ScoreBatch:
    response = client.messages.parse(
        model=model,
        max_tokens=4000,                # 50 items × ~50 tokens, with headroom
        system=[{"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}}],   # shared prefix, see §4
        messages=[{"role": "user", "content": rendered_items}],
        output_format=ScoreBatch,
    )
    if response.stop_reason != "end_turn":   # "max_tokens" = truncated JSON, "refusal" on Opus 5
        raise RuntimeError(f"scoring stopped with {response.stop_reason}")
    return response.parsed_output           # validated ScoreBatch
```

Schema limits worth knowing: no `minimum`/`maximum`, `minLength`, recursive
schemas or `additionalProperties` other than `false`. Checked in the SDK
source: `Field(ge=0, le=100)` does not error — the SDK moves the constraint
into the field description (`"{maximum: 100, minimum: 0}"`) and Pydantic still
validates the parsed value client-side. Use `Literal[...]` for closed choices
(e.g. `"own_site" | "none"`, the two flags in step 4). Structured output is
incompatible with citations, which we do not use.

## 4. Batching the 4,000 purpose texts

Measured on `data/v0/kept_3203.json` (3,993 texts): full purpose text
averages 648 characters, the **head clause** (split on `Die Gesellschaft kann`
/ `Sie kann`, as LEARNINGS §2 says) averages 213 characters ≈ 70 tokens.
With UID and name, one item ≈ 85 input tokens; a scored item
(`uid`, `score`, one-line reason) ≈ 40 output tokens. Prompt + goal + schema
≈ 800 tokens per call.

Cost for 4,000 texts (input 0.34M payload + prompt, output 0.16M):

| Model | $/MTok in / out | Sync | Batches API (−50%) |
|---|---|---|---|
| `claude-haiku-4-5` | 1 / 5 | **≈ $1.2** | ≈ $0.6 |
| `claude-sonnet-5` | 2 / 10 | ≈ $2.3 | ≈ $1.2 |
| `claude-opus-5` | 5 / 25 | ≈ $5.9 | ≈ $2.9 |

Note that the one-line *reason* is two thirds of the cost. If that matters,
score everything with `score` only and ask for reasons on the top 100 in a
second call (Haiku ≈ $0.7, Opus ≈ $3.3).

**Batch size: 50, not 100, and make it a parameter.** 100 items is within
every limit (≈ 9,000 tokens in, 4,000 out), but three practical risks grow
with the list: (1) a single `max_tokens` cut or refusal loses the whole batch;
(2) missing or duplicated UIDs — the code must compare the returned UID set
with the sent one and re-send the difference; (3) drift: scores in a long
list become relative to their neighbours, and models use the middle of a long
context less reliably ("Lost in the Middle", Liu et al. 2023; position bias
in LLM judges, Zheng et al. 2023). Halving the batch costs only the repeated
prompt (80 instead of 40 calls ≈ +32k input tokens ≈ $0.03 on Haiku).
Shuffle the pool with a recorded seed before batching, and let the golden
set (§6) tell whether scores are stable across positions.

**Per-item calls (4,000 × one text) with prompt caching** are the reliable
extreme: no position effects, one failure = one item. But they cost 2–4× more
and take 4,000 HTTP round-trips: Haiku ≈ $4.3 (its cache minimum is 4,096
tokens, so an 800-token prompt never caches), Sonnet 5 ≈ $3.1 with the
prompt cached (minimum 1,024 tokens; reads at 0.1×). Not worth it for the
first pass; keep it as a fallback for re-scoring a shortlist.

**Message Batches API** is the right default for the 4,000-text step: 50%
off every token, up to 100,000 requests per batch, most batches finish in
under an hour (hard limit 24 h), results kept 29 days, structured outputs and
caching work inside it (cache hits best-effort, use `ttl: "1h"`). Results
come back in any order — key by `custom_id`. Offer it as `--batch` and keep
the synchronous path for small pools and for development.

## 5. Prompt organisation and provenance

```
prompts/
  select.md      # step 1: goal → selection prompt
  score.md       # step 2
  pick_site.md   # step 3
  pick_pages.md  # step 4a
  extract.md     # step 4b
  draft.md       # step 5
```

Each file starts with a three-line header, then the prompt body:

```
---
name: score
version: 3
---
Du bewertest Handelsregister-Zwecktexte gegen dieses Ziel: $goal
...
$items
```

Loader: read the file, parse the header, `string.Template(body).substitute(**variables)`.
`string.Template` beats `str.format` because German prompts contain `{`
(JSON examples) that `str.format` would choke on, and `substitute` raises on a
missing variable instead of silently leaving a hole. Jinja2 is not needed:
lists (the 50 items) are rendered in Python and passed as one string.

Provenance: every output row carries
`{"prompt": "score@3", "prompt_sha": "ab12cd34", "model": "claude-opus-5",
"usage": {"input": 9120, "output": 1980, "cache_read": 0}, "ts": ...}` —
the hash (first 8 hex of SHA-256 of the file) catches edits made without a
version bump. Rows go to `data/runs/<run_id>/<step>.jsonl`; the run's
`meta.json` records the goal, seed, batch size and model per step. Keep the
system prompt free of dates and run IDs so the cached prefix stays stable.

Model defaults: `claude-opus-5` for steps 1, 3, 4, 5 — ten calls per run,
judgment matters, the difference to Sonnet is cents. The scoring model is a
setting; the golden set decides whether Haiku or Sonnet hold up there.

## 6. Evaluation without a framework

- `data/golden/purpose_texts.jsonl` — 30 hand-labelled head clauses
  (`uid`, `text`, `label: yes|no|borderline`, `note`). Company register
  text is public, but AGENTS.md keeps all company data under gitignored
  `data/`, so the golden set lives there too.
- `data/golden/sites/` — the 15 known pairs from LEARNINGS §4: `expected.jsonl`
  (`uid`, `name`, `expected: <domain>|none`) plus saved Impressum/home text of
  the 2–3 candidates per company, so the test never touches the network.
- `tests/test_prompts.py`, marked `@pytest.mark.llm` and skipped unless
  `RUN_LLM_EVALS=1`. Two checks: scoring — all 30 in one call, assert at
  least 8 of the 10 `yes` texts rank above every `no` text (precision at 10);
  site choice — 15 calls, assert ≥ 13 correct **and** that every returned
  quote is found verbatim in the candidate page (a deterministic check the
  production code does anyway).
- Each run appends one line to `data/evals/results.jsonl` with `prompt@version`,
  model, score and cost from `usage`; a `make eval` target prints the last
  runs as a table. One eval run costs about $0.02 (scoring) + $0.20 (sites).
  Sampling parameters are gone on the current models, so run twice to see the
  noise before trusting a one-point difference.

## 7. Cost and latency per run (4,000 texts, 10 companies, 10 drafts)

| Step | Calls | Tokens in / out | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|---|---|
| 1 selection prompt | 1 | 0.5k / 0.5k | $0.02 | $0.01 | <$0.01 |
| 2 scoring (sync) | 80 | 372k / 160k | $5.9 | $2.3 | $1.2 |
| 3 site choice | 10 | 5.3k / 0.2k each | $0.30 | $0.12 | $0.06 |
| 4 pages + extraction | 20 | 2k+9k / 0.1k+0.4k each | $0.68 | $0.27 | $0.14 |
| 5 drafts | 10 | 1k / 0.3k each | $0.13 | $0.05 | $0.03 |
| **Total** | | | **≈ $7.0** | **≈ $2.8** | **≈ $1.4** |

Recommended mix (Opus 5 for 1, 3–5; scoring model per eval) lands at
$2.4–7.1 per run, or $1.3–4.2 with scoring through the Batches API.
Latency: sync scoring ≈ 15 s per call, so ~20 min sequential or ~4 min with 5
concurrent calls; the Batches API needs no attention but typically returns in
under an hour. Enrichment is dominated by fetching sites, not by the model
(≈ 30–60 s of LLM time per company).

## Sources

- Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Batch processing: https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI SDK compatibility (limits): https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk
- Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Python SDK `messages.parse` source: https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/resources/messages/messages.py
- PyPI: https://pypi.org/project/anthropic/ · https://pypi.org/project/litellm/ · https://pypi.org/project/pydantic-ai/ · https://pypi.org/project/instructor/ · https://pypi.org/project/openai/
- Liu et al., "Lost in the Middle" (2023): https://arxiv.org/abs/2307.03172
- Zheng et al., "Judging LLM-as-a-Judge" (2023): https://arxiv.org/abs/2306.05685
- Local measurements: `data/v0/kept_3203.json` (purpose-text lengths), SDK `anthropic/lib/_parse/_transform.py` (schema transformation).
