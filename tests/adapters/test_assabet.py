"""Assabet Interactive: JSON-LD dates believed only when the visible card agrees.

Fixture: the Somerville Public Library's November 2026 month listing, saved
2026-10-05 by Cambridge Calendar. It has every quirk at once: JSON-LD with raw
newlines, excerpts entity-encoded twice, cancelled events, "All Closed" cards,
online programs, and three branches.
"""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.adapters import assabet
from src.adapters.assabet import AssabetAdapter
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

F = "adapters/assabet"
HOST = "https://somervillepubliclibrary.assabetinteractive.com"
LISTING = f"{HOST}/calendar/event-listing/"
VENUE = {"name": "Somerville Public Library", "street": "79 Highland Ave", "city": "Somerville", "zip": "02143"}


@pytest.fixture(autouse=True)
def eastern(zone):
    zone("America/New_York")


@pytest.fixture
def november():
    return read_fixture(F, "somerville-listing-2026-11.html.gz")


def library(**params) -> AssabetAdapter:
    return AssabetAdapter("Somerville Public Library", HOST, VENUE, **params)


def _card(day: str, time: str, branch: str = "", room: str = "", address: str = "") -> BeautifulSoup:
    parts = [f'<span class="event-day">{day}</span>', f'<span class="event-time">{time}</span>']
    if branch:
        parts.append(f'<span class="event-location-branch">{branch}</span>')
    if room:
        parts.append(f'<span class="event-location-location">{room}</span>')
    if address:
        parts.append(f'<span class="event-location-address">{address}</span>')
    return BeautifulSoup(f'<div class="listing-event">{"".join(parts)}</div>', "html.parser").div


def test_every_scheduled_event_is_read(november, offline):
    """Every scheduled event must come through, dated in November, with
    readable text: a JSON-LD block that fails strict parsing, or an over-strict
    cross-check, would silently drop real events."""
    scraper = library()
    soup = BeautifulSoup(november, "html.parser")
    structured = scraper.json_ld_by_url(soup)
    cards = [c for c in soup.select("div.listing-event") if c.select_one("h3 a[href]")]
    cancelled = [u for u, d in structured.items() if d["eventStatus"].endswith("EventCancelled")]
    assert soup.select("div.listing-event.branch-closed"), "fixture should contain closure cards"
    assert cancelled, "fixture should contain cancelled events"

    events = scraper.parse_month(soup)

    assert len(structured) == len(cards), "a JSON-LD block failed to parse"
    assert len(events) == len(cards) - len(cancelled), "the date cross-check rejected a real event"
    assert all((e.start_datetime.year, e.start_datetime.month) == (2026, 11) for e in events)
    assert not [e.description for e in events
                if any(junk in e.description for junk in ("&amp;", "&nbsp;", "&#", "Learn More", "<"))]
    assert not [e for e in events if e.title.startswith("All Closed")]


def test_the_venue_comes_from_the_branch_or_says_online(november, offline):
    """A branch is published under the library's name, with the card's own
    address; a Zoom program is "Online" with no street, not the main branch."""
    events = library().parse_month(BeautifulSoup(november, "html.parser"))

    branches = [e for e in events if e.venue_name.startswith("Somerville Public Library – ")]
    assert {e.venue_name for e in branches} == {
        "Somerville Public Library – Central Library", "Somerville Public Library – West Branch",
        "Somerville Public Library – East Branch"}
    assert all(e.street_address and e.zip_code and e.city == "Somerville" and e.state == "MA" for e in branches)
    west = next(e for e in branches if e.venue_name.endswith("West Branch"))
    assert (west.street_address, west.zip_code) == ("40 College Ave", "02144")
    online = [e for e in events if e.venue_name == "Online"]
    assert online and all(e.street_address is None and e.city is None for e in online)


def test_branch_prefix_names_the_branches(november, offline):
    events = library(branch_prefix="SPL").parse_month(BeautifulSoup(november, "html.parser"))
    assert any(e.venue_name == "SPL – Central Library" for e in events)


def test_an_off_site_event_is_at_its_own_place():
    """With no branch, the card's place and address are the venue - an off-site
    program must not be pulled back to the main library's address."""
    card = _card("Tuesday, November 3", "6:00—7:00 PM", room="Davis Square Plaza",
                 address="1 Elm St, Somerville, MA, 02144")
    place = library().location(card, {})
    assert (place["venue_name"], place["street_address"], place["zip_code"]) == ("Davis Square Plaza", "1 Elm St", "02144")


