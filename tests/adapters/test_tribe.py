"""The `tribe` adapter: The Events Calendar's REST API.

Fixtures (tests/fixtures/adapters/tribe/) are Cambridge Calendar's saved API
responses, so the tests read them in Eastern time:

  - mount-auburn-2026-10-06.json.gz     one page, 39 events; [] venues, a [Virtual] event
  - longy-2026-10-06.json.gz            one page, 34 events; shortcodes, off-site venues
  - dance-complex-2026-09-01-page1      page 1 of 31; the venue field is a studio room
  - harvard-square-2026-09-01-page1     page 1 of 17; an image of `False`, [] venues
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime

import pytest
import requests

from src.adapters import tribe
from src.adapters.tribe import TribeEventsAdapter
from src.quality.invariants import check_invariants
from src.sources import Source
from tests.conftest import read_fixture

MOUNT_AUBURN = {"name": "Mount Auburn Cemetery", "street": "580 Mount Auburn Street",
                "city": "Cambridge", "zip": "02138"}
LONGY = {"name": "Longy School of Music", "street": "27 Garden Street", "city": "Cambridge", "zip": "02138"}
DANCE_COMPLEX = {"name": "The Dance Complex", "street": "536 Massachusetts Ave",
                 "city": "Cambridge", "zip": "02139"}


def _payload(name: str) -> dict:
    return json.loads(read_fixture("adapters/tribe", name))


def _adapter(name="Longy School of Music", venue=LONGY, **params) -> TribeEventsAdapter:
    return TribeEventsAdapter(source_name=name, url="https://longy.edu/calendar/", venue=venue, **params)


def _errors(events, now=datetime(2026, 10, 6)):
    return [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=now)
            if v.severity == "error"]


class _Response:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        if not isinstance(self._body, dict):
            raise ValueError("not JSON")
        return self._body


def _serve(monkeypatch, total_pages: int) -> list:
    """Answer like the API for `total_pages` pages: Dance Complex's page 1 again
    and again, each event's URL made unique so de-duplication hides nothing."""
    page_one = _payload("dance-complex-2026-09-01-page1.json.gz")
    requested = []

    def fake_get(url, params=None, **kwargs):
        page = params["page"]
        requested.append(page)
        if page > total_pages:
            return _Response(400, {"code": "rest_invalid_param"})
        body = copy.deepcopy(page_one)
        body["total_pages"] = total_pages
        for item in body["events"]:
            item["url"] = f"{item['url']}?page={page}"
        return _Response(200, body)

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


# --------------------------------------------------------------------------- #
# Paging and fetching
# --------------------------------------------------------------------------- #

def test_every_page_the_api_reports_is_read(monkeypatch, offline, zone):
    """The Dance Complex's cap was 20 pages (1,000 events) while its window
    held 868 and rising; past the cap the loop stopped as if it had reached the
    end. 25 pages must mean 25 requests."""
    zone("America/New_York")
    requested = _serve(monkeypatch, total_pages=25)

    events = _adapter("The Dance Complex", DANCE_COMPLEX).scrape_events()

    assert requested == list(range(1, 26))
    assert len(events) == 25 * 50
    assert any(e.source_url.endswith("?page=25") for e in events)


def test_hitting_the_sanity_cap_is_an_error(monkeypatch, offline, zone, caplog):
    """A cap that truncates must say so. The old loops just stopped."""
    zone("America/New_York")
    requested = _serve(monkeypatch, total_pages=8)

    with caplog.at_level(logging.ERROR, logger=tribe.__name__):
        events = _adapter("The Dance Complex", DANCE_COMPLEX, max_pages=5).scrape_events()

    assert requested == [1, 2, 3, 4, 5]
    assert len(events) == 5 * 50
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and "reports 8 pages but max_pages is 5" in errors[0]


