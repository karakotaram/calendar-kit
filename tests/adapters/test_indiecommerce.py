"""IndieCommerce: the month calendar's FullCalendar JSON, checked against the teaser.

Fixtures: Porter Square Books' and Harvard Book Store's October 2026 calendars
as an ordinary browser received them on 2026-10-06 (Cambridge Calendar). Their
times are Eastern, so every test here runs in that zone.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime

import pytest

from src.adapters import indiecommerce
from src.adapters.indiecommerce import MONTH_PAUSE_MS, IndieCommerceAdapter
from src.quality.invariants import check_invariants
from src.scrapers.base_playwright_scraper import ScrapeRefusedError
from tests.conftest import read_fixture

F = "adapters/indiecommerce"
NOW = datetime(2026, 10, 6)
PORTER = {"name": "Porter Square Books", "street": "1815 Massachusetts Ave", "city": "Cambridge", "zip": "02140"}
HARVARD = {"name": "Harvard Book Store", "street": "1256 Massachusetts Ave", "city": "Cambridge", "zip": "02138"}
BRANCHES = {"Cambridge Edition": "Porter Square Books Cambridge Edition",
            "Boston Edition": "Porter Square Books Boston Edition"}


@pytest.fixture(autouse=True)
def eastern(zone):
    zone("America/New_York")


@pytest.fixture
def porter_html():
    return read_fixture(F, "porter-calendar-2026-10.html.gz")


@pytest.fixture
def harvard_html():
    return read_fixture(F, "harvard-calendar-2026-10.html.gz")


def porter(**params) -> IndieCommerceAdapter:
    return IndieCommerceAdapter("Porter Square Books", "https://portersquarebooks.com", PORTER,
                                place_names=BRANCHES, **params)


def harvard(**params) -> IndieCommerceAdapter:
    return IndieCommerceAdapter("Harvard Book Store", "https://www.harvard.com/events/calendar", HARVARD,
                                homes={"Harvard Book Store": {}}, **params)


def _errors(events):
    return [str(v) for v in check_invariants([e.model_dump(mode="json") for e in events], now=NOW)
            if v.severity == "error"]


def _first_item(html: str) -> dict:
    return dict(IndieCommerceAdapter.calendar_items(html)[0])


def test_porter_reads_the_whole_month_with_real_cities(porter_html, offline):
    """The old scraper set city="Cambridge" on everything, including events at
    the store's Boston branch and at host venues. Each event's city now comes
    from its own address, and a branch label is renamed to say whose branch."""
    events = porter().parse_calendar(porter_html)

    assert len(events) == 45
    assert not _errors(events)
    assert all((e.start_datetime.year, e.start_datetime.month) == (2026, 10) for e in events)
    assert {e.city for e in events} == {"Cambridge", "Boston"}
    story = next(e for e in events if e.title == "PSB Story Hour!")
    assert (story.start_datetime, story.end_datetime) == (datetime(2026, 10, 1, 10, 0), datetime(2026, 10, 1, 10, 15))
    assert (story.venue_name, story.street_address, story.zip_code, story.state) == (
        "Porter Square Books Cambridge Edition", "1815 Massachusetts Avenue", "02140", "MA")
    grub = [e for e in events if e.city == "Boston"]
    assert grub and all(e.venue_name == "Porter Square Books Boston Edition – GrubStreet Center for Creative Writing"
                        for e in grub)


def test_harvard_places_events_tagged_with_the_store(harvard_html, offline):
    """Some events have no place block, only the store's own location tag.
    `homes` says what that tag means; {} means the registry venue."""
    events = harvard().parse_calendar(harvard_html)

    assert len(events) == 45
    assert not _errors(events)
    assert all(e.city and e.venue_name for e in events)
    boyd = next(e for e in events if e.title.startswith("danah boyd"))
    assert (boyd.start_datetime, boyd.street_address, boyd.cost) == (
        datetime(2026, 10, 1, 19, 0), "1256 Massachusetts Ave", "Free")
    assert {e.city for e in events} >= {"Cambridge", "Boston", "Needham"}, "host venues keep their own city"


def test_an_unplaced_event_without_a_home_tag_falls_back_to_the_registry_venue(harvard_html, offline):
    """With no `homes`, an event with no place block still lands at the venue
    the registry names, never at an empty or invented place."""
    plain = IndieCommerceAdapter("Harvard Book Store", "https://www.harvard.com", HARVARD)
    events = plain.parse_calendar(harvard_html)
    assert len(events) == 45
    assert all(e.venue_name for e in events)


def test_a_start_that_disagrees_with_the_printed_date_or_time_is_skipped(porter_html, offline):
    """The ISO start is believed only when the teaser prints the same moment."""
    scraper = porter()
    item = _first_item(porter_html)

    assert scraper.parse_item(item) is not None
    assert scraper.parse_item({**item, "start": "2026-10-02T10:00:00-04:00"}) is None, "wrong day"
    assert scraper.parse_item({**item, "start": "2026-10-01T22:00:00-04:00"}) is None, "wrong time"
    assert scraper.parse_item({**item, "start": "next Thursday"}) is None, "unreadable"


def test_an_unprinted_time_is_never_guessed(porter_html, offline):
    """A timed event whose teaser prints no time cannot be checked, so it is
    skipped rather than published on the ISO value alone."""
    item = _first_item(porter_html)
    assert "10:00am - 10:15am" in item["title"]
    item["title"] = item["title"].replace("10:00am - 10:15am", "")
    assert porter().parse_item(item) is None


def test_an_all_day_event_is_midnight_and_flagged(porter_html, offline):
    """A date with no time is published as all day at 00:00, never at a time
    someone made up, and carries no end."""
    item = _first_item(porter_html)
    item.update({"allDay": True, "start": "2026-10-01", "end": "2026-10-02"})
    item["title"] = item["title"].replace("10:00am - 10:15am", "")

    event = porter().parse_item(item)
    assert (event.start_datetime, event.all_day, event.end_datetime) == (datetime(2026, 10, 1, 0, 0), True, None)


def test_an_offset_start_is_converted_into_the_calendar_zone(porter_html, zone, offline):
    """The ISO value carries the store's offset; it is converted to the
    calendar's zone (same instant), never stripped and read as local."""
    zone("America/Los_Angeles")
    event = porter().parse_item(_first_item(porter_html))
    assert event.start_datetime == datetime(2026, 10, 1, 7, 0)
    assert event.start_datetime.tzinfo is None


