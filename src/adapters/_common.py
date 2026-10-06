"""Helpers shared by the platform adapters (not an adapter: the leading
underscore keeps it out of discovery)."""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Optional

from src.models.event import to_local_naive

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def venue_fields(venue: Optional[dict], *, name: Optional[str] = None,
                 street: Optional[str] = None, city: Optional[str] = None,
                 zip_code: Optional[str] = None) -> dict:
    """EventCreate location fields: the event's own place first, then the
    registry's venue defaults. An event's own address always wins - a venue
    hosting off-site events must not pull them back to its own address."""
    venue = venue or {}
    own = any((name, street, city))
    return {
        "venue_name": (name or (None if own else venue.get("name"))) or None,
        "street_address": (street or (None if own else venue.get("street"))) or None,
        "city": city or (None if own else venue.get("city")) or None,
        "zip_code": (zip_code or (None if own else venue.get("zip"))) or None,
    }


def clean(text: Optional[str]) -> str:
    """Decode entities (some sources encode twice) and collapse whitespace."""
    text = text or ""
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return " ".join(text.split())


def strip_html(text: Optional[str]) -> str:
    """Plain text from an HTML fragment."""
    from bs4 import BeautifulSoup
    text = clean(text)
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return " ".join(text.split())


def on_the_minute(dt: Optional[datetime]) -> Optional[datetime]:
    """Local wall clock, seconds dropped. Feeds that carry milliseconds would
    otherwise trip the sub-minute invariant meant to catch clock readings."""
    if dt is None:
        return None
    return to_local_naive(dt).replace(second=0, microsecond=0)


def year_for_weekday(month: int, day: int, weekday: str, reference: date) -> Optional[int]:
    """The year near `reference` in which month/day falls on the printed weekday.

    For listings that print "Wed, October 7" with no year. Only the weekday
    decides; if no nearby year matches, the date is unreadable (return None and
    skip the event). The clock supplies candidate years, never the answer.
    """
    wanted = WEEKDAYS.index(weekday[:3].lower())
    for year in (reference.year, reference.year + 1, reference.year - 1):
        try:
            if date(year, month, day).weekday() == wanted:
                return year
        except ValueError:
            continue
    return None


CANCELLED = re.compile(r"\b(cancell?ed|postponed)\b", re.IGNORECASE)
