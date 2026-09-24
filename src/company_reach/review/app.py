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
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from company_reach.profile import load_profile
from company_reach.review.cards import Card, first_undecided, load_cards
from company_reach.settings import Settings
from company_reach.tools.db import (
    connect,
    record_decision,
    sent_this_month,
    suppress,
)
from company_reach.tools.mailto import build

HERE = Path(__file__).parent
SKIP_REASONS = ("Not a fit", "No address", "Foreign group", "Distributor")
# A navigation this page started, or one the reviewer typed. Anything else is
# another site in the same browser — the company's own site, one click away
# on the card — and must not be able to decide anything (audit:242).
_SAME_ORIGIN = ("same-origin", "none")


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

    @app.get("/", response_class=HTMLResponse)
    def runs(request: Request) -> HTMLResponse:
        """Every run with results, newest first, with how many are left."""
        with connect(settings.db_path) as conn:
            rows = conn.execute(
                """select r.run_id, count(*) as companies,
                          sum(r.recommendation = 'send') as to_send,
                          max(r.finished_at) as finished
                     from results r group by r.run_id order by finished desc"""
            ).fetchall()
        return templates.TemplateResponse(request, "runs.html", {"runs": rows})

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

    def next_url(run_id: str, n: int) -> str:
        """The next undecided company after this one, else the first
        undecided anywhere: a decision slides on to the next open card."""
        cards = cards_for(run_id)
        later = [i for i in range(n + 1, len(cards)) if cards[i].decision is None]
        return f"/review/{run_id}/{later[0] if later else first_undecided(cards)}"

    def after(run_id: str, n: int, done: str) -> RedirectResponse:
        return RedirectResponse(
            f"{next_url(run_id, n)}?done={quote(done)}", status_code=303
        )

    @app.post("/decide/{run_id}/{uid}", response_model=None)
    async def decide(
        request: Request, run_id: str, uid: str, n: int = 0
    ) -> HTMLResponse | RedirectResponse:
        if request.headers.get("sec-fetch-site") not in _SAME_ORIGIN:
            raise HTTPException(403, "decisions are accepted from this page only")
        form = await request.form()
        action = str(form.get("action", ""))
        card = next((c for c in cards_for(run_id) if c.uid == uid), None)
        if card is None:
            raise HTTPException(404, f"{uid} is not in run {run_id!r}")

        if action == "send":
            return send(request, run_id, card, str(form.get("to", "")), n)
        if action.startswith("skip:"):
            reason = action.removeprefix("skip:")
            if reason not in SKIP_REASONS:
                raise HTTPException(400, f"no skip reason {reason!r}")
            if card.decision is not None:
                raise HTTPException(409, f"already decided: {card.decision}")
            with connect(settings.db_path) as conn:
                record_decision(conn, uid, "skipped", run_id=run_id, note=reason)
            return after(run_id, n, f"Skipped: {reason}")
        if action == "never":
            if card.decision in ("never", "sent"):
                raise HTTPException(409, f"already decided: {card.decision}")
            if form.get("confirm") != "yes":
                return templates.TemplateResponse(
                    request,
                    "confirm_never.html",
                    {"run_id": run_id, "card": card, "n": n},
                )
            with connect(settings.db_path) as conn:
                suppress(conn, uid, reason="never again, from the review page")
                record_decision(conn, uid, "never", run_id=run_id)
            return after(run_id, n, "Added to the never-again list")
        if action == "undo":
            # only a skip is taken back; a send happened in a mail client and
            # never again was promised to be permanent before the click
            if card.decision != "skipped":
                raise HTTPException(409, f"cannot undo {card.decision or 'nothing'}")
            with connect(settings.db_path) as conn:
                record_decision(conn, uid, "undone", run_id=run_id)
            return RedirectResponse(
                f"/review/{run_id}/{n}?done={quote('Skip undone')}", status_code=303
            )
        raise HTTPException(400, f"unknown action {action!r}")

    def send(
        request: Request, run_id: str, card: Card, to: str, n: int
    ) -> HTMLResponse:
        """Record first, then open the mail. "Contacted once, ever" rests on
        the ledger, so it must not depend on a link being followed."""
        if card.send_block:
            raise HTTPException(409, card.send_block)
        offered = [a.email for a in card.contact.addresses] if card.contact else []
        if to not in offered:
            raise HTTPException(400, "send only to an address the card offered")
        draft = card.draft
        with connect(settings.db_path) as conn:
            record_decision(
                conn, card.uid, "sent", address=to, draft_id=draft.id, run_id=run_id
            )
        link = build(to, draft.subject, draft.body)
        if not link.fits:
            # the body would not survive the trip; open address and subject
            # and let the reviewer paste the text shown on the page
            link = build(to, draft.subject, "")
        return templates.TemplateResponse(
            request,
            "recorded.html",
            {
                "href": link.href,
                "card": card,
                "to": to,
                "body_to_copy": None if link.fits else draft.body,
                "next_url": next_url(run_id, n),
            },
        )

    return app