def test_a_complete_listing_logs_nothing_alarming(monkeypatch, offline, zone, caplog):
    zone("America/New_York")
    requested = _serve(monkeypatch, total_pages=3)
    with caplog.at_level(logging.WARNING, logger=tribe.__name__):
        _adapter("The Dance Complex", DANCE_COMPLEX).scrape_events()
    assert requested == [1, 2, 3]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_one_honest_request_per_page_with_a_server_side_window(monkeypatch, offline, zone):
    """Longy's host rate-limits into a challenge page, so one page costs one
    request with no retries, and the client says who it is. The window is
    the API's own (`start_date=now`, `end_date=+N days`), never our clock."""
    zone("America/New_York")
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Response(
        200, _payload("longy-2026-10-06.json.gz")))

    events = _adapter(days=60, categories=["main-stage", 12], venue_ids=[7]).scrape_events()

    assert len(events) == 34 and len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://longy.edu/wp-json/tribe/events/v1/events"
    assert kwargs["params"] == {"per_page": 50, "start_date": "now", "status": "publish", "page": 1,
                                "end_date": "+60 days", "categories": "main-stage,12", "venue": "7"}
    assert "Mozilla" not in kwargs["headers"]["User-Agent"], "never a browser's user-agent"


@pytest.mark.parametrize("response", [
    _Response(403, "<html>Forbidden</html>"),
    _Response(200, "<html>Imunify360 bot protection</html>"),
])
def test_a_refusal_or_a_challenge_fails_the_source(response, monkeypatch, offline):
    """Zero events from a block must be a failure, so the run keeps the
    venue's existing listings instead of recording a venue with nothing on."""
    monkeypatch.setattr(requests, "get", lambda url, **kw: response)
    with pytest.raises((requests.HTTPError, ValueError)):
        _adapter().run()


def test_the_registry_constructs_it_with_its_params(offline):
    """A registry row's `venue` defaults and `params` reach the constructor;
    the venue-id filter is `venue_ids` so it cannot collide with `venue`."""
    source = Source(name="Longy School of Music", kind="requests", adapter="tribe",
                    url="https://longy.edu/calendar/", venue=LONGY,
                    params={"days": 90, "venue_ids": [3], "category": "music"})
    adapter = source.load()
    assert isinstance(adapter, TribeEventsAdapter)
    assert adapter.query(1)["end_date"] == "+90 days" and adapter.query(1)["venue"] == "3"
    assert adapter.venue == LONGY and adapter.category == "music"


# --------------------------------------------------------------------------- #
# Times
# --------------------------------------------------------------------------- #

def test_times_come_from_utc_and_agree_with_the_local_field(zone, offline):
    """Starts are read from `utc_start_date` and converted explicitly, across
    the November clock change, and must equal Tribe's local `start_date`."""
    zone("America/New_York")
    items = _payload("longy-2026-10-06.json.gz")["events"]
    events = {e.source_url: e for e in _adapter().parse_items(items)}

    for item in items:
        assert str(events[item["url"]].start_datetime) == item["start_date"], item["title"]
    vivo = events["https://longy.edu/calendar/vivo-avery-gagliano/"]
    assert (vivo.start_datetime, vivo.end_datetime) == (datetime(2026, 10, 8, 19, 30), datetime(2026, 10, 8, 21, 0))

    auburn = _adapter("Mount Auburn Cemetery", MOUNT_AUBURN).parse_items(
        _payload("mount-auburn-2026-10-06.json.gz")["events"])
    starts = {(e.title, e.start_datetime.date()): e.start_datetime for e in auburn}
    assert starts[("Death Café", datetime(2027, 3, 19).date())] == datetime(2027, 3, 19, 18, 0)              # EDT
    assert starts[("Discover Mount Auburn Walking Tour", datetime(2026, 12, 5).date())] == datetime(2026, 12, 5, 13, 0)  # EST
    assert all(e.start_datetime.tzinfo is None and not e.start_datetime.second for e in auburn)


