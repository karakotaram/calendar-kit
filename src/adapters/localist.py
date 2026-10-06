"""Localist calendars (universities, cities), read from `/api/2/events`.

    <site>/api/2/events?days=60&pp=100&page=N

returns one item per occurrence in the window, paged by `page.total`. Each
item's `event_instances[].event_instance` holds `start` and `end` as ISO 8601
with an offset, and an `all_day` flag. The site's homepage is no substitute:
MIT's homepage JSON-LD carried today's and featured events, about 5% of the
calendar.

Fixes carried over from Cambridge Calendar's MIT Events scraper:

  - One event per `event_instance`, so a recurring series and a `distinct`
    response read the same way.
  - A start without an offset, or with seconds, is refused: Localist always
    sends both, so anything else means the field changed meaning, and a guess
    would move events by hours. An all-day instance is dated from the date it
    is written on - converting its midnight to another zone would move it to
    the day before.
  - Most of a university calendar is internal. Only events open to the general
    public are kept: those whose audience filter includes `audience` (MIT:
    "Public"), or whose own text says "open to the public" - some are untagged -
    unless the text restricts them ("invite-only", "not open to the public",
    "open to the MIT community only"; one MIT event tagged Public said so).
    "Free for MIT students only" is a price, not an audience.
  - Holidays are closures, not events (`exclude_types`).
  - Departments re-post events under new URLs ("copy-of-copy-of-symplectic-
    geometry-seminar"), so identity is title, start and place.
  - A virtual event is "Online", not placed on campus.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests

from src.adapters._common import clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "localist"
KIND = "requests"
CLASS = "LocalistAdapter"
SIGNATURES = [
    "Powered by Localist",
    "localist.com",
    "localist-widget",
    "/api/2/events",
]
PARAMS = {
    "days": "days ahead to read, as the API's own window (default 60; Localist allows up to 370)",
    "audience": "keep events whose audience filter includes this value, or whose text says they "
                "are open to the public (default 'Public'); null keeps every unrestricted event",
    "audience_filter": "the Localist filter that names the audience (default 'event_audience')",
    "affiliation": "the institution's names (e.g. ['MIT']), so 'MIT ID required' or 'open only to "
                   "the MIT community' read as restrictions",
    "exclude_types": "event types that are not attendable events (default: holiday and "
                     "academic-calendar types; a list replaces it)",
    "query": "extra API query parameters, e.g. {group_id: 123} for one department",
    "api_url": "the API endpoint, when it is not <site>/api/2/events",
    "max_pages": "sanity cap on pages of 100 (default 30); reaching it is logged as an error",
    "category": "an EventCategory value for every event (e.g. lectures); default: left to enrichment",
}

API_PATH = "/api/2/events"
PAGE_SIZE = 100      # Localist's maximum

DEFAULT_EXCLUDED_TYPES = ("Holidays", "Holiday", "Institute Holidays", "University Holidays",
                          "Academic Calendar", "Academic Dates", "Closures")

OPEN_TO_PUBLIC = re.compile(
    r"(?<!not )(?<!n't )open\s+to\s+the\s+(?:general\s+)?public|public\s+is\s+welcome", re.IGNORECASE)

# Statements about who may attend - not about who gets in free.
_GROUP = r"(?:community|students|affiliates|faculty|staff|employees)"
_RESTRICTED = (
    r"\binvite[\s-]+only|\bby\s+invitation\s+only",
    r"(?:not|n't)\s+open\s+to\s+the\s+(?:general\s+)?public",
    r"\bclosed\s+to\s+the\s+public",
    # "open to the MIT community only", and MIT's own "open the MIT Community only"
    rf"\bopen\s+(?:only\s+)?(?:to\s+)?(?:the\s+)?(?:[\w&.'-]+\s+){{0,3}}{_GROUP}(?:\s+members)?\s+only",
)

NOTICE_TITLE = re.compile(
    r"^\W*(?:cancell?ed|postponed)\b|[\s(\[:–—-](?:cancell?ed|postponed)\W*$", re.IGNORECASE)


def restriction_pattern(affiliation: Iterable[str] = ()) -> re.Pattern:
    """The restriction rules, with the institution's own names where they matter."""
    names = "|".join(re.escape(n) for n in affiliation if n)
    rules = list(_RESTRICTED)
    if names:
        rules += [
            rf"\b(?:only\s+open|open\s+only)\s+to\s+(?:the\s+)?(?:{names})\b",
            rf"\brestricted\s+to\s+(?:the\s+)?(?:{names})\b",
            rf"\b(?:{names})\s+(?:id|kerberos|certificate|login|credentials?)\s+(?:is\s+|are\s+)?required",
        ]
    return re.compile("|".join(rules), re.IGNORECASE)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


