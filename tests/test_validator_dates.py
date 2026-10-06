"""The validator is the last line of defence against clock-derived event dates."""
from datetime import datetime, timedelta

from src.models.event import EventCreate
from src.utils.validator import EventValidator


def make_event(start_datetime):
    return EventCreate(
        title="Danehy Park Family Day",
        description="Activities, arts and crafts, entertainment, free food.",
        start_datetime=start_datetime,
        source_url="https://www.cambridgema.gov/citycalendar/view.aspx?guid=abc",
        source_name="City of Cambridge",
    )


def test_published_times_are_accepted():
    soon = (datetime.now() + timedelta(days=14)).replace(hour=11, minute=0, second=0, microsecond=0)
    assert EventValidator.validate_event(make_event(soon)) == (True, None)


def test_scrape_timestamps_are_rejected():
    """`datetime.now() + timedelta(weeks=2)` is what put 117 events on one day."""
    stamped = datetime.now() + timedelta(days=14)
    assert stamped.microsecond  # what a real clock reading looks like
    is_valid, error = EventValidator.validate_event(make_event(stamped))
    assert not is_valid
    assert "scrape timestamp" in error


def test_a_source_saying_family_friendly_is_believed():
    """The keyword guess says no to anything at 7 PM or later, and used to
    overwrite a submitter's explicit yes."""
    from src.models.event import EventCreate
    from src.utils.validator import EventValidator

    event = EventCreate(title="Halloween Lantern Walk", description="Bring lanterns.",
                        start_datetime=datetime(2026, 10, 31, 19, 30), family_friendly=True,
                        source_url="https://example.org/walk", source_name="User Submitted")
    assert EventValidator.clean_and_enhance(event).family_friendly is True
