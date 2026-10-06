"""Squarespace events collections, read as JSON (`<collection>?format=json`).

Any Squarespace collection page returns its data as JSON when asked. For an
events collection, `upcoming[]` holds every upcoming event in one response,
with `startDate`/`endDate` as epoch milliseconds (UTC), the post body, the
excerpt and, when the venue filled it in, a location. `past[]` is ignored.
The rendered page is no better a source: it is cached (The Lily Pad's still
marked shows from two days earlier as upcoming) and capped per page.

Fixes carried over from Cambridge Calendar's Lily Pad, Portico and MIT Open
Space scrapers:

  - Epoch milliseconds are converted from UTC explicitly and floored to the
    minute. Squarespace keeps creation-time noise in the milliseconds (7:30 PM
    arrives as ...400563), which the validator rightly rejects as a clock
    reading.
  - Bodies carry the editor's "Double-click to edit..." placeholder; titles
    carry entities ("Music &amp; Soup") and trailing padding.
  - Private bookings ("private event", "invite only") are not public events.
  - A title that *starts* with RESCHEDULED, CANCELLED or POSTPONED is a notice
    on the old date, not an event: MIT Open Space renames the original listing
    "RESCHEDULED: <title>" and posts the new date as a separate item. Squarespace
    has no status field, so the title is all there is. One that merely
    mentions the word is kept.
  - The venue comes from each item's own location when it has one. An empty
    location still carries map coordinates - Squarespace's default, in New
    York - so coordinates are used only alongside an address.
  - Dollar amounts in a trivia listing are prizes, not admission (Portico).

Not covered: a Squarespace *store* used as an events list (Skip the Small Talk).
Its products carry the date only as free text in tags and titles, so reading
one is a venue-specific parser, not this platform's contract.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.adapters._common import clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "squarespace"
KIND = "requests"
CLASS = "SquarespaceEventsAdapter"
SIGNATURES = [
    "<!-- This is Squarespace. -->",
    "static1.squarespace.com",
    "Static.SQUARESPACE_CONTEXT",
    "eventlist-event",
]
PARAMS = {
    "category": "an EventCategory value for every event (e.g. music); default: left to enrichment",
}

# Squarespace editor placeholders that survive into published bodies
PLACEHOLDERS = re.compile(r"double-click to edit\.*|your custom text here", re.IGNORECASE)

PRIVATE = ("private party", "private event", "closed to public", "closed to the public",
           "invite only", "invite-only", "members only", "by invitation")

# A notice on the old date, not an event; see the module docstring
NOTICE_TITLE = re.compile(
    r"^\W*(?:re-?scheduled|cancell?ed|postponed)\b|[\s(\[:–—-](?:cancell?ed|postponed)\W*$", re.IGNORECASE)

TRIVIA = ("trivia", "quiz", "jeopardy", "bingo", "prize")
PRICE = re.compile(r"\$\d+(?:\.\d{2})?(?:\s*[-–]\s*\$?\d+(?:\.\d{2})?|\s*/\s*\$\d+(?:\.\d{2})?)?")
ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def epoch_ms(value) -> Optional[datetime]:
    """Squarespace epoch milliseconds (UTC) -> local wall clock, on the minute."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return on_the_minute(datetime.fromtimestamp(value / 1000, tz=timezone.utc))


