"""`cal detect`: which adapter fits a page, from saved pages only.

Each platform case is a real page saved from a Cambridge Calendar venue, served
through an injected `fetch`, along with the one probe answer it needs (the
Tribe REST endpoint, Squarespace's ?format=json, the Localist API, an Assabet
listing). Any URL not in a case's map answers 404, so a probe that wanders is
visible. Cases whose adapter module does not exist yet are skipped.
"""
from __future__ import annotations

import pytest

from src import adapters
from src.detect import MAX_REQUESTS, DetectError, Match, detect
from tests.conftest import read_fixture

ASSABET = "https://somervillepubliclibrary.assabetinteractive.com"


def serve(pages: dict):
    """A fake fetch over {url: (status, fixture folder, fixture name, final url or None)}."""
    calls = []

    def fetch(url):
        calls.append(url)
        if url not in pages:
            return 404, "<html><title>Page not found</title></html>", url
        status, folder, name, final = pages[url]
        return status, read_fixture(folder, name), final or url

    return fetch, calls


def needs(*names):
    missing = [n for n in names if n not in adapters.available()]
    if missing:
        pytest.skip(f"adapter(s) not written yet: {missing}")


def run(url: str, pages: dict):
    fetch, calls = serve(pages)
    matches = detect(url, fetch=fetch)
    assert len(calls) <= MAX_REQUESTS, f"{len(calls)} requests for one URL"
    return matches, calls


# --------------------------------------------------------------------------- #
# Blocked pages
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", [403, 503, 200])
def test_a_challenge_page_is_reported_blocked_and_nothing_else(status, offline):
    """A Cloudflare interstitial is still HTML. Matching a platform against it
    would produce a scraper that parses nothing, forever. Detection reports
    the block - whatever status the interstitial came with - and probes nothing."""
    url = "https://portersquarebooks.com/events/calendar"
    matches, calls = run(url, {url: (status, "detect", "cloudflare-challenge.html.gz", None)})

    assert [m.adapter for m in matches] == ["blocked"]
    assert matches[0].score == 1.0
    assert "Just a moment" in matches[0].evidence
    assert calls == [url]


def test_a_bare_403_is_blocked(offline):
    url = "https://venue.example/events"
    fetch = lambda u: (403, "<html><title>403 Forbidden</title></html>", u)  # noqa: E731
    [match] = detect(url, fetch=fetch)
    assert (match.adapter, match.score) == ("blocked", 1.0)
    assert "403" in match.evidence


def test_a_missing_page_is_an_error_not_a_verdict(offline):
    """A 404 says nothing about the platform: it says the URL is wrong."""
    url = "https://venue.example/events"
    with pytest.raises(DetectError, match="404"):
        detect(url, fetch=lambda u: (404, "<title>Not found</title>", u))


def test_a_probe_that_is_refused_says_so(offline):
    """An IndieCommerce store whose calendar page is challenged is reported as
    a likely IndieCommerce store whose calendar refused the probe - not as
    confirmed, and not as nothing."""
    needs("indiecommerce")
    home = "https://store.example/"
    page = '<html><head><title>Store</title><script>{"indiecommerceNewsletter":{}}</script></head></html>'
    calls = []

    def fetch(url):
        calls.append(url)
        if url == home:
            return 200, page, url
        return 403, read_fixture("detect", "cloudflare-challenge.html.gz"), url

    matches = detect(home, fetch=fetch)
    match = next(m for m in matches if m.adapter == "indiecommerce")
    assert match.score < 0.8
    assert "refused" in match.evidence
    assert match.suggested_url == "https://store.example/events/calendar"
    assert calls == [home, "https://store.example/events/calendar"]


# --------------------------------------------------------------------------- #
# The four adapters written alongside this module
# --------------------------------------------------------------------------- #

def test_eventon_month_calendar(offline):
    needs("eventon")
    url = "https://www.centralsquaretheater.org/calendar/"
    matches, calls = run(url, {url: (200, "adapters/eventon", "central-square-calendar-2026-10-06.html.gz", None)})

    assert matches[0].adapter == "eventon" and matches[0].score >= 0.8
    assert "calendar_type fullcal" in matches[0].evidence
    assert calls == [url], "the proof is in the page; no probe request needed"


def test_eventon_list_calendar_whose_page_has_only_shells(offline):
    """The Regent's page as served has no listings at all - only EventON's
    settings. That is enough to know the adapter can read it."""
    needs("eventon")
    url = "https://regenttheatre.com/schedule/list/"
    matches, _ = run(url, {url: (200, "adapters/eventon", "regent-schedule-list-raw-2026-10-06.html.gz", None)})
    assert matches[0].adapter == "eventon" and matches[0].score >= 0.8
    assert "calendar_type el" in matches[0].evidence


