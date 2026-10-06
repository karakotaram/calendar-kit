"""Server-rendered Drupal Views listings whose rows carry `<time datetime="...Z">`.

Many city and institutional sites run Drupal and list events as a View: one
`.views-row` per event with a title link, a `<time>` for the start (and often a
second for the end), a teaser, and a pager (`?page=N`). Drupal's `<time>`
element states each moment twice: as a UTC instant in its `datetime` attribute
and as local wall-clock text ("Mon, October 5, 2026 - 10:00am").

What Cambridge Calendar learned reading the City of Somerville this way:

- **A start is believed only when the attribute and the visible text agree.**
  On 2026-10-05 all ~400 did; a disagreement means one of them changed meaning,
  and dropping the event beats guessing which to trust. An evening start is on
  the local date, not the UTC one.
- **A date-only `<time>` is an all-day event** at 00:00, never a guessed time.
- **Paging is `?page=N` and stops when a page adds nothing new** (or has no
  next link): a pager that repeats its last page must not loop.
- **Not every row is an event a reader can attend.** Office-closure notices
  ("Holiday: Thanksgiving" at 12:00am), closed executive sessions, and CMS
  placeholders are skipped (`exclude`), as are cancelled events.
- **A row without a detail page** links to that day's listing, not to nothing.
- **The listing has no venue**, so each event's own page is read for its
  address (`detail_pages`). That pass never touches a date: the listing is the
  only source of truth for when. The detail body's inline `<style>` is not
  prose, and accessibility and interpreter notices laid out as an icon in a
  table are boilerplate; both are stripped before the body is used as the
  description.

Selectors default to a stock Drupal View and can be overridden per source;
`exclude` adds title patterns to skip, and `category` sets one EventCategory
for every event instead of the keyword rules.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from src.adapters._common import CANCELLED, clean, on_the_minute, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "drupal_listing"
KIND = "requests"
CLASS = "DrupalListingAdapter"
SIGNATURES = [
    "drupal-settings-json",     # Drupal 8+ pages
    "views-row",                # a View's rows
    "<time datetime=",          # Drupal's datetime formatter
    "pager__item",              # the View's pager
]

DEFAULT_EXCLUDE = (
    r"^holiday:",               # office closures, listed at 12:00am
    r"executive session",       # closed to the public under open-meeting law
    r"^example .*event$",       # CMS placeholders left live
)
DEFAULTS = {
    "row_selector": ".views-row",
    "title_selector": ".views-field-title",
    "time_selector": "time[datetime]",
    "body_selector": ".views-field-body",
    "image_selector": "img[src]",
    # Drupal 8+ and Drupal 7 pagers. Not a[rel=next]: calendar day pagers use it too.
    "next_selector": ".pager__item--next a, .pager-next a",
    "address_selector": ".field--name-field-address .address, .address",
    "detail_body_selector": "main .field--name-body, .field--name-body",
}
PARAMS = {
    "max_pages": "listing pages to read at most (default 40); paging stops earlier when a page adds nothing",
    "exclude": "extra title regexes for rows that are not attendable events (added to the defaults: "
               "holiday closures, executive sessions, CMS placeholders)",
    "detail_pages": "read each event's own page for its venue, address and a fuller description (default true)",
    "category": "an EventCategory value for every event (e.g. theater); default: keywords for civic calendars (meetings are community, classes lectures)",
    **{name: f"CSS selector (default {default!r})" for name, default in DEFAULTS.items()},
}

DETAIL_WORKERS = 4

MONTH_DATE = re.compile(r"([A-Z][a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})")
NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?(?![a-z])", re.IGNORECASE)
DATE_ONLY_ATTR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ONLINE_TITLE = re.compile(r"\b(virtual|zoom)\b", re.IGNORECASE)


class DrupalListingAdapter(BaseScraper):
    """One Drupal Views event listing."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 max_pages: int = 40, exclude: Optional[list] = None, detail_pages: bool = True,
                 category: Optional[str] = None, **selectors):
        unknown = set(selectors) - set(DEFAULTS)
        if unknown:
            raise TypeError(f"{source_name}: unknown drupal_listing params {sorted(unknown)}")
        super().__init__(source_name=source_name, source_url=url)
        self.venue = dict(venue or {})
        self.max_pages = max(1, int(max_pages))
        self.exclude = re.compile("|".join(f"(?:{p})" for p in (*DEFAULT_EXCLUDE, *(exclude or []))),
                                  re.IGNORECASE)
        self.detail_pages = bool(detail_pages)
        self.category = EventCategory(category) if category else None
        self.sel = {**DEFAULTS, **selectors}
        parts = urlsplit(url)
        self.site = f"{parts.scheme}://{parts.netloc}"

    def page_url(self, page: int) -> str:
        parts = urlsplit(self.source_url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
        if page:
            query.append(("page", str(page)))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()  # (url, start): a recurring event shares one URL across dates
        for page in range(self.max_pages):
            url = self.page_url(page)
            try:
                soup = self.parse_html(self.fetch_html(url))
            except Exception as e:
                if not page:
                    raise          # the first page failing is the source failing, not "no events"
                logger.error(f"{self.source_name}: stopped at {url}: {e}")
                break

            new = 0
            for event in self.parse_listing(soup):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
                    new += 1
            # A page that adds nothing means the pager is repeating itself
            if not new or not soup.select_one(self.sel["next_selector"]):
                break

        if self.detail_pages:
            self.enrich_from_detail_pages(events)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # --- the listing (pure) ------------------------------------------------------

    def parse_listing(self, soup) -> List[EventCreate]:
        """Every attendable, dateable event on one listing page."""
        events = []
        for row in soup.select(self.sel["row_selector"]):
            title_el = row.select_one(self.sel["title_selector"])
            times = row.select(self.sel["time_selector"])
            if not title_el or not times:
                continue
            title = clean(title_el.get_text(" "))
            if len(title) < 3 or self.exclude.search(title):
                continue
            if CANCELLED.search(title):
                logger.info(f"{self.source_name}: skipping cancelled '{title}'")
                continue

            start, all_day = self.read_time(times[0])
            if start is None:
                # Never guess: an event with an unknown date pollutes another day
                logger.warning(f"Skipping '{title}' - start unreadable or self-contradictory")
                continue
            end = None
            if len(times) > 1 and not all_day:
                end, _ = self.read_time(times[1])

            link = title_el.find("a", href=True) if title_el.name != "a" else title_el
            if link is not None and link.get("href"):
                event_url = urljoin(self.source_url, link["href"])
            else:
                # A row with no detail page links to that day's listing
                event_url = f"{self.page_url(0)}{'&' if '?' in self.page_url(0) else '?'}event_date={start:%Y-%m-%d}"

            body = row.select_one(self.sel["body_selector"])
            description = clean(body.get_text(" ")) if body else ""
            image = row.select_one(self.sel["image_selector"])
            image_url = self._normalize_image_url(image["src"], self.site) if image else None

            online = bool(ONLINE_TITLE.search(title))
            location = venue_fields(self.venue, name="Online" if online else None)
            if online:
                location["city"] = None

            where = location.get("venue_name") or self.venue.get("name") or self.source_name
            events.append(EventCreate(
                title=title[:200],
                description=(description or f"{title} - {where}.")[:2000],
                start_datetime=start,
                end_datetime=end if end and end > start else None,
                all_day=all_day,
                source_url=event_url,
                source_name=self.source_name,
                image_url=image_url,
                category=self.category or self.categorize(title, description),
                **location,
            ))
        return events

    @staticmethod
    def read_time(time_el, quiet: bool = False) -> Tuple[Optional[datetime], bool]:
        """(local start, all_day) from a `<time>`, or (None, False) unless both renderings agree.

        Local means the calendar's zone (calendar.config.yaml): a venue in
        another zone disagrees with itself here, which is the right outcome.
        """
        warn = (lambda *a: None) if quiet else logger.warning
        raw = (time_el.get("datetime") or "").strip()
        text = " ".join(time_el.get_text(" ").split())
        shown_date = _visible_date(text)
        if not raw or shown_date is None:
            return None, False
        clock = CLOCK.search(text)

        if DATE_ONLY_ATTR.match(raw):
            # A date with no time: all day, if the text says the same date and no time
            try:
                day = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                return None, False
            if clock or day.date() != shown_date:
                return None, False
            return day, True

        try:
            instant = on_the_minute(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None, False
        if instant.date() != shown_date:
            warn(f"<time> disagrees with itself: {raw!r} vs {text!r}")
            return None, False
        if clock is None:
            # Text gives only the date: an all-day event if the instant is local midnight
            if (instant.hour, instant.minute) == (0, 0):
                return instant, True
            return None, False
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "p" else 0)
        if (instant.hour, instant.minute) != (hour, int(clock.group(2) or 0)):
            warn(f"<time> disagrees with itself: {raw!r} vs {text!r}")
            return None, False
        return instant, False

    # --- detail pages (venue and description only; never a date) ----------------

    def enrich_from_detail_pages(self, events: List[EventCreate]) -> None:
        """Best-effort: every event already has its date, title and description
        from the listing, so a failed fetch just means a card with no venue."""
        by_url = {}
        for event in events:
            if event.source_url.startswith(self.site) and "event_date=" not in event.source_url:
                by_url.setdefault(event.source_url, []).append(event)
        if not by_url:
            return
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            for url, details in zip(by_url, pool.map(self.fetch_event_details, by_url)):
                location, description = details
                for event in by_url[url]:
                    if location:
                        for field, value in location.items():
                            setattr(event, field, value)
                    if description and len(description) > len(event.description):
                        event.description = description[:2000]

    def fetch_event_details(self, url: str) -> Tuple[Optional[dict], Optional[str]]:
        try:
            html = self.fetch_html(url, retries=2)
        except Exception as e:
            logger.warning(f"Could not fetch detail page {url}: {e}")
            return None, None
        return self.parse_detail(html)

    def parse_detail(self, html: str) -> Tuple[Optional[dict], Optional[str]]:
        """(location fields or None, description or None) from an event's own page."""
        soup = self.parse_html(html)
        location = None
        address = soup.select_one(self.sel["address_selector"])
        if address:
            def part(cls):
                el = address.select_one(f".{cls}")
                return (clean(el.get_text()) or None) if el else None

            street = ", ".join(p for p in (part("address-line1"), part("address-line2")) if p) or None
            name = part("organization") or street
            if name or street:
                location = venue_fields(None, name=name, street=street, city=part("locality"),
                                        zip_code=part("postal-code"))
                location["state"] = part("administrative-area")

        description = None
        body = soup.select_one(self.sel["detail_body_selector"])
        if body:
            for tag in body.find_all(["style", "script"]):
                tag.decompose()
            for table in body.find_all("table"):
                if table.find("img"):
                    table.decompose()       # icon-and-text accessibility/interpreter notices
            description = clean(body.get_text(" ")) or None
        return location, description

    @staticmethod
    def categorize(title: str, description: str) -> EventCategory:
        """Keywords, in order: "Zumba Gold (Council on Aging)" is a fitness class, not a meeting."""
        text = f"{title} {description}".lower()
        if any(w in text for w in ("exercise", "yoga", "zumba", "walking club", "healthy steps",
                                   "tai chi", "fitness")):
            return EventCategory.SPORTS
        if any(w in text for w in ("meeting", "committee", "commission", "board", "hearing",
                                   "subcommittee", "office hours")):
            return EventCategory.COMMUNITY
        if any(w in text for w in ("concert", "music", "band")):
            return EventCategory.MUSIC
        if any(w in text for w in ("theater", "theatre", "shakespeare", "performance")):
            return EventCategory.THEATER
        if any(w in text for w in ("farmers market", "luncheon", "tea party", "dinner", "brunch")):
            return EventCategory.FOOD_DRINK
        if any(w in text for w in ("workshop", "training", "class", "lecture", "talk", "seminar")):
            return EventCategory.LECTURES
        if any(w in text for w in ("festival", "celebration", "heritage", "art", "movie", "bingo",
                                   "knitting", "crochet", "mahjong", "halloween")):
            return EventCategory.ARTS_CULTURE
        return EventCategory.COMMUNITY


def _visible_date(text: str):
    """The date printed in a `<time>`'s text: "October 5, 2026" or "10/5/2026"."""
    m = MONTH_DATE.search(text)
    if m:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
            except ValueError:
                continue
    m = NUMERIC_DATE.search(text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            return None
    return None
