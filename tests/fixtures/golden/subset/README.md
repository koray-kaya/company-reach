# The public golden subset

Fictional companies, people and sites — nothing here is real (`AGENTS.md`,
hard rules). The author's real, hand-labelled golden set lives in
`data/golden/` and stays out of git; the evaluations in
`tests/test_prompts.py` use it when it is there and this subset otherwise,
so a fresh clone can run them.

| file | evaluation | what it holds |
|---|---|---|
| `goal.txt` | scoring | the goal the labels were written against (`profile.toml.example`'s) |
| `scoring.jsonl` | scoring | 20 register entries with a 0-10 label each |
| `extraction.jsonl` | extraction; contact choice | 5 sites as saved pages, with the people and personal addresses they carry |
| `drafts.jsonl` | drafts | 5 profiles and contacts to draft for |

**The labels were written for this fixture, against that goal, not measured
from a person's judgement.** Five companies clearly make and sell on (9),
five are close (8, or 6 for a bakery that sells mostly in its own shops),
and ten are local trades, services or property (0-2). The top five and top
ten are therefore unambiguous, which is what a small public check needs; it
is not a substitute for the real set.

Each extraction site is one case the live runs met: a personal address only
in a `mailto:` link, names with no address, a site that names nobody, an
address only in Cloudflare's encoded form, and an Impressum naming someone
at another firm.

The same pages grade contact choice and the recommendation without a model,
in every suite: `tests/test_find_contact.py` runs each site through
`check_profile`, `find_contact` and `recommend` and compares who the mail
goes to, at which address, and whether it is sent or held.
