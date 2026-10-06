"""The `jsonld` adapter: schema.org Event JSON-LD on a listing page.

`comedy-studio-2026-10-06.html.gz` (tests/fixtures/adapters/jsonld/) is The
Comedy Studio's home page as saved by Cambridge Calendar, trimmed to its
JSON-LD: SeatEngine's EventVenue with 163 shows nested under `events`, Oct
2026 to May 2027, in no particular order, with Eastern offsets. Other shapes
(@graph, lists, series) are built inline.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import pytest
import requests

from src.adapters import jsonld
from src.adapters.jsonld import JsonLdAdapter
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

STUDIO = {"name": "The Comedy Studio", "street": "5 John F. Kennedy St", "city": "Cambridge", "zip": "02138"}
HOME = "https://www.thecomedystudio.com/"


def _studio_html() -> str:
    return read_fixture("adapters/jsonld", "comedy-studio-2026-10-06.html.gz")


def _adapter(**params) -> JsonLdAdapter:
    return JsonLdAdapter(source_name="The Comedy Studio", url=HOME, venue=STUDIO, **params)


def _page(*blocks, raw: str = "") -> str:
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks)
    return f"<html><head>{scripts}{raw}</head><body></body></html>"


def _event(**fields) -> dict:
    return {"@context": "https://schema.org", "@type": "Event", "name": "Night Show",
            "startDate": "2026-11-05T19:30:00-08:00", "url": "https://venue.example.org/night-show",
            "description": "An evening of new work by local artists.", **fields}


class _Response:
    def __init__(self, text: str, status: int = 200):
        self.status_code, self.text, self.content = status, text, text.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")


def test_every_show_in_the_listing_is_read(monkeypatch, offline, zone):
    """The scraper took `events[:30]` of a list that is not in date order, so
    it kept an arbitrary slice of a 163-show season. Offsets (-04:00/-05:00)
    become local wall clock; one honest request."""
    zone("America/New_York")
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Response(_studio_html()))

    events = _adapter().scrape_events()

    assert [url for url, _ in calls] == [HOME]
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"]
    assert len(events) == 163
    assert min(e.start_datetime for e in events) == datetime(2026, 10, 6, 19, 0)
    assert max(e.start_datetime for e in events) == datetime(2027, 5, 22, 21, 30)
    kill_tiny = {e.start_datetime: e for e in events if e.title == "Kill Tiny"}
    show = kill_tiny[datetime(2026, 10, 22, 21, 30)]
    assert show.end_datetime == datetime(2026, 10, 22, 23, 0)
    assert (show.venue_name, show.street_address, show.city, show.state) == (
        "The Comedy Studio", "5 John F. Kennedy St", "Cambridge", "MA")
    assert show.cost == "$10.00" and show.source_url.startswith("https://www.thecomedystudio.com/checkout/")
    assert not [e for e in events if "<p" in e.description or "TICKET LINK" in e.description.upper()]

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_a_show_without_its_own_image_uses_its_performers(zone):
    """`performer` is a list of Person objects on this site. The fallback only
    handled a single dict, so it never fired."""
    zone("America/New_York")
    html = _studio_html()
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1),
                      strict=False)
    show = data["events"][0]
    performer_image = show["performer"][0]["image"]
    del show["image"]
    data["events"] = [show]

    [event] = _adapter().parse_page(_page(data))
    assert event.image_url == performer_image


def test_events_are_found_in_graphs_lists_and_series(zone):
    """Plugins publish Events at the top level, in a list, under @graph, or as
    an EventSeries' subEvents; descriptions carry raw newlines, which strict
    JSON rejects, and some blocks are wrapped in CDATA."""
    zone("America/Los_Angeles")
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "Events"},
        _event(name="Graph Show", **{"@type": ["Event", "MusicEvent"]}),
    ]}
    listing = [_event(name="List Show A", startDate="2026-11-06T20:00:00-08:00", **{"@type": "TheaterEvent"}),
               _event(name="List Show B", startDate="2026-11-07T20:00:00-08:00")]
    series = {"@context": "https://schema.org", "@type": "EventSeries", "name": "Fall Season",
              "startDate": "2026-09-01", "subEvent": [_event(name="Series Night", startDate="2026-11-08T19:00:00-08:00")]}
    raw_newline = ('<script type="application/ld+json">//<![CDATA[\n{"@type": "Event", "name": "Raw Newline Show", '
                   '"startDate": "2026-11-09T18:00:00-08:00", "description": "line one\nline two of a long description"}'
                   '\n//]]></script>')

    events = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"}).parse_page(
        _page(graph, listing, series, raw=raw_newline))

    assert sorted(e.title for e in events) == ["Graph Show", "List Show A", "List Show B", "Raw Newline Show", "Series Night"]
    by_title = {e.title: e for e in events}
    assert by_title["Graph Show"].category == "music"
    assert by_title["List Show A"].category == "theater"
    assert by_title["Raw Newline Show"].description == "line one line two of a long description"


def test_date_only_is_all_day_and_a_time_is_used(zone):
    """A date with no time is dated, not timed: never a default hour. A time
    written without an offset is the region's wall clock."""
    zone("America/Los_Angeles")
    adapter = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"})
    events = {e.title: e for e in adapter.parse_page(_page([
        _event(name="Book Fair", startDate="2026-11-14", endDate="2026-11-15"),
        _event(name="Talk", startDate="2026-11-14T18:00"),
        _event(name="Concert", startDate="2026-11-14T19:30:00-08:00", endDate="2026-11-14T21:00:00-08:00"),
    ]))}
    assert (events["Book Fair"].start_datetime, events["Book Fair"].all_day, events["Book Fair"].end_datetime) == (
        datetime(2026, 11, 14), True, None)
    assert (events["Talk"].start_datetime, events["Talk"].all_day) == (datetime(2026, 11, 14, 18, 0), False)
    assert (events["Concert"].start_datetime, events["Concert"].end_datetime) == (
        datetime(2026, 11, 14, 19, 30), datetime(2026, 11, 14, 21, 0))


