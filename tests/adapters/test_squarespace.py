"""The `squarespace` adapter: an events collection's `?format=json`.

Fixtures (tests/fixtures/adapters/squarespace/) are Cambridge Calendar's saved
payloads of 2026-10-06, trimmed to the fields read, so the tests use Eastern:

  - lilypad-2026-10-06.json.gz         115 upcoming, one a private booking
  - portico-2026-10-06.json.gz         28 upcoming; 17 with an empty location
  - openspace-mit-2026-10-06.json.gz   13 upcoming, 30 past (a RESCHEDULED notice)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest
import requests

from src.adapters import squarespace
from src.adapters.squarespace import SquarespaceEventsAdapter, epoch_ms
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

LILY_PAD = {"name": "The Lily Pad", "street": "1353 Cambridge St", "city": "Cambridge", "zip": "02139"}
PORTICO = {"name": "Portico Brewing", "street": "101 South St", "city": "Somerville", "zip": "02143"}
OPEN_SPACE = {"name": "Kendall/MIT Open Space", "street": "292 Main Street", "city": "Cambridge", "zip": "02142"}

SOURCES = {
    "lilypad": ("The Lily Pad", "https://www.lilypadinman.com/", LILY_PAD),
    "portico": ("Portico Brewing", "https://porticobrewing.com/upcoming-events", PORTICO),
    "openspace-mit": ("MIT Open Space", "https://www.openspace.mit.edu/calendar", OPEN_SPACE),
}


def _payload(key: str) -> dict:
    return json.loads(read_fixture("adapters/squarespace", f"{key}-2026-10-06.json.gz"))


def _adapter(key: str) -> SquarespaceEventsAdapter:
    name, url, venue = SOURCES[key]
    return SquarespaceEventsAdapter(source_name=name, url=url, venue=venue)


def _events(key: str):
    return _adapter(key).parse_collection(_payload(key))


def _respond(monkeypatch, body, status=200) -> list:
    calls = []

    class _Response:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError(f"{status} Client Error")

        def json(self):
            return body

    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Response())
    return calls


def test_every_upcoming_event_in_one_request(monkeypatch, offline, zone):
    """The Selenium scraper opened each detail page with a pause and stopped at
    30 of ~120 shows; its upcoming filter tested a class that does not exist.
    One honest request returns them all, and nothing from `past` is read."""
    zone("America/New_York")
    payload = _payload("lilypad")
    calls = _respond(monkeypatch, payload)

    events = _adapter("lilypad").scrape_events()

    assert [(url, kw["params"]) for url, kw in calls] == [("https://www.lilypadinman.com/", {"format": "json"})]
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"], "never a browser's user-agent"
    assert len(payload["upcoming"]) == 115 and len(events) == 114
    past = {f"https://www.lilypadinman.com{p['fullUrl']}" for p in payload["past"]}
    assert past and not past & {e.source_url for e in events}
    assert max(e.start_datetime for e in events) >= datetime(2027, 1, 1)


def test_epoch_milliseconds_become_local_wall_clock_on_the_minute(zone):
    """startDate is UTC epoch milliseconds with noise (1791329400563). Read
    naively it is either 23:30 or carries .563 seconds, which the validator
    rejects as a clock reading."""
    zone("America/New_York")
    assert epoch_ms(1791329400563) == datetime(2026, 10, 6, 19, 30)    # EDT
    assert epoch_ms(1797724800123) == datetime(2026, 12, 19, 19, 0)    # EST
    assert epoch_ms(None) is None and epoch_ms("soon") is None and epoch_ms(True) is None

    by_title = {e.title: e for e in _events("lilypad")}
    montvales = by_title["The Montvales"]
    assert (montvales.start_datetime, montvales.end_datetime) == (datetime(2026, 10, 6, 19, 30),
                                                                  datetime(2026, 10, 6, 21, 30))
    late = by_title["The AJ Foss Band / The Glory Dogs / Madmax and the Perpetrators"]
    assert late.start_datetime == datetime(2026, 10, 8, 22, 0), "02:00 UTC is 10 PM the evening before"
    # 23:00Z on Nov 6 is 6 PM EST (DST ended Nov 1), not 7 PM
    reel_rock = next(e for e in _events("openspace-mit") if e.title == "Reel Rock")
    assert reel_rock.start_datetime == datetime(2026, 11, 6, 18, 0)


def test_times_are_stored_in_the_calendars_own_zone(zone):
    """The conversion target is the configured zone, not Eastern: a Pacific
    calendar sees the Montvales' 7:30 PM Boston show at 4:30 PM."""
    zone("America/Los_Angeles")
    montvales = next(e for e in _events("lilypad") if e.title == "The Montvales")
    assert montvales.start_datetime == datetime(2026, 10, 6, 16, 30)


