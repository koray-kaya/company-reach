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

## Notes

- Built with Python, LangGraph, SearXNG, FastAPI and SQLite.
- Uses only public data: the Swiss commercial register (Zefix, via LINDAS) and
  the Swiss Official Gazette of Commerce (SHAB). See [NOTICE](NOTICE).
- The design, research notes and build plan are in [docs/](docs/) and
  [IMPLEMENTATION.md](IMPLEMENTATION.md).

MIT licence. Koray Kaya, 2026.
