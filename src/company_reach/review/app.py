"""The review page: FastAPI serving one company per page from SQLite.

Design in `docs/design/ux/review-page-ux.md`, decisions in the M7 plan. It
reads what a run left behind (`review/cards.py`) and writes only the ledger
and the suppression list; the tool still sends nothing — Send records the
decision and hands the draft to the reviewer's own mail client.

Every card is its own URL, `/review/{run}/{n}`, and every action is a form
POST, so the page works without its small script. Jinja2 autoescapes the
`.html` templates, which matters here: company names, page quotes and drafts
all started as text a website wrote.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from company_reach.profile import load_profile
from company_reach.review.cards import Card, first_undecided, load_cards
from company_reach.settings import Settings
from company_reach.tools.db import connect, sent_this_month

HERE = Path(__file__).parent
SKIP_REASONS = ("Not a fit", "No address", "Foreign group", "Distributor")


def create_app(settings: Settings) -> FastAPI:
    # No /docs, no /openapi.json: a local page for one person, not an API.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    def cards_for(run_id: str) -> list[Card]:
        profile = load_profile(settings.profile_path)
        with connect(settings.db_path) as conn:
            cards = load_cards(
                conn,
                run_id,
                survey_url=profile.survey_url,
                sending_approved=settings.sending_approved,
            )
        if not cards:
            raise HTTPException(404, f"run {run_id!r} has no results to review")
        return cards

    @app.get("/review/{run_id}")
    def open_run(run_id: str) -> RedirectResponse:
        return RedirectResponse(
            f"/review/{run_id}/{first_undecided(cards_for(run_id))}", status_code=303
        )

    @app.get("/review/{run_id}/{n}", response_class=HTMLResponse)
    def card(request: Request, run_id: str, n: int, done: str = "") -> HTMLResponse:
        cards = cards_for(run_id)
        if not 0 <= n < len(cards):
            raise HTTPException(404, f"run {run_id!r} has {len(cards)} companies")
        with connect(settings.db_path) as conn:
            sent = sent_this_month(conn)
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "run_id": run_id,
                "cards": cards,
                "n": n,
                "card": cards[n],
                "goal": load_profile(settings.profile_path).goal,
                "sent_this_month": sent,
                "skip_reasons": SKIP_REASONS,
                "done": done,
            },
        )

    return app
