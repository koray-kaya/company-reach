"""A fictional sender and invitation — the frame@1 study's example values —
for every test that drafts or rebuilds a mail. Nobody here is a real person.
"""

from datetime import date

from company_reach.profile import Invitation, Sender

SENDER = Sender(
    name="Lena Brunner",
    affiliation="Masterstudentin, OST Ostschweizer Fachhochschule",
    school_short="OST",
    place="St. Gallen",
    supervisor="Prof. Dr. Hans Vorbild",
)
INVITATION = Invitation(
    topic="wie KMU zu Kunden und Lieferanten kommen",
    minutes=15,
    closes=date(2026, 10, 30),
    offer_results=True,
    no_login=True,
)

SECTIONS = """
[sender]
name = "Lena Brunner"
affiliation = "Masterstudentin, OST Ostschweizer Fachhochschule"
school_short = "OST"
place = "St. Gallen"
supervisor = "Prof. Dr. Hans Vorbild"

[invitation]
topic = "wie KMU zu Kunden und Lieferanten kommen"
minutes = 15
closes = 2026-10-30
offer_results = true
no_login = true
"""


def profile_text(
    survey_url: str = "https://survey.example/form",
    *,
    goal: str = "Firms that make things.",
    sections: bool = True,
) -> str:
    """A whole profile.toml. `sections=False` leaves out [sender] and
    [invitation], as a profile written before frame@1 did."""
    head = f'goal = "{goal}"\nsurvey_url = "{survey_url}"\n'
    return head + (SECTIONS if sections else "")
