"""The Events Calendar (Tribe) for WordPress, read from its REST API.

Every site running The Events Calendar serves `/wp-json/tribe/events/v1/events`:
one JSON object per event, paged, with `total_pages`. It is the only complete,
machine-readable view. The HTML is no substitute: Tribe v6 renders its views a
day at a time in JavaScript, and the list page's JSON-LD carries only its first
ten events (Mount Auburn Cemetery: eleven days of a year-long programme).

Fixes carried over from Cambridge Calendar's five Tribe scrapers:

  - Pages are followed to the API's own `total_pages`. `max_pages` is only a
    sanity cap, and reaching it is logged as an error: The Dance Complex's old
    cap of 20 pages was about to truncate its listing silently.
  - `venue` and `image` are polymorphic: a dict, an empty list, a list of
    dicts, or `False`. See `_as_dict`.
  - Starts come from `utc_start_date`, converted to the calendar's zone, and
    are believed only if they agree with the local `start_date` in the event's
    own `timezone`. A naive local string is never read as if it were ours.
  - `all_day` means the venue gave no time: the event is dated, not timed.
  - Titles and descriptions carry HTML entities ("&#8211;", "&#038;"), and
    descriptions carry page-builder shortcodes ("[vc_row ...]").
  - One request per page and no retries, with an honest user-agent: Longy's
    host rate-limits into an Imunify360 challenge, and a second attempt is
    what trips it. A refusal or a challenge page raises, so the source is
    recorded as failed rather than as a venue with nothing scheduled.
  - A "[Virtual]" event with no venue is online, not at the venue's address.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import pytz
import requests

from src.adapters._common import clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "tribe"
KIND = "requests"
CLASS = "TribeEventsAdapter"
SIGNATURES = [
    "/wp-content/plugins/the-events-calendar/",
    "tribe/events/v1",
    "tribe-events-calendar",
    "tribe-events-view",
]
PARAMS = {
    "per_page": "events per API request (default 50, Tribe's usual maximum)",
    "max_pages": "sanity cap on pages followed (default 50); reaching it is logged as an error",
    "days": "only events starting within this many days, as a server-side window; "
            "default: everything upcoming",
    "api_url": "the REST endpoint, when it is not <site>/wp-json/tribe/events/v1/events "
               "(WordPress in a subdirectory, or no pretty permalinks)",
    "categories": "only these Tribe event categories (list of slugs or ids)",
    "venue_ids": "only events at these Tribe venue ids (list)",
    "rooms": "true when a venue the API gives without an address is a room inside the registry "
             "venue (The Dance Complex's 'Studio 7', Longy's 'Pickman Hall'): the event takes the "
             "registry venue, with the room appended to its street. Venues with an address keep it",
    "category": "an EventCategory value for every event (e.g. music); default: left to enrichment",
}

API_PATH = "/wp-json/tribe/events/v1/events"

# WPBakery layout markup left in descriptions: "[vc_row type=...]", "[/vc_column]"
SHORTCODE = re.compile(r"\[/?[a-z][a-z0-9_]*(?:\s[^\]]*)?\]")

# A listing that is a notice about an event, not an event: "CANCELLED: X",
# "X (Postponed)". A title that merely mentions the word is kept.
NOTICE_TITLE = re.compile(
    r"^\W*(?:cancell?ed|postponed)\b|[\s(\[:–—-](?:cancell?ed|postponed)\W*$", re.IGNORECASE)

# Mount Auburn writes online-only events as "[Virtual] Archival Silences ..."
ONLINE_TITLE = re.compile(r"^\s*(?:\[\s*(?:virtual|online)\s*\]|(?:virtual|online)\s*:)", re.IGNORECASE)

# Tribe's manual-offset zones: "UTC-5", "UTC+5.5"
UTC_OFFSET = re.compile(r"^UTC(?:([+-]\d{1,2}(?:\.\d+)?))?$")


def _as_dict(value) -> dict:
    """Tribe returns venue/image as a dict, an empty list, a list of dicts, or False."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _naive(value) -> Optional[datetime]:
    """Tribe writes "2026-10-08 19:30:00", with no offset."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _zone(name) -> Optional[pytz.BaseTzInfo]:
    """The event's own time zone: an IANA name, or Tribe's "UTC-5" form."""
    if not name or not isinstance(name, str):
        return None
    match = UTC_OFFSET.match(name.strip())
    if match:
        hours = float(match.group(1) or 0)
        return pytz.utc if not hours else pytz.FixedOffset(round(hours * 60))
    try:
        return pytz.timezone(name.strip())
    except pytz.UnknownTimeZoneError:
        return None


def _text(value) -> str:
    """Plain text from Tribe's HTML-and-entities fields."""
    return strip_html(value) if isinstance(value, str) else ""


