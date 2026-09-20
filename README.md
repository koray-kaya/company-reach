# company-reach

A small tool I am building for my master's thesis. I need to interview people
at small Swiss companies, and finding the right company and the right person
by hand takes a long time. This tool does the searching and drafts an
invitation; I read each one and decide whether to send it.

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

Milestone 1 of 8 is done: the first step — listing a town's companies and
applying the rule-based exclusions — runs.

## Run it

Needs [uv](https://docs.astral.sh/uv/); it installs Python 3.13 itself.
Nothing else so far: no Docker, no model key.

```bash
git clone https://github.com/koray-kaya/company-reach.git
cd company-reach
uv sync
cp .env.example .env      # milestone 1 uses no key, but the file must exist

uv run company-reach pool --municipality 3203 --run-id first
uv run company-reach screen --run-id first
```

`3203` is the federal id of a municipality (that one is St. Gallen). The
first command writes the companies into `data/company_reach.db`; the second
marks the ones the rules exclude and prints how many were kept. Running
either one again is safe.

Tests run offline: `uv run pytest`.

## Notes

- Built with Python, LangGraph, SearXNG, FastAPI and SQLite.
- Uses only public data: the Swiss commercial register (Zefix, via LINDAS) and
  the Swiss Official Gazette of Commerce (SHAB). See [NOTICE](NOTICE).
- The design, research notes and build plan are in [docs/](docs/) and
  [IMPLEMENTATION.md](IMPLEMENTATION.md).

MIT licence. Koray Kaya, 2026.
