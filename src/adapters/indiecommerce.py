"""IndieCommerce bookstores (the ABA's Drupal platform): the month calendar's FullCalendar JSON.

IndieCommerce stores publish a month calendar at `/events/calendar/YYYY/MM`
whose `drupalSettings` embed every event as FullCalendar JSON: an ISO start and
end with the store's UTC offset, an all-day flag, the event URL, tags, and a
`<template>` holding the teaser with the printed date, time and place.

What Cambridge Calendar learned reading Porter Square Books and Harvard Book
Store this way:

- **The ISO start is believed only if the teaser prints the same moment.** The
  date must match the printed "Thu, 10/1/2026" and, unless the event is all
  day, the time must match the printed "7:00pm". A disagreement means one of
  them changed meaning; the event is skipped rather than resolved by guessing.
- **Each event's city comes from its own address.** The old scraper stamped
  every event "Cambridge", including those at the store's Boston branch and
  at host venues across town. Teasers carry either a structured address block
  or plain lines ("Boston Edition / GrubStreet / 50 Liberty Drive / Boston, MA
  02210"); both are read. An event with no place block but tagged with one of
  the store's own location tags (`homes`) is at that home.
- **Months chain from the page's own `calander_view` (sic)**, the month the
  page says it is showing, so no clock is read.
- **Never click through a bot check.** Both stores sit behind Cloudflare.
  `wait_past_challenge()` waits for an interstitial to clear on its own and
  fails the source if it does not, rather than parsing "Just a moment..." as an
  empty month.

Params
------
`months`: months to read, starting with the one the calendar opens on
(default 1). On 2026-10-06 both Cambridge stores served the first page of a
session and then put later pages behind a check that did not clear, so one is
the dependable default; raise it if a store serves later months reliably.

`visible_browser`: **opt-in, chosen by a human, default false.** Some stores'
Cloudflare settings refuse a browser that announces itself as HeadlessChrome
but serve an ordinary browser window. Cambridge Calendar opened a visible
window for two bookstores at its owner's explicit request. It is not a way
around a block that a venue intends: if a store refuses headless access,
consider asking it, or mark the source blocked. A visible window needs a
display, so such a source must also be registered `runs_in_ci: false` and run
locally (scrape_local.py). The browser keeps its own, honest user-agent and no
automation-hiding flags either way.

`homes`: the store's own location tags, mapped to the place they mean, e.g.
`{"Harvard Book Store": {}}` (an empty mapping means the registry venue) or
`{"Cambridge": {name: ..., street: ..., city: ..., zip: ...}}`. Used only for
events with no place block.

`place_names`: printed place lines renamed for publication, e.g.
`{"Cambridge Edition": "Porter Square Books Cambridge Edition"}`, for stores
that label their own branches with a word that means nothing out of context.

`category`: one EventCategory for every event (default: keywords, else
lectures - a bookstore's calendar is mostly author talks).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from src.adapters._common import CANCELLED, clean, on_the_minute, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_playwright_scraper import BasePlaywrightScraper

logger = logging.getLogger(__name__)

ADAPTER = "indiecommerce"
KIND = "playwright"
CLASS = "IndieCommerceAdapter"
SIGNATURES = [
    "indiecommerce",            # drupalSettings keys and module paths
    "fullCalendarView",         # the calendar's settings block
    "calander_view",            # IndieCommerce's own (misspelt) month state
    "event-teaser__details",    # the teaser template
    "/events/calendar",         # the calendar's path, linked from every page
]
PARAMS = {
    "months": "months to read, starting with the calendar's current one (default 1)",
    "visible_browser": "opt-in, human-chosen: open a visible browser window for a store whose "
                       "Cloudflare refuses headless Chromium (default false; needs runs_in_ci: false)",
    "homes": "the store's own location tags -> the place they mean ({} = the registry venue); "
             "used for events with no place block",
    "place_names": "printed place lines -> published venue names, e.g. a branch label",
    "category": "an EventCategory value for every event (e.g. theater); default: music and story-time keywords, else lectures",
}

# A pause between month pages, when there is more than one
MONTH_PAUSE_MS = 10_000

DATE_TEXT = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
TIME_TEXT = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m", re.IGNORECASE)
CITY_LINE = re.compile(r"^(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})")


class IndieCommerceAdapter(BasePlaywrightScraper):
    """One IndieCommerce store's event calendar."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 months: int = 1, visible_browser: bool = False,
                 homes: Optional[dict] = None, place_names: Optional[dict] = None,
                 category: Optional[str] = None):
        parts = urlsplit(url)
        self.base = f"{parts.scheme}://{parts.netloc}"
        # Always the undated calendar, which opens on the current month: a
        # registry URL naming one month would pin every run to that month.
        # headless unless a human chose otherwise for this source (module docstring)
        super().__init__(source_name=source_name, source_url=f"{self.base}/events/calendar",
                         headless=not visible_browser)
        self.venue = dict(venue or {})
        self.months = max(1, int(months))
        self.homes = dict(homes or {})
        self.place_names = dict(place_names or {})
        self.category = EventCategory(category) if category else None

    # --- the run (needs a browser) ---------------------------------------------

    def scrape_events(self) -> List[EventCreate]:
        events, seen = [], set()
        url = self.source_url
        for month in range(self.months):
            if month:
                self.page.wait_for_timeout(MONTH_PAUSE_MS)
            self.goto(url)
            self.wait_past_challenge(timeout_s=45)
            html = self.get_html()
            for event in self.parse_calendar(html):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
            following = self.next_month_path(html)
            if following is None:
                break
            url = f"{self.base}{following}"
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # --- one month page (pure) ---------------------------------------------------

    @staticmethod
    def settings(html_or_soup) -> dict:
        """The page's drupalSettings."""
        soup = (BeautifulSoup(html_or_soup or "", "html.parser")
                if isinstance(html_or_soup, (str, bytes, type(None))) else html_or_soup)
        node = soup.find("script", attrs={"data-drupal-selector": "drupal-settings-json"})
        try:
            return json.loads(node.string) if node and node.string else {}
        except ValueError:
            return {}

    @classmethod
    def next_month_path(cls, html: str) -> Optional[str]:
        """The month after the one this page shows, from the page's own state."""
        view = cls.settings(html).get("indiecommerce_events", {}).get("calander_view", "")
        match = re.fullmatch(r"(\d{4})-(\d{2})", view or "")
        if not match:
            return None
        year, month = int(match.group(1)), int(match.group(2))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return f"/events/calendar/{year}/{month:02d}"

    @classmethod
    def calendar_items(cls, html: str) -> Optional[list]:
        """The FullCalendar event list, or None if the page carries no calendar."""
        try:
            options = cls.settings(html)["fullCalendarView"][0]["calendar_options"]
            if isinstance(options, str):
                options = json.loads(options)
            return list(options.get("events", []))
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return None

    def parse_calendar(self, html: str) -> List[EventCreate]:
        """Every event the month's FullCalendar JSON carries."""
        items = self.calendar_items(html)
        if items is None:
            logger.warning(f"{self.source_name}: no calendar data on the page")
            return []
        events = []
        for item in items:
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: could not parse {item.get('url')!r}: {e}")
                continue
            if event:
                events.append(event)
        return events

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title_html = BeautifulSoup(item.get("title") or "", "html.parser")
        teaser = self._teaser(title_html)
        heading = title_html.find("span")
        title = clean(heading.get_text() if heading else title_html.get_text())
        if len(title) < 3:
            return None
        if CANCELLED.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start, all_day = self._start(item, teaser, title)
        if start is None:
            return None
        end = None
        if item.get("end") and not all_day:
            try:
                end = on_the_minute(datetime.fromisoformat(item["end"]))
            except ValueError:
                end = None

        tags = [clean(t.get_text())
                for t in BeautifulSoup(item.get("des") or "", "html.parser").find_all("span")]
        place = self._place(teaser, tags)

        image_url = None
        image = teaser.find("img", src=True) if teaser else None
        if image:
            image_url = self._normalize_image_url(image["src"], self.base)

        path = item.get("url") or ""
        where = place.get("venue_name") or self.venue.get("name") or self.source_name
        description = f"{title}." if where.lower() in title.lower() else f"{title} at {where}."
        if tags:
            description += f" {', '.join(tags)}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            all_day=all_day,
            source_url=f"{self.base}{path}" if path.startswith("/") else (path or self.source_url),
            source_name=self.source_name,
            cost="Free" if "Free" in tags else ("Ticketed" if "Ticketed" in tags else None),
            category=self.category or self._categorize(title, tags),
            image_url=image_url,
            **place,
        )

    @staticmethod
    def _teaser(title_html) -> Optional[BeautifulSoup]:
        """Re-parse the <template>: html.parser does not descend into one."""
        template = title_html.find("template")
        if template is None:
            return None
        return BeautifulSoup(template.decode_contents(), "html.parser")

    @staticmethod
    def _details(teaser) -> dict:
        found = {}
        block = teaser.find(class_="event-teaser__details") if teaser else None
        if block:
            for label, value in zip(block.find_all("dt"), block.find_all("dd")):
                found[clean(label.get_text()).rstrip(":").lower()] = clean(value.get_text(" "))
        return found

    def _start(self, item: dict, teaser, title: str) -> Tuple[Optional[datetime], bool]:
        """The ISO start, if the printed date (and time) say the same."""
        raw = item.get("start") or ""
        try:
            start = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning(f"Skipping '{title}' - unreadable start {raw!r}")
            return None, False
        all_day = bool(item.get("allDay")) or len(raw) == 10

        details = self._details(teaser)
        printed_date = DATE_TEXT.search(details.get("date", ""))
        if printed_date is None:
            logger.warning(f"Skipping '{title}' - no printed date to check {raw!r} against")
            return None, False
        month, day, year = (int(g) for g in printed_date.groups())
        # The ISO value's own wall clock (in the store's offset) is what the teaser prints
        if (start.year, start.month, start.day) != (year, month, day):
            logger.warning(f"Skipping '{title}' - start {raw!r} disagrees with printed date {details['date']!r}")
            return None, False

        if all_day:
            # A date with no time: midnight of that date, flagged all day
            return datetime(start.year, start.month, start.day), True
        printed_time = TIME_TEXT.search(details.get("time", ""))
        if printed_time is None:
            logger.warning(f"Skipping '{title}' - no printed time to check {raw!r} against")
            return None, False
        hour = int(printed_time.group(1)) % 12 + (12 if printed_time.group(3).lower() == "p" else 0)
        if (start.hour, start.minute) != (hour, int(printed_time.group(2))):
            logger.warning(f"Skipping '{title}' - start {raw!r} disagrees with printed time {details['time']!r}")
            return None, False
        return on_the_minute(start), False

    def _home(self, tags: List[str]) -> Optional[dict]:
        tag = next((t for t in tags if t in self.homes), None)
        if tag is None:
            return None
        home = self.homes[tag] or {}
        merged = {k: home.get(k) or self.venue.get(k) for k in ("name", "street", "city", "zip", "state")}
        fields = venue_fields(None, name=merged["name"] or self.source_name, street=merged["street"],
                              city=merged["city"], zip_code=merged["zip"])
        fields["state"] = merged["state"]
        return fields

    def _place(self, teaser, tags: List[str]) -> dict:
        """EventCreate location fields from either address shape, a home tag, or the venue."""
        node = teaser.find(class_="event-teaser__details-location") if teaser else None
        if node is None:
            home = self._home(tags)
            if home:
                return home
            fields = venue_fields(self.venue)
            fields["venue_name"] = fields["venue_name"] or self.source_name
            fields["state"] = self.venue.get("state")
            return fields

        structured = node.find(class_="address")
        if structured is not None:
            def part(cls):
                el = structured.find(class_=cls)
                return (clean(el.get_text()) or None) if el else None
            org, line1, line2 = part("organization"), part("address-line1"), part("address-line2")
            if org:
                name, street = org, ", ".join(p for p in (line1, line2) if p) or None
            elif line2:
                name, street = line1, line2            # IndieCommerce puts the venue in line 1
            elif line1 and line1[:1].isdigit():
                name, street = None, line1
            else:
                name, street = line1, None
            fields = venue_fields(self.venue, name=self._rename(name), street=street,
                                  city=part("locality"), zip_code=(part("postal-code") or "")[:5] or None)
            fields["state"] = part("administrative-area")
            return fields

        address = node.find("address")
        lines = [clean(s) for s in (address or node).stripped_strings]
        lines = [line for line in lines if line]
        place = CITY_LINE.match(lines[-1]) if lines else None
        if place is None or len(lines) < 2:
            text = clean(node.get_text(" "))
            fields = venue_fields(self.venue, name=self._rename(text) or None)
            fields["state"] = None
            return fields
        # Every line before the street names a place: a branch, then a host venue
        names = [self._rename(line) for line in lines[:-2]]
        fields = venue_fields(self.venue, name=" – ".join(names) or None, street=lines[-2],
                              city=place["city"].strip(), zip_code=place["zip"])
        fields["state"] = place["state"]
        return fields

    def _rename(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return name
        return self.place_names.get(name, name)

    @staticmethod
    def _categorize(title: str, tags: List[str]) -> EventCategory:
        text = f"{title} {' '.join(tags)}".lower()
        if any(w in text for w in ("music", "concert")):
            return EventCategory.MUSIC
        if any(w in text for w in ("story hour", "storytime", "story time", "kids", "children")):
            return EventCategory.ARTS_CULTURE
        # A bookstore's calendar is overwhelmingly author talks and readings
        return EventCategory.LECTURES
