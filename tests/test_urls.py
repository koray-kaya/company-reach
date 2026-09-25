"""Which site a URL or an e-mail address belongs to.

`registered_domain` stripped only `www.`, so a site found at
`de.muster-metallbau.ch` counted the company's own `info@muster-metallbau.ch`
as another domain's and guessed `info@de.muster-metallbau.ch` instead, and a
site on a site builder guessed an inbox under the builder's name (audit).
The public suffix list, through tldextract, knows where a registered domain
begins; hosts are fictional here, as everywhere in the tests.
"""

import pytest

from company_reach.tools import urls
from company_reach.tools.urls import email_domain, inbox_domain, registered_domain


def test_a_subdomain_site_keeps_its_apex_inbox():
    site = registered_domain("https://de.muster-metallbau.ch/kontakt")
    assert site == "muster-metallbau.ch"
    assert email_domain("info@muster-metallbau.ch") == site


def test_an_address_on_a_mail_subdomain_is_the_same_domain():
    assert email_domain("anna@mail.muster-metallbau.ch") == "muster-metallbau.ch"


def test_www_and_case_are_folded_as_before():
    assert registered_domain("https://WWW.Muster.ch/impressum") == "muster.ch"
    assert email_domain("Anna.Muster@WWW.Muster.ch") == "muster.ch"


def test_a_two_part_ending_is_one_ending():
    assert registered_domain("https://shop.muster.co.uk/") == "muster.co.uk"


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://muster.wixsite.com/metallbau", "muster.wixsite.com"),
        ("https://muster.jimdosite.com/", "muster.jimdosite.com"),
        ("https://www.muster.jimdofree.com/", "muster.jimdofree.com"),
        ("https://muster.webnode.ch/", "muster.webnode.ch"),
        ("https://muster.business.site/", "muster.business.site"),
    ],
)
def test_a_site_builder_host_is_the_customer_not_the_builder(url, site):
    """Two firms on one builder are two sites; one is not the builder's."""
    assert registered_domain(url) == site


def test_an_inbox_is_guessed_only_on_a_domain_of_its_own():
    assert inbox_domain("https://www.muster-metallbau.ch/") == "muster-metallbau.ch"
    # info@de.muster-metallbau.ch does not exist, and info@ on a builder's
    # host is nobody's — or, for a builder the list lacks, the builder's own
    assert inbox_domain("https://de.muster-metallbau.ch/") is None
    assert inbox_domain("https://muster.wixsite.com/metallbau") is None
    assert inbox_domain("https://muster.jimdosite.com/") is None


def test_a_host_outside_the_suffix_list_is_kept_whole():
    assert registered_domain("https://www.muster.example/") == "muster.example"
    assert registered_domain("http://127.0.0.1:8080/") == "127.0.0.1"
    assert registered_domain("/impressum") is None


def test_the_suffix_list_is_the_bundled_one():
    # decided 2026-09-25: no fetch of the list, at run time or in tests
    assert urls._EXTRACT.suffix_list_urls == ()
