"""Drupal Views listings: a start believed only when `<time>` agrees with itself.

Fixtures: the City of Somerville's /calendar pages 9, 10 and 13 (the last),
saved 2026-10-05 by Cambridge Calendar, and two of its event pages saved
2026-10-06 - one with an address, one with no address and the city's
accessibility notices laid out as icons in a table. Times are Eastern.
"""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.adapters import drupal_listing
from src.adapters.drupal_listing import DEFAULTS, DrupalListingAdapter
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

F = "adapters/drupal_listing"
CITY = "https://www.somervillema.gov"
PAGES = {0: "somerville-gov-calendar-2026-10-05-page9.html.gz",
         1: "somerville-gov-calendar-2026-10-05-page10.html.gz",
         2: "somerville-gov-calendar-2026-10-05-page13.html.gz"}


@pytest.fixture(autouse=True)
def eastern(zone):
    zone("America/New_York")


def somerville(**params) -> DrupalListingAdapter:
    return DrupalListingAdapter("City of Somerville", f"{CITY}/calendar", {"city": "Somerville"}, **params)


def _time(datetime_attr: str, text: str):
    return BeautifulSoup(f'<time datetime="{datetime_attr}">{text}</time>', "html.parser").time


def _soups():
    return [BeautifulSoup(read_fixture(F, PAGES[n]), "html.parser") for n in (0, 1)]


def _serve(scraper, pages: dict, details: dict | None = None):
    """Replace the network seam with saved pages; anything else is a 404."""
    fetched = []

    def fetch_html(url, retries=3):
        fetched.append(url)
        if url in pages:
            return pages[url]
        if details and url in details:
            return details[url]
        raise OSError(f"404 {url}")

    scraper.fetch_html = fetch_html
    return fetched


def test_a_start_is_believed_only_when_both_renderings_agree():
    """The attribute is a UTC instant and the text is local wall clock. On
    2026-10-05 every row agreed; a disagreement means one changed meaning,
    and the event is dropped rather than resolved by guessing."""
    read = DrupalListingAdapter.read_time
    assert read(_time("2026-10-05T14:00:00Z", "Mon, October 5, 2026 - 10:00am")) == (datetime(2026, 10, 5, 10, 0), False)
    # Standard time: the offset changes, the wall clock does not
    assert read(_time("2026-12-07T15:00:00Z", "Mon, December 7, 2026 - 10:00am")) == (datetime(2026, 12, 7, 10, 0), False)
    # An evening start is on the local date, not the UTC one
    assert read(_time("2026-10-11T01:00:00Z", "Sat, October 10, 2026 - 9:00pm")) == (datetime(2026, 10, 10, 21, 0), False)

    assert read(_time("2026-10-05T14:00:00Z", "Mon, October 5, 2026 - 2:00pm")) == (None, False)
    assert read(_time("2026-10-05T14:00:00Z", "Tue, October 6, 2026 - 10:00am")) == (None, False)
    assert read(_time("2026-10-05T14:00:00Z", "Mon, October 5, 2026")) == (None, False), \
        "a time the text does not state cannot be checked"
    assert read(_time("", "Mon, October 5, 2026 - 10:00am")) == (None, False)


def test_a_date_only_time_is_all_day_at_midnight():
    """A date with no time is all day at 00:00, never a guessed hour."""
    read = DrupalListingAdapter.read_time
    assert read(_time("2026-10-05", "Mon, October 5, 2026")) == (datetime(2026, 10, 5, 0, 0), True)
    assert read(_time("2026-10-05T04:00:00Z", "Monday, October 5, 2026")) == (datetime(2026, 10, 5, 0, 0), True)
    assert read(_time("2026-10-05", "Tue, October 6, 2026")) == (None, False)
    assert read(_time("2026-10-05", "Mon, October 5, 2026 - 10:00am")) == (None, False)


def test_a_venue_in_another_zone_is_not_shifted_silently(zone):
    """Agreement is checked in the calendar's own zone. A Bay Area calendar
    reading an Eastern listing sees the two renderings disagree and skips,
    instead of publishing 10am events at 7am."""
    zone("America/Los_Angeles")
    assert DrupalListingAdapter.read_time(_time("2026-10-05T14:00:00Z", "Mon, October 5, 2026 - 10:00am")) == (None, False)


def test_only_attendable_events_are_published(offline):
    """The city calendar carries office-closure notices ("Holiday: Thanksgiving"
    at 12:00am) and School Committee executive sessions, which are closed to
    the public. Neither is an event a reader can go to."""
    scraper = somerville()
    soups = _soups()
    listed = [" ".join(t.get_text().split()) for s in soups for t in s.select(".views-field-title")]
    assert any(t.startswith("Holiday:") for t in listed), "fixture should contain a closure notice"
    assert any("Executive Session" in t for t in listed), "fixture should contain an executive session"

    events = [e for s in soups for e in scraper.parse_listing(s)]
    assert len(events) >= 30
    assert not [e.title for e in events if e.title.startswith("Holiday:") or "Executive Session" in e.title]
    assert not [e for e in events if (e.start_datetime.hour, e.start_datetime.minute) == (0, 0)]


def test_exclude_adds_to_the_defaults(offline):
    events = [e for s in _soups() for e in somerville(exclude=[r"vaccine clinic"]).parse_listing(s)]
    assert not [e for e in events if "Vaccine" in e.title]
    assert not [e for e in events if e.title.startswith("Holiday:")], "the defaults still apply"


