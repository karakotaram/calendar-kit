"""EventON: listings read through EventON's own endpoint, each start stated twice.

Fixtures (Cambridge Calendar, saved 2026-10-06):

- Central Square Theater, a month calendar: its calendar page (nonces and
  settings) and the endpoint's answers for October, November and December 2026
  (December is empty).
- Regent Theatre, a list calendar: the page as served (empty shells), the
  endpoint's single answer for it, and the page as a browser rendered it (34
  listings, 15 of them in standard time), for `mode: page`.

Times are Eastern, so the tests run in that zone.
"""
from __future__ import annotations

import inspect
import json
import time
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.adapters import eventon
from src.adapters.eventon import EventONAdapter
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

F = "adapters/eventon"
NOW = datetime(2026, 10, 6)
CSQ = "https://www.centralsquaretheater.org"
CSQ_VENUE = {"name": "Central Square Theater", "street": "450 Massachusetts Avenue", "city": "Cambridge", "zip": "02139"}
REGENT_VENUE = {"name": "Regent Theatre", "street": "7 Medford St", "city": "Arlington", "zip": "02474"}


@pytest.fixture(autouse=True)
def eastern(zone):
    zone("America/New_York")


def _errors(events):
    return [str(v) for v in check_invariants([e.model_dump(mode="json") for e in events], now=NOW)
            if v.severity == "error"]


@pytest.fixture
def csq(offline):
    """A Central Square run, its two network seams replaced by the saved answers."""
    months = [json.loads(read_fixture(F, f"central-square-month-2026-{m}.json.gz")) for m in ("10", "11", "12")]
    scraper = EventONAdapter("Central Square Theater", f"{CSQ}/calendar/", CSQ_VENUE)
    calls = []

    def fetch_month(endpoint, params, sc, direction):
        calls.append((endpoint, direction, sc.get("fixed_month"), sc.get("fixed_year"), params.get("n")))
        return months[min(len(calls) - 1, 2)]

    scraper.fetch_page = lambda url: read_fixture(F, "central-square-calendar-2026-10-06.html.gz")
    scraper.fetch_month = fetch_month
    return scraper.scrape_events(), calls


@pytest.fixture
def regent(offline):
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/schedule/list/", REGENT_VENUE)
    calls = []

    def fetch_month(endpoint, params, sc, direction):
        calls.append((endpoint, direction, sc.get("calendar_type")))
        return json.loads(read_fixture(F, "regent-ajax-none-2026-10-06.json.gz"))

    scraper.fetch_page = lambda url: read_fixture(F, "regent-schedule-list-raw-2026-10-06.html.gz")
    scraper.fetch_month = fetch_month
    return scraper.scrape_events(), calls


def _rendered_regent():
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/schedule/list/", REGENT_VENUE, mode="page")
    scraper.fetch_page = lambda url: read_fixture(F, "regent-schedule-list-2026-10-06.html.gz")
    return scraper.scrape_events()


def test_every_month_is_requested_without_clicking(csq):
    """Clicking "next month" failed on every run because a popup intercepted
    the click, so two of four months were read. Months now come from EventON's
    endpoint, each request carrying the settings the previous answer returned -
    exactly what the page's own arrow sends."""
    events, calls = csq
    assert calls[0] == (f"{CSQ}/?evo-ajax=eventon_get_events", "none", "10", "2026", "1fd33b3650")
    assert [c[1] for c in calls[1:]] == ["next"] * 11, "months=12 by default"
    assert calls[1][2:4] == ("10", "2026") and calls[2][2:4] == ("11", "2026"), \
        "each request must send the settings the last answer returned"
    assert {e.start_datetime.month for e in events} == {10, 11}
    assert len(events) == 17 + 21
    assert not _errors(events)


def test_times_are_local_and_both_renderings_agree(csq):
    """data-time is Unix seconds (UTC); the schema repeats the wall clock.
    After the clocks change, 02:00 UTC is 9 PM Eastern, not 10."""
    events, _ = csq
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Eleanor", datetime(2026, 10, 6, 19, 30)) in starts
    assert ("Eleanor", datetime(2026, 10, 10, 14, 0)) in starts
    assert ("Scholar Social for The Getaway Driver", datetime(2026, 11, 5, 21, 0)) in starts
    assert all(e.start_datetime.tzinfo is None and e.start_datetime.second == 0 for e in events)