def test_times_are_stored_in_the_calendars_own_zone(zone, offline):
    """The calendar's zone comes from calendar.config.yaml. The same instant
    read by a Pacific calendar is three hours earlier on the wall clock - a
    naive local string copied through would have kept Boston's 7:30 PM."""
    zone("America/Los_Angeles")
    events = {e.source_url: e for e in _adapter().parse_items(_payload("longy-2026-10-06.json.gz")["events"])}
    vivo = events["https://longy.edu/calendar/vivo-avery-gagliano/"]
    assert vivo.start_datetime == datetime(2026, 10, 8, 16, 30)


def test_a_start_its_fields_disagree_on_is_skipped(zone, caplog):
    """If `start_date` and `utc_start_date` disagree, one of them is wrong and
    nothing says which; the event is skipped, not guessed."""
    zone("America/New_York")
    item = copy.deepcopy(_payload("longy-2026-10-06.json.gz")["events"][0])
    adapter = _adapter()
    assert adapter.parse_item(item) is not None

    shifted = dict(item, start_date="2026-10-08 20:30:00")
    with caplog.at_level(logging.WARNING, logger=tribe.__name__):
        assert adapter.parse_item(shifted) is None
    assert "Skipping" in caplog.text

    # Without the UTC field, the local one is used only if its zone is known
    no_utc = dict(item, utc_start_date=None)
    assert adapter.parse_item(no_utc).start_datetime == datetime(2026, 10, 8, 19, 30)
    assert adapter.parse_item(dict(no_utc, timezone="")) is None
    # Tribe's manual-offset zone ("UTC-4") is a zone too
    assert adapter.parse_item(dict(no_utc, timezone="UTC-4")).start_datetime == datetime(2026, 10, 8, 19, 30)


def test_all_day_events_are_dated_not_timed(zone):
    """Tribe's all_day flag means the venue gave no time; publish the date only."""
    zone("America/New_York")
    item = {"title": "Open House", "status": "publish", "all_day": True, "timezone": "America/New_York",
            "start_date": "2026-11-14 00:00:00", "end_date": "2026-11-14 23:59:59",
            "utc_start_date": "2026-11-14 05:00:00", "utc_end_date": "2026-11-15 04:59:59",
            "url": "https://longy.edu/calendar/open-house/"}
    event = _adapter().parse_item(item)
    assert (event.start_datetime, event.all_day, event.end_datetime) == (datetime(2026, 11, 14), True, None)


# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #

def test_polymorphic_venue_and_image_fields(zone):
    """Tribe returns `venue` and `image` as a dict, [], a list of dicts, or
    False (Harvard Square's "Afro-Cuban Roots"). A dict-only read raised."""
    zone("America/New_York")
    assert tribe._as_dict({"venue": "X"}) == {"venue": "X"}
    assert tribe._as_dict([]) == {} and tribe._as_dict(False) == {} and tribe._as_dict(None) == {}
    assert tribe._as_dict([{"venue": "Y"}]) == {"venue": "Y"}

    items = _payload("harvard-square-2026-09-01-page1.json.gz")["events"]
    assert {type(i["image"]).__name__ for i in items} == {"dict", "bool"}
    assert {type(i["venue"]).__name__ for i in items} == {"dict", "list"}
    events = _adapter("Harvard Square", {"name": "Harvard Square", "city": "Cambridge"}).parse_items(items)
    assert len(events) == 50
    afro = next(e for e in events if e.title.startswith("Afro-Cuban Roots"))
    assert afro.title == "Afro-Cuban Roots – Los Sugar Kings in The Performance Space"
    assert afro.image_url is None