def test_cancelled_events_are_skipped(porter_html, offline):
    item = _first_item(porter_html)
    assert porter().parse_item(item) is not None
    item["title"] = item["title"].replace("PSB Story Hour!", "CANCELLED: PSB Story Hour!")
    assert porter().parse_item(item) is None


def test_months_chain_from_the_page_not_the_clock(porter_html, offline):
    """The next month comes from the page's own `calander_view` (sic). December
    rolls over to January."""
    assert IndieCommerceAdapter.next_month_path(porter_html) == "/events/calendar/2026/11"
    december = porter_html.replace('"calander_view":"2026-10"', '"calander_view":"2026-12"')
    assert december != porter_html
    assert IndieCommerceAdapter.next_month_path(december) == "/events/calendar/2027/01"
    assert IndieCommerceAdapter.next_month_path("<html></html>") is None


class _Response:
    def __init__(self, url):
        self.status, self.url = 200, url


class _CalendarPage:
    """Serves saved calendars by URL, as a browser page would."""

    def __init__(self, pages: dict):
        self.pages, self.url, self.waits = pages, None, []

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return _Response(url)

    def title(self):
        return "Events | Porter Square Books"

    def content(self):
        return self.pages[self.url]

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def close(self):
        pass


def test_a_run_reads_the_requested_months_and_pauses_between_them(porter_html, offline):
    """`months` pages are read, chained by the page's own state, with a pause
    between them; the same event is not published twice."""
    scraper = porter(months=2)
    page = _CalendarPage({"https://portersquarebooks.com/events/calendar": porter_html,
                          "https://portersquarebooks.com/events/calendar/2026/11": porter_html})
    scraper._page = page

    events = scraper.scrape_events()

    assert [url for url, _, _ in scraper.navigations] == [
        "https://portersquarebooks.com/events/calendar", "https://portersquarebooks.com/events/calendar/2026/11"]
    assert MONTH_PAUSE_MS in page.waits
    assert len(events) == 45, "the repeated month added nothing"


def test_a_page_without_calendar_data_yields_nothing_loudly(offline, caplog):
    """A page that is not the calendar (a redesign, an interstitial that
    slipped through) is reported, not parsed into junk."""
    assert porter().parse_calendar("<html><title>Events</title></html>") == []
    assert "no calendar data" in caplog.text


class _StuckPage:
    """A page that never gets past a bot check."""
    url = "https://example.org/events"

    def title(self):
        return "Just a moment..."

    def wait_for_timeout(self, ms):
        pass


def test_a_challenge_that_does_not_clear_fails_the_source():
    """Nothing interacts with a challenge. If it does not clear on its own the
    source fails loudly, rather than parsing an interstitial as no events."""
    scraper = porter()
    scraper._page = _StuckPage()
    with pytest.raises(ScrapeRefusedError, match="bot-check"):
        scraper.wait_past_challenge(timeout_s=2)


def test_headless_unless_a_human_opts_into_a_window():
    """The visible window Cambridge used for two bookstores is opt-in per
    source. By default the browser is headless and keeps its own user-agent;
    nothing in the adapter hides automation."""
    assert porter().headless is True
    visible = porter(visible_browser=True)
    assert visible.headless is False
    assert visible.user_agent is None, "the browser's own, honest user-agent"

    assert "opt-in" in indiecommerce.PARAMS["visible_browser"]
    assert "runs_in_ci" in indiecommerce.PARAMS["visible_browser"]
    code = inspect.getsource(indiecommerce)
    for flag in ("AutomationControlled", "enable-automation", "navigator.webdriver", "stealth"):
        assert flag not in code


def test_the_calendar_url_is_derived_from_any_store_url():
    """A registry URL naming one month must not pin every run to that month."""
    assert porter().source_url == "https://portersquarebooks.com/events/calendar"
    month = IndieCommerceAdapter("X", "https://store.example/events/calendar/2026/11", {})
    assert month.source_url == "https://store.example/events/calendar"


def test_the_registry_params_are_the_constructor_params():
    """PARAMS is what `cal adapters` shows a human; it must not drift from the code."""
    accepted = {n for n, p in inspect.signature(IndieCommerceAdapter).parameters.items()
                if p.kind is p.KEYWORD_ONLY}
    assert accepted == set(indiecommerce.PARAMS)
    assert indiecommerce.KIND == "playwright"
    json.dumps(indiecommerce.PARAMS)
