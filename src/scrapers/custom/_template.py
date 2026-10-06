"""TEMPLATE for a custom scraper. Replace this docstring with the venue's story.

Write a custom scraper only when no adapter fits (`cal detect <url>`,
`cal adapters`). To start one:

    cp src/scrapers/custom/_template.py src/scrapers/custom/<slug_>.py
    cal add --name "<Venue Name>" --adapter custom --url <listing url> \\
        --venue-name "<Venue Name>" --street "<street>" --city "<city>" --zip "<zip>"

`cal add` points the registry file at `src.scrapers.custom.<slug_>` and class
`Scraper`, so keep the class name, and set SOURCE_NAME to the registry `name:`
exactly. The leading underscore keeps this file out of the registry and out
of the "every custom module is registered" check in tests/test_docs.py.

Where to read the events from, best first:

  1. The JSON the page itself fetches. Search the page source and its scripts
     for "/wp-json/", "/api/", "?format=json" (Squarespace), "__NEXT_DATA__",
     "admin-ajax", "evo-ajax", or a fetch() URL. The venue's own page trusts
     it, it carries every event, and its times are machine-readable.
  2. Structured data: an iCal feed (.ics, webcal:), JSON-LD
     <script type="application/ld+json"> with "@type": "Event", microdata.
     Check it is complete: a list page's JSON-LD often holds only its first
     ten events (Cambridge: MIT's held 5% of the calendar).
  3. The visible HTML, which is what this template parses.

What it demonstrates (CLAUDE.md, docs/rules-and-edge-cases.md):

  - Pure. parse_listing() turns one page's text into events. All I/O is in
    scrape_events(), through BaseScraper's honest user-agent, so a test can
    replay a saved page (tests/sources/_template.py).
  - No date, no event. A date with no time is all-day at 00:00. Time text that
    cannot be read ("7:30" with no AM/PM) skips the event. Nothing is ever
    filled in from the clock.
  - A missing year is chosen by the printed weekday (year_for_weekday); the
    clock (`as_of`) only supplies the candidate years.
  - Every page is read until the listing ends. The page cap is a sanity cap,
    and hitting it is logged as an error.
  - A refusal raises, so the run records the source as failed and keeps its
    events, instead of "ok, 0 events".

When step 1 or 2 succeeds, the fetch and the time handling look like this:

    response = requests.get(api_url, params={...}, timeout=30,
                            headers={**self.get_browser_headers(), "Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()        # a challenge page is HTML, so this raises: good
    # ISO with an offset:   on_the_minute(datetime.fromisoformat(item["start"]))
    # epoch ms (UTC):       on_the_minute(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))
    # never datetime.fromtimestamp(x) without tz=: it reads the machine's zone,
    # which is UTC on the CI runner (Cambridge: 10:30 services published at 14:30)

A browser-driven scraper extends BasePlaywrightScraper instead, registers
`kind: playwright`, and waits with wait_for_stable_count(), never
wait_for_selector() alone: that returns on the first match of a list that is
still rendering.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.adapters._common import CANCELLED, WEEKDAYS, clean, venue_fields, year_for_weekday
from src.models.event import EventCreate
from src.scrapers.base_playwright_scraper import CHALLENGE_TITLES, ScrapeRefusedError
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

SOURCE_NAME = "Example Hall"    # exactly the registry file's `name:`
MAX_PAGES = 30                  # a sanity cap, never a limit: reaching it logs an error
PAGE_DELAY_S = 1.5              # between page requests; one visitor, not a crowd

# The markup this template reads. Replace the selectors with the venue's.
#
#   <article class="event">
#     <h3 class="event-title"><a href="/events/late-set">Late Set</a></h3>
#     <p class="event-date">Fri, Oct 9</p>
#     <p class="event-time">Doors 7:00 PM / Show 8:30 PM</p>
#     <p class="event-place">                       (only when it is elsewhere)
#       <span class="name">..</span> <span class="street">..</span> <span class="city">..</span></p>
#     <div class="event-summary">..</div>
#     <img src="/media/late-set.jpg">
#   </article>
#   <a class="next" href="/events/?page=2">Next</a>
CARD = "article.event"
NEXT_PAGE = "a.next[href], a[rel=next][href]"

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

# "Fri, Oct 9" | "Friday, October 9, 2026" | "October 9, 2026"
DATE = re.compile(
    r"\b(?:(?P<weekday>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"
    r"(?:,?\s+(?P<year>\d{4}))?",
    re.IGNORECASE)

# A clock reading: "7", "7:30", "7pm", "7:30 p.m."
CLOCK = re.compile(r"(?<![\d:$])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?:(?P<ap>[ap])\.?\s?m\b\.?)?",
                   re.IGNORECASE)
RANGE_GAP = re.compile(r"^\s*(?:-|–|—|to|until)\s*$", re.IGNORECASE)
DOORS = re.compile(r"\bdoors?\b", re.IGNORECASE)
NO_TIME = re.compile(r"^(?:time\s+)?(?:tba|tbd|all[\s-]?day)?$", re.IGNORECASE)


def read_date(text: str, as_of: date) -> date:
    """The day a listing names.

    "October 9, 2026" is read as printed (and its weekday, if printed, must
    agree). "Fri, Oct 9" takes the year near `as_of` in which Oct 9 is a
    Friday: Cambridge rolled "today" a year ahead by comparing with the clock,
    and published a show on a Thursday that the venue listed as a Wednesday.

    Raises ValueError when the text names no day, an impossible one, or prints
    neither a year nor a weekday to choose one by. The caller skips the event.
    """
    match = DATE.search(clean(text))
    if not match:
        raise ValueError(f"no date in {text!r}")
    month = MONTHS.index(match["month"][:3].lower()) + 1
    day = int(match["day"])
    weekday = (match["weekday"] or "")[:3].lower()
    if match["year"]:
        found = date(int(match["year"]), month, day)          # ValueError if impossible
        if weekday and WEEKDAYS[found.weekday()] != weekday:
            raise ValueError(f"{text!r}: {found} is not a {match['weekday']}")
        return found
    if not weekday:
        raise ValueError(f"{text!r} prints neither a year nor a weekday")
    year = year_for_weekday(month, day, weekday, as_of)
    if year is None:
        raise ValueError(f"{text!r}: no year near {as_of.year} puts that date on that weekday")
    return date(year, month, day)


def read_time(text: str) -> Optional[Tuple[int, int]]:
    """The start time in a listing's time text, as (hour, minute).

    Returns None when the listing gives no time ("", "TBA", "All day"): the
    event is published all-day. Raises ValueError for time text it cannot
    read: the event is skipped, because a guessed hour is a fabricated one.

        "7:30 PM"                         -> (19, 30)
        "6:00 - 8:00 PM"                  -> (18, 0)   the start borrows the end's meridiem
        "11 - 1 pm"                       -> (11, 0)   ...unless that would put it after the end
        "Doors 7:00 PM / Show 8:30 PM"    -> (20, 30)  doors are not the start
        "19:30"                           -> (19, 30)  unambiguous 24-hour
        "7:30"                            -> ValueError: AM or PM?
    """
    text = clean(text).strip(" .")
    if NO_TIME.match(text):
        return None
    text = re.sub(r"\bnoon\b", "12:00 pm", text, flags=re.IGNORECASE)      # not inside "afternoon"
    text = re.sub(r"\bmidnight\b", "12:00 am", text, flags=re.IGNORECASE)

    raw = list(CLOCK.finditer(text))

    def gap(a, b) -> str:
        return text[a.end():b.start()]

    # A bare number is a clock reading only as the start of a range ("6 - 8 PM")
    clocks = [c for i, c in enumerate(raw)
              if c["m"] or c["ap"]
              or (i + 1 < len(raw) and raw[i + 1]["ap"] and RANGE_GAP.match(gap(c, raw[i + 1])))]
    if not clocks:
        raise ValueError(f"no clock time in {text!r}")

    meridiem = [(c["ap"] or "").lower() or None for c in clocks]
    is_range_end = [False] * len(clocks)
    for i in range(len(clocks) - 1):
        if RANGE_GAP.match(gap(clocks[i], clocks[i + 1])):
            is_range_end[i + 1] = True
            if meridiem[i] is None and meridiem[i + 1]:
                start, end = int(clocks[i]["h"]) % 12, int(clocks[i + 1]["h"]) % 12
                same = meridiem[i + 1]
                meridiem[i] = same if start <= end else ("a" if same == "p" else "p")

    starts = [i for i in range(len(clocks)) if not is_range_end[i]]
    doors = {i for i in starts
             if DOORS.search(text[(clocks[i - 1].end() if i else 0):clocks[i].start()])}
    chosen = next((i for i in starts if i not in doors), starts[0])

    hour, minute = int(clocks[chosen]["h"]), int(clocks[chosen]["m"] or 0)
    if minute > 59 or hour > 23:
        raise ValueError(f"{text!r} is not a clock time")
    if meridiem[chosen] is None:
        if hour >= 13:
            return hour, minute
        raise ValueError(f"{text!r}: {hour}:{minute:02d} with no AM or PM")
    if hour == 0 or hour > 12:
        raise ValueError(f"{text!r} mixes a 24-hour clock with AM/PM")
    return hour % 12 + (12 if meridiem[chosen] == "p" else 0), minute


def _text(node, selector: str) -> str:
    found = node.select_one(selector)
    return clean(found.get_text(" ")) if found else ""


def parse_listing(html: str, page_url: str, *, venue: Optional[dict], as_of: date,
                  source_name: str = SOURCE_NAME) -> Tuple[List[EventCreate], Optional[str]]:
    """One listing page: its events, and the next page's URL (None at the end).

    Pure: the same page always gives the same answer. A card that cannot be
    dated is skipped with a warning naming it.
    """
    soup = BeautifulSoup(html, "html.parser")
    events: List[EventCreate] = []
    for card in soup.select(CARD):
        heading = card.select_one(".event-title")
        title = clean(heading.get_text(" ")) if heading else ""
        if not title:
            continue
        if CANCELLED.search(title):
            logger.info(f"{source_name}: skipping cancelled listing {title!r}")
            continue
        link = heading.find("a", href=True)
        href = link["href"].strip() if link else ""
        url = urljoin(page_url, href) if href and not href.startswith("#") else page_url

        try:
            day = read_date(_text(card, ".event-date"), as_of)
            clock = read_time(_text(card, ".event-time"))
        except ValueError as reason:
            logger.warning(f"{source_name}: skipping {title!r} - {reason} ({url})")
            continue
        hour, minute = clock or (0, 0)
        start = datetime(day.year, day.month, day.day, hour, minute)

        # The event's own place wins over the registry's venue defaults: a
        # venue's off-site events must not be pulled back to its address.
        place = card.select_one(".event-place")
        location = venue_fields(
            venue,
            name=_text(place, ".name") if place else None,
            street=_text(place, ".street") if place else None,
            city=_text(place, ".city") if place else None,
        )

        # EventValidator rejects a description under 10 characters, so a bare
        # listing gets one built from facts (Cambridge lost a whole run of
        # performances to "Description is too short").
        summary = _text(card, ".event-summary")
        where = location.get("venue_name") or (venue or {}).get("name") or ""
        description = summary or (f"{title} at {where}" if where else title)

        image = card.select_one("img[src]")
        events.append(EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=clock is None,
            source_url=url,
            source_name=source_name,
            image_url=urljoin(page_url, image["src"]) if image else None,
            **location,
        ))

    following = soup.select_one(NEXT_PAGE)
    return events, (urljoin(page_url, following["href"]) if following else None)


class Scraper(BaseScraper):
    """<Venue Name>'s listing, every page of it."""

    def __init__(self, source_name: str = SOURCE_NAME, url: Optional[str] = None,
                 venue: Optional[dict] = None, as_of: Optional[date] = None):
        # The registry is where the URL and the venue defaults live; the
        # scraper only knows its own name (CLAUDE.md: one place per source).
        if url is None or venue is None:
            from src.sources import BY_NAME      # lazily: the registry imports scrapers lazily too
            entry = BY_NAME.get(source_name)
            if entry is None:
                raise ValueError(f"{source_name!r} is not registered: `cal add` it first")
            url = url or entry.url
            venue = entry.venue if venue is None else venue
        super().__init__(source_name, url)
        self.venue = venue or {}
        # Only the candidate years for a yearless date come from the clock.
        # Tests pin it to the capture's date so a saved page parses the same forever.
        self.as_of = as_of

    def scrape_events(self) -> List[EventCreate]:
        as_of = self.as_of or date.today()
        events: List[EventCreate] = []
        seen = set()
        url: Optional[str] = self.source_url
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                time.sleep(PAGE_DELAY_S)
            html = self.fetch_html(url)               # raises on an error status
            title = BeautifulSoup(html[:5000], "html.parser").title
            if title and title.get_text(strip=True).lower().startswith(CHALLENGE_TITLES):
                raise ScrapeRefusedError(f"{self.source_name}: {url} served a bot check, not the listing")

            batch, next_url = parse_listing(html, url, venue=self.venue, as_of=as_of,
                                            source_name=self.source_name)
            fresh = [e for e in batch if (e.title, e.start_datetime, e.source_url) not in seen]
            if batch and not fresh:
                # Past the end, some sites serve page 1 again (Cambridge: BentoBox
                # wrapped around; a WordPress cache served page 1 for page 11).
                logger.warning(f"{self.source_name}: page {page} repeats earlier pages; stopping")
                break
            seen.update((e.title, e.start_datetime, e.source_url) for e in fresh)
            events.extend(fresh)
            if not next_url:
                break
            url = next_url
        else:
            logger.error(f"{self.source_name}: stopped at MAX_PAGES={MAX_PAGES} with a next page still "
                         f"linked ({url}); the listing is truncated. Raise MAX_PAGES.")

        logger.info(f"{self.source_name}: {len(events)} events")
        return events
