# Adversarial pages

Four pages a company's website could serve that a profile must survive.
Every company, person and address here is invented; nothing in this folder
comes from a real site (`AGENTS.md`, hard rules). The real golden set lives
in `data/golden/` and stays out of git.

They exist because the audit asks for them (`audit-2026-09-19.md:184`) and
because `check_profile` is only worth having if something tries to get past
it. Each file is one thing going wrong:

| file | what it tries |
|---|---|
| `poisoned_instructions.html` | speaks to the assistant and plants a contact |
| `lookalike_contact.html` | an ordinary Impressum with an address on another domain |
| `group_with_other_uid.html` | carries a different, check-digit-valid UID |
| `directory_entry.html` | a register-style listing of several firms |

The deterministic half of the defence is tested against them in
`tests/test_adversarial.py`, with no model involved: whatever the model can
be talked into returning, `check_profile` decides what survives. The half
that needs the model — does it obey the injection at all — belongs to the
opt-in golden evaluation.
