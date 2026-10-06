"""The `ical` adapter: iCalendar feeds.

Fixtures (tests/fixtures/adapters/ical/) are Cambridge Calendar's saved feeds
of 2026-10-06, read in Eastern time:

  - armory-events-manager-2026-10-06.ics.gz   WordPress Events Manager: 81 events,
    TZID=America/New_York, URL/CATEGORIES/ATTACH, DTSTAMP = each event's last edit
  - theatre-at-first-google-2026-10-06.ics.gz Google Calendar: 596 VEVENTs back to
    2009, 91 recurring, with EXDATE and RECURRENCE-ID overrides, DTSTAMP = generation time

Past productions are the only recurring ones, so the recurrence tests read the
Google feed "as of" a date when one was running: `parse_feed(as_of=...)`.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

import pytest
import requests

from src.adapters import ical
from src.adapters.ical import ICalAdapter, read_moment
from src.quality.invariants import check_invariants
from tests.conftest import read_fixture

ARMORY = {"name": "Arts at the Armory", "street": "191 Highland Ave", "city": "Somerville", "zip": "02143"}
UNITY = {"name": "Unity Somerville", "street": "6 William St", "city": "Somerville", "zip": "02144"}
GOOGLE_FEED = ("https://calendar.google.com/calendar/ical/"
               "ckof39gfrbnt72qpjph558qu3k%40group.calendar.google.com/public/basic.ics")
TAF_PAGE = "https://www.theatreatfirst.org/learn-more/calendar"
# Theatre@First's shared calendar also holds its internal business
TAF_INTERNAL = [r"steering", r"\bt@f\b", r"committee", r"board meeting", r"work ?(day|party|session)",
                r"strike", r"load[- ]?in", r"tech rehearsal", r"\brehearsal\b", r"production meeting"]


def _armory_feed() -> str:
    return read_fixture("adapters/ical", "armory-events-manager-2026-10-06.ics.gz")


def _google_feed() -> str:
    return read_fixture("adapters/ical", "theatre-at-first-google-2026-10-06.ics.gz")


def _armory() -> ICalAdapter:
    return ICalAdapter(source_name="Arts at the Armory", url="https://artsatthearmory.org/events.ics", venue=ARMORY)


def _theatre(**params) -> ICalAdapter:
    params.setdefault("exclude", TAF_INTERNAL)
    return ICalAdapter(source_name="Theatre at First", url=GOOGLE_FEED, venue=UNITY, page_url=TAF_PAGE, **params)


def _errors(events, now):
    return [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=now) if v.severity == "error"]


def _feed(*events: str, header: str = "") -> str:
    """A small feed: VEVENT bodies given as lines joined by newlines."""
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{header}{body}END:VCALENDAR\r\n"


def _respond(monkeypatch, text: str, status: int = 200) -> list:
    calls = []

    class _Response:
        status_code, content = status, text.encode("utf-8")

        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError(f"{status} Client Error")

    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Response())
    return calls


# --------------------------------------------------------------------------- #
# Events Manager feed
# --------------------------------------------------------------------------- #

def test_every_event_in_the_feed_is_read(monkeypatch, offline, zone):
    """The Armory's old scraper took the first heading under each category tab
    and capped at 20: about 12 events. The feed lists 81, through Sep 2027,
    in one honest request."""
    zone("America/New_York")
    calls = _respond(monkeypatch, _armory_feed())

    events = _armory().scrape_events()

    assert [url for url, _ in calls] == ["https://artsatthearmory.org/events.ics"]
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"]
    assert len(events) == 81
    assert max(e.start_datetime for e in events) == datetime(2027, 9, 28, 18, 30)
    assert Counter(e.title for e in events)["The Moth: Boston StorySLAM"] >= 9
    assert not _errors(events, datetime(2026, 10, 6))


def test_times_match_the_venues_listing(zone):
    """The listing shows "Fri. Oct. 09, 2026 | 8:00 pm" for Nebula Night and
    "Tue. Oct. 06, 2026 | 7:30 pm - 9:30 pm" for Smut Slam. A DTEND equal to
    DTSTART means no end was given."""
    zone("America/New_York")
    first = {}
    for e in sorted(_armory().parse_feed(_armory_feed()), key=lambda e: e.start_datetime):
        first.setdefault(e.title, e)
    nebula = first["The Nova Comedy Collective Presents: Nebula Night"]
    assert (nebula.start_datetime, nebula.end_datetime) == (datetime(2026, 10, 9, 20, 0), None)
    smut = first["Smut Slam"]
    assert (smut.start_datetime, smut.end_datetime) == (datetime(2026, 10, 6, 19, 30), datetime(2026, 10, 6, 21, 30))
    assert smut.source_url.startswith("https://artsatthearmory.org/events/smut-slam")


def test_times_are_stored_in_the_calendars_own_zone(zone):
    """TZID=America/New_York is converted, not copied: a Pacific calendar sees
    Smut Slam's 7:30 PM Eastern start at 4:30 PM."""
    zone("America/Los_Angeles")
    smut = min((e for e in _armory().parse_feed(_armory_feed()) if e.title == "Smut Slam"),
               key=lambda e: e.start_datetime)
    assert smut.start_datetime == datetime(2026, 10, 6, 16, 30)