def test_assabet_listing(offline):
    needs("assabet")
    url = f"{ASSABET}/calendar/event-listing/"
    month = f"{ASSABET}/calendar/2026-november/event-listing/"
    matches, calls = run(url, {url: (200, "adapters/assabet", "somerville-listing-2026-11.html.gz", month)})

    assert matches[0].adapter == "assabet" and matches[0].score >= 0.8
    assert matches[0].suggested_url == url, "the undated listing, not the month it redirected to"
    assert calls == [url]
    jsonld = [m for m in matches if m.adapter == "jsonld"]
    assert not jsonld or jsonld[0].score < matches[0].score, "the platform beats its own JSON-LD"


def test_a_library_site_that_links_its_assabet_calendar(offline):
    """Libraries embed Assabet on their own site. Detection follows the link to
    the calendar, confirms it there, and says where the adapter should read."""
    needs("assabet")
    home = "https://www.somervillepubliclibrary.org/"
    matches, calls = run(home, {
        home: (200, "detect", "assabet-embedding-library-home.html.gz", "https://somervillepubliclibrary.org/"),
        f"{ASSABET}/calendar/event-listing/": (200, "adapters/assabet", "somerville-listing-2026-11.html.gz", None),
    })
    assert matches[0].adapter == "assabet" and matches[0].score >= 0.8
    assert matches[0].suggested_url == f"{ASSABET}/calendar/event-listing/"
    assert calls == [home, f"{ASSABET}/calendar/event-listing/"]


def test_indiecommerce_calendar(offline):
    """An IndieCommerce store is a Drupal site too; the platform adapter must
    outrank the generic Drupal listing reader."""
    needs("indiecommerce")
    url = "https://portersquarebooks.com/events/calendar"
    matches, calls = run(url, {url: (200, "adapters/indiecommerce", "porter-calendar-2026-10.html.gz", None)})

    assert matches[0].adapter == "indiecommerce" and matches[0].score >= 0.8
    assert "45 events" in matches[0].evidence
    assert matches[0].suggested_url == url
    assert all(m.score < matches[0].score for m in matches[1:])
    assert calls == [url]


def test_indiecommerce_home_page_points_to_the_calendar(offline):
    needs("indiecommerce")
    home = "https://portersquarebooks.com/"
    page = ('<html><head><title>Porter Square Books</title><script type="application/json" '
            'data-drupal-selector="drupal-settings-json">{"indiecommerceNewsletter":{}}</script></head>'
            '<body><a href="/events/calendar">Events</a></body></html>')
    calls = []

    def fetch(url):
        calls.append(url)
        if url == home:
            return 200, page, url
        if url == "https://portersquarebooks.com/events/calendar":
            return 200, read_fixture("adapters/indiecommerce", "porter-calendar-2026-10.html.gz"), url
        return 404, "", url

    matches = detect(home, fetch=fetch)
    assert matches[0].adapter == "indiecommerce" and matches[0].score >= 0.8
    assert matches[0].suggested_url == "https://portersquarebooks.com/events/calendar"


def test_drupal_listing(zone, offline):
    """Detection checks agreement in the calendar's zone; these rows are Eastern."""
    needs("drupal_listing")
    zone("America/New_York")
    url = "https://www.somervillema.gov/calendar"
    matches, _ = run(url, {url: (200, "adapters/drupal_listing", "somerville-gov-calendar-2026-10-05-page9.html.gz", None)})
    assert matches[0].adapter == "drupal_listing"
    assert matches[0].score >= 0.55
    assert "20 of 20 rows" in matches[0].evidence


def test_drupal_listing_in_another_zone_is_flagged(zone, offline):
    """A Bay Area calendar pointed at an Eastern listing hears that the times
    disagree in its zone, instead of a clean match it would then mis-time."""
    needs("drupal_listing")
    zone("America/Los_Angeles")
    url = "https://www.somervillema.gov/calendar"
    matches, _ = run(url, {url: (200, "adapters/drupal_listing", "somerville-gov-calendar-2026-10-05-page9.html.gz", None)})
    match = next(m for m in matches if m.adapter == "drupal_listing")
    assert match.score < 0.55
    assert "America/Los_Angeles" in match.evidence


# --------------------------------------------------------------------------- #
# Platforms whose adapters are written elsewhere: detect codes to the contract
# --------------------------------------------------------------------------- #

def test_tribe_beats_its_own_json_ld_and_ical_links(offline):
    """A The Events Calendar site also carries JSON-LD Events and iCal export
    links. The REST endpoint answering JSON confirms Tribe, which reads the
    site better than either generic reader."""
    needs("tribe")
    url = "https://therockwell.org/calendar/"
    rest = "https://therockwell.org/wp-json/tribe/events/v1/events?per_page=1"
    matches, calls = run(url, {url: (200, "detect", "tribe-rockwell-calendar.html.gz", None),
                               rest: (200, "detect", "tribe-rest-events.json.gz", None)})
    assert matches[0].adapter == "tribe" and matches[0].score >= 0.8
    assert rest in calls
    assert "Tribe REST API answers JSON" in matches[0].evidence
    generic = [m for m in matches if m.adapter in ("ical", "jsonld")]
    assert generic and all(m.score < matches[0].score for m in generic)