def test_a_row_without_a_detail_page_links_to_its_day(offline):
    events = [e for s in _soups() for e in somerville().parse_listing(s)]
    unlinked = [e for e in events if "?event_date=" in e.source_url]
    assert unlinked, "fixture should contain a row with no detail link"
    assert all(e.source_url == f"{CITY}/calendar?event_date={e.start_datetime:%Y-%m-%d}" for e in unlinked)


def test_virtual_events_are_online(offline):
    events = [e for s in _soups() for e in somerville().parse_listing(s)]
    virtual = [e for e in events if e.title.startswith("Virtual")]
    assert virtual and all(e.venue_name == "Online" and e.city is None for e in virtual)


def test_paging_stops_at_the_last_page(offline):
    """Page 13 has no pager link to a next page. It does have the calendar's
    own day-pager marked rel="next", which must not be mistaken for one."""
    scraper = somerville(detail_pages=False)
    pages = {scraper.page_url(n): read_fixture(F, name) for n, name in PAGES.items()}
    fetched = _serve(scraper, pages)

    events = scraper.scrape_events()

    assert fetched == [f"{CITY}/calendar", f"{CITY}/calendar?page=1", f"{CITY}/calendar?page=2"]
    assert len(events) > 40
    assert not [str(v) for v in check_invariants([e.model_dump(mode="json") for e in events],
                                                 now=datetime(2026, 10, 6)) if v.severity == "error"]


def test_paging_stops_when_a_page_adds_nothing_new(offline):
    """A pager that repeats its last page must not loop to max_pages."""
    scraper = somerville(detail_pages=False)
    same = read_fixture(F, PAGES[0])
    fetched = _serve(scraper, {scraper.page_url(n): same for n in range(40)})
    scraper.scrape_events()
    assert len(fetched) == 2


def test_a_first_page_that_fails_fails_the_source(offline):
    scraper = somerville()
    _serve(scraper, {})
    with pytest.raises(OSError):
        scraper.scrape_events()


def test_the_venue_comes_from_the_detail_page_and_never_the_date(offline):
    """The listing has no venue; each event's own page supplies its place and a
    fuller description. A detail page that fails just leaves a card with no
    venue - the event and its date stay."""
    scraper = somerville()
    pages = {scraper.page_url(n): read_fixture(F, name) for n, name in PAGES.items()}
    details = {
        f"{CITY}/events/2026/11/19/transgender-day-remembrance-ceremony":
            read_fixture(F, "somerville-gov-event-with-address.html.gz"),
        f"{CITY}/events/2026/11/19/planning-board-meeting":
            read_fixture(F, "somerville-gov-event-with-notices.html.gz"),
    }
    listed = {(e.source_url, e.start_datetime) for s in _soups() for e in scraper.parse_listing(s)}
    _serve(scraper, pages, details)

    events = scraper.scrape_events()

    ceremony = next(e for e in events if e.title == "Transgender Day of Remembrance Ceremony")
    assert (ceremony.venue_name, ceremony.street_address, ceremony.city, ceremony.state, ceremony.zip_code) == (
        "Council on Aging", "167 Holland Street", "Somerville", "MA", "02144")
    assert ceremony.start_datetime == datetime(2026, 11, 19, 17, 0)
    assert ceremony.description.startswith("Mayor Jake Wilson and the Somerville Department")

    board = next(e for e in events if e.title == "Planning Board Meeting" and e.start_datetime.day == 19)
    assert board.description.startswith("Pursuant to Chapter 2 of the Acts of 2025")
    assert "Individuals with disabilities" not in board.description, "the icon-table notice is boilerplate"
    assert board.venue_name is None

    assert listed <= {(e.source_url, e.start_datetime) for e in events}, "no event lost to a failed detail page"


def test_inline_style_is_not_prose():
    html = ('<main><div class="field--name-body"><style>.x{color:red}</style><p>Join us for a walk.</p>'
            '<table><tr><td><img src="ada.png"></td><td>Accessibility notice</td></tr></table></div></main>')
    location, description = somerville().parse_detail(html)
    assert (location, description) == (None, "Join us for a walk.")


def test_selectors_are_params(offline):
    """Another Drupal site's View can use other classes; selectors are registry params."""
    html = ('<div class="event-card"><h3><a href="/e/1">Harvest Festival</a></h3>'
            '<span class="when"><time datetime="2026-10-17T17:00:00Z">Saturday, October 17, 2026 - 1:00pm</time></span>'
            '<div class="teaser">Apples, cider, and music on the common.</div></div>')
    scraper = DrupalListingAdapter("Town", "https://town.example/events", {"name": "Town Common", "city": "Arlington"},
                                   row_selector=".event-card", title_selector="h3", body_selector=".teaser",
                                   detail_pages=False)
    [event] = scraper.parse_listing(BeautifulSoup(html, "html.parser"))
    assert (event.title, event.start_datetime, event.source_url, event.venue_name, event.city) == (
        "Harvest Festival", datetime(2026, 10, 17, 13, 0), "https://town.example/e/1", "Town Common", "Arlington")
    with pytest.raises(TypeError):
        DrupalListingAdapter("Town", "https://town.example/events", {}, rows=".event-card")


def test_the_registry_params_are_the_constructor_params():
    params = inspect.signature(DrupalListingAdapter).parameters
    accepted = {n for n, p in params.items() if p.kind is p.KEYWORD_ONLY} | set(DEFAULTS)
    assert accepted == set(drupal_listing.PARAMS)