def test_descriptions_are_prose_not_the_ticket_buttons_script(zone):
    """41 of 81 descriptions embed `$('#getTixButton').click(function() {
    fbq('track', ...) });` as text, and all are iCalendar-escaped."""
    zone("America/New_York")
    events = _armory().parse_feed(_armory_feed())
    assert not [e for e in events if "fbq" in e.description or "$(" in e.description]
    assert not [e for e in events if "&amp;" in e.description or "\\," in e.description or "\\n" in e.description]
    clap = next(e for e in events if e.title.startswith("Arts at the Armory Spotlight Series"))
    assert clap.title.endswith("Clap Your Hands Say Yeah - Piano & Voice"), "the feed's title is not truncated"
    assert "Alec Ounsworth" in clap.description


def test_categories_and_images_come_from_the_feed(zone):
    zone("America/New_York")
    moth = next(e for e in _armory().parse_feed(_armory_feed()) if e.title == "The Moth: Boston StorySLAM")
    assert "Literary Art" in moth.tags
    assert moth.image_url and moth.image_url.startswith("https://artsatthearmory.org/")
    assert (moth.venue_name, moth.street_address) == ("Arts at the Armory", "191 Highland Ave")


# --------------------------------------------------------------------------- #
# Google Calendar feed
# --------------------------------------------------------------------------- #

def test_the_window_comes_from_the_feed_not_the_clock(monkeypatch, offline, zone):
    """Theatre@First's window was [now - 1 day, now + 365 days], so the saved
    feed parsed differently every day. It is anchored on the feed's own DTSTAMP
    (2026-10-06 13:41:38Z). Internal meetings are excluded by pattern."""
    zone("America/New_York")
    _respond(monkeypatch, _google_feed())
    events = _theatre().scrape_events()

    assert [(e.title, e.start_datetime, e.end_datetime) for e in events] == [
        ("Iphigenia", datetime(2026, 10, 10, 16, 0), datetime(2026, 10, 10, 18, 30)),
        ("Iphigenia", datetime(2026, 10, 11, 16, 0), datetime(2026, 10, 11, 18, 30)),
    ]
    assert {e.source_url for e in events} == {TAF_PAGE}, "Google events have no URL of their own"
    assert not _errors(events, datetime(2026, 10, 6))

    everything = _theatre(exclude=()).parse_feed(_google_feed())
    assert len(everything) > len(events), "without `exclude`, internal meetings are listed too"


def test_a_recurring_run_lists_every_performance(zone):
    """"None Escape" (March 2025) is one VEVENT: weekly Thu-Sun at 8 PM until
    Mar 29, minus an EXDATE on Thu Mar 20, with the closing Saturday moved to a
    2 PM matinee by a RECURRENCE-ID override. Reading DTSTART alone listed
    opening night and the matinee - two of nine performances."""
    zone("America/New_York")
    run = sorted(e.start_datetime for e in _theatre().parse_feed(_google_feed(), as_of=datetime(2025, 3, 1))
                 if e.title == "None Escape")
    assert run == [datetime(2025, 3, d, 20, 0) for d in (14, 15, 16, 21, 22, 23, 27, 28)] + [datetime(2025, 3, 29, 14, 0)]


def test_an_outdoor_run_keeps_its_exclusions_end_time_and_place(zone):
    """"The Tempest" (June 2025): Fri-Sun at 7 PM until Jun 21 at Nathan Tufts
    Park, Jun 7 and 14 excluded. UNTIL is a UTC instant (20250622T035959Z,
    23:59:59 EDT on the 21st), so Sun Jun 22 is out."""
    zone("America/New_York")
    as_of = datetime(2025, 6, 1)
    events = _theatre().parse_feed(_google_feed(), as_of=as_of)
    tempest = [e for e in events if e.title == "The Tempest"]

    assert sorted(e.start_datetime.day for e in tempest) == [6, 8, 13, 15, 20, 21]
    assert all((e.start_datetime.hour, e.start_datetime.minute) == (19, 0) for e in tempest)
    assert all((e.end_datetime - e.start_datetime).total_seconds() == 90 * 60 for e in tempest)
    assert {(e.venue_name, e.street_address, e.city, e.state) for e in tempest} == {
        ("Nathan Tufts Park", "850 Broadway", "Somerville", "MA")}
    assert not _errors(events, as_of)