def test_squarespace_events_collection(offline):
    needs("squarespace")
    url = "https://www.firstparishcambridge.org/events/"
    matches, calls = run(url, {url: (200, "detect", "squarespace-firstparish-events.html.gz", None),
                               f"{url}?format=json": (200, "detect", "squarespace-format-json.json.gz", None)})
    assert matches[0].adapter == "squarespace" and matches[0].score >= 0.8
    assert "events collection" in matches[0].evidence
    assert calls == [url, f"{url}?format=json"]


def test_localist_beats_its_ical_and_json_ld(offline):
    needs("localist")
    url = "https://calendar.mit.edu/"
    api = "https://calendar.mit.edu/api/2/events?pp=1"
    matches, calls = run(url, {url: (200, "detect", "localist-mit-home.html.gz", None),
                               api: (200, "detect", "localist-api-events.json.gz", None)})
    assert matches[0].adapter == "localist" and matches[0].score >= 0.8
    assert api in calls


def test_a_google_calendar_embed_suggests_its_ical_feed(offline):
    """Theatre@First publishes a Google Calendar embed; its public feed is
    the thing to read, and detection names it."""
    needs("ical")
    url = "https://www.theatreatfirst.org/learn-more/calendar"
    matches, _ = run(url, {url: (200, "detect", "ical-google-embed-calendar.html.gz", None)})
    assert matches[0].adapter == "ical"
    assert matches[0].suggested_url == ("https://calendar.google.com/calendar/ical/"
                                        "ckof39gfrbnt72qpjph558qu3k%40group.calendar.google.com/public/basic.ics")


def test_json_ld_events(offline):
    needs("jsonld")
    url = "https://brattlefilm.org/coming-soon/"
    matches, calls = run(url, {url: (200, "detect", "jsonld-brattle-listing.html.gz", None)})
    assert matches[0].adapter == "jsonld"
    assert "schema.org Event" in matches[0].evidence


# --------------------------------------------------------------------------- #
# The mechanics
# --------------------------------------------------------------------------- #

def test_probe_requests_stay_within_budget(offline):
    """A page that hints at every platform at once still costs at most
    MAX_REQUESTS requests in total."""
    page = ("<html><head><title>Everything</title></head><body>"
            "tribe-events squarespace localist indiecommerce "
            '<a href="https://x.assabetinteractive.com/calendar/">library</a></body></html>')
    calls = []

    def fetch(url):
        calls.append(url)
        return (200, page, url) if len(calls) == 1 else (404, "", url)

    detect("https://everything.example/", fetch=fetch)
    assert 1 < len(calls) <= MAX_REQUESTS


def test_a_plain_page_matches_nothing(offline):
    url = "https://venue.example/"
    assert detect(url, fetch=lambda u: (200, "<html><title>Hello</title><p>We are a bar.</p></html>", u)) == []


def test_a_redirect_is_reported_as_where_to_read(offline):
    needs("eventon")
    url = "https://www.centralsquaretheater.org/events"
    landed = "https://www.centralsquaretheater.org/calendar/"
    matches, _ = run(url, {url: (200, "adapters/eventon", "central-square-calendar-2026-10-06.html.gz", landed)})
    assert matches[0].suggested_url == landed


def test_match_serializes_for_the_cli():
    match = Match("eventon", 0.9, "an EventON calendar", None)
    assert match.to_dict() == {"adapter": "eventon", "score": 0.9, "evidence": "an EventON calendar",
                               "suggested_url": None}


def test_the_default_fetch_identifies_honestly(monkeypatch):
    """Detection is the first contact with a venue. It sends the calendar's own
    user-agent, never an impersonation of a browser."""
    import requests

    from src.config import USER_AGENT

    seen = {}

    class _Response:
        status_code, text, url = 200, "<html><title>Hi</title></html>", "https://venue.example/"

    def fake_get(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return _Response()

    monkeypatch.setattr(requests, "get", fake_get)
    detect("https://venue.example/")
    assert seen["User-Agent"] == USER_AGENT
    assert "Mozilla" not in seen["User-Agent"]


def test_cal_detect_prints_the_best_match(monkeypatch, capsys, offline):
    """The CLI calls detect(url) with the default fetch."""
    import argparse

    import src.detect as detect_module
    from src.cli import cmd_detect

    needs("eventon")
    page = read_fixture("adapters/eventon", "central-square-calendar-2026-10-06.html.gz")
    monkeypatch.setattr(detect_module, "http_fetch", lambda url: (200, page, url))

    code = cmd_detect(argparse.Namespace(url="https://www.centralsquaretheater.org/calendar/", json=False))
    assert code == 0
    assert "eventon" in capsys.readouterr().out