def test_an_offset_that_is_not_the_regions_is_not_believed(zone, caplog):
    """EventON writes -4:00 all winter; a WordPress site left on UTC writes
    local times as +00:00. Where the offset and the region disagree, nothing
    says which is right, so the event is skipped - loudly - until `offsets`
    says which to trust. A Pacific calendar reading Boston's offsets is the
    same disagreement."""
    zone("America/New_York")
    adapter = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"})
    winter_edt = _event(name="Winter Show", startDate="2027-2-4T19:30-4:00")      # EventON's form, wrong in winter
    assert adapter.parse_page(_page(winter_edt)) == []
    assert JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"}, offsets="wall").parse_page(
        _page(winter_edt))[0].start_datetime == datetime(2027, 2, 4, 19, 30)
    summer = _event(name="Summer Show", startDate="2026-7-9T19:30-4:00")
    assert adapter.parse_page(_page(summer))[0].start_datetime == datetime(2026, 7, 9, 19, 30)

    zone("America/Los_Angeles")
    with caplog.at_level(logging.WARNING, logger=jsonld.__name__):
        assert _adapter().parse_page(_studio_html()) == []
    assert "offset is not America/Los_Angeles's" in caplog.text and "set `offsets`" in caplog.text
    instant = {e.start_datetime for e in _adapter(offsets="instant").parse_page(_studio_html()) if e.title == "Kill Tiny"}
    assert datetime(2026, 10, 22, 18, 30) in instant, "9:30 PM Eastern is 6:30 PM Pacific"
    with pytest.raises(ValueError):
        _adapter(offsets="guess")


def test_cancelled_and_postponed_events_are_skipped(zone):
    zone("America/Los_Angeles")
    adapter = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"})
    assert adapter.parse_page(_page([
        _event(name="Gone", eventStatus="https://schema.org/EventCancelled"),
        _event(name="Later", eventStatus="https://schema.org/EventPostponed"),
        _event(name="CANCELLED: Night Show"),
    ])) == []
    [kept] = adapter.parse_page(_page(_event(eventStatus="https://schema.org/EventRescheduled")))
    assert kept.start_datetime == datetime(2026, 11, 5, 19, 30), "a rescheduled event's startDate is its new date"


