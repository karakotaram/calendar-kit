# Platform cheatsheet

How to recognise the website platforms venues run on, where each keeps its
event data, which adapter reads it, and what has gone wrong with it before.
Everything here was learned on a real venue at Cambridge Calendar; the venue is
named so you can see the pattern.

`cal detect <url>` does the recognising for you; `cal adapters` lists every
adapter with its params. When the two disagree with this page, they win: they
read the code.

General rules for every platform:

- **Read the data the page itself loads.** Open the browser's network tab and
  reload; the JSON or feed the page fetches beats the rendered HTML, which may
  be cached, cut to a first page, or rendered progressively.
- **Page to the end.** Most platforms default to 10–50 items per request.
- **Times are the trap.** Every platform below has had at least one way of
  publishing a wrong time. Check two events against the page
  ([onboarding-sources.md](onboarding-sources.md#7-spot-check-against-the-venue)).

| Platform | Adapter | Typical data source |
|---|---|---|
| [The Events Calendar (Tribe)](#the-events-calendar-tribe) | `tribe` | `/wp-json/tribe/events/v1/events` |
| [Squarespace](#squarespace) | `squarespace` | `<collection URL>?format=json` |
| [Localist](#localist) | `localist` | `/api/2/events` |
| [IndieCommerce](#indiecommerce) | `indiecommerce` | `drupalSettings` on `/events/calendar/YYYY/MM` |
| [Assabet Interactive](#assabet-interactive) | `assabet` | listing pages with JSON-LD |
| [EventON](#eventon) | `eventon` | `/?evo-ajax=eventon_get_events` |
| [Drupal views](#drupal-views) | `drupal_listing` | server-rendered listing with `<time datetime>` |
| [iCal feeds](#ical-feeds) | `ical` | `.ics` |
| [JSON-LD](#json-ld) | `jsonld` | `<script type="application/ld+json">` |
| [Without an adapter](#platforms-without-an-adapter) | custom | see below |

---

## The Events Calendar (Tribe)

The most common WordPress events plugin.

- **Recognise it**: `/wp-content/plugins/the-events-calendar/` in asset paths,
  `tribe-events` class names, links with `?ical=1`, or
  `<site>/wp-json/tribe/events/v1/events` answering JSON.
- **Data**: `GET /wp-json/tribe/events/v1/events?start_date=...&per_page=50&page=N`.
  The response carries `total_pages`; each event has `utc_start_date`,
  `utc_end_date`, `all_day`, `venue`, `image`, `categories`, `cost`, `url`.
- **Adapter**: `tribe`. Params: `days` (a server-side window; default everything upcoming), `per_page` (default 50), `max_pages` (sanity cap, default 50; reaching it logs an error), `api_url` (WordPress in a subdirectory, or no pretty permalinks), `categories` and `venue_ids` (filters), `rooms` (the API's venue is a room inside the registry venue), `category`.
- **Quirks**:
  - Read starts from the `utc_*` fields and convert explicitly; the local
    `start_date` is right only if the site's zone is yours. The adapter also
    requires the two to agree, and skips an event where they do not.
  - `venue` and `image` arrive as a dict, an empty list, a list of dicts, or
    `False`, depending on the event. Assuming a dict killed a whole run on the
    first event with no venue.
  - A "[Virtual]" event with no venue is online, not at the venue's address;
    hidden, unpublished and cancelled listings are skipped.
  - Honour `all_day`; otherwise an all-day event shows at 00:00.
  - Follow `total_pages`; a fixed page cap must be loud. The Dance Complex's
    cap of 20 pages × 50 was 87% full on 2026-10-06, and filling it would have
    dropped the overflow without a word.
  - Recurring classes multiply fast (1,527 instances at one dance studio):
    bound the window with a param rather than reading years ahead.
  - Titles carry entities (`&#038;`, `&#8220;`); descriptions carry WPBakery
    shortcodes (`[vc_row ...]`).
  - The site's own iCal export is unreliable: it returned HTTP 200 with an empty
    body at The Dance Complex (every variant) and held only the current day's
    events at Harvard Square (2026-01-24). Per-day `/events/YYYY-MM-DD/` pages
    stopped listing anything at Harvard Square. The REST API was fine in every
    case.
  - Some hosts rate-limit into a challenge (Longy, Imunify360): one request per
    page, no retries.
- **Seen at**: The Rockwell, The Dance Complex, Harvard Square (an aggregator),
  Mount Auburn Cemetery, Longy School of Music.

## Squarespace

- **Recognise it**: `static1.squarespace.com` assets, a
  `<!-- This is Squarespace. -->` comment, `sqs-` class names,
  `Squarespace` in the generator meta tag.
- **Data**: append `?format=json` to an events collection URL (the page that
  lists events, e.g. `/events?format=json`). An events collection returns
  `upcoming[]` and `past[]`; a store collection returns `items[]` (products).
  `startDate`/`endDate` are UTC epoch milliseconds.
- **Adapter**: `squarespace`. Params: `category`. Point `url` at the events collection page.
- **Quirks**:
  - Epoch milliseconds carry noise (19:30 arrives as `...400563`): convert from
    UTC explicitly and floor to the minute, or the validator rejects them as
    clock readings. Read with the machine's zone, First Parish's 10:30 services
    published at 14:30 from CI.
  - Read `upcoming`, not the rendered list: The Lily Pad's rendered page was
    cached and still marked past shows as upcoming, and its scraper had been
    capped at 30 of about 115.
  - Bodies carry editor placeholders ("Double-click to edit..."); private
    bookings ("** Private Event **", "invite only") are not public events.
  - An empty location still carries map coordinates: Squarespace's default, in
    Lower Manhattan. Use coordinates only alongside an address.
  - Squarespace has no status field. A title that *starts* with RESCHEDULED,
    CANCELLED or POSTPONED is a notice on the old date (MIT Open Space posts the
    new date as a separate item); a title that merely mentions the word is an
    event.
  - A store used as an event listing (products whose tags carry the date) is
    not covered by the adapter: it needs a custom read. A category filter in a
    store URL is not a filter (an unknown category returned Skip the Small
    Talk's whole national list), so check each item; cloned products can carry
    another event's date in their URL.
  - A plain Squarespace page of text blocks (not a collection) has no JSON
    events; that is a custom scraper (Grolier Poetry Book Shop).
- **Seen at**: The Lily Pad, Portico Brewing, MIT Open Space, First Parish in
  Cambridge, Skip the Small Talk (store).

## Localist

University and city calendars.

- **Recognise it**: `localist` in script paths or page source, a
  `calendar.<institution>.edu` host, `/api/2/events` answering JSON.
- **Data**: `GET /api/2/events?days=60&pp=100&page=N`, paged by `page.total`.
  Each event carries `event_instances[].event_instance` with `start` (ISO with
  offset), `end` and `all_day`, plus `location`, `geo` and `filters`.
- **Adapter**: `localist`. Params: `days` (default 60; Localist allows up to 370), `audience` (default `Public`; `null` keeps every unrestricted event), `audience_filter` (default `event_audience`), `affiliation` (e.g. `[MIT]`, so "MIT ID required" reads as a restriction), `exclude_types`, `query` (extra API filters, e.g. one department), `api_url`, `max_pages` (sanity cap, default 30), `category`.
- **Quirks**:
  - The homepage's JSON-LD holds only today's and featured events: about 5% of
    MIT's calendar on 2026-10-05.
  - Most of a university calendar is internal. Audience lives in
    `filters.event_audience` (MIT: Public, MIT Community, Students...); on
    2026-10-06, 166 of MIT's 665 occurrences included Public. Use the tag, and
    let the description override it either way ("open to the public";
    "MIT ID required").
  - Holidays are closures, not events. Virtual events have no place: venue
    "Online".
  - Departments re-post the same talk (`copy-of-copy-of-...`): same title,
    start and room, two URLs.
  - An all-day instance keeps the date it is written on; converting its
    midnight to another zone would move it to the day before.
  - If a calendar has no audience filter, or names it differently, the run
    says so rather than judging every event on its text; set `audience_filter`,
    or `audience: null` deliberately.
  - A start without an offset, or with seconds, means the field changed meaning:
    refuse it.
- **Seen at**: MIT Events (calendar.mit.edu).

## IndieCommerce

The American Booksellers Association's Drupal platform for independent
bookstores.

- **Recognise it**: a bookstore site with `/events/calendar/YYYY/MM` month
  pages, `drupalSettings` in the page source carrying FullCalendar event data,
  `indiecommerce` in assets.
- **Data**: the month page embeds every event in `drupalSettings` as
  FullCalendar JSON: an ISO start and end with offset, `allDay`, the event URL,
  tags, and a `<template>` holding the teaser with the printed date, time and
  place. Months chain from the page's own `calander_view` (sic) link.
- **Adapter**: `indiecommerce`. Params: `months` (default 1), `visible_browser` (opt-in, decided by a person; needs `runs_in_ci: false`), `homes` (the store's own location tags and the place each means), `place_names` (rename printed branch labels).
- **Quirks**:
  - Python's `html.parser` does not descend into `<template>` content, so the
    teasers look blank; re-parse each template on its own.
  - Believe the ISO start only if the teaser prints the same date and time.
  - Read the city from each event's own address: Porter Square Books has a
    Boston branch, and the old scraper called everything Cambridge.
  - Both stores Cambridge read sit behind Cloudflare settings that refuse plain
    HTTP and headless browsers. They serve an ordinary visible browser, so they
    run only in [visible-browser mode](access-policy.md#visible-browser-mode),
    locally, one month per run.
- **Seen at**: Porter Square Books, Harvard Book Store.

## Assabet Interactive

A library events platform, usually embedded in the library's own site.

- **Recognise it**: an embed or link to `*.assabetinteractive.com`, a library
  calendar with month listing pages.
- **Data**: the month listing at `/calendar/event-listing/` (it redirects to
  the current month), read directly rather than through the library's iframe.
  Each event's JSON-LD gives `startDate` and `doorTime`; the visible card
  repeats the time. Months chain by the listing's own "Next Month" link, so no
  clock is read; an empty month is the edge of what has been published.
- **Adapter**: `assabet`. Params: `max_months` (default 6), `branch_prefix` (prepended to branch names; default the registry venue's name).
- **Quirks**:
  - The JSON-LD contains raw newlines; parse with `strict=False`.
  - Excerpts are sometimes entity-encoded twice.
  - The card omits the start's meridiem when it matches the end's
    ("6:00-7:00 PM"): the start borrows the end's.
  - `doorTime` conventionally means doors-open; it was the start for all 522
    events checked on 2026-10-05, and the cross-check against the card is what
    will notice if that changes.
  - Skip cancelled events and "All Closed" holiday cards.
  - The venue comes from the card: a branch, "Online" for a virtual program,
    or the named place for an off-site one.
- **Seen at**: Somerville Public Library.

## EventON

A WordPress calendar plugin.

- **Recognise it**: `eventon` in script or style paths, `evo_` and `eventon_`
  class names, an `evo-ajax` endpoint.
- **Data**: POST the calendar's settings to `/?evo-ajax=eventon_get_events`;
  the response is the month's HTML plus the settings for the next month, which
  you chain into the next request (exactly what the page's own arrow does). Each
  event carries `data-time` (Unix seconds) and schema.org microdata with a
  `startDate`.
- **Adapter**: `eventon`. Params: `months` (default 12, one request each for a month calendar; a list calendar returns its whole range at once), `mode` (`ajax`, the default, or `page` for a site that renders its listings server-side).
- **Quirks**:
  - The microdata's offset is `-4:00` all year, so in standard time it is an
    hour wrong: Regent Theatre published 15 of 34 events an hour early. Discard
    the offset, use the wall clock, and require it to agree with `data-time`
    converted from UTC.
  - 23:59 is EventON's "no end time" placeholder; do not publish it.
  - The schema `description` is often just the title in quotes ("'Eleanor'"),
    which the validator rejects as too short; take the show's synopsis from its
    page.
  - Some listings link to `#`; use the show page, the event's own page or the
    calendar instead.
  - Clicking "next month" in a browser can be intercepted by a newsletter popup;
    the endpoint needs no browser.
- **Seen at**: Regent Theatre, Central Square Theater.

## Drupal views

Municipal and institutional sites built on Drupal, with a server-rendered
calendar listing.

- **Recognise it**: `Drupal.settings` or `drupalSettings`, `/sites/default/files/`
  paths, `views-row` classes, a `/calendar` listing paged with `?page=N`.
- **Data**: the listing HTML. Each row's `<time datetime="...Z">` carries a UTC
  instant; its text carries local wall clock.
- **Adapter**: `drupal_listing`. Params: `max_pages` (default 40), `exclude` (extra title patterns for rows that are not events), `detail_pages` (default true: read each event's page for its address), and CSS selectors for a non-stock View (`row_selector`, `title_selector`, `time_selector`, `body_selector`, `image_selector`, `next_selector`, `address_selector`, `detail_body_selector`).
- **Quirks**:
  - Two renderings of each time: publish only when they agree (all ~400 did on
    2026-10-05 at City of Somerville). A date-only `<time>` is all-day.
  - Page with `?page=N` and stop when a page adds nothing new: a pager that
    repeats its last page must not loop.
  - The listing may have no venue; detail pages supply the address, and should
    never be read for a date.
  - Exclude what is not an event: "Holiday:" office closures listed at
    12:00 am, closed executive sessions, a live "Example Meeting Event"
    placeholder. Strip the accessibility boilerplate appended to every body.
  - Never assume a `datetime` attribute is 24-hour or UTC without checking:
    Cambridge.gov's (not Drupal) is a 12-hour clock with no meridiem, so 5 PM is
    `05:00:00`.
- **Seen at**: City of Somerville.

## iCal feeds

Any platform that exports `.ics`: WordPress Events Manager, The Events Calendar,
Google Calendar, many ticketing systems.

- **Recognise it**: links ending `.ics`, `webcal://` links, "Subscribe" or
  "Add to calendar" buttons, `?ical=1`, `/events.ics`, a Google Calendar embed
  (see below).
- **Data**: `VEVENT`s with `DTSTART`, `DTEND`, `SUMMARY`, `DESCRIPTION`,
  `LOCATION`, `URL`, `CATEGORIES`, `RRULE`, `EXDATE`, `RECURRENCE-ID`, `STATUS`.
- **Adapter**: `ical`. Params: `days` (how far past the feed's `DTSTAMP` recurring events are expanded; default 365), `exclude` (title patterns to drop, e.g. internal meetings on a shared calendar), `page_url` (the public page for events the feed gives no URL), `category`.
- **Quirks**:
  - Honour `TZID`; a `Z` time is UTC; a floating time takes the feed's
    `X-WR-TIMEZONE`, else the region's. An unknown `TZID` skips the event
    rather than guessing its zone.
  - `VALUE=DATE` means all-day, and an all-day `DTEND` is exclusive (the day
    after the last day).
  - `DTEND` equal to `DTSTART` means no end time.
  - Recurring runs are one `VEVENT` with an `RRULE`. Expand it, apply `EXDATE`,
    and let a `RECURRENCE-ID` override replace the slot it moves; expand in local
    wall clock so a run keeps its curtain time across a clock change. Reading
    `DTSTART` alone listed 2 of 9 performances of one Theatre@First run.
  - Bound the expansion by the feed's own `DTSTAMP`, never the clock, so a saved
    feed always gives the same events.
  - A `VALARM` nested in a `VEVENT` has its own `UID` and `DESCRIPTION`; a flat
    parse lets them overwrite the event's.
  - Skip `STATUS:CANCELLED` and `CLASS:PRIVATE`; a shared Google Calendar also
    carries committee meetings and rehearsals (`exclude`).
  - A feed can answer 200 with an empty body; treat that as a failure, not an
    empty calendar.
  - One feed request can replace dozens of page loads: Arts at the Armory's
    `/events.ics` held exactly the 81 events its seven listing pages showed.
    Cambridge's Armory scraper fetched the feed inside a browser when its host
    refused plain requests from CI; the adapter deliberately does not, because
    that is a way round a block. Register such a feed `runs_in_ci: false`.
- **Seen at**: Arts at the Armory (WordPress Events Manager), Theatre@First
  (Google Calendar).

## JSON-LD

schema.org `Event` data embedded in a page, often added by SEO plugins.

- **Recognise it**: `<script type="application/ld+json">` containing
  `"@type": "Event"` or a subtype (`MusicEvent`, `TheaterEvent`,
  `ComedyEvent`...), at the top level, in a list, under `@graph`, or nested
  (SeatEngine lists a venue's shows under an `EventVenue`'s `events`).
- **Data**: inline in the page: `name`, `startDate`, `endDate`, `location`,
  `image`, `performer`, `offers`, `url`, `description`.
- **Adapter**: `jsonld`. Params: `paths` (extra listing pages), `max_pages` (follow `rel=next`; default 1), `offsets` (`check`, the default, skips a start whose offset is not the region's; `wall` trusts the written clock; `instant` trusts the offset), `category` (default: from the schema.org type).
- **Quirks**:
  - Check it is complete before relying on it. JSON-LD is often only the first
    page or featured items: 10 events at Mount Auburn (of 39) and Longy (of 34).
    When the page has a real API, use that and keep JSON-LD as a cross-check.
  - `image` and `performer` may be a single value or a list, a URL string or an
    object. The Comedy Studio's performer fallback never fired because it
    expected a dict.
  - The list may be in no particular order; never slice it.
  - Offsets can be wrong: EventON writes `-4:00` all winter, and a WordPress
    site left on UTC writes local times as `+00:00`. By default a start whose
    offset is not the region's is skipped with a warning; look at the page,
    then set `offsets: wall` or `offsets: instant`.
  - Raw newlines in descriptions: parse with `strict=False`.
  - `eventStatus` cancelled or postponed is skipped. A page with no JSON-LD at
    all is an error: the adapter no longer fits the site.
- **Seen at**: The Comedy Studio; as a cross-check at The Mad Monkfish and
  Somerville Public Library.

---

## Platforms without an adapter

Seen at Cambridge, read with custom scrapers. If several of your venues share
one, it is worth an adapter.

| Platform | Recognise it | Where the data is | Quirks |
|---|---|---|---|
| **AudienceView storefronts** (box offices) | `/Online/default.asp?...WScontent::loadArticle...` URLs; "AudienceView" in assets | an "Upcoming Events" widget rendered client-side; needs a browser | the page never reaches `networkidle`: wait for the widget's own markup. One box office sells for several venues: filter to yours. Runs listed "December 11-28, 2026" with no time are all-day. (Sanders Theatre via the Harvard Box Office) |
| **NPS calendar** (nps.gov park sites) | `nps.gov/<park>/planyourvisit/calendar.htm` | JSON from the park's `EventCalendarService.cfc`, fetched by the page | rendered cards race the page: 54 cards alone, 4 under load. The service took ~150 s per page for a plain client and ~3 s from inside the page, so capture the page's own response in a browser. Hourly tours share a title; do not dedupe them. (Longfellow House-Washington's Headquarters) |
| **BentoBox** (restaurants and bars) | `getbento.com` assets | listing pages `?p=N`, ten cards each; event pages with JSON-LD | the forward pager link is labelled "Previous" and "Load More" is a `<button>`; past the last page it wraps to page 1, so stop on a page that adds nothing new. Card titles carry the date and rarely a time; date each show from its event page. A "12-1am" set is listed under the evening before. (The Mad Monkfish) |
| **Google Calendar embeds** | `calendar.google.com/calendar/embed?src=<id>` iframe | `https://calendar.google.com/calendar/ical/<id>/public/basic.ics` | use the `ical` adapter with that feed URL. Small organisations enter whole runs as one recurring event; expand them. (Theatre@First) |
| **Ticketing and aggregator listings** (Ticketmaster-, Eventbrite-, TicketWeb-style pages and local listings sites) | a site listing many venues' shows | the listing's rows; venue and city often in `data-` attributes | register `kind: aggregator` so venues' own listings win. Filter to your region per row. Read the venue from the row's data attribute, not the second link (17 "Unknown Venue" rows at BostonShows.org). Never default a missing time: on 2026-10-05, 300 BostonShows events sat at 20:00. Exclude venues you scrape directly. Prefer an official API where the platform offers one, under its terms. (BostonShows.org, Harvard Square) |

Others seen once, each read from its own data:

| Platform | Data | Note |
|---|---|---|
| Sidearm Sports (college athletics) | `/services/responsive-calendar.ashx?date=M/D/YYYY`, one week per call | read several weeks; dedupe multi-day events on the item id; the ISO `date` time can disagree with the displayed `time` (trust the displayed one); "TBA" is all-day. (Harvard Athletics) |
| Shopify (event "products") | the collection page | never slice the product list; a date-only card is all-day. (Lamplighter Brewing) |
| Theater for WordPress | `wp_theatre_event_startdate` / `_starttime` elements | read date and time from their own elements; flattened text runs them together. (Somerville Theatre) |
| A WordPress site's custom REST route | e.g. `/wp-json/calendar/v1/events` | look for `/wp-json/` routes before scraping HTML. (MIT Music & Theater) |
| A "Load More" endpoint | e.g. `/events/events_ajax/{offset}` returning HTML inside a JSON string | decode the JSON string, then parse; stop on a short page. (The Sinclair) |