def test_a_date_only_event_is_all_day(zone):
    """VALUE=DATE events have no time; they are all-day, not midnight starts."""
    zone("America/New_York")
    events = _theatre().parse_feed(_google_feed(), as_of=datetime(2010, 1, 1))
    bare = [e for e in events if e.title == "Bare Bones Performance" and e.start_datetime.year == 2010]
    assert [(e.start_datetime, e.all_day, e.end_datetime) for e in bare] == [(datetime(2010, 3, 27), True, None)]


# --------------------------------------------------------------------------- #
# Parser rules, on small feeds
# --------------------------------------------------------------------------- #

def test_an_alarm_does_not_overwrite_the_event(zone):
    """A VEVENT can nest a VALARM with its own UID and DESCRIPTION. Read as
    flat key/values, the alarm's lines replaced the event's - and a replaced
    UID would detach an override from its series."""
    zone("America/New_York")
    feed = _feed("""
DTSTART;TZID=America/New_York:20261105T193000
DTEND;TZID=America/New_York:20261105T213000
RRULE:FREQ=DAILY;COUNT=3
DTSTAMP:20261006T134138Z
UID:show@google.com
DESCRIPTION:A new play in three performances at Unity Somerville.
SUMMARY:Test Play
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:This is an event reminder
UID:ALARM-1
TRIGGER:-P0DT0H30M0S
END:VALARM""", """
DTSTART;TZID=America/New_York:20261107T140000
DTEND;TZID=America/New_York:20261107T160000
RECURRENCE-ID;TZID=America/New_York:20261107T193000
DTSTAMP:20261006T134138Z
UID:show@google.com
SUMMARY:Test Play""")
    events = _theatre().parse_feed(feed)
    assert [e.start_datetime for e in events] == [
        datetime(2026, 11, 5, 19, 30), datetime(2026, 11, 6, 19, 30), datetime(2026, 11, 7, 14, 0)]
    assert events[0].description == "A new play in three performances at Unity Somerville."


def test_a_series_keeps_its_own_wall_clock_across_a_clock_change(zone):
    """A weekly 8 PM Pacific series crosses the Nov 1 DST change. Expanded in
    UTC it would drift to 7 PM; expanded in its own zone it stays at 8 PM. A
    cancelled override removes its night; RDATE adds one; floating times take
    the calendar's X-WR-TIMEZONE."""
    zone("America/Los_Angeles")
    feed = _feed("""
UID:weekly@example.org
DTSTAMP:20261006T120000Z
DTSTART;TZID=America/Los_Angeles:20261022T200000
DURATION:PT2H
RRULE:FREQ=WEEKLY;UNTIL=20261113T040000Z
RDATE;TZID=America/Los_Angeles:20261115T150000
SUMMARY:Open Mic""", """
UID:weekly@example.org
DTSTAMP:20261006T120000Z
RECURRENCE-ID;TZID=America/Los_Angeles:20261105T200000
DTSTART;TZID=America/Los_Angeles:20261105T200000
STATUS:CANCELLED
SUMMARY:Open Mic""", """
UID:floating@example.org
DTSTAMP:20261006T120000Z
DTSTART:20261120T190000
SUMMARY:Floating Show""", header="X-WR-TIMEZONE:America/New_York\r\n")
    events = ICalAdapter("Venue", "https://venue.example.org/cal.ics", {"name": "Venue"}).parse_feed(feed)
    mic = [(e.start_datetime, e.end_datetime) for e in events if e.title == "Open Mic"]
    assert mic == [
        (datetime(2026, 10, 22, 20, 0), datetime(2026, 10, 22, 22, 0)),
        (datetime(2026, 10, 29, 20, 0), datetime(2026, 10, 29, 22, 0)),
        (datetime(2026, 11, 12, 20, 0), datetime(2026, 11, 12, 22, 0)),
        (datetime(2026, 11, 15, 15, 0), datetime(2026, 11, 15, 17, 0)),
    ]
    floating = next(e for e in events if e.title == "Floating Show")
    assert floating.start_datetime == datetime(2026, 11, 20, 16, 0), "7 PM in the feed's own zone (Eastern)"


