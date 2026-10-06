"""The `localist` adapter: a Localist calendar's `/api/2/events`.

Fixtures (tests/fixtures/adapters/localist/) are the seven pages of MIT's
`api/2/events?days=60&pp=100` fetched by Cambridge Calendar on 2026-10-06
(665 occurrences of 392 events), trimmed to the fields read. Times are Eastern.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest
import requests

from src.adapters import localist
from src.adapters.localist import LocalistAdapter
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

PAGES = 7
MIT = {"name": "MIT", "city": "Cambridge"}


def _page(n: int) -> dict:
    return json.loads(read_fixture("adapters/localist", f"mit-60d-2026-10-06-page{n}.json.gz"))


def _items() -> list:
    return [x["event"] for n in range(1, PAGES + 1) for x in _page(n)["events"]]


def _adapter(**params) -> LocalistAdapter:
    params.setdefault("affiliation", ["MIT"])
    return LocalistAdapter(source_name="MIT Events", url="https://calendar.mit.edu/", venue=MIT, **params)


@pytest.fixture
def api(monkeypatch, offline):
    """The API, answering by page from the fixtures."""
    requested = []

    def fake_get(url, params=None, headers=None, **kwargs):
        requested.append({"url": url, "headers": headers, **params})
        body = _page(int(params["page"]))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


def test_reads_the_whole_calendar_page_by_page(api, zone):
    """MIT's Playwright scraper read the homepage JSON-LD: about 5% of the
    calendar. The API is paged by `page.total`; every page is read, with the
    window set server-side and an honest user-agent."""
    zone("America/New_York")
    events = _adapter().scrape_events()

    assert [p["page"] for p in api] == list(range(1, PAGES + 1))
    assert all(p["url"] == "https://calendar.mit.edu/api/2/events" for p in api)
    assert all(p["days"] == 60 and p["pp"] == 100 for p in api)
    assert "Mozilla" not in api[0]["headers"]["User-Agent"]
    # 169 public occurrences of 139 events; two are re-posts of one seminar
    assert len(events) == 167
    assert len({e.source_url for e in events}) == 137
    days = {e.start_datetime.date() for e in events}
    assert min(days) == datetime(2026, 10, 6).date() and max(days) >= datetime(2026, 12, 1).date()

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_only_events_open_to_the_general_public(zone):
    """Most of a university calendar is internal. An event is kept if its
    audience includes "Public" or its text says it is open to the public,
    unless its text restricts it."""
    zone("America/New_York")
    items = _items()
    published = {e.source_url for e in _adapter().parse_items(items)}

    def audience(item):
        return {a["name"] for a in item["filters"].get("event_audience", [])}

    community_only = [i for i in items if audience(i) == {"MIT Community"}
                      and "open to the public" not in (i["description_text"] or "").lower()]
    assert len(community_only) > 100, "the fixture should be mostly internal"
    assert not [i for i in community_only if i["localist_url"] in published]

    wreath = next(i for i in items if i["title"].startswith("Coffee Social & Holiday Wreath"))
    assert "Public" in audience(wreath) and wreath["localist_url"] not in published, "tagged Public, text says otherwise"

    book = next(i for i in items if i["title"].startswith("Our Own Language"))
    assert not audience(book) and book["localist_url"] in published, "untagged, but 'Free and open to the public'"

    # audience: null keeps everything its text does not restrict
    everything = _adapter(audience=None).parse_items(items)
    assert len(everything) > 600 and wreath["localist_url"] not in {e.source_url for e in everything}


@pytest.mark.parametrize("text,public", [
    ("Free and open to the public.", True),
    ("This event is open the MIT Community only.", False),
    ("This event is invite-only for the MIT community.", False),
    ("This lecture is not open to the public.", False),
    ("Tickets $10, free for MIT students only.", None),   # a price, not an audience
    ("MIT ID required for entry.", False),
    ("This tour is open only to the MIT community and their guests.", False),
])
def test_audience_statements(text, public):
    adapter = _adapter()
    untagged = {"title": "Talk", "description_text": text, "filters": {"event_audience": []}}
    tagged = {"title": "Talk", "description_text": text, "filters": {"event_audience": [{"name": "Public"}]}}
    if public is None:
        assert adapter.is_public(tagged) is True
        assert adapter.is_public(untagged) is False
    else:
        assert adapter.is_public(untagged) is public
        if public is False:
            assert adapter.is_public(tagged) is False


def test_institution_rules_follow_the_affiliation_param():
    """"MIT ID required" restricts an MIT event; at another university it is
    that university's ID. Without an affiliation, only the generic rules apply."""
    tagged = {"title": "Talk", "filters": {"event_audience": [{"name": "Public"}]}}
    assert _adapter(affiliation=["Stanford"]).is_public(dict(tagged, description_text="Stanford ID required.")) is False
    assert _adapter(affiliation=[]).is_public(dict(tagged, description_text="Stanford ID required.")) is True
    assert _adapter(affiliation=[]).is_public(dict(tagged, description_text="Invite-only reception.")) is False