class LocalistAdapter(BaseScraper):
    """Public events from a Localist calendar."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 days: int = 60, audience: Optional[str] = "Public",
                 audience_filter: str = "event_audience", affiliation=(),
                 exclude_types=DEFAULT_EXCLUDED_TYPES, query: Optional[dict] = None,
                 api_url: Optional[str] = None, max_pages: int = 30,
                 category: Optional[str] = None):
        super().__init__(source_name, url)
        self.venue = venue or {}
        self.days = _int(days, 60)
        self.audience = audience or None
        self.audience_filter = audience_filter or "event_audience"
        self.restricted = restriction_pattern(_as_list(affiliation))
        self.exclude_types = {str(t).lower() for t in _as_list(exclude_types)}
        self.query = dict(query or {})
        if not api_url:
            parts = urlparse(url)
            api_url = f"{parts.scheme or 'https'}://{parts.netloc}{API_PATH}"
        self.api_url = api_url
        self.max_pages = max(1, _int(max_pages, 30))
        self.category = EventCategory(category) if category else None

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #

    def fetch_page(self, page: int) -> dict:
        response = requests.get(
            self.api_url,
            params={**self.query, "days": self.days, "pp": PAGE_SIZE, "page": page},
            headers={**self.get_browser_headers(), "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("events"), list):
            raise ValueError(f"{self.source_name}: Localist response has no events[] list")
        return body

    def scrape_events(self) -> List[EventCreate]:
        items: List[dict] = []
        page, total = 1, 1
        while page <= total and page <= self.max_pages:
            body = self.fetch_page(page)
            total = _int((body.get("page") or {}).get("total"), 0)
            items.extend(x.get("event") or {} for x in body["events"] if isinstance(x, dict))
            page += 1
        if page <= total:
            logger.error(f"{self.source_name}: the Localist API reports {total} pages but max_pages is "
                         f"{self.max_pages}; the rest of the {self.days}-day window was not read")
        events = self.parse_items(items)
        logger.info(f"Scraped {len(events)} public events from {self.source_name} ({len(items)} listed)")
        return events

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def parse_items(self, items: List[dict]) -> List[EventCreate]:
        if self.audience and items and not any(
                (i.get("filters") or {}).get(self.audience_filter) for i in items):
            # Every event will be judged on its text alone; that is a
            # configuration error, not a quiet calendar
            logger.error(f"{self.source_name}: none of {len(items)} events carries the "
                         f"'{self.audience_filter}' filter. Name this calendar's audience filter "
                         f"(`audience_filter`), or set `audience: null` to keep every unrestricted event.")
        events: List[EventCreate] = []
        seen = set()
        for item in items:
            try:
                parsed = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse item {item.get('id')}: {e}")
                continue
            for event in parsed:
                key = (event.title.lower(), event.start_datetime, event.venue_name)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        return events

    def is_public(self, item: dict) -> bool:
        """Open to the general public, by the audience filter or the event's own words."""
        text = f"{item.get('title') or ''}\n{item.get('description_text') or ''}"
        if self.restricted.search(text):
            return False
        if not self.audience:
            return True
        audiences = {str(a.get("name") or "").lower()
                     for a in (item.get("filters") or {}).get(self.audience_filter) or [] if isinstance(a, dict)}
        return self.audience.lower() in audiences or bool(OPEN_TO_PUBLIC.search(text))

    def parse_item(self, item: dict) -> List[EventCreate]:
        """One EventCreate per instance of a public event."""
        title = clean(item.get("title"))
        url = (item.get("localist_url") or "").strip()
        if len(title) < 3 or not url.startswith("http"):
            return []
        if item.get("private") or str(item.get("status") or "").lower() in ("canceled", "cancelled"):
            return []
        if NOTICE_TITLE.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}' ({url})")
            return []

        types = [str(t.get("name") or "") for t in (item.get("filters") or {}).get("event_types") or []
                 if isinstance(t, dict)]
        if self.exclude_types.intersection(t.lower() for t in types) or not self.is_public(item):
            return []

        description = clean(item.get("description_text")) or strip_html(item.get("description"))
        location, lat, lng = self.location(item)
        if len(description) < 20:
            description = f"{title} at {location['venue_name'] or self.source_name}."
        website = item.get("url")

        events = []
        for wrapper in item.get("event_instances") or []:
            instance = (wrapper or {}).get("event_instance") or {}
            all_day = bool(instance.get("all_day"))
            start = self.read_instant(instance.get("start"), all_day)
            if start is None:
                logger.warning(f"Skipping an instance of '{title}' - unreadable start "
                               f"{instance.get('start')!r} ({url})")
                continue
            end = None if all_day else self.read_instant(instance.get("end"))
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end if end and end > start else None,
                all_day=all_day,
                **location,
                latitude=lat,
                longitude=lng,
                category=self.category,
                tags=[t for t in types if t],
                cost=self.cost(item),
                source_url=url,
                source_name=self.source_name,
                website_url=website if isinstance(website, str) and website.startswith("http") else None,
                image_url=item.get("photo_url") or None,
            ))
        return events

    @staticmethod
    def read_instant(value, all_day: bool = False) -> Optional[datetime]:
        """ISO 8601 with an offset and whole minutes, as local wall clock.

        An all-day instance is its written date at midnight, unconverted.
        """
        if not value or not isinstance(value, str):
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None or dt.second or dt.microsecond:
            return None
        if all_day:
            return datetime(dt.year, dt.month, dt.day)
        return on_the_minute(dt)

    def location(self, item: dict) -> tuple:
        """(place fields, latitude, longitude). Virtual events are "Online"."""
        if item.get("experience") == "virtual":
            return {**venue_fields(self.venue, name="Online"), "state": None}, None, None

        geo = item.get("geo") or {}
        name = clean(item.get("location_name")) or clean(item.get("location"))
        room = clean(item.get("room_number"))
        if name and room:
            # "449" -> "Building 2, Room 449"; "Thomas Tull Concert Hall" as is
            name = f"{name}, Room {room}" if re.match(r"^[A-Z]?-?\d", room) else f"{name}, {room}"
        street, city = clean(geo.get("street")), clean(geo.get("city"))
        fields = venue_fields(self.venue, name=name[:150], street=street, city=city,
                              zip_code=clean(geo.get("zip")) or None)
        if not (name or street or city):
            return {**fields, "state": None}, None, None
        state = clean(geo.get("state"))
        try:
            lat = float(geo["latitude"]) if geo.get("latitude") else None
            lng = float(geo["longitude"]) if geo.get("longitude") else None
        except (TypeError, ValueError):
            lat = lng = None
        return {**fields, "state": state if re.fullmatch(r"[A-Z]{2}", state) else None}, lat, lng

    @staticmethod
    def cost(item: dict) -> Optional[str]:
        raw = clean(str(item.get("ticket_cost") or ""))
        if raw and raw not in ("0", "$0"):
            return (raw[:1].upper() + raw[1:])[:100]
        if item.get("free") or raw in ("0", "$0"):
            return "Free"
        return None