def test_a_placeholder_description_is_not_published(csq):
    """EventON's schema description is often the title in quotes ("'Eleanor'"),
    which the validator rejects as too short: all 12 October performances of
    Eleanor were once dropped. Markup in real descriptions is stripped."""
    events, _ = csq
    assert not [e for e in events if len(e.description) < 20 or e.description.strip("'\" ") == e.title]
    assert not [e for e in events if "<" in e.description or "&#" in e.description or "&amp;" in e.description]
    talk = next(e for e in events if e.title == "The Hidden Lives of Caregivers")
    assert talk.description.startswith("Nearly every single person will play the role of caregiver")


def test_talks_link_somewhere_real(csq):
    """Talks have href="#" in the listing and were once published with
    source_url "#". A listing's link is used only if it is a real URL, then the
    event's own page, then the calendar."""
    events, _ = csq
    assert not [e for e in events if not e.source_url.startswith("http")]
    talk = next(e for e in events if e.title == "The Hidden Lives of Caregivers")
    assert talk.source_url == f"{CSQ}/event-on/the-hidden-lives-of-caregivers/"
    show = next(e for e in events if e.title == "Eleanor")
    assert show.source_url.startswith("https://ci.ovationtix.com/"), "performances keep their ticket links"


def test_a_list_calendar_is_one_request(regent):
    """The Regent's page as served holds only empty shells - the old scraper
    parsed it and returned nothing, silently. Its list calendar answers the
    endpoint with its whole range at once, so one request reads it."""
    events, calls = regent
    assert calls == [("https://regenttheatre.com/?evo-ajax=eventon_get_events", "none", "el")]
    assert len(events) == 36
    assert not _errors(events)
    lennon = next(e for e in events if "JOHN LENNON" in e.title)
    assert (lennon.start_datetime, lennon.end_datetime) == (datetime(2026, 10, 9, 20, 0), None), "23:59 is not an end"
    assert lennon.source_url == "https://regenttheatre.com/events/john-lennon/"
    assert lennon.description.startswith("First Time at the Regent!")
    assert lennon.venue_name == "Regent Theatre" and lennon.city == "Arlington"


def test_every_start_matches_the_time_on_the_card(offline):
    """EventON's microdata writes a -4:00 offset year-round. Reading it put all
    15 events in standard time an hour early ("7:00 pm (GMT-05:00)" published
    as 6:00 pm). The time printed on each card is the arbiter."""
    events = _rendered_regent()
    assert len(events) == 34

    soup = BeautifulSoup(read_fixture(F, "regent-schedule-list-2026-10-06.html.gz"), "html.parser")
    card_times = {}
    for node in soup.find_all(class_="eventon_list_event"):
        title = node.find(class_="evcal_event_title")
        shown = node.select_one(".evcal_cblock em.time")
        if title and shown:
            card_times.setdefault(" ".join(title.get_text().split()), set()).add(shown.get_text(strip=True))
    for e in events:
        assert e.start_datetime.strftime("%-I:%M %p").lower() in card_times[e.title], e.title

    standard_time = [e for e in events
                     if datetime(2026, 11, 1, 2, 0) <= e.start_datetime < datetime(2027, 3, 14, 2, 0)]
    assert len(standard_time) == 15, "the fixture should exercise the offset bug"


def test_eventon_placeholder_end_is_not_published(offline):
    """EventON stores 23:59 when no end time was set; the venue never said that."""
    events = _rendered_regent()
    assert not [e for e in events if e.end_datetime and e.end_datetime.strftime("%H:%M") == "23:59"]
    ends = sorted((e.start_datetime, e.end_datetime) for e in events if e.end_datetime)
    assert ends == [(datetime(2026, 10, 25, 16, 30), datetime(2026, 10, 25, 18, 30)),
                    (datetime(2026, 10, 25, 19, 0), datetime(2026, 10, 25, 21, 0))]


@pytest.fixture
def machine_zone(monkeypatch):
    """Run under a given process time zone, as CI (UTC) does."""
    def use(name: str):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("name", ["UTC", "America/New_York", "Asia/Tokyo"])
def test_times_do_not_depend_on_the_machine_zone(name, machine_zone, offline):
    """`datetime.fromtimestamp()` without a zone reads Unix time in the
    machine's zone, which is UTC in CI. The same page must give the same times
    wherever it is parsed."""
    machine_zone(name)
    starts = {(e.title, e.start_datetime.date()): e.start_datetime for e in _rendered_regent()}
    assert starts[("Monster Ft. Gurleen Pannu Standup Comedy", datetime(2026, 11, 1).date())] == datetime(2026, 11, 1, 19, 0)


