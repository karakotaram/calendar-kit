"""iCalendar (.ics) feeds: Google Calendar, WordPress Events Manager, and others.

One request returns the whole calendar. A feed carries what an HTML listing
often loses: the untruncated title, each performance's own time, and the zone
that time is in.

Fixes carried over from Cambridge Calendar's Arts at the Armory (Events
Manager) and Theatre@First (Google Calendar) scrapers:

  - Times honour their own form: TZID=<zone> wall clock, UTC ("...Z"), or
    floating (no zone: the calendar's X-WR-TIMEZONE, else the region's). A
    DATE-only value is an all-day event at midnight, flagged, never given an
    hour. An unknown TZID skips the event rather than guessing its zone.
  - Recurring events are expanded: a production is often one VEVENT "weekly on
    Thu-Sun until Mar 29". Reading DTSTART alone listed two of nine
    performances. EXDATE removes dates, RDATE adds them, and a RECURRENCE-ID
    override (a moved matinee, a cancelled night) replaces its slot. Series
    are expanded in their own zone's wall clock, so an 8 PM run stays at 8 PM
    across a DST change.
  - The expansion window is anchored on the feed's own DTSTAMP, not the
    scraper's clock, so a saved feed parses the same way on any day. Google
    stamps every event with the feed's generation time; Events Manager stamps
    each with its last edit, which can be weeks old, so the upper bound is
    applied only to recurrence (which needs one to be finite). A one-off
    event is kept from a day before the anchor onwards; the validator, not
    this adapter, decides what is too old.
  - A nested VALARM's own DESCRIPTION and UID must not overwrite the event's:
    properties are read per component, not as one flat list.
  - STATUS:CANCELLED, and titles marked cancelled, are skipped; so are
    private (CLASS:PRIVATE) events and titles matching `exclude` - a shared
    Google calendar also carries committee meetings and rehearsals.
  - A feed that answers 200 with an empty body (The Dance Complex's did,
    silently) or with an HTML page is an error, not an empty calendar.
  - Descriptions are unescaped (\\n, \\,) and stripped of HTML and of
    ticket-button scripts pasted in as text (Arts at the Armory).

Not ported: Armory's fallback of fetching the feed inside a browser when its
host refuses plain requests. That is a way around a block, which the kit does
not do; register such a feed `runs_in_ci: false` instead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pytz
import requests
from dateutil.rrule import rrulestr

from src import config
from src.adapters._common import clean, on_the_minute, strip_html, venue_fields
from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ADAPTER = "ical"
KIND = "requests"
CLASS = "ICalAdapter"
SIGNATURES = [
    "BEGIN:VCALENDAR",
    'type="text/calendar"',
    "webcal://",
    "calendar.google.com/calendar/embed",
    re.compile(r"""href=["'][^"']*\.ics\b""", re.IGNORECASE),
]
PARAMS = {
    "page_url": "the public page an event links to when the feed gives it no URL "
                "(default: the feed URL)",
    "days": "how far past the feed's own timestamp recurring events are expanded (default 365)",
    "exclude": "regexes; events whose title matches any of them are dropped (internal meetings)",
    "category": "an EventCategory value for every event (e.g. theater); default: left to enrichment",
}

# Keep a one-off event from a day before the feed's timestamp: last night's
# show while tonight's is still on
LOOKBACK = timedelta(days=1)

NOTICE_TITLE = re.compile(
    r"^\W*(?:cancell?ed|postponed)\b|[\s(\[:–—-](?:cancell?ed|postponed)\W*$", re.IGNORECASE)

# Ticket-button scripts pasted into descriptions as text:
#   $('#getTixButton').click(function() { fbq('track', 'Purchase', {...}); });
TRACKING_SCRIPT = re.compile(
    r"\$\(\s*['\"][^'\"]*['\"]\s*\)\.\w+\(\s*function\s*\(\)\s*\{.*?\n\s*\}\s*\)\s*;", re.S)

