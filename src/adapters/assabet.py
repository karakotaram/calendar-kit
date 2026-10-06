"""Assabet Interactive library calendars (`<library>.assabetinteractive.com`).

Many public libraries run Assabet Interactive and embed it on their own site in
an iframe; read Assabet directly. Its month listing
(`/calendar/event-listing/`, which redirects to the current month) renders
server-side and describes every event twice:

  - a schema.org JSON-LD block: `startDate` (date only), `doorTime` (start),
    `duration`, `eventStatus`, attendance mode, address, image
  - a visible card: "Thursday, October 1", "10:30 AM—12:00 PM", branch, room,
    address, and the event's Assabet categories as CSS classes

What Cambridge Calendar learned reading the Somerville Public Library:

- **The JSON-LD start is believed only when the card agrees.** `doorTime`
  normally means doors-open; on Assabet it is the start for every event
  checked, but if that ever changes the cross-check notices and the event is
  skipped. The card writes "6:00—7:00 PM" when both ends share a meridiem: the
  start borrows the end's, or every evening program fails the check.
- **Months chain by the listing's own "Next Month" link**, so no clock is read.
  An empty month is the edge of what has been published.
- **Closure cards ("All Closed") are not events** - they have no event link -
  and cancelled events are skipped.
- **JSON-LD descriptions carry raw newlines** (invalid under strict JSON) and
  excerpts are entity-encoded twice; both are handled.
- **The venue comes from the card**: a branch ("Somerville Public Library –
  West Branch"), "Online" for virtual programs, or the named place for an
  off-site one. An address on the card wins over the registry's.

Params: `max_months` (default 6; programming is published about six months
out), `branch_prefix` (prepended to branch names; default the registry venue's
name, else the source name), `category` (one EventCategory for every event,
instead of Assabet's own categories).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from src.adapters._common import CANCELLED, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "assabet"
KIND = "requests"
CLASS = "AssabetAdapter"
SIGNATURES = [
    "assabetinteractive.com",
    "Assabet Interactive",      # <meta name="generator">
    "listing-event",            # the month listing's cards
    "event-listing",            # the listing's path
]
PARAMS = {
    "max_months": "months to read, following the listing's own Next Month link (default 6)",
    "branch_prefix": "prepended to a branch's name for the venue, e.g. \"Somerville Public Library\" "
                     "(default: the registry venue's name, else the source name)",
    "category": "an EventCategory value for every event (e.g. theater); default: Assabet's own categories, then keywords",
}

# "10:30 AM—12:00 PM", "6:00—7:00 PM" (the start shares the end's meridiem), "2:00 PM"
VISIBLE_TIME = re.compile(
    r"(\d{1,2}):(\d{2})\s*([AP]\.?M\.?)?(?:\s*[—–-]\s*\d{1,2}:\d{2}\s*([AP]\.?M\.?))?", re.IGNORECASE)
ALL_DAY = re.compile(r"\ball[\s-]*day\b", re.IGNORECASE)
# "79 Highland Ave, Somerville , MA, 02143"
ADDRESS = re.compile(r"^(?P<street>.+?),\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Z]{2}),?\s*(?P<zip>\d{5})?$")
DURATION = re.compile(r"^PT(\d+)S$")
ONLINE_PLACES = {"zoom", "virtual", "online"}

FAMILY_CATEGORIES = {"children", "storytimes", "all-ages", "kids", "family", "families"}


class AssabetAdapter(BaseScraper):
    """One Assabet Interactive calendar."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 max_months: int = 6, branch_prefix: Optional[str] = None,
                 category: Optional[str] = None):
        parts = urlsplit(url)
        # Always the undated listing, which redirects to the current month: a
        # registry URL naming one month would pin every run to that month.
        super().__init__(source_name=source_name,
                         source_url=f"{parts.scheme}://{parts.netloc}/calendar/event-listing/")
        self.venue = dict(venue or {})
        self.max_months = max(1, int(max_months))
        self.branch_prefix = branch_prefix or self.venue.get("name") or source_name
        self.category = EventCategory(category) if category else None

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        url = self.source_url
        for month in range(self.max_months):
            try:
                soup = self.parse_html(self.fetch_html(url))
            except Exception as e:
                if not month:
                    raise          # the first page failing is the source failing, not "no events"
                logger.error(f"{self.source_name}: stopped at {url}: {e}")
                break

            new = 0
            for event in self.parse_month(soup):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
                    new += 1

            following = self.next_month_url(soup, url)
            if not new or not following:
                break
            url = following

        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def next_month_url(soup, current: str) -> Optional[str]:
        link = soup.select_one('a[href*="/event-listing/?from=next"]')
        return urljoin(current, link["href"]) if link else None

    def parse_month(self, soup) -> List[EventCreate]:
        """Every dateable, scheduled event on one month's listing."""
        structured = self.json_ld_by_url(soup)
        events = []
        for card in soup.select("div.listing-event"):
            # Closure notices ("All Closed") are cards too, with no event link
            link = card.select_one("h3 a[href]")
            if not link:
                continue
            data = structured.get(link["href"])
            if data is None:
                logger.warning(f"Skipping {link['href']} - no structured data to date it by")
                continue
            try:
                event = self.parse_event(card, data)
            except Exception as e:
                logger.warning(f"{self.source_name}: could not parse {link['href']}: {e}")
                continue
            if event:
                events.append(event)
        return events

    @staticmethod
    def json_ld_by_url(soup) -> Dict[str, dict]:
        out = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                # Descriptions carry raw newlines, which strict JSON forbids
                data = json.loads(script.string or "", strict=False)
            except ValueError as e:
                logger.warning(f"Unparseable JSON-LD block: {e}")
                continue
            if isinstance(data, dict) and data.get("@type") == "Event" and data.get("url"):
                out[data["url"]] = data
        return out

    def parse_event(self, card, data: dict) -> Optional[EventCreate]:
        title = strip_html(data.get("name") or "")
        if len(title) < 3:
            return None
        if (data.get("eventStatus") or "").endswith("EventCancelled") or CANCELLED.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start, all_day = self.start(card, data)
        if start is None:
            # Never guess a date
            logger.warning(f"Skipping '{title}' - start unreadable or self-contradictory ({data.get('url')})")
            return None

        end = None
        duration = DURATION.match(data.get("duration") or "")
        if duration and not all_day:
            end = start + timedelta(seconds=int(duration.group(1)))

        categories = {c[len("category-"):] for c in card.get("class", []) if c.startswith("category-")}
        location = self.location(card, data)
        where = location.get("venue_name") or self.branch_prefix
        description = self.description(data.get("description") or "") or f"{title} at {where}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            all_day=all_day,
            source_url=data["url"],
            source_name=self.source_name,
            image_url=data.get("image") or None,
            category=self.category or self.categorize(categories, title, description),
            family_friendly=bool(categories & FAMILY_CATEGORIES),
            **location,
        )

    def start(self, card, data: dict) -> tuple:
        """(start, all_day): `startDate` + `doorTime`, if the visible card says the same.

        (None, False) when either is unreadable or they disagree.
        """
        try:
            day_value = datetime.strptime(str(data.get("startDate"))[:10], "%Y-%m-%d")
        except ValueError:
            return None, False
        day = card.select_one(".event-day")
        when = card.select_one(".event-time")
        if not day or f"{day_value:%B} {day_value.day}" not in " ".join(day.get_text().split()):
            if day:
                logger.warning(f"Card day {day.get_text()!r} disagrees with startDate {data.get('startDate')}")
            return None, False
        shown = " ".join(when.get_text().split()) if when else ""

        door = data.get("doorTime")
        if ALL_DAY.search(shown) and (not door or door.startswith("00:00")):
            return day_value, True          # a date with no time

        try:
            start = datetime.strptime(f"{data.get('startDate')} {door}", "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None, False
        match = VISIBLE_TIME.search(shown)
        meridiem = (match.group(3) or match.group(4) or "") if match else ""
        if not match or not meridiem:
            return None, False
        hour = int(match.group(1)) % 12 + (12 if meridiem.upper().startswith("P") else 0)
        if (hour, int(match.group(2))) != (start.hour, start.minute):
            logger.warning(f"Card time {match.group(0)!r} disagrees with doorTime {door}")
            return None, False
        return start, False

    @staticmethod
    def description(raw: str) -> str:
        """The excerpt is entity-encoded (sometimes twice) HTML ending in "Learn More"."""
        text = strip_html(raw)
        return re.sub(r"\s*Learn More\s*$", "", text).strip()

    def location(self, card, data: dict) -> dict:
        """EventCreate location fields for a branch, an online event, or an off-site one."""
        def text(selector):
            el = card.select_one(selector)
            return (" ".join(el.get_text().split()) or None) if el else None

        branch = text(".event-location-branch")
        room = text(".event-location-location")
        online = (data.get("eventAttendanceMode") or "").endswith("OnlineEventAttendanceMode")
        virtual_place = (branch or room or "").lower() in ONLINE_PLACES
        if online or (virtual_place and not text(".event-location-address")):
            fields = venue_fields(None, name="Online")
            fields["state"] = None
            return fields

        name = f"{self.branch_prefix} – {branch}" if branch else room
        address = text(".event-location-address")
        match = ADDRESS.match(address or "")
        if match:
            fields = venue_fields(self.venue, name=name, street=match["street"].strip(),
                                  city=match["city"].strip(), zip_code=match["zip"])
            fields["state"] = match["state"]
            return fields
        fields = venue_fields(self.venue, name=name, street=address)
        if name or address:
            # A named place with no readable address is still in the library's city
            fields["city"] = fields["city"] or self.venue.get("city")
        fields["state"] = None
        return fields

    @staticmethod
    def categorize(categories: set, title: str, description: str) -> EventCategory:
        """Assabet's own categories first, keywords for the rest."""
        text = f"{title} {description}".lower()
        if categories & {"esl", "us-citizenship", "social-work"}:
            return EventCategory.COMMUNITY
        if "music-movies" in categories:
            return EventCategory.MUSIC if any(w in text for w in ("music", "concert", "sing")) else EventCategory.ARTS_CULTURE
        if categories & {"storytimes", "hands-on", "book-clubs"}:
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("concert", "singalong", "sing-along", "music")):
            return EventCategory.MUSIC
        if any(w in text for w in ("workshop", "class", "lecture", "talk", "author")):
            return EventCategory.LECTURES
        if any(w in text for w in ("craft", "art", "film", "movie", "game", "club")):
            return EventCategory.ARTS_CULTURE
        return EventCategory.COMMUNITY