def test_disagreeing_renderings_are_skipped():
    """If the epoch and the schema wall clock ever disagree, one of them changed
    meaning; the event is dropped rather than resolved by guessing."""
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/schedule/list/", REGENT_VENUE)
    node = BeautifulSoup(
        '<div class="eventon_list_event" data-time="1793577600-1793595540">'
        '<span class="evcal_event_title">A Show</span>'
        '<meta itemprop="startDate" content="2026-11-1T18:00-5:00"/></div>',
        "html.parser").div
    assert scraper.parse_item(node) is None

    node["data-time"] = "1793574000-1793595540"      # 18:00 EST
    assert scraper.parse_item(node).start_datetime == datetime(2026, 11, 1, 18, 0)


def test_an_all_day_listing_is_midnight_and_flagged():
    """A listing EventON marks all day is published at 00:00 with the flag,
    never at the placeholder time it stores."""
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/", REGENT_VENUE)
    node = BeautifulSoup(
        '<div class="eventon_list_event" data-time="1793592000-1793678340">'
        '<span class="evcal_event_title">Holiday Craft Fair</span>'
        '<em class="evcal_time">(All Day: Sunday)</em>'
        '<meta itemprop="startDate" content="2026-11-1T00:00-4:00"/></div>',
        "html.parser").div
    event = scraper.parse_item(node)
    assert (event.start_datetime, event.all_day, event.end_datetime) == (datetime(2026, 11, 1, 0, 0), True, None)


def test_cancelled_listings_are_skipped():
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/", REGENT_VENUE)
    node = BeautifulSoup(
        '<div class="eventon_list_event cancelled" data-time="1793574000-1793595540">'
        '<span class="evcal_event_title">A Show</span></div>', "html.parser").div
    assert scraper.parse_item(node) is None


def test_a_page_without_eventon_settings_fails_loudly(offline):
    """A redesign that drops the calendar must raise, not return "ok, 0 events"."""
    scraper = EventONAdapter("Regent Theatre", "https://regenttheatre.com/", REGENT_VENUE)
    scraper.fetch_page = lambda url: "<html><title>Schedule</title><p>Coming soon</p></html>"
    with pytest.raises(ValueError, match="mode: page"):
        scraper.scrape_events()


def test_an_endpoint_error_fails_loudly(offline):
    scraper = EventONAdapter("Central Square Theater", f"{CSQ}/calendar/", CSQ_VENUE)
    scraper.fetch_page = lambda url: read_fixture(F, "central-square-calendar-2026-10-06.html.gz")
    scraper.fetch_month = lambda *a: {"status": "BAD", "msg": "nonce"}
    with pytest.raises(ValueError, match="EventON returned"):
        scraper.scrape_events()


def test_the_endpoint_is_the_one_the_page_uses():
    page = "https://venue.example/calendar/"
    assert EventONAdapter.endpoint_url({"ajax_method": "endpoint", "evo_ajax_url": "/?evo-ajax=%%endpoint%%"}, page) == \
        "https://venue.example/?evo-ajax=eventon_get_events"
    assert EventONAdapter.endpoint_url({"ajax_method": "ajax", "ajaxurl": "https://venue.example/wp-admin/admin-ajax.php"},
                                       page) == "https://venue.example/wp-admin/admin-ajax.php"
    assert EventONAdapter.endpoint_url({}, page) == "https://venue.example/?evo-ajax=eventon_get_events"


def test_category_is_a_registry_choice(csq):
    """A playhouse's registry row can say every event is theater; a typo in it
    fails when the source is loaded, not silently at publish."""
    scraper = EventONAdapter("Central Square Theater", f"{CSQ}/calendar/", CSQ_VENUE, category="theater")
    node = BeautifulSoup(
        '<div class="eventon_list_event" data-time="1793574000-1793595540">'
        '<span class="evcal_event_title">An Evening of Songs</span></div>', "html.parser").div
    assert scraper.parse_item(node).category == "theater"
    with pytest.raises(ValueError):
        EventONAdapter("X", "https://venue.example/", {}, category="plays")


def test_mode_is_validated():
    with pytest.raises(ValueError):
        EventONAdapter("X", "https://venue.example/", {}, mode="browser")


def test_the_registry_params_are_the_constructor_params():
    accepted = {n for n, p in inspect.signature(EventONAdapter).parameters.items() if p.kind is p.KEYWORD_ONLY}
    assert accepted == set(eventon.PARAMS)
    assert eventon.KIND == "requests"
