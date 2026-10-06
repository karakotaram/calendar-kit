"""EventON (WordPress plugin) calendars, read through EventON's own AJAX endpoint.

EventON pages ship empty `.eventon_events_list` shells behind loading bars and
fetch their listings afterwards: the page POSTs the calendar's settings (the
`data-sc` of its `.evo_cal_data`) and nonces (`evo_general_params`) to
`/?evo-ajax=eventon_get_events` and receives `{"status": "GOOD", "html": ...,
"SC": ...}`, where `SC` is the settings for what it now shows. This adapter
makes the same requests with plain HTTP, the way the calendar's own month
arrows do, carrying each response's `SC` into the next request.

What Cambridge Calendar learned from two EventON venues:

- **Parsing the page as served reads nothing** (Regent Theatre: the shells
  only), and **clicking "next month" in a browser is fragile** (Central Square
  Theater: a newsletter popup intercepted the click on every run, so two of
  four months were read). The endpoint avoids both. A month calendar is read
  one month per request (`months`); a list calendar returns its whole range in
  one.
- **Each listing states its start twice, and both are read.** `data-time` is a
  start-end pair of Unix seconds - a UTC instant, converted to local wall clock
  explicitly (`datetime.fromtimestamp()` without a zone reads it in the
  machine's zone, which is UTC in CI). The schema `startDate`
  ("2026-11-1T19:00-4:00") has the right wall clock and a wrong offset: EventON
  writes -4:00 year-round, which once put every standard-time event an hour
  early. Only its wall clock is read, and an event whose two renderings
  disagree is skipped.
- **23:59 is EventON's stand-in for "no end time"**, not something the venue
  published; that end is dropped, as is an end equal to the start.
- **The schema description is often the title in quotes** ("'Eleanor'"), too
  short to publish; markup in descriptions is stripped to text.
- **Talks often link to "#"**: a listing's link is used only if it is a real
  URL, then the event's own page from the schema, then the calendar page.

Params: `months` (default 12: requests for a month calendar, starting with its
current month), `mode` ("ajax", the default; or "page" to parse listings
already rendered into the page, for a site that renders them server-side),
`category` (one EventCategory for every event, e.g. theater for a playhouse).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from src.adapters._common import CANCELLED, clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "eventon"
KIND = "requests"
CLASS = "EventONAdapter"
SIGNATURES = [
    "evo_general_params",       # EventON's nonces and endpoint, on every calendar page
    "ajde_evcal_calendar",      # the calendar container
    "evo_cal_data",             # the calendar's settings
    "eventon",                  # plugin asset paths
]
PARAMS = {
    "months": "months to request from a month calendar, starting with its current one (default 12; "
              "a list calendar returns its whole range in one request)",
    "mode": "\"ajax\" (default): request listings from EventON's endpoint as the page does; "
            "\"page\": parse listings already rendered in the page's HTML",
    "category": "an EventCategory value for every event (e.g. theater); default: title keywords, else left to enrichment",
}

ENDPOINT = "eventon_get_events"
SCHEMA_WALL_CLOCK = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:T(\d{1,2}):(\d{2}))?")
ALL_DAY = re.compile(r"\ball[\s-]*day\b", re.IGNORECASE)


class EventONAdapter(BaseScraper):
    """One EventON calendar."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 months: int = 12, mode: str = "ajax", category: Optional[str] = None):
        super().__init__(source_name=source_name, source_url=url)
        if mode not in ("ajax", "page"):
            raise ValueError(f"{source_name}: eventon mode must be 'ajax' or 'page', not {mode!r}")
        self.venue = dict(venue or {})
        self.months = max(1, int(months))
        self.mode = mode
        self.category = EventCategory(category) if category else None
        self.session = requests.Session()
        self.session.headers.update(self.get_browser_headers())

    # --- fetching (the two seams tests replace) -----------------------------

    def fetch_page(self, url: str) -> str:
        return self.fetch_html(url)

    def fetch_month(self, endpoint: str, params: dict, sc: dict, direction: str) -> dict:
        """One request to EventON's endpoint, as the calendar's own arrows send it."""
        data = {"direction": direction, "ajaxtype": "switchmonth",
                "nonce": params.get("n", ""), "nonceX": params.get("nonce", "")}
        if params.get("ajax_method") == "ajax":
            data["action"] = ENDPOINT
        data.update({f"shortcode[{k}]": v for k, v in sc.items()})
        response = self.session.post(endpoint, data=data, timeout=30,
                                     headers={"X-WP-Nonce": params.get("nonce", "")})
        response.raise_for_status()
        return response.json()

    # --- the run -----------------------------------------------------------------

    def scrape_events(self) -> List[EventCreate]:
        page = self.fetch_page(self.source_url)
        if self.mode == "page":
            return self._unique(self.parse_listing(page))

        params, sc = self.calendar_state(page)
        endpoint = self.endpoint_url(params, self.source_url)
        events: List[EventCreate] = []
        direction = "none"                  # the calendar's current month first, then forward
        for _ in range(self.months):
            payload = self.fetch_month(endpoint, params, sc, direction)
            if not isinstance(payload, dict) or payload.get("status") != "GOOD" or "SC" not in payload:
                raise ValueError(f"{self.source_name}: EventON returned {str(payload)[:200]}")
            sc = payload["SC"]
            events.extend(self.parse_listing(payload.get("html", "")))
            if not str(sc.get("fixed_month") or "").strip():
                break                       # a list calendar: one response is its whole range
            direction = "next"
        return self._unique(events)

    @staticmethod
    def _unique(events: List[EventCreate]) -> List[EventCreate]:
        seen, out = set(), []
        for event in events:
            key = (event.title, event.start_datetime)
            if key not in seen:
                seen.add(key)
                out.append(event)
        return out

    @staticmethod
    def calendar_state(page: str) -> Tuple[dict, dict]:
        """EventON's nonces and endpoint, and the first calendar's settings."""
        params = re.search(r"evo_general_params\s*=\s*(\{.*?\});", page or "")
        sc = None
        for node in BeautifulSoup(page or "", "html.parser").select(".evo_cal_data[data-sc]"):
            try:
                candidate = json.loads(node["data-sc"])
            except ValueError:
                continue
            if isinstance(candidate, dict) and candidate:
                sc = candidate
                break
        if not (params and sc):
            raise ValueError("the page carries no EventON calendar settings "
                             "(evo_general_params and a .evo_cal_data data-sc); "
                             "if it renders its listings server-side, use mode: page")
        return json.loads(params.group(1)), sc

    @staticmethod
    def endpoint_url(params: dict, page_url: str) -> str:
        """Where the page itself sends its requests."""
        method = params.get("ajax_method")
        if method == "ajax" and params.get("ajaxurl"):
            return urljoin(page_url, params["ajaxurl"])
        template = params.get("rest_url") if method == "rest" else params.get("evo_ajax_url")
        if template and "%%endpoint%%" in template:
            return urljoin(page_url, template.replace("%%endpoint%%", ENDPOINT))
        parts = urlsplit(page_url)
        return f"{parts.scheme}://{parts.netloc}/?evo-ajax={ENDPOINT}"

    # --- listings (pure) -------------------------------------------------------

    def parse_listing(self, html: str) -> List[EventCreate]:
        events = []
        for item in BeautifulSoup(html or "", "html.parser").select(".eventon_list_event"):
            if "no_events" in (item.get("class") or []):
                continue
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse a listing: {e}")
                continue
            if event:
                events.append(event)
        return events

    @staticmethod
    def schema(item) -> dict:
        script = item.find("script", type="application/ld+json")
        if not script or not script.string:
            return {}
        try:
            data = json.loads(script.string, strict=False)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def parse_item(self, item) -> Optional[EventCreate]:
        title_el = item.find(class_="evcal_event_title")
        title = clean(title_el.get_text()) if title_el else ""
        if len(title) < 3:
            return None

        schema = self.schema(item)
        classes = item.get("class") or []
        if ("cancelled" in classes or str(schema.get("eventStatus", "")).endswith("EventCancelled")
                or CANCELLED.search(title)):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start, all_day = self.start(item, schema)
        if start is None:
            logger.warning(f"Skipping '{title}' - no start, or its two renderings disagree ({self.source_url})")
            return None
        end = None if all_day else self.epoch(item, 1)
        if end is not None and (end.time() == time(23, 59) or end <= start):
            end = None      # EventON's stand-in for "no end time given"

        location = self.location(item, schema)
        where = location.get("venue_name") or self.venue.get("name") or self.source_name
        description = self.description(title, schema.get("description"), where)

        image = schema.get("image") or self._attr(item, "[itemprop=image]", "content")
        if not image:
            image = self._attr(item, "[data-img]", "data-img")

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            source_url=self.link(item, schema),
            source_name=self.source_name,
            category=self.category or self.categorize(title),
            image_url=image if isinstance(image, str) and image.startswith("http") else None,
            **location,
        )

    @staticmethod
    def _attr(item, selector: str, attribute: str) -> Optional[str]:
        el = item.select_one(selector)
        return el.get(attribute) if el else None

    def start(self, item, schema: dict) -> Tuple[Optional[datetime], bool]:
        """(start, all_day) from data-time and the schema wall clock, if they agree."""
        instant = self.epoch(item, 0)
        listed, listed_has_time = self.schema_wall_clock(
            schema.get("startDate") or self._attr(item, "[itemprop=startDate]", "content"))
        shown = " ".join(item.select_one(".evcal_time").get_text().split()) if item.select_one(".evcal_time") else ""

        if ALL_DAY.search(shown):
            days = {d.date() for d in (instant, listed) if d is not None}
            if len(days) != 1:
                return None, False
            return datetime.combine(days.pop(), time(0, 0)), True

        if instant and listed and listed_has_time and instant != listed:
            # One of the two renderings changed meaning; do not pick a side.
            logger.warning(f"data-time says {instant}, schema says {listed}")
            return None, False
        if instant:
            return instant, False
        if listed and listed_has_time:
            return listed, False
        return None, False

    @staticmethod
    def epoch(item, index: int) -> Optional[datetime]:
        """data-time="1791590400-1791604740" is a start-end pair of Unix seconds (UTC)."""
        parts = (item.get("data-time") or "").split("-")
        raw = parts[index] if len(parts) > index else ""
        if not raw.isdigit():
            return None
        try:
            return on_the_minute(datetime.fromtimestamp(int(raw), tz=timezone.utc))
        except (ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def schema_wall_clock(value) -> Tuple[Optional[datetime], bool]:
        """The wall clock in a schema startDate like "2027-2-4T19:30-4:00", and whether it has a time.

        Only the wall clock is read: EventON writes -4:00 in standard time too.
        The month and day are unpadded, which strptime would refuse.
        """
        m = SCHEMA_WALL_CLOCK.match(str(value or "").strip())
        if not m:
            return None, False
        year, month, day, hour, minute = m.groups()
        try:
            if hour is None:
                return datetime.combine(date(int(year), int(month), int(day)), time(0, 0)), False
            return datetime(int(year), int(month), int(day), int(hour), int(minute)), True
        except ValueError:
            return None, False

    def link(self, item, schema: dict) -> str:
        anchor = item.find("a", class_="evcal_list_a") or item.find("a", href=True)
        href = (anchor.get("href") or "").strip() if anchor else ""
        if href.startswith("http"):
            return href                                     # often the performance's ticket page
        own = str(schema.get("url") or self._attr(item, "[itemprop=url]", "href") or "")
        if own.startswith("http"):
            return own                                      # the event's own page on the site
        return self.source_url

    @staticmethod
    def description(title: str, schema_description: Optional[str], where: str) -> str:
        """The schema's own text if it says more than the title, else a plain sentence."""
        own = strip_html(schema_description)
        if len(own) >= 20 and _key(own) != _key(title):
            return own
        return f"{title} at {where}."

    def location(self, item, schema: dict) -> dict:
        """The event's own place (schema location, or the listing's location name), else the venue."""
        place = schema.get("location")
        if isinstance(place, list):
            place = place[0] if place else None
        name = street = city = zip_code = state = None
        if isinstance(place, dict):
            name = clean(place.get("name")) or None
            address = place.get("address")
            if isinstance(address, dict):
                street = clean(address.get("streetAddress")) or None
                city = clean(address.get("addressLocality")) or None
                zip_code = clean(address.get("postalCode")) or None
                state = clean(address.get("addressRegion")) or None
            elif isinstance(address, str):
                street = clean(address) or None
        if not name:
            data = self._attr(item, ".evoet_data[data-d]", "data-d")
            try:
                name = (clean(json.loads(data).get("loc.n")) or None) if data else None
            except (ValueError, AttributeError):
                name = None
        fields = venue_fields(self.venue, name=name, street=street, city=city, zip_code=zip_code)
        fields["state"] = state
        return fields

    @staticmethod
    def categorize(title: str) -> Optional[EventCategory]:
        """Keywords only; anything unclear is left for the enrichment step."""
        text = title.lower()
        if any(w in text for w in ("comedy", "comedian", "stand-up", "standup", "improv")):
            return EventCategory.THEATER
        if any(w in text for w in ("film", "movie", "screening", "cinema")):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("concert", "band", "tribute", "orchestra", "jazz", "live music")):
            return EventCategory.MUSIC
        if any(w in text for w in ("talk", "lecture", "conversation", "panel")):
            return EventCategory.LECTURES
        return None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
