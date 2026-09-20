# company-reach

A small tool I am building for my master's thesis. I need people at small
Swiss companies to fill in a short survey, and finding the right company and
the right person by hand takes a long time. This tool does the searching and
drafts an invitation; I read each one and decide whether to send it.

![Review screen with fictional sample data](docs/assets/review-page.png)

## What it does

1. Lists the companies of a Swiss town from the public commercial register.
2. Picks the ones that fit my goal, using an AI model.
3. Finds each company's website and the person to write to.
4. Writes a short invitation in German.
5. Shows me one company at a time. I choose send or skip. It never sends mail
   by itself; "send" opens the draft in my own mail program.

## Status

Work in progress. The design is done; I am building it step by step, in
eight milestones. Each milestone is written with an AI coding assistant and
explained to me before I review and merge it, so I learn how it works.

Milestones 1 and 2 of 8 are done: listing a town's companies, applying the
rule-based exclusions, and scoring the survivors against a goal with a
language model.

## Run it

Needs [uv](https://docs.astral.sh/uv/); it installs Python 3.13 itself. From
milestone 2 on it also needs a model: any OpenAI-compatible endpoint, set in
`.env`. No Docker yet.

```bash
git clone https://github.com/koray-kaya/company-reach.git
cd company-reach
uv sync
cp .env.example .env              # base url, key and model id
cp profile.toml.example profile.toml   # your goal, in one sentence

uv run company-reach doctor       # settings, prompts, database, endpoint
uv run company-reach pool --municipality 3203
uv run company-reach screen --run-id <the run it printed>
uv run company-reach criteria     # what your goal means, before paying for it
uv run company-reach score --limit 200
```

`3203` is the federal id of a municipality (that one is St. Gallen). `pool`
writes the companies into `data/company_reach.db`, `screen` marks the ones
the rules exclude, `score` ranks the rest against your goal. Every command is
safe to run again: nothing already done is repeated.

Scoring is deliberately incremental — `--limit` scores that many and stops,
so you can start reading results long before the whole town is scored.

Tests run offline in a few seconds: `uv run pytest`. The prompt evaluation
calls the real model and is opt-in: `RUN_LLM_EVALS=1 uv run pytest
tests/test_prompts.py -s`.

## Notes

- Built with Python, LangGraph, SearXNG, FastAPI and SQLite.
- Uses only public data: the Swiss commercial register (Zefix, via LINDAS) and
  the Swiss Official Gazette of Commerce (SHAB). See [NOTICE](NOTICE).
- The design, research notes and build plan are in [docs/](docs/) and
  [IMPLEMENTATION.md](IMPLEMENTATION.md).

MIT licence. Koray Kaya, 2026.