def _image(value) -> Optional[str]:
    if isinstance(value, str):
        return value if value.startswith("http") else None
    url = _as_dict(value).get("url")
    return url if isinstance(url, str) and url.startswith("http") else None


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


class TribeEventsAdapter(BaseScraper):
    """A venue's The Events Calendar listing, read from the Tribe REST API."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 per_page: int = 50, max_pages: int = 50, days: Optional[int] = None,
                 api_url: Optional[str] = None, categories=None, venue_ids=None,
                 rooms: bool = False, category: Optional[str] = None):
        super().__init__(source_name, url)
        self.venue = venue or {}
        self.per_page = _int(per_page, 50)
        self.max_pages = max(1, _int(max_pages, 50))
        self.days = _int(days, 0) or None
        if not api_url:
            parts = urlparse(url)
            api_url = f"{parts.scheme or 'https'}://{parts.netloc}{API_PATH}"
        self.api_url = api_url
        self.categories = [str(c) for c in _as_list(categories)]
        self.venue_ids = [str(v) for v in _as_list(venue_ids)]
        self.rooms = bool(rooms)
        self.category = EventCategory(category) if category else None

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #

    def query(self, page: int) -> dict:
        params = {"per_page": self.per_page, "start_date": "now", "status": "publish", "page": page}
        if self.days:
            # Tribe reads the window with strtotime, so no clock is read here
            params["end_date"] = f"+{self.days} days"
        if self.categories:
            params["categories"] = ",".join(self.categories)
        if self.venue_ids:
            params["venue"] = ",".join(self.venue_ids)
        return params

    def fetch_page(self, page: int) -> Optional[dict]:
        """One page of the API. No retries: see the module docstring."""
        response = requests.get(
            self.api_url,
            params=self.query(page),
            headers={**self.get_browser_headers(), "Accept": "application/json"},
            timeout=30,
        )
        if page > 1 and response.status_code == 400:
            # Tribe answers 400 for a page past the last; the listing shrank
            # between two requests
            logger.warning(f"{self.source_name}: page {page} is past the end of the listing")
            return None
        response.raise_for_status()
        return response.json()   # a challenge page is HTML: this raises

    def scrape_events(self) -> List[EventCreate]:
        items: List[dict] = []
        total_pages = 1
        for page in range(1, self.max_pages + 1):
            payload = self.fetch_page(page)
            if payload is None:
                break
            batch = payload.get("events")
            if not isinstance(batch, list):
                raise ValueError(f"{self.source_name}: Tribe API response has no events[] list")
            total_pages = _int(payload.get("total_pages"), total_pages)
            if not batch:
                if page > 1 and page <= total_pages:
                    logger.warning(f"{self.source_name}: API page {page} of {total_pages} came back empty")
                break
            items.extend(batch)
            if page >= total_pages:
                break
        else:
            # The loop ran out before the API did: the listing is truncated.
            logger.error(f"{self.source_name}: the Tribe API reports {total_pages} pages but max_pages "
                         f"is {self.max_pages}; about {(total_pages - self.max_pages) * self.per_page} "
                         f"events were not read. Raise max_pages or set a shorter `days` window.")

        events = self.parse_items(items)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(items)} listed)")
        return events

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def parse_items(self, items: List[dict]) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        for item in items:
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse {item.get('url')}: {e}")
                continue
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key not in seen:
                seen.add(key)
                events.append(event)
        return events

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        if item.get("status", "publish") != "publish" or item.get("hide_from_listings"):
            return None
        title = _text(item.get("title"))
        if len(title) < 3:
            return None
        url = item.get("url") or self.source_url
        if NOTICE_TITLE.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}' ({url})")
            return None

        all_day = bool(item.get("all_day"))
        if all_day:
            local = _naive(item.get("start_date"))
            start = local.replace(hour=0, minute=0, second=0) if local else None
            end = None
        else:
            start = self.read_time(item, "start_date")
            end = self.read_time(item, "end_date")
        if start is None:
            # Never guess a date - see CLAUDE.md, "Never fabricate a date".
            logger.warning(f"Skipping '{title}' - no start that its own fields agree on ({url})")
            return None
        if end is not None and end <= start:
            end = None

        location = self.location(item, title)
        description = self.prose(item.get("excerpt")) or self.prose(item.get("description"))
        if len(description) < 20:
            description = f"{title} at {location['venue_name'] or self.source_name}."

        tags = [_text(c.get("name")) for c in item.get("categories") or [] if isinstance(c, dict)]

        website = item.get("website")
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            **location,
            category=self.category,
            tags=[t for t in tags if t],
            cost=_text(item.get("cost")) or None,
            source_name=self.source_name,
            source_url=url,
            website_url=website if isinstance(website, str) and website.startswith("http") else None,
            image_url=_image(item.get("image")),
        )

    def read_time(self, item: dict, key: str) -> Optional[datetime]:
        """A start or end as local wall clock, from two fields that must agree.

        `utc_<key>` is UTC wall clock with no offset written; `<key>` is the
        event's local wall clock in its own `timezone`.
        """
        utc = _naive(item.get(f"utc_{key}"))
        local = _naive(item.get(key))
        zone = _zone(item.get("timezone"))
        if utc is None:
            # Without the UTC field, the local one is usable only if we know its zone
            return on_the_minute(zone.localize(local)) if local is not None and zone else None
        instant = pytz.utc.localize(utc)
        if local is not None and zone is not None:
            stated = instant.astimezone(zone).replace(tzinfo=None)
            if stated != local:
                logger.warning(f"{self.source_name}: {key} says {local} but utc_{key} is {stated} "
                               f"in {item.get('timezone')} ({item.get('url')})")
                return None
        return on_the_minute(instant)

    def location(self, item: dict, title: str) -> dict:
        place = _as_dict(item.get("venue"))
        name = _text(place.get("venue"))
        street = _text(place.get("address"))
        city = _text(place.get("city"))
        zip_code = _text(place.get("zip"))
        state = _text(place.get("stateprovince") or place.get("state") or place.get("province"))

        if ONLINE_TITLE.match(title) or (item.get("is_virtual") and not (name or street or city)):
            return {**venue_fields(self.venue, name="Online"), "state": None}
        if self.rooms and name and not (street or city):
            fields = venue_fields(self.venue)
            room_street = f"{fields['street_address']}, {name}" if fields["street_address"] else None
            return {**fields, "street_address": room_street, "state": None}
        fields = venue_fields(self.venue, name=name, street=street, city=city, zip_code=zip_code)
        own = bool(name or street or city)
        return {**fields, "state": state if own and re.fullmatch(r"[A-Z]{2}", state) else None}

    @staticmethod
    def prose(value) -> str:
        """A description without its page-builder shortcodes."""
        return clean(SHORTCODE.sub(" ", _text(value)))