def test_text_is_unescaped_and_free_of_page_builder_markup(zone):
    """Titles arrive as "Outdoor Watercolor &#038; Sketch Class"; Longy's
    descriptions carry WPBakery shortcodes ("[vc_row ...]")."""
    zone("America/New_York")
    auburn = _adapter("Mount Auburn Cemetery", MOUNT_AUBURN).parse_items(
        _payload("mount-auburn-2026-10-06.json.gz")["events"])
    longy = _adapter().parse_items(_payload("longy-2026-10-06.json.gz")["events"])

    assert "Outdoor Watercolor & Sketch Class" in {e.title for e in auburn}
    for e in auburn + longy:
        assert "&#" not in e.title and "&amp;" not in e.title
        assert "&#" not in e.description and "<p" not in e.description and "[vc_" not in e.description
        assert len(e.description) >= 20


def test_venues_rooms_and_online_events(zone):
    """An event's own venue wins (Longy's concert at the Regattabar keeps its
    address). A "[Virtual]" event is online, not at 580 Mount Auburn Street.
    With `rooms`, a venue given without an address - The Dance Complex's
    "Studio 7", Longy's "Edward M. Pickman Hall" - is a room in the registry
    venue rather than a place with no address."""
    zone("America/New_York")
    auburn = _adapter("Mount Auburn Cemetery", MOUNT_AUBURN).parse_items(
        _payload("mount-auburn-2026-10-06.json.gz")["events"])
    virtual = [e for e in auburn if e.title.startswith("[Virtual]")]
    assert virtual and all(e.venue_name == "Online" and e.street_address is None for e in virtual)
    assert {e.venue_name for e in auburn if e not in virtual} >= {"Mount Auburn Cemetery"}

    longy = _adapter(rooms=True).parse_items(_payload("longy-2026-10-06.json.gz")["events"])
    regattabar = [e for e in longy if "Regattabar" in (e.venue_name or "")]
    assert regattabar and regattabar[0].street_address == "One Bennett Street"
    assert all(e.street_address for e in longy)
    pickman = [e for e in longy if e.street_address == "27 Garden Street, Edward M. Pickman Hall"]
    assert pickman and all(e.venue_name == "Longy School of Music" for e in pickman)

    dance = _adapter("The Dance Complex", DANCE_COMPLEX, rooms=True).parse_items(
        _payload("dance-complex-2026-09-01-page1.json.gz")["events"])
    assert {e.venue_name for e in dance} == {"The Dance Complex"}
    assert "536 Massachusetts Ave, Studio 7" in {e.street_address for e in dance}


def test_cancelled_hidden_and_unpublished_listings_are_skipped(zone):
    """Notices are not events, and hidden or draft items are not listed."""
    zone("America/New_York")
    item = _payload("longy-2026-10-06.json.gz")["events"][0]
    adapter = _adapter()
    for title in ("CANCELLED: Vivo Performing Arts", "Vivo Performing Arts (Postponed)",
                  "Vivo Performing Arts - Canceled"):
        assert adapter.parse_item(dict(item, title=title)) is None, title
    assert adapter.parse_item(dict(item, title="The Postponed Wedding, a comic opera")) is not None
    assert adapter.parse_item(dict(item, hide_from_listings=True)) is None
    assert adapter.parse_item(dict(item, status="draft")) is None


@pytest.mark.parametrize("fixture,name,venue", [
    ("mount-auburn-2026-10-06.json.gz", "Mount Auburn Cemetery", MOUNT_AUBURN),
    ("longy-2026-10-06.json.gz", "Longy School of Music", LONGY),
    ("dance-complex-2026-09-01-page1.json.gz", "The Dance Complex", DANCE_COMPLEX),
    ("harvard-square-2026-09-01-page1.json.gz", "Harvard Square", {"name": "Harvard Square"}),
])
def test_output_satisfies_invariants(fixture, name, venue, zone):
    zone("America/New_York")
    events = _adapter(name, venue).parse_items(_payload(fixture)["events"])
    assert events and {e.source_name for e in events} == {name}
    errors = _errors(events)
    assert not errors, "\n".join(str(v) for v in errors)
