"""EB Tools for laptops is a static page served by the API itself (static/ebtools.html)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ebtools_page_is_served(client):
    for path in ("/ebtools", "/ebtools/"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["content-type"].startswith("text/html")
        assert "no-cache" in res.headers["cache-control"]
        assert "EB Tools" in res.text
        assert "__BASE__" not in res.text
        assert 'const BASE = "";' in res.text and 'src="/static/ebtools_portfolios.js"' in res.text


def test_the_ebtools_host_gets_the_page_at_its_root(client):
    page = client.get("/", headers={"host": "ebtools.mumbaimun.com"})
    assert page.status_code == 200 and "EB Tools" in page.text

    status = client.get("/")  # every other host (the keep-alive ping) is unchanged
    assert status.json() == {"message": "Server is up and running"}


def test_behind_the_mumbaimun_proxy_every_url_gets_the_prefix(client):
    """mumbaimun.com's nginx sends mumbaimun.com/ebtools/ here as "/" with the prefix."""
    page = client.get("/", headers={"x-forwarded-prefix": "/ebtools"})
    assert page.status_code == 200
    assert 'const BASE = "/ebtools";' in page.text
    assert 'src="/ebtools/static/ebtools_portfolios.js"' in page.text
    assert 'url("/ebtools/static/fonts/Inter-Bold.woff2")' in page.text
    assert "__BASE__" not in page.text


def test_a_prefix_that_is_not_a_plain_path_is_ignored(client):
    for bad in ('/x"><script>', "ebtools", "/eb tools", "//evil.example", "/" + "a" * 40):
        page = client.get("/ebtools", headers={"x-forwarded-prefix": bad})
        assert 'const BASE = "";' in page.text, bad
    # ...and on "/" it is just the status message again
    assert client.get("/", headers={"x-forwarded-prefix": "/x y"}).json() == {"message": "Server is up and running"}


def test_every_asset_the_page_loads_exists(client):
    html = client.get("/ebtools").text
    srcs = re.findall(r'(?:src|href)="(/static/[^"]+)"', html) + re.findall(r'url\("(/static/[^"]+)"\)', html)
    assert len(srcs) >= 5
    for src in srcs:
        assert client.get(src).status_code == 200, src


def test_the_page_only_uses_endpoints_the_api_has(client):
    """It must not call anything the app's EB Tools doesn't: same API, nothing new."""
    html = (ROOT / "static" / "ebtools.html").read_text()
    paths = set(client.app.openapi()["paths"])
    assert "/committees/{committee_id}/floor/ops" in paths and "/login" in paths
    assert "/public/committees/{committee_id}/portfolios" in paths and "/delegates/me" in paths
    for needle in ('"/login"', '"/delegates/me"', '"/public/committees"', "/floor/ops", "/portfolios"):
        assert needle in html, needle