@pytest.mark.parametrize("raw,params,expected", [
    ("20261006T193000", {"TZID": "America/New_York"}, (datetime(2026, 10, 6, 19, 30), False)),
    ("20261007T001500Z", {}, (datetime(2026, 10, 6, 20, 15), False)),
    ("20261010", {"VALUE": "DATE"}, (datetime(2026, 10, 10), True)),
    ("20261006T193000", {"TZID": "/mozilla.org/20050126_1/America/New_York"}, (datetime(2026, 10, 6, 19, 30), False)),
    ("20261006T163000", {"TZID": "Pacific Standard Time"}, (datetime(2026, 10, 6, 19, 30), False)),
    ("20261006T193000", {}, (datetime(2026, 10, 6, 19, 30), False)),          # floating: the region's
    ("20261006T193000", {"TZID": "Mars/Olympus_Mons"}, None),               # unknown zone: never guessed
    ("not a date", {"TZID": "America/New_York"}, None),
])
def test_date_time_forms(raw, params, expected, zone):
    tz = zone("America/New_York")
    moment = read_moment(raw, params, tz)
    assert (None if moment is None else (moment.local(), moment.all_day)) == expected


def test_cancelled_private_and_excluded_events_are_skipped(zone):
    zone("America/New_York")
    stamp = "DTSTAMP:20261006T120000Z\r\nDTSTART;TZID=America/New_York:20261020T190000"
    feed = _feed(f"UID:1\r\n{stamp}\r\nSUMMARY:Called Off\r\nSTATUS:CANCELLED",
                 f"UID:2\r\n{stamp}\r\nSUMMARY:CANCELLED: Poetry Night",
                 f"UID:3\r\n{stamp}\r\nSUMMARY:Donor Dinner\r\nCLASS:PRIVATE",
                 f"UID:4\r\n{stamp}\r\nSUMMARY:Board Meeting",
                 f"UID:5\r\n{stamp}\r\nSUMMARY:Poetry Night",
                 "UID:6\r\nDTSTAMP:20261006T120000Z\r\nSUMMARY:No Start Given")
    adapter = ICalAdapter("Venue", "webcal://venue.example.org/cal.ics", {"name": "Venue"}, exclude=r"board meeting")
    assert adapter.source_url == "https://venue.example.org/cal.ics"
    assert [e.title for e in adapter.parse_feed(feed)] == ["Poetry Night"]


@pytest.mark.parametrize("location,expected", [
    ("Unity Somerville, 6 William St, Somerville, MA 02144, USA",
     ("Unity Somerville", "6 William St", "Somerville", "MA", "02144")),
    ("Nathan Tufts Park", ("Nathan Tufts Park", None, None, None, None)),
    ("1317 San Pablo Ave, Berkeley, CA 94702", (None, "1317 San Pablo Ave", "Berkeley", "CA", "94702")),
    ("https://zoom.us/j/123", ("Online", None, None, None, None)),
    ("", ("Unity Somerville", "6 William St", "Somerville", None, "02144")),
])
def test_locations(location, expected):
    """The event's own place wins over the registry venue; a link is online."""
    place = _theatre().place(location)
    assert (place["venue_name"], place["street_address"], place["city"], place["state"], place["zip_code"]) == expected


@pytest.mark.parametrize("body,status,error", [
    ("", 200, ValueError),                                   # The Dance Complex's feed, silently empty
    ("<html><body>Please enable cookies</body></html>", 200, ValueError),
    ("Forbidden", 403, requests.HTTPError),
])
def test_a_feed_that_is_not_a_calendar_fails_the_source(body, status, error, monkeypatch, offline):
    """An empty or HTML answer is a failure, so the run keeps the source's
    existing events instead of recording a venue with nothing on."""
    _respond(monkeypatch, body, status)
    with pytest.raises(error):
        _armory().run()


def test_a_feed_without_a_timestamp_cannot_say_what_is_current(zone, caplog):
    zone("America/New_York")
    feed = _feed("UID:1\r\nDTSTART;TZID=America/New_York:20261020T190000\r\nSUMMARY:Poetry Night")
    with pytest.raises(ValueError, match="DTSTAMP"):
        _armory().parse_feed(feed)
    assert [e.title for e in _armory().parse_feed(feed, as_of=datetime(2026, 10, 6))] == ["Poetry Night"]
    with caplog.at_level(logging.WARNING, logger=ical.__name__):
        assert _armory().parse_feed("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n") == []
    assert "holds no events" in caplog.text
