"""The review page: FastAPI serving one company per page from SQLite.

Design in `docs/design/ux/review-page-ux.md`, decisions in the M7 plan. It
reads what a run left behind (`review/cards.py`) and writes the ledger, the
suppression list, and the reviewer's two corrections to a mail: Frau /
Herr / ohne, and another of the card's addresses. Each rebuilds the draft
by code around the same sentence, and Send goes only to the address the
mail was written for. The tool still sends nothing — Send records the
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

from company_reach.models import Contact
from company_reach.nodes.check_draft import reassemble
from company_reach.profile import load_profile
from company_reach.review.cards import Card, first_undecided, load_cards
from company_reach.settings import Settings
from company_reach.tools.db import (
    connect,
    readdress_contact,
    record_decision,
    rewrite_draft,
    sent_this_month,
    set_salutation,
    suppress,
)
from company_reach.tools.invitation import named
from company_reach.tools.mailto import build

HERE = Path(__file__).parent
SKIP_REASONS = ("Not a fit", "No address", "Foreign group", "Distributor")
SALUTATIONS = ("Frau", "Herr", "ohne")
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
                profile=profile,
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
        if action.startswith("address:"):
            email = action.removeprefix("address:")
            rows = (
                {a.email: a.kind for a in card.contact.addresses}
                if card.contact
                else {}
            )
            if email not in rows:
                raise HTTPException(400, "choose only an address the card offered")
            if rows[email] == "third_party":
                raise HTTPException(
                    409, "an address on another domain is never sent to"
                )
            if card.decision is not None:
                raise HTTPException(409, f"already decided: {card.decision}")
            if not card.can_readdress:
                raise HTTPException(409, "no current draft to rebuild")
            readdress(run_id, card, email)
            return RedirectResponse(
                f"/review/{run_id}/{n}?done={quote(f'Written for {email}')}",
                status_code=303,
            )
        if action.startswith("salutation:"):
            choice = action.removeprefix("salutation:")
            if choice not in SALUTATIONS:
                raise HTTPException(400, f"no salutation {choice!r}")
            if card.decision is not None:
                raise HTTPException(409, f"already decided: {card.decision}")
            if not card.can_choose_salutation:
                raise HTTPException(409, "no current draft to a named person here")
            resalute(run_id, card, choice)
            return RedirectResponse(
                f"/review/{run_id}/{n}?done={quote(f'Anrede: {choice}')}",
                status_code=303,
            )
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
        offered = (
            {a.email: a.kind for a in card.contact.addresses} if card.contact else {}
        )
        if to not in offered:
            raise HTTPException(400, "send only to an address the card offered")
        if offered[to] == "third_party":
            # the way a hostile page plants a contact: shown, never sent to
            raise HTTPException(409, "an address on another domain is never sent to")
        if to != card.contact.email:
            # the greeting, the first line and the privacy text describe the
            # address the mail was written for, and only that one
            raise HTTPException(
                409,
                f"the mail is written for {card.contact.email}; choose {to} on "
                "the card first and read the rebuilt mail",
            )
        draft = card.draft
        # "generic/site/named": the kind the mail was written for, which is
        # the address it goes to; no personal data. The survey's answers are
        # compared by it.
        kind = f"{card.contact.email_kind}/{card.contact.source}/"
        kind += "named" if named(card.contact) else "none"
        with connect(settings.db_path) as conn:
            record_decision(
                conn,
                card.uid,
                "sent",
                address=to,
                draft_id=draft.id,
                run_id=run_id,
                frame_version=draft.frame_version,
                arm=draft.arm,
                contact_kind=kind,
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

    def resalute(run_id: str, card: Card, choice: str) -> None:
        """The reviewer's Frau / Herr / ohne: stored on the contact, and the
        mail rebuilt by code around the same sentence — no model call, so
        the text the reviewer already read stays word for word. The rebuilt
        draft is checked again, and the card sends it only if it passes."""
        profile = load_profile(settings.profile_path)
        if gaps := profile.drafting_gaps():
            raise HTTPException(
                409, f"profile.toml lacks {', '.join(gaps)}; cannot rebuild the mail"
            )
        view = card.draft
        if view is None or not view.model_text or card.contact is None:
            raise HTTPException(409, "no current draft to rebuild")
        contact = card.contact.model_copy(
            update={"salutation": choice, "salutation_origin": "reviewer"}
        )
        rebuilt, found = reassemble(view.model_text, contact, profile, card.uid)
        with connect(settings.db_path) as conn:
            set_salutation(conn, run_id, card.uid, choice)
            rewrite_draft(conn, view.id, rebuilt, found=found)

    def readdress(run_id: str, card: Card, email: str) -> None:
        """Another of the card's addresses: the contact now points there, and
        the mail is rebuilt for it around the same sentence and checked
        again — no model call. The reviewer reads the rebuilt mail, then
        sends."""
        profile = load_profile(settings.profile_path)
        if gaps := profile.drafting_gaps():
            raise HTTPException(
                409, f"profile.toml lacks {', '.join(gaps)}; cannot rebuild the mail"
            )
        view = card.draft
        contact = readdressed(card.contact, email)
        rebuilt, found = reassemble(view.model_text or "", contact, profile, card.uid)
        with connect(settings.db_path) as conn:
            readdress_contact(conn, run_id, card.uid, contact)
            rewrite_draft(conn, view.id, rebuilt, found=found)

    return app


def readdressed(contact: Contact, email: str) -> Contact:
    """The contact, written to at another of its addresses, which comes
    first. When that address is another named person's own — the site
    listed them with it, as an alternative — the mail is for that person,
    and the one it was for becomes an alternative. Otherwise it is for the
    same person, or nobody, at that address; the privacy text follows the
    address's kind."""
    rows = sorted(contact.addresses, key=lambda a: a.email != email)
    kind = next(a.kind for a in rows if a.email == email)
    for entry in contact.alternatives:
        parts = entry.split(", ")
        if len(parts) >= 2 and parts[-1].lower() == email.lower():
            own = contact.email if contact.email_kind == "seen" else None
            former = ", ".join(p for p in (contact.name, contact.role, own) if p)
            others = [a for a in contact.alternatives if a != entry]
            return contact.model_copy(
                update={
                    "name": parts[0],
                    "role": ", ".join(parts[1:-1]) or None,
                    "salutation": None,
                    "salutation_origin": None,
                    "email": email,
                    "email_kind": kind,
                    "addresses": rows,
                    "alternatives": [former, *others] if former else others,
                }
            )
    return contact.model_copy(
        update={"email": email, "email_kind": kind, "addresses": rows}
    )