DATE_TIME = re.compile(r"(\d{8})T(\d{4})(\d{2})?(Z)?")
DURATION = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
IMAGE_PATH = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?|$)", re.IGNORECASE)
STATE_ZIP = re.compile(r"(?:(?P<city>.*?)\s+)?(?P<state>[A-Z]{2})(?:\s+(?P<zip>\d{5})(?:-\d{4})?)?")

# Outlook and Exchange write Windows zone names
WINDOWS_ZONES = {
    "pacific standard time": "America/Los_Angeles",
    "mountain standard time": "America/Denver",
    "us mountain standard time": "America/Phoenix",
    "central standard time": "America/Chicago",
    "eastern standard time": "America/New_York",
    "alaskan standard time": "America/Anchorage",
    "hawaiian standard time": "Pacific/Honolulu",
    "gmt standard time": "Europe/London",
    "coordinated universal time": "UTC",
}

Property = Tuple[str, Dict[str, str], str]      # (NAME, {PARAM: value}, value)


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #

def unfold(text: str) -> List[str]:
    """Logical lines, with RFC 5545 folding undone.

    Splits on line breaks only: str.splitlines() would also split a description
    at U+2028 and other separators that are legal inside a value.
    """
    lines: List[str] = []
    for line in re.split(r"\r\n|\n|\r", text):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _split_outside_quotes(text: str, sep: str, maxsplit: int = -1) -> List[str]:
    parts, current, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted and maxsplit != 0:
            parts.append("".join(current))
            current = []
            maxsplit -= 1
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def parse_line(line: str) -> Optional[Property]:
    """NAME;PARAM=value:VALUE -> (NAME, {PARAM: value}, VALUE). Quoted
    parameter values may contain ':' and ';'."""
    split = _split_outside_quotes(line, ":", maxsplit=1)
    if len(split) != 2:
        return None
    head, value = split
    name, *raw = _split_outside_quotes(head, ";")
    params = {}
    for p in raw:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip().strip('"')
    name = name.strip().upper().rsplit(".", 1)[-1]       # drop a vCard-style group prefix
    return name, params, value


def read_components(text: str) -> Tuple[List[Property], List[List[Property]]]:
    """(calendar properties, one property list per VEVENT).

    Tracks nesting, so a VALARM inside a VEVENT, or a VTIMEZONE's own DTSTART,
    is never mistaken for the event's.
    """
    calendar: List[Property] = []
    events: List[List[Property]] = []
    stack: List[str] = []
    current: Optional[List[Property]] = None
    for line in unfold(text):
        upper = line.strip().upper()
        if upper.startswith("BEGIN:"):
            stack.append(upper[6:])
            if stack[-1] == "VEVENT":
                current = []
            continue
        if upper.startswith("END:"):
            ended = stack.pop() if stack else None
            if ended == "VEVENT" and current is not None:
                events.append(current)
                current = None
            continue
        prop = parse_line(line)
        if prop is None or not stack:
            continue
        if stack[-1] == "VEVENT" and current is not None:
            current.append(prop)
        elif stack == ["VCALENDAR"]:
            calendar.append(prop)
    return calendar, events


def first(props: List[Property], name: str) -> Optional[Property]:
    return next((p for p in props if p[0] == name), None)


def every(props: List[Property], name: str) -> List[Property]:
    return [p for p in props if p[0] == name]


def value(props: List[Property], name: str) -> str:
    prop = first(props, name)
    return prop[2] if prop else ""


def text_value(raw: str) -> str:
    """An iCalendar TEXT value as plain prose."""
    text = re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), raw or "")
    text = TRACKING_SCRIPT.sub(" ", text)
    return strip_html(text)


# --------------------------------------------------------------------------- #
# Times
# --------------------------------------------------------------------------- #