def test_descriptions_are_the_post_body_without_editor_placeholders(zone):
    """Bodies carry "Double-click to edit..."; titles carry entities and
    padding. A price followed by "/ 8:15 start" is just the price, and the
    dollar amounts in a trivia listing are prizes, not admission."""
    zone("America/New_York")
    lily = _events("lilypad")
    assert not [e for e in lily if "Double-click" in e.description or "&amp;" in e.description]
    assert all(e.title == e.title.strip() and "&amp;" not in e.title for e in lily)
    gill = next(e for e in lily if e.title == "Gill Aharon Trio")
    assert "cartoon music" in gill.description and gill.cost == "$10"

    trivia = [e for e in _events("portico") if "Trivia" in e.title]
    assert trivia and all(e.cost is None for e in trivia)

    for e in _events("openspace-mit"):
        assert "<p" not in e.description and "&nbsp;" not in e.description and "&amp;" not in e.title


def test_the_venue_is_the_items_own_location_never_squarespaces_default_map(zone):
    """An empty location still carries map coordinates - Squarespace's default,
    in Lower Manhattan. Those must not reach a pin. Events at the MIT Welcome
    Center are not placed at the Open Space."""
    zone("America/New_York")
    portico = _events("portico")
    unplaced = [e for e in portico if e.street_address == "101 South St"]
    assert len(unplaced) == 17
    assert all(e.latitude is None and e.longitude is None for e in unplaced)
    assert all(e.venue_name == "Portico Brewing" and e.city == "Somerville" for e in portico)
    assert not [e for e in portico if e.latitude and e.latitude < 41]

    welcome = next(e for e in _events("openspace-mit") if e.title.startswith("Midday Music & Soup"))
    assert (welcome.venue_name, welcome.street_address, welcome.zip_code) == ("MIT Welcome Center", "292 Main Street", "02142")
    assert welcome.source_url == "https://www.openspace.mit.edu/calendar/midday-music-soup-joseph-borsellino-november-2026"


def test_rescheduled_notices_are_skipped_but_mentions_kept(zone):
    """MIT Open Space renames the old listing "RESCHEDULED: <title>" and posts
    the new date separately. Only a leading marker is a notice; a title that
    mentions the word is an event."""
    zone("America/New_York")
    wiz = [i for i in _payload("openspace-mit")["past"] if "The Wiz" in i["title"]]
    adapter = _adapter("openspace-mit")
    events = adapter.parse_collection({"upcoming": wiz})
    assert [(e.title, e.start_datetime) for e in events] == [("Outdoor Movie: The Wiz", datetime(2026, 9, 3, 18, 30))]

    plain = next(i for i in wiz if not i["title"].startswith("RESCHEDULED"))
    assert adapter.parse_collection({"upcoming": [dict(plain, title="Midday Music: the rescheduled July concert")]})
    for marker in ("RESCHEDULED – Midday Music", "Cancelled: Fall Party", "POSTPONED - Reel Rock",
                   "Fall Party (Canceled)"):
        assert adapter.parse_collection({"upcoming": [dict(plain, title=marker)]}) == [], marker


def test_private_bookings_are_not_public_events(zone):
    """The Lily Pad lists its private parties on the public calendar."""
    zone("America/New_York")
    private = [i for i in _payload("lilypad")["upcoming"] if "private" in i["title"].lower()]
    assert private and _adapter("lilypad").parse_collection({"upcoming": private}) == []


def test_an_unreadable_start_is_skipped_not_now(zone, caplog):
    """The HTML scraper this replaced fell back to datetime.now() at 18:00."""
    zone("America/New_York")
    adapter = _adapter("openspace-mit")
    item = {"title": "Something", "fullUrl": "/calendar/something", "excerpt": "<p>An event that has no date.</p>"}
    with caplog.at_level(logging.WARNING, logger=squarespace.__name__):
        for start in (None, "soon", 0):
            assert adapter.parse_item(dict(item, startDate=start)) is None
    assert "Skipping 'Something'" in caplog.text


def test_a_page_that_is_not_an_events_collection_fails_the_source(monkeypatch, offline):
    """A wrong URL or a shape change must fail loudly, not publish nothing."""
    _respond(monkeypatch, {"items": [], "collection": {"typeName": "page"}})
    with pytest.raises(ValueError, match="upcoming"):
        _adapter("portico").run()
    _respond(monkeypatch, None, status=403)
    with pytest.raises(requests.HTTPError):
        _adapter("portico").run()


@pytest.mark.parametrize("key", sorted(SOURCES))
def test_output_satisfies_invariants(key, zone):
    zone("America/New_York")
    events = _events(key)
    assert events and {e.source_name for e in events} == {SOURCES[key][0]}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