def test_places_virtual_locations_and_defaults(zone):
    """The event's own place wins; a VirtualLocation or EventMovedOnline is
    "Online"; an event with no location takes the registry venue."""
    zone("America/Los_Angeles")
    venue = {"name": "Freight & Salvage", "street": "2020 Addison St", "city": "Berkeley", "zip": "94704"}
    adapter = JsonLdAdapter("Freight", "https://thefreight.example.org/", venue)
    place = {"@type": "Place", "name": "The Back Room", "geo": {"latitude": 37.87, "longitude": "-122.27"},
             "address": {"@type": "PostalAddress", "streetAddress": "1984 Bonita Ave",
                         "addressLocality": "Berkeley", "addressRegion": "CA", "postalCode": "94704"}}
    events = {e.title: e for e in adapter.parse_page(_page([
        _event(name="Off-site Show", location=place),
        _event(name="Stringed Address", location={
            "@type": "Place", "name": "Ashkenaz", "address": "1317 San Pablo Ave, Berkeley, CA 94702"}),
        _event(name="Webcast", location={"@type": "VirtualLocation", "url": "https://stream.example.org"}),
        _event(name="Hybrid", location=[{"@type": "VirtualLocation", "url": "https://stream.example.org"}, place]),
        _event(name="Moved", eventStatus="https://schema.org/EventMovedOnline", location=place),
        _event(name="Home Show"),
    ]))}
    off = events["Off-site Show"]
    assert (off.venue_name, off.street_address, off.city, off.state, off.zip_code, off.latitude, off.longitude) == (
        "The Back Room", "1984 Bonita Ave", "Berkeley", "CA", "94704", 37.87, -122.27)
    ash = events["Stringed Address"]
    assert (ash.street_address, ash.city, ash.state, ash.zip_code) == ("1317 San Pablo Ave", "Berkeley", "CA", "94702")
    assert events["Webcast"].venue_name == "Online" and events["Webcast"].street_address is None
    assert events["Moved"].venue_name == "Online"
    assert events["Hybrid"].venue_name == "The Back Room"
    home = events["Home Show"]
    assert (home.venue_name, home.street_address, home.latitude) == ("Freight & Salvage", "2020 Addison St", None)


def test_offers_become_a_cost():
    cost = JsonLdAdapter.cost
    assert cost([{"@type": "Offer", "price": "10.00", "priceCurrency": "USD"}]) == "$10.00"
    assert cost({"@type": "Offer", "price": 0}) == "Free"
    assert cost({"@type": "AggregateOffer", "lowPrice": 15, "highPrice": 40, "priceCurrency": "USD"}) == "$15 - $40"
    assert cost(None) is None and cost([{"url": "https://tickets.example.org"}]) is None


def test_json_ld_without_events_is_empty_and_no_json_ld_is_an_error(zone, caplog):
    """A listing with nothing on is a real answer, and says so; a page with no
    JSON-LD at all means the site changed and the adapter no longer fits."""
    zone("America/Los_Angeles")
    adapter = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"})
    with caplog.at_level(logging.WARNING, logger=jsonld.__name__):
        assert adapter.parse_page(_page({"@context": "https://schema.org", "@type": "Organization", "name": "Venue"})) == []
    assert "has JSON-LD but no events" in caplog.text
    with pytest.raises(ValueError, match="no JSON-LD"):
        adapter.parse_page("<html><body><h1>Events</h1></body></html>")


def test_extra_paths_and_next_links_are_followed_once_each(monkeypatch, offline, zone):
    """`paths` adds listing pages; `max_pages` follows each one's rel=next.
    A page reached twice is fetched once."""
    zone("America/Los_Angeles")
    site = {
        "https://venue.example.org/events": _page(_event(name="Page One Show"))
        + '<link rel="next" href="/events?page=2">',
        "https://venue.example.org/events?page=2": _page(_event(name="Page Two Show", url="/page-two")),
        "https://venue.example.org/workshops": _page(_event(name="Workshop Night", url="/workshop"))
        + '<a rel="next" href="/events?page=2">More</a>',
    }
    fetched = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: fetched.append(url) or _Response(site[url]))

    events = JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"},
                           paths=["/workshops"], max_pages=3).scrape_events()

    assert fetched == ["https://venue.example.org/events", "https://venue.example.org/events?page=2",
                       "https://venue.example.org/workshops"]
    assert sorted(e.title for e in events) == ["Page One Show", "Page Two Show", "Workshop Night"]
    assert {e.source_url for e in events} >= {"https://venue.example.org/page-two", "https://venue.example.org/workshop"}

    fetched.clear()
    JsonLdAdapter("Venue", "https://venue.example.org/events", {"name": "Venue"}).scrape_events()
    assert fetched == ["https://venue.example.org/events"], "max_pages defaults to the page itself"
