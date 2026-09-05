from brand_serp.domains import (domain_of, host_from_url, query_keys,
                                registrable_domain, same_site, strip_www)


def test_domain_of_strips_www_and_path():
    assert domain_of("https://www.starcasino.nl/live-casino?x=1") == "starcasino.nl"


def test_domain_of_handles_subdomains():
    assert domain_of("https://go.affiliate.example.co.uk/out") == "example.co.uk"


def test_domain_of_ignores_port_and_credentials():
    assert domain_of("http://user:pw@casino.nl:8080/page") == "casino.nl"


def test_domain_of_returns_none_for_garbage():
    for bad in ["", "not a url", "javascript:void(0)", None, "http:///"]:
        assert domain_of(bad) is None


def test_registrable_domain_multipart_suffix():
    assert registrable_domain("shop.brand.com.au") == "brand.com.au"
    assert registrable_domain("brand.nl") == "brand.nl"


def test_strip_www_and_host():
    assert strip_www(host_from_url("https://WWW.Casino.NL/")) == "casino.nl"


def test_same_site():
    assert same_site("https://www.starcasino.nl/a", "http://starcasino.nl/b")
    assert not same_site("https://starcasino.nl", "https://starcasinobonus.net")


def test_query_keys_lowercased():
    assert query_keys("https://x.nl/?BTag=1&subid=2") == {"btag", "subid"}
