"""schema.org `Event` JSON-LD embedded in a venue's listing page.

Many sites - ticketing platforms such as SeatEngine, WordPress plugins, site
builders - publish their events as `<script type="application/ld+json">`.
This reads every Event in every such block, wherever it sits: top level, in a
list, under `@graph`, or nested (The Comedy Studio's SeatEngine page lists its
163 shows under an EventVenue's `events`).

Rules, from Cambridge Calendar's Comedy Studio scraper and the rules every
scraper follows:

  - Blocks are parsed with `json.loads(strict=False)`: descriptions carry raw
    newlines, which strict JSON forbids (Somerville Public Library).
  - A `startDate` with a time is used; a date-only one is an all-day event at
    midnight, never given an hour.
  - An offset is believed only where it is the region's own offset at that
    wall clock. Plugins get it wrong: EventON writes -4:00 all winter
    (Central Square Theater), and a WordPress site left on UTC writes its
    local times as +00:00. Where the two disagree nothing says which is right,
    so the event is skipped with a warning; after looking at the page, set
    `offsets: wall` (the written clock is right) or `offsets: instant` (the
    offset is right).
  - Every event in the listing is read. The Comedy Studio's list is not in
    date order, so the old `[:30]` cap kept an arbitrary slice of the season.
  - `performer` holds one value or a list; a show without its own image uses
    its first performer's.
  - Cancelled and postponed events (`eventStatus`) are skipped.
  - A page with JSON-LD but no events returns nothing, and says so. A page
    with no JSON-LD at all is an error: the adapter no longer fits the site.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pytz import utc

from src import config
from src.adapters._common import clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "jsonld"
KIND = "requests"
CLASS = "JsonLdAdapter"
SIGNATURES = [
    re.compile(r'"@type"\s*:\s*\[?\s*"(?:https?://schema\.org/)?[A-Za-z]*(?:Event|Festival)"'),
]
PARAMS = {
    "paths": "extra listing pages to read, relative to the url (e.g. ['/events/list/?page=2'])",
    "max_pages": "follow each listing's rel=next link up to this many pages (default 1: no following)",
    "offsets": "how to read a startDate whose offset is not the region's: 'check' (default: skip it), "
               "'wall' (the written clock is right, the offset wrong), 'instant' (the offset is right)",
    "category": "an EventCategory value for every event (e.g. theater); default: from the schema.org "
                "type where it says (MusicEvent -> music), else left to enrichment",
}

NOT_OCCURRENCES = {"EventSeries", "DeliveryEvent", "PublicationEvent", "BroadcastEvent", "OnDemandEvent"}

CATEGORY_BY_TYPE = {
    "MusicEvent": EventCategory.MUSIC,
    "TheaterEvent": EventCategory.THEATER,
    "ComedyEvent": EventCategory.THEATER,
    "DanceEvent": EventCategory.THEATER,
    "ScreeningEvent": EventCategory.ARTS_CULTURE,
    "VisualArtsEvent": EventCategory.ARTS_CULTURE,
    "ExhibitionEvent": EventCategory.ARTS_CULTURE,
    "LiteraryEvent": EventCategory.ARTS_CULTURE,
    "FoodEvent": EventCategory.FOOD_DRINK,
    "SportsEvent": EventCategory.SPORTS,
    "EducationEvent": EventCategory.LECTURES,
    "SocialEvent": EventCategory.COMMUNITY,
    "ChildrensEvent": EventCategory.COMMUNITY,
}

# "2026-10-22", "2026-10-22T21:30:00-04:00", EventON's "2027-2-4T19:30-4:00"
ISO = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})"
                 r"(?:[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{1,2}(?::?\d{2})?)?)?\s*")

NOTICE_TITLE = re.compile(
    r"^\W*(?:cancell?ed|postponed)\b|[\s(\[:–—-](?:cancell?ed|postponed)\W*$", re.IGNORECASE)
TICKET_LINK = re.compile(r"TICKET\s+LINK:\s*https?://\S+\s*", re.IGNORECASE)
STATE_ZIP = re.compile(r"(?:(?P<city>.*?)\s+)?(?P<state>[A-Z]{2})(?:\s+(?P<zip>\d{5})(?:-\d{4})?)?")


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _type_names(node: dict) -> List[str]:
    """`@type` as bare names: "Event", ["Event", "MusicEvent"], "schema:Event"."""
    return [re.split(r"[/:#]", t)[-1] for t in _as_list(node.get("@type")) if isinstance(t, str)]


def is_event(node: dict) -> bool:
    return any((t.endswith("Event") or t in ("Festival", "Hackathon")) and t not in NOT_OCCURRENCES
               for t in _type_names(node))


def collect_events(node, out: Optional[list] = None) -> list:
    """Every Event object anywhere in a JSON-LD document."""
    out = [] if out is None else out
    if isinstance(node, list):
        for child in node:
            collect_events(child, out)
    elif isinstance(node, dict):
        if is_event(node):
            out.append(node)
        for key, child in node.items():
            if key != "@context" and isinstance(child, (dict, list)):
                collect_events(child, out)
    return out


def json_ld_blocks(html: str) -> List[object]:
    """The parsed contents of every ld+json script on a page."""
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = []
    for script in soup.find_all("script", type=lambda t: bool(t) and "ld+json" in t.lower()):
        raw = (script.string or script.get_text() or "").strip()
        raw = re.sub(r"^\s*(?://\s*)?<!\[CDATA\[|(?://\s*)?\]\]>\s*$|^\s*<!--|-->\s*$", "", raw).strip()
        if not raw:
            continue
        try:
            # Descriptions carry raw newlines, which strict JSON forbids
            blocks.append(json.loads(raw, strict=False))
        except ValueError as e:
            logger.warning(f"Unparseable JSON-LD block ({e}): {raw[:80]!r}")
    return blocks


def _image_url(value) -> Optional[str]:
    """An ImageObject, a URL string, or a list of either."""
    for item in _as_list(value):
        if isinstance(item, str) and item.startswith("http"):
            return item
        if isinstance(item, dict):
            url = item.get("url") or item.get("contentUrl")
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


class JsonLdAdapter(BaseScraper):
    """Events from the schema.org JSON-LD on a venue's listing page(s)."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 paths=(), max_pages: int = 1, offsets: str = "check",
                 category: Optional[str] = None):
        super().__init__(source_name, url)
        self.venue = venue or {}
        self.paths = [paths] if isinstance(paths, str) else list(paths or ())
        try:
            self.max_pages = max(1, int(max_pages))
        except (TypeError, ValueError):
            self.max_pages = 1
        if offsets not in ("check", "wall", "instant"):
            raise ValueError(f"offsets must be 'check', 'wall' or 'instant', not {offsets!r}")
        self.offsets = offsets
        self.category = EventCategory(category) if category else None

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #

    def fetch(self, url: str) -> str:
        """One page. No retries: a failure fails the source."""
        response = requests.get(url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        if "charset" in (response.headers.get("Content-Type") or "").lower():
            return response.text
        # Without a declared charset requests assumes Latin-1; the web is UTF-8
        return response.content.decode("utf-8", errors="replace")

    def scrape_events(self) -> List[EventCreate]:
        pages: List[Tuple[str, str]] = []
        visited = set()
        for start in [self.source_url] + [urljoin(self.source_url, p) for p in self.paths]:
            url = start
            for _ in range(self.max_pages):
                if not url or url in visited:
                    break
                visited.add(url)
                html = self.fetch(url)
                pages.append((url, html))
                url = self.next_page(html, url)
        events = self.parse_pages(pages)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(pages)} pages)")
        return events

    @staticmethod
    def next_page(html: str, url: str) -> Optional[str]:
        soup = BeautifulSoup(html or "", "html.parser")
        link = soup.find(["link", "a"], rel="next", href=True)
        return urljoin(url, link["href"]) if link else None

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def parse_pages(self, pages: Iterable[Tuple[str, str]]) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        found_json_ld = False
        for url, html in pages:
            blocks = json_ld_blocks(html)
            found_json_ld = found_json_ld or bool(blocks)
            nodes = collect_events(blocks)
            if blocks and not nodes:
                logger.warning(f"{self.source_name}: {url} has JSON-LD but no events in it")
            for node in nodes:
                try:
                    event = self.parse_event(node, url)
                except Exception as e:
                    logger.warning(f"{self.source_name}: failed to parse an event on {url}: {e}")
                    continue
                if event is None:
                    continue
                key = (event.source_url, event.start_datetime, event.title)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        if not found_json_ld:
            raise ValueError(f"{self.source_name}: no JSON-LD on {self.source_url} - "
                             "the page has changed, or this adapter does not fit it")
        return events

    def parse_page(self, html: str, url: Optional[str] = None) -> List[EventCreate]:
        return self.parse_pages([(url or self.source_url, html)])

    def parse_event(self, node: dict, page_url: str) -> Optional[EventCreate]:
        title = strip_html(node.get("name") if isinstance(node.get("name"), str) else "")
        if len(title) < 3:
            return None
        status = str(node.get("eventStatus") or "")
        if status.endswith(("EventCancelled", "EventPostponed")) or NOTICE_TITLE.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        url = self.event_url(node, page_url)
        raw_start = _as_list(node.get("startDate"))[0] if node.get("startDate") else None
        when = self.read_when(raw_start)
        if when is None:
            # Never guess a date - see CLAUDE.md, "Never fabricate a date".
            why = (f"its offset is not {config.TZ.zone}'s at that time; set `offsets` once you know "
                   "which is right" if self.read_when(raw_start, trust_offset=True) else "unreadable")
            logger.warning(f"Skipping '{title}' - startDate {raw_start!r}: {why} ({url})")
            return None
        start, all_day = when
        end = None
        if not all_day and node.get("endDate"):
            ended = self.read_when(_as_list(node.get("endDate"))[0])
            if ended and not ended[1] and ended[0] > start:
                end = ended[0]

        online = status.endswith("EventMovedOnline")
        location, lat, lng = self.location(node.get("location"), online)
        description = clean(TICKET_LINK.sub(" ", strip_html(node.get("description")
                                                            if isinstance(node.get("description"), str) else "")))
        if len(description) < 20:
            description = f"{title} at {location['venue_name'] or self.source_name}."

        image = _image_url(node.get("image"))
        for performer in _as_list(node.get("performer")):
            if image:
                break
            if isinstance(performer, dict):
                image = _image_url(performer.get("image"))

        category = self.category or next(
            (CATEGORY_BY_TYPE[t] for t in _type_names(node) if t in CATEGORY_BY_TYPE), None)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            **location,
            latitude=lat,
            longitude=lng,
            category=category,
            cost=self.cost(node.get("offers")),
            source_name=self.source_name,
            source_url=url,
            image_url=image,
        )

    def read_when(self, value, trust_offset: bool = False) -> Optional[Tuple[datetime, bool]]:
        """(local wall clock, is_all_day), or None if unreadable or self-contradictory."""
        match = ISO.fullmatch(value) if isinstance(value, str) else None
        if not match:
            return None
        year, month, day = (int(g) for g in match.group(1, 2, 3))
        try:
            if match.group(4) is None:
                return datetime(year, month, day), True
            wall = datetime(year, month, day, int(match.group(4)), int(match.group(5)))
        except ValueError:
            return None
        offset = match.group(7)
        if offset is None or self.offsets == "wall":
            return wall, False

        stated = timedelta(0)
        if offset != "Z":
            sign = -1 if offset[0] == "-" else 1
            hours, _, minutes = offset[1:].partition(":")
            if not minutes and len(hours) > 2:
                hours, minutes = hours[:-2], hours[-2:]
            stated = sign * timedelta(hours=int(hours), minutes=int(minutes or 0))
        instant = (wall - stated).replace(tzinfo=None)
        if self.offsets == "instant" or trust_offset:
            return on_the_minute(utc.localize(instant)), False

        # The offset is believed only where it is the region's own at that
        # wall clock; then the instant and the written clock agree
        region = config.TZ
        if stated not in {region.localize(wall, is_dst=dst).utcoffset() for dst in (True, False)}:
            return None
        return wall, False

    @staticmethod
    def event_url(node: dict, page_url: str) -> str:
        """The event's own page; else its first ticket offer; else the listing."""
        for candidate in [node.get("url")] + [o.get("url") for o in _as_list(node.get("offers")) if isinstance(o, dict)]:
            candidate = _as_list(candidate)[0] if candidate else None
            if isinstance(candidate, str) and candidate.strip():
                return urljoin(page_url, candidate.strip())
        return page_url

    def location(self, value, online: bool = False) -> Tuple[dict, Optional[float], Optional[float]]:
        """Place fields (and coordinates) from a Place, a VirtualLocation, a
        string, or a list of these. A physical place wins over a virtual one."""
        places = [p for p in _as_list(value) if isinstance(p, (dict, str))]
        physical = next((p for p in places if isinstance(p, str) or "VirtualLocation" not in _type_names(p)), None)
        if online or (places and physical is None):
            return {**venue_fields(self.venue, name="Online"), "state": None}, None, None
        if physical is None:
            return {**venue_fields(self.venue), "state": None}, None, None
        if isinstance(physical, str):
            physical = {"name": physical}

        name = strip_html(physical.get("name") if isinstance(physical.get("name"), str) else "")
        address = physical.get("address")
        street = city = state = zip_code = None
        if isinstance(address, dict):
            street = clean(address.get("streetAddress")) or None
            city = clean(address.get("addressLocality")) or None
            region = clean(address.get("addressRegion"))
            state = region if re.fullmatch(r"[A-Z]{2}", region) else None
            zip_code = clean(str(address.get("postalCode") or "")) or None
        elif isinstance(address, str):
            street, city, state, zip_code = self.split_address(clean(address))

        fields = venue_fields(self.venue, name=(name or "")[:150], street=(street or "")[:200],
                              city=(city or "")[:50], zip_code=zip_code)
        own = bool(name or street or city)
        geo = physical.get("geo") if isinstance(physical.get("geo"), dict) else {}
        try:
            lat = float(geo["latitude"]) if own and geo.get("latitude") not in (None, "") else None
            lng = float(geo["longitude"]) if own and geo.get("longitude") not in (None, "") else None
        except (TypeError, ValueError):
            lat = lng = None
        return {**fields, "state": state if own else None}, lat, lng

    @staticmethod
    def split_address(text: str) -> tuple:
        """"5 John F. Kennedy St, Cambridge, MA 02138" -> (street, city, state, zip)."""
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if parts and parts[-1].lower() in ("usa", "us", "united states"):
            parts.pop()
        tail = STATE_ZIP.fullmatch(parts[-1]) if len(parts) > 1 else None
        if not tail:
            return text or None, None, None, None
        parts.pop()
        city = tail.group("city") or parts.pop()
        return ", ".join(parts) or None, city, tail.group("state"), tail.group("zip")

    @staticmethod
    def cost(offers) -> Optional[str]:
        for offer in _as_list(offers):
            if not isinstance(offer, dict):
                continue
            currency = offer.get("priceCurrency") or "USD"
            symbol = "$" if currency == "USD" else f"{currency} "
            low, high = offer.get("lowPrice"), offer.get("highPrice")
            price = offer.get("price", low)
            try:
                if price is not None and float(price) == 0 and not high:
                    return "Free"
                if low is not None and high is not None and float(high) > float(low):
                    return f"{symbol}{low} - {symbol}{high}"
                if price is not None and str(price).strip():
                    return f"{symbol}{price}"
            except (TypeError, ValueError):
                continue
        return None