def test_the_start_shares_the_end_meridiem():
    """Assabet writes "6:00—7:00 PM": the start borrows the end's meridiem.
    Reading the bare "6:00" as morning would fail the cross-check and drop
    every evening program."""
    card = _card("Tuesday, November 3", "6:00—7:00 PM")
    start = library().start
    assert start(card, {"startDate": "2026-11-03", "doorTime": "18:00:00"}) == (datetime(2026, 11, 3, 18, 0), False)


def test_a_card_that_disagrees_with_the_json_ld_is_skipped():
    """If doorTime ever means doors-open, or the card shows another day, the
    two renderings disagree and the event is skipped, not resolved by guessing."""
    card = _card("Tuesday, November 3", "6:00—7:00 PM")
    start = library().start
    assert start(card, {"startDate": "2026-11-03", "doorTime": "17:30:00"}) == (None, False)
    assert start(card, {"startDate": "2026-11-04", "doorTime": "18:00:00"}) == (None, False)
    assert start(_card("Tuesday, November 3", "6:00—7:00"), {"startDate": "2026-11-03", "doorTime": "18:00:00"}) == (None, False), \
        "no meridiem anywhere: unreadable, not morning"
    assert start(card, {"startDate": "2026-11-03"}) == (None, False), "no doorTime: nothing to check"


def test_an_all_day_card_is_midnight_and_flagged():
    """A date with no time is all day at 00:00, never a guessed time."""
    card = _card("Saturday, November 7", "All Day")
    assert library().start(card, {"startDate": "2026-11-07"}) == (datetime(2026, 11, 7, 0, 0), True)


def test_times_do_not_move_with_the_calendar_zone(november, zone, offline):
    """Assabet's times carry no offset: they are the library's wall clock and
    must read the same whatever zone the calendar is configured for."""
    soup = BeautifulSoup(november, "html.parser")
    eastern = {(e.source_url, e.start_datetime) for e in library().parse_month(soup)}
    zone("America/Los_Angeles")
    pacific = {(e.source_url, e.start_datetime) for e in library().parse_month(soup)}
    assert eastern == pacific
    assert all(start.tzinfo is None for _, start in eastern)


def test_output_satisfies_invariants(november, offline):
    events = library().parse_month(BeautifulSoup(november, "html.parser"))
    errors = [str(v) for v in check_invariants([e.model_dump(mode="json") for e in events],
                                               now=datetime(2026, 10, 6)) if v.severity == "error"]
    assert not errors
    assert {e.source_name for e in events} == {"Somerville Public Library"}


def test_months_chain_by_the_listings_own_next_link(november, offline):
    """No clock is read: the next month is the link the listing prints, and a
    month that adds nothing is the edge of what has been published."""
    scraper = library()
    fetched = []
    december = f"{HOST}/calendar/2026-december/event-listing/?from=next"

    def fetch_html(url, retries=3):
        fetched.append(url)
        return november if url == LISTING else "<html><title>December</title></html>"

    scraper.fetch_html = fetch_html
    events = scraper.scrape_events()

    assert fetched == [LISTING, december]
    assert len(events) == 146


def test_a_first_page_that_fails_fails_the_source(offline):
    """An unreachable listing must raise, not return [] - zero events from a
    page that never loaded would be recorded as "ok, 0 events"."""
    scraper = library()

    def refused(url, retries=3):
        raise OSError("403 Forbidden")

    scraper.fetch_html = refused
    with pytest.raises(OSError):
        scraper.scrape_events()


def test_the_listing_url_is_derived_from_any_calendar_url():
    """A registry URL naming one month must not pin every run to that month."""
    assert library().source_url == LISTING
    month = AssabetAdapter("X", f"{HOST}/calendar/2026-november/event-listing/", {})
    assert month.source_url == LISTING
    assert library().branch_prefix == "Somerville Public Library"
    assert AssabetAdapter("Some Library", HOST, {}).branch_prefix == "Some Library"


def test_the_registry_params_are_the_constructor_params():
    accepted = {n for n, p in inspect.signature(AssabetAdapter).parameters.items() if p.kind is p.KEYWORD_ONLY}
    assert accepted == set(assabet.PARAMS)