def test_times_are_local_wall_clock_and_all_day_is_dated(zone):
    """`start` carries an offset ("2026-10-06T18:30:00-04:00"); the model stores
    naive local time. An all-day instance is flagged all-day, not given a time."""
    zone("America/New_York")
    events = _adapter().parse_items(_items())
    assert all(e.start_datetime.tzinfo is None for e in events)

    eakin = next(e for e in events if "Emily Eakin" in e.title)
    assert eakin.start_datetime == datetime(2026, 10, 6, 18, 30)
    assert (eakin.venue_name, eakin.street_address, eakin.city, eakin.state) == (
        "Boston French Library", "53 Marlborough Street", "Boston", "MA")

    whitney = [e for e in events if e.title.startswith("What Are People For")]
    assert whitney and all(e.all_day and e.start_datetime == datetime(2026, 10, 24) for e in whitney)
    assert not [e for e in events if e.all_day and e.title != whitney[0].title]


def test_all_day_dates_survive_a_different_calendar_zone(zone):
    """Converted to Pacific, midnight Eastern is 9 PM the day before. An
    all-day instance keeps the date it is written on; timed ones convert."""
    zone("America/Los_Angeles")
    events = _adapter().parse_items(_items())
    whitney = [e for e in events if e.title.startswith("What Are People For")]
    assert whitney and all(e.start_datetime == datetime(2026, 10, 24) for e in whitney)
    eakin = next(e for e in events if "Emily Eakin" in e.title)
    assert eakin.start_datetime == datetime(2026, 10, 6, 15, 30)


def test_reposted_events_are_one_event(zone):
    """"symplectic-geometry-seminar" and "copy-of-copy-of-copy-of-symplectic-
    geometry-seminar" are the same talk, room and time under two URLs."""
    zone("America/New_York")
    seminar = [e for e in _adapter().parse_items(_items()) if e.title == "Symplectic Geometry Seminar"
               and e.start_datetime == datetime(2026, 10, 8, 16, 30)]
    assert len(seminar) == 1


def test_holidays_are_not_events_and_virtual_events_are_online(zone):
    """Institute Holidays are closures. A virtual event is "Online", not placed
    on campus. `exclude_types` replaces the default list."""
    zone("America/New_York")
    items = _items()
    events = _adapter().parse_items(items)
    assert not [e for e in events if e.title.strip() in ("Veterans Day", "Thanksgiving Day")]
    online = [e for e in events if e.venue_name == "Online"]
    assert online and all(e.street_address is None and e.latitude is None for e in online)

    holidays = [i for i in items if any(t["name"] == "Institute Holidays" for t in i["filters"].get("event_types", []))]
    assert holidays and _adapter(audience=None).parse_items(holidays) == []
    assert _adapter(audience=None, exclude_types=["Exhibits"]).parse_items(holidays)


def test_a_start_without_an_offset_is_refused():
    """Localist always sends an offset and whole minutes. Anything else means
    the field changed meaning, and a guess would move events by hours."""
    read = LocalistAdapter.read_instant
    assert read("2026-10-06T18:30:00-04:00") is not None
    assert read("2026-10-06T18:30:00") is None
    assert read("2026-10-06T18:30:17-04:00") is None
    assert read("") is None and read(None) is None and read("soon") is None


def test_a_calendar_without_the_audience_filter_is_reported(zone, caplog):
    """Another Localist may name its audience filter differently, or have none.
    Judging every event on its text alone would quietly drop most of them, so
    the run says so; `audience: null` is the deliberate way to read them all."""
    zone("America/New_York")
    items = [dict(i, filters={"event_types": i["filters"].get("event_types", [])}) for i in _items()]
    with caplog.at_level(logging.ERROR, logger=localist.__name__):
        kept = _adapter().parse_items(items)
    assert "none of 665 events carries the 'event_audience' filter" in caplog.text
    assert kept, "events whose text says 'open to the public' still count"

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=localist.__name__):
        everything = _adapter(audience=None).parse_items(items)
    assert not caplog.records and len(everything) > len(kept)


def test_query_api_url_and_the_page_cap(monkeypatch, offline, zone, caplog):
    """Extra filters go to the API; a truncated read is an error, not silence."""
    zone("America/New_York")
    requested = []

    def fake_get(url, params=None, **kwargs):
        requested.append((url, params))
        body = _page(int(params["page"]))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = _adapter(query={"group_id": 42}, days=30, max_pages=2, api_url="https://events.example.edu/api/2/events")
    with caplog.at_level(logging.ERROR, logger=localist.__name__):
        adapter.scrape_events()
    assert requested == [("https://events.example.edu/api/2/events", {"group_id": 42, "days": 30, "pp": 100, "page": n})
                         for n in (1, 2)]
    assert "reports 7 pages but max_pages is 2" in caplog.text