class SquarespaceEventsAdapter(BaseScraper):
    """A venue's Squarespace events collection."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 category: Optional[str] = None):
        super().__init__(source_name, url)
        self.venue = venue or {}
        self.category = EventCategory(category) if category else None

    def fetch_collection(self) -> dict:
        response = requests.get(
            self.source_url,
            params={"format": "json"},
            headers={**self.get_browser_headers(), "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        events = self.parse_collection(self.fetch_collection())
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_collection(self, payload: dict) -> List[EventCreate]:
        upcoming = payload.get("upcoming") if isinstance(payload, dict) else None
        if not isinstance(upcoming, list):
            # A shape change, or a URL that is not an events collection, must
            # fail the source rather than publish it empty.
            raise ValueError(f"{self.source_name}: Squarespace JSON has no upcoming[] list - "
                             f"is {self.source_url} an events collection?")
        events: List[EventCreate] = []
        seen = set()
        for item in upcoming:
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse {item.get('fullUrl')}: {e}")
                continue
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key not in seen:
                seen.add(key)
                events.append(event)
        return events

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = clean(item.get("title"))
        if len(title) < 3:
            return None
        path = item.get("fullUrl") or ""
        url = urljoin(self.source_url, path) if path else self.source_url

        if NOTICE_TITLE.search(title):
            logger.info(f"{self.source_name}: skipping notice '{title}' ({url})")
            return None

        start = epoch_ms(item.get("startDate"))
        if start is None:
            # Never guess a date - see CLAUDE.md, "Never fabricate a date".
            logger.warning(f"Skipping '{title}' - unreadable startDate {item.get('startDate')!r} ({url})")
            return None
        end = epoch_ms(item.get("endDate"))
        if end is not None and end <= start:
            end = None

        # The post's text blocks; else the excerpt; else whatever text the body has
        excerpt = strip_html(item.get("excerpt"))
        description = (self.body_text(item.get("body")) or excerpt
                       or self.body_text(item.get("body"), blocks_only=False))
        if any(k in f"{title} {description}".lower() for k in PRIVATE):
            logger.info(f"{self.source_name}: skipping private event '{title}' ({url})")
            return None

        location, lat, lng = self.location(item.get("location"))
        if len(description) < 20:
            description = f"{title} at {location['venue_name'] or self.source_name}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            **location,
            latitude=lat,
            longitude=lng,
            category=self.category,
            tags=[clean(c) for c in item.get("categories") or [] if isinstance(c, str) and clean(c)],
            cost=self.cost(title, excerpt, description),
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("assetUrl") or None,
        )

    @staticmethod
    def body_text(body: Optional[str], blocks_only: bool = True) -> str:
        """The post's text blocks, without editor placeholders."""
        soup = BeautifulSoup(body if isinstance(body, str) else "", "html.parser")
        blocks = [b.get_text(" ") for b in soup.select(".sqs-html-content")]
        if not blocks and not blocks_only:
            blocks = [soup.get_text(" ")]
        blocks = [PLACEHOLDERS.sub("", clean(b)).strip() for b in blocks]
        return clean(" ".join(b for b in blocks if b))

    @staticmethod
    def cost(title: str, excerpt: str, description: str) -> Optional[str]:
        if any(w in f"{title} {description}".lower() for w in TRIVIA):
            return None
        match = PRICE.search(excerpt) or PRICE.search(description)
        return match.group() if match else None

    def location(self, loc) -> Tuple[dict, Optional[float], Optional[float]]:
        """EventCreate place fields and coordinates from a Squarespace location."""
        loc = loc if isinstance(loc, dict) else {}
        name = clean(loc.get("addressTitle"))
        street = clean(loc.get("addressLine1"))
        line2 = clean(loc.get("addressLine2"))       # "Cambridge, MA, 02139"
        parts = [p.strip() for p in line2.split(",") if p.strip()]
        city = parts[0] if parts and not ZIP.fullmatch(parts[0]) else ""
        state = next((p for p in parts[1:] if re.fullmatch(r"[A-Z]{2}", p)), None)
        zip_match = ZIP.search(line2)

        fields = venue_fields(self.venue, name=name, street=street, city=city,
                              zip_code=zip_match.group(1) if zip_match else None)
        if not (name or street or city):
            return {**fields, "state": None}, None, None
        lat = loc.get("markerLat") or loc.get("mapLat")
        lng = loc.get("markerLng") or loc.get("mapLng")
        if not (street and isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
            lat = lng = None
        return {**fields, "state": state}, lat, lng
