"""Every stored event time is naive local wall clock, in the calendar's own zone.

These cases are written for an Eastern calendar (the kit grew out of Cambridge
Calendar); the `zone` fixture runs them as one, whatever calendar.config.yaml says.
The Pacific cases at the end prove the zone really comes from config.

Most scrapers return naive datetimes; a handful of sources stamp a UTC offset.
Storing both kinds meant date comparisons raised TypeError, and any consumer
that converted to the viewer's local timezone moved the stamped events off the
day their naive neighbours sat on.
"""
from datetime import datetime

from dateutil import parser as dp

import pytest

from src.models.event import Event, EventCreate, to_local_naive


def make(model, **overrides):
    fields = dict(
        title="TCS presents: Shane Torres",
        description="Stand-up at The Comedy Studio in Somerville.",
        start_datetime=datetime(2026, 9, 16, 19, 30),
        source_url="https://www.thecomedystudio.com/",
        source_name="The Comedy Studio",
    )
    fields.update(overrides)
    if model is Event:
        fields["id"] = "e1"
    return model(**fields)


@pytest.fixture(autouse=True)
def _eastern(zone):
    zone("America/New_York")


def test_edt_offset_is_stripped_without_moving_the_clock():
    """-04:00 is already Eastern, so 7:30 PM stays 7:30 PM."""
    for model in (EventCreate, Event):
        event = make(model, start_datetime=dp.parse("2026-09-16T19:30:00-04:00"))
        assert event.start_datetime == datetime(2026, 9, 16, 19, 30)
        assert event.start_datetime.tzinfo is None


def test_utc_is_converted_not_just_stripped():
    """A summer UTC stamp is four hours ahead of Eastern."""
    event = make(Event, start_datetime=dp.parse("2026-09-16T23:30:00Z"))
    assert event.start_datetime == datetime(2026, 9, 16, 19, 30)


def test_conversion_respects_daylight_saving():
    """Same UTC hour, different offset: -05:00 in January, -04:00 in July."""
    assert to_local_naive(dp.parse("2026-01-05T20:00:00Z")) == datetime(2026, 1, 5, 15, 0)
    assert to_local_naive(dp.parse("2026-07-05T20:00:00Z")) == datetime(2026, 7, 5, 16, 0)


def test_a_late_evening_event_keeps_its_own_day():
    """The failure mode: 9:30 PM Eastern is tomorrow in UTC."""
    event = make(Event, start_datetime=dp.parse("2026-09-10T21:30:00-04:00"))
    assert event.start_datetime.date() == datetime(2026, 9, 10).date()


def test_naive_times_are_left_alone():
    """Naive already means Eastern - every venue here is in Greater Boston."""
    event = make(EventCreate, start_datetime=datetime(2026, 9, 16, 19, 30))
    assert event.start_datetime == datetime(2026, 9, 16, 19, 30)


def test_end_datetime_is_normalized_too():
    event = make(
        Event,
        start_datetime=dp.parse("2026-09-16T19:30:00-04:00"),
        end_datetime=dp.parse("2026-09-16T22:00:00-04:00"),
    )
    assert event.end_datetime == datetime(2026, 9, 16, 22, 0)
    assert event.end_datetime.tzinfo is None


def test_missing_end_datetime_stays_none():
    assert make(Event, end_datetime=None).end_datetime is None


def test_mixed_sources_sort_and_compare_without_raising():
    """The original symptom: TypeError comparing aware against naive."""
    events = [
        make(Event, start_datetime=dp.parse("2026-09-16T19:30:00-04:00")),
        make(Event, start_datetime=datetime(2026, 9, 16, 18, 0)),
        make(Event, start_datetime=dp.parse("2026-09-16T23:00:00Z")),
    ]
    starts = sorted(e.start_datetime for e in events)
    assert starts == [
        datetime(2026, 9, 16, 18, 0),
        datetime(2026, 9, 16, 19, 0),
        datetime(2026, 9, 16, 19, 30),
    ]


def test_pacific_calendar_stores_pacific_wall_clock(zone):
    """A Bay Area calendar: the same UTC instant lands three hours earlier."""
    zone("America/Los_Angeles")
    assert to_local_naive(dp.parse("2026-07-05T20:00:00Z")) == datetime(2026, 7, 5, 13, 0)
    assert to_local_naive(dp.parse("2026-01-05T20:00:00Z")) == datetime(2026, 1, 5, 12, 0)