def zone_named(tzid: Optional[str]) -> Optional[pytz.BaseTzInfo]:
    """An IANA zone from a TZID, tolerating the common non-IANA spellings."""
    if not tzid:
        return None
    name = tzid.strip().strip('"')
    candidates = [name]
    # "/mozilla.org/20050126_1/America/New_York", "/citadel.org/.../America/Chicago"
    path = re.search(r"([A-Za-z_]+/[A-Za-z_+-]+(?:/[A-Za-z_+-]+)?)$", name)
    if path:
        candidates += [path.group(1), "/".join(path.group(1).split("/")[-2:])]
    if name.lower() in WINDOWS_ZONES:
        candidates.append(WINDOWS_ZONES[name.lower()])
    for candidate in candidates:
        try:
            return pytz.timezone(candidate)
        except pytz.UnknownTimeZoneError:
            continue
    return None


@dataclass(frozen=True)
class Moment:
    """A DATE or DATE-TIME as written: wall clock plus the zone it is in.
    `zone` is None for an all-day DATE."""
    wall: datetime
    zone: Optional[pytz.BaseTzInfo]

    @property
    def all_day(self) -> bool:
        return self.zone is None

    def local(self) -> datetime:
        """As the calendar's local wall clock, on the minute."""
        return self.wall if self.zone is None else on_the_minute(self.zone.localize(self.wall))

    def aware(self) -> Optional[datetime]:
        return None if self.zone is None else self.zone.localize(self.wall)


def read_moment(raw: str, params: Dict[str, str], floating: pytz.BaseTzInfo) -> Optional[Moment]:
    """Parse one DATE or DATE-TIME. None when unreadable or in an unknown zone."""
    raw = (raw or "").strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", raw):
        try:
            return Moment(datetime.strptime(raw[:8], "%Y%m%d"), None)
        except ValueError:
            return None
    match = DATE_TIME.fullmatch(raw)
    if not match:
        return None
    try:
        wall = datetime.strptime(match.group(1) + match.group(2) + (match.group(3) or "00"), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if match.group(4):
        return Moment(wall, pytz.utc)
    if params.get("TZID"):
        zone = zone_named(params["TZID"])
        return Moment(wall, zone) if zone else None
    return Moment(wall, floating)


def read_duration(raw: str) -> Optional[timedelta]:
    match = DURATION.fullmatch((raw or "").strip())
    if not match or not any(match.groups()[1:]):
        return None
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups()[1:])
    span = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    return -span if match.group(1) == "-" else span


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #

