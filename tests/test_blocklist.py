from company_reach.tools.blocklist import is_blocked, load_blocklist
from company_reach.tools.urls import registered_domain


def test_a_listed_domain_is_blocked():
    assert is_blocked("https://www.linkedin.com/company/x")
    assert is_blocked("https://zefix.ch/de/search")


def test_subdomains_are_blocked_too():
    """tel.search.ch is a path into the same directory; blocking the
    registered domain has to cover everything under it."""
    assert is_blocked("https://tel.search.ch/")
    assert is_blocked("https://jobs.linkedin.com/x")


def test_a_domain_that_merely_ends_the_same_is_not_blocked():
    """mylinkedin.com is not linkedin.com. A naive endswith would block it."""
    assert not is_blocked("https://mylinkedin.com/")
    assert not is_blocked("https://notzefix.ch/")


def test_a_real_company_site_passes():
    assert not is_blocked("https://muster-metallbau.ch/impressum")


def test_the_list_is_complete():
    entries = load_blocklist()
    assert len(entries) == 47
    assert "linkedin.com" in entries
    assert "cylex-swiss.ch" in entries
    assert "cylex.ch" not in entries  # redirects to cylex-swiss.ch


def test_the_federal_registers_are_blocked():
    # the UID register and Zefix live under admin.ch
    assert is_blocked("https://www.uid.admin.ch/Detail.aspx?uid_id=CHE000000046")
    assert is_blocked("https://www.zefix.admin.ch/de/search/entity/list")


def test_every_entry_is_a_registered_domain():
    """Review of E6: a host is judged by its registered domain, so an entry
    below one — `uid.admin.ch` — could never match and blocked nothing."""
    for entry in load_blocklist():
        assert registered_domain(f"https://{entry}/") == entry, entry