class ICalAdapter(BaseScraper):
    """A venue's iCalendar feed."""

    def __init__(self, source_name: str, url: str, venue: Optional[dict] = None, *,
                 page_url: Optional[str] = None, days: int = 365, exclude=(),
                 category: Optional[str] = None):
        if url and url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        super().__init__(source_name, url)
        self.venue = venue or {}
        self.page_url = page_url or url
        try:
            self.days = int(days)
        except (TypeError, ValueError):
            self.days = 365
        patterns = [exclude] if isinstance(exclude, str) else list(exclude or ())
        self.exclude = [re.compile(p, re.IGNORECASE) for p in patterns]
        self.category = EventCategory(category) if category else None

    def fetch_feed(self) -> str:
        response = requests.get(
            self.source_url,
            headers={**self.get_browser_headers(), "Accept": "text/calendar, */*;q=0.5"},
            timeout=30,
        )
        response.raise_for_status()
        # Feeds are UTF-8 but often served without a charset, which requests
        # would decode as Latin-1
        text = response.content.decode("utf-8", errors="replace").lstrip("﻿")
        if "BEGIN:VCALENDAR" not in text[:2000].upper():
            raise ValueError(f"{self.source_name}: {self.source_url} answered {response.status_code} "
                             f"without a calendar ({text[:60].strip()!r})")
        return text

    def scrape_events(self) -> List[EventCreate]:
        events = self.parse_feed(self.fetch_feed())
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_feed(self, raw: str, as_of: Optional[datetime] = None) -> List[EventCreate]:
        """Every public occurrence in the feed's current window.

        `as_of` (local wall clock) defaults to the feed's own latest DTSTAMP.
        """
        calendar, components = read_components(raw)
        if not components:
            logger.warning(f"{self.source_name}: the feed holds no events")
            return []
        floating = zone_named(value(calendar, "X-WR-TIMEZONE")) or zone_named(value(calendar, "TZID")) or config.TZ

        if as_of is None:
            as_of = self.feed_time(components, floating)
            if as_of is None:
                raise ValueError(f"{self.source_name}: the feed has no DTSTAMP, so nothing says which "
                                 "of its events are current")
        lo, hi = as_of - LOOKBACK, as_of + timedelta(days=self.days)

        # Occurrences of a series that a separate VEVENT replaces or cancels
        overridden = set()
        for props in components:
            rid = first(props, "RECURRENCE-ID")
            moment = read_moment(rid[2], rid[1], floating) if rid else None
            if moment is not None:
                overridden.add((value(props, "UID"), moment.local()))

        events: List[EventCreate] = []
        seen = set()
        for props in components:
            try:
                parsed = self.parse_component(props, floating, (lo, hi), overridden)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse VEVENT {value(props, 'UID')!r}: {e}")
                continue
            for event in parsed:
                key = (event.title, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        return events

    @staticmethod
    def feed_time(components: List[List[Property]], floating) -> Optional[datetime]:
        """When the feed was generated, as near as it says: its latest DTSTAMP."""
        for name in ("DTSTAMP", "LAST-MODIFIED"):
            stamps = [read_moment(p[2], p[1], floating) for props in components for p in every(props, name)]
            stamps = [m.local() for m in stamps if m is not None and not m.all_day]
            if stamps:
                return max(stamps)
        return None

    def parse_component(self, props: List[Property], floating, window, overridden) -> List[EventCreate]:
        summary = text_value(value(props, "SUMMARY"))
        if len(summary) < 3 or any(p.search(summary) for p in self.exclude):
            return []
        url = value(props, "URL").strip()
        url = url if url.startswith(("http://", "https://")) else self.page_url
        if value(props, "STATUS").strip().upper() == "CANCELLED" or NOTICE_TITLE.search(summary):
            logger.info(f"{self.source_name}: skipping cancelled '{summary}'")
            return []
        if value(props, "CLASS").strip().upper() in ("PRIVATE", "CONFIDENTIAL"):
            return []

        dtstart = first(props, "DTSTART")
        start = read_moment(dtstart[2], dtstart[1], floating) if dtstart else None
        if start is None:
            # Never guess a date - see CLAUDE.md, "Never fabricate a date".
            logger.warning(f"Skipping '{summary}' - no readable DTSTART "
                           f"({dtstart[1] if dtstart else ''} {dtstart[2] if dtstart else ''!r})")
            return []

        length = None
        if not start.all_day:
            dtend = first(props, "DTEND")
            end = read_moment(dtend[2], dtend[1], floating) if dtend else None
            if end is not None and not end.all_day:
                length = end.aware() - start.aware()
            else:
                length = read_duration(value(props, "DURATION"))
            if length is not None and length <= timedelta(0):
                length = None

        location = self.place(text_value(value(props, "LOCATION")))
        description = text_value(value(props, "DESCRIPTION"))
        if len(description) < 20:
            description = f"{summary} at {location['venue_name'] or self.source_name}."
        labels = (clean(t.replace("\\", "")) for p in every(props, "CATEGORIES")
                  for t in re.split(r"(?<!\\),", p[2]))
        tags = list(dict.fromkeys(t for t in labels if t))

        return [
            EventCreate(
                title=summary[:200],
                description=description[:2000],
                start_datetime=occurrence,
                end_datetime=on_the_minute(occurrence + length) if length else None,
                all_day=start.all_day,
                **location,
                category=self.category,
                tags=tags,
                source_url=url,
                source_name=self.source_name,
                image_url=self.image(props),
            )
            for occurrence in self.occurrences(props, start, floating, window, overridden, summary)
        ]

    # ------------------------------------------------------------------ #
    # Recurrence
    # ------------------------------------------------------------------ #

    def occurrences(self, props, start: Moment, floating, window, overridden, summary) -> List[datetime]:
        """The local starts of one VEVENT that fall in the window."""
        lo, hi = window
        rule = first(props, "RRULE")
        if rule is None or first(props, "RECURRENCE-ID"):
            # A one-off or an override: kept from the lower bound on
            return [start.local()] if start.local() >= lo else []

        try:
            series = rrulestr(self._rule_in_frame(rule[2], start), dtstart=start.wall)
            pad = timedelta(days=1)
            walls = series.between(self._to_frame(lo, start) - pad, self._to_frame(hi, start) + pad, inc=True)
        except (ValueError, TypeError, OverflowError) as e:
            # The first date is still real; the rest cannot be read
            logger.warning(f"{self.source_name}: could not expand '{summary}' RRULE {rule[2]!r}: {e}")
            return [start.local()] if start.local() >= lo else []

        candidates = {Moment(w, start.zone).local() for w in walls}
        for prop in every(props, "RDATE"):
            for part in prop[2].split(","):
                added = read_moment(part, prop[1], floating) if "/" not in part else None
                if added is not None:
                    candidates.add(added.local())
        excluded = set()
        for prop in every(props, "EXDATE"):
            for part in prop[2].split(","):
                gone = read_moment(part, prop[1], floating)
                if gone is not None:
                    excluded.add(gone.local())

        uid = value(props, "UID")
        return sorted(c for c in candidates
                      if lo <= c <= hi and c not in excluded and (uid, c) not in overridden)

    @staticmethod
    def _to_frame(local: datetime, start: Moment) -> datetime:
        """A local wall-clock time restated in the series' own frame."""
        if start.zone is None:
            return local
        return config.TZ.localize(local).astimezone(start.zone).replace(tzinfo=None)

    @staticmethod
    def _rule_in_frame(rule: str, start: Moment) -> str:
        """Restate UNTIL in the frame the series is expanded in.

        dateutil refuses a UTC UNTIL against a naive DTSTART, and a DATE UNTIL
        against a timed series would end it at midnight of its last day.
        """
        def convert(match):
            raw = match.group(1)
            if raw.endswith("Z"):
                instant = pytz.utc.localize(datetime.strptime(raw, "%Y%m%dT%H%M%SZ"))
                frame = start.zone or config.TZ
                return "UNTIL=" + instant.astimezone(frame).strftime("%Y%m%dT%H%M%S")
            if len(raw) == 8 and start.zone is not None:
                return f"UNTIL={raw}T235959"
            return match.group(0)
        return re.sub(r"UNTIL=(\d{8}(?:T\d{6}Z?)?)", convert, rule)

    # ------------------------------------------------------------------ #
    # Fields
    # ------------------------------------------------------------------ #

    def place(self, text: str) -> dict:
        """Place fields from a LOCATION such as
        "Unity Somerville, 6 William St, Somerville, MA 02144, USA"."""
        if not text:
            return {**venue_fields(self.venue), "state": None}
        if re.match(r"https?://", text) or text.lower() in ("online", "zoom", "virtual"):
            return {**venue_fields(self.venue, name="Online"), "state": None}

        parts = [p.strip() for p in text.split(",") if p.strip()]
        if parts and parts[-1].lower() in ("usa", "us", "united states", "united states of america"):
            parts.pop()
        city = state = zip_code = None
        tail = STATE_ZIP.fullmatch(parts[-1]) if len(parts) > 1 else None
        if tail:
            parts.pop()
            state, zip_code = tail.group("state"), tail.group("zip")
            city = tail.group("city") or parts.pop()

        name = street = None
        if parts and re.match(r"\d", parts[0]):
            street = ", ".join(parts)
        elif parts:
            name = parts[0]
            rest = parts[1:]
            if rest and re.match(r"\d", rest[0]):
                street = ", ".join(rest)
            elif rest:
                name = ", ".join(parts)
        fields = venue_fields(self.venue, name=(name or "")[:150], street=(street or "")[:200],
                              city=(city or "")[:50], zip_code=zip_code)
        return {**fields, "state": state}

    @staticmethod
    def image(props: List[Property]) -> Optional[str]:
        for _, params, url in every(props, "ATTACH"):
            url = url.strip()
            if url.startswith("http") and (params.get("FMTTYPE", "").startswith("image/") or IMAGE_PATH.search(url)):
                return url
        return None
