---
name: add-source
description: Onboard one venue or events-calendar URL as a source for this calendar. Detects the website's platform, registers it with an adapter or a custom scraper, captures its traffic for an offline test, checks invariants, spot-checks events (one in the evening) against the venue's own page, checks coverage for page-one-only reads, and records notes; a venue that refuses access is registered as blocked. Use for /add-source <url>, "add this venue", "scrape this calendar", "can we get events from <url>", and for each URL a source-onboarder subagent handles.
argument-hint: <url> [| hint]
---

# /add-source

One URL in (`$ARGUMENTS`; anything after ` | ` is a hint from the owner). One of
these out:

- **added**: an active `registry/<slug>.yaml`, a test that replays the venue's
  real traffic, and events you have compared with the venue's own page;
- **blocked**: a `status: blocked` registry file with dated notes, and the facts
  an outreach email needs;
- **needs-human**: no registry entry, and a precise account of the decision or
  work a person must supply;
- **duplicate** or **skipped**, with the reason.

Never a source that "seems to work". Most broken sources in Cambridge's history
looked fine: they returned events, just not the right ones or not all of them.

## Before you start

- Rules: `CLAUDE.md`, and `docs/access-policy.md` on what may be fetched. The
  procedure by hand, for reference: `docs/onboarding-sources.md`. Where each
  platform keeps its events: `docs/platform-cheatsheet.md`.
- `cal` below means `.venv/bin/python -m src.cli` (aliases do not persist
  between tool calls).
- Names: **Name** is the venue's own name ("The Rockwell"); it is permanent in
  practice (every event's `source_name`, part of every id, what monitoring
  watches). **slug** is its kebab-case (`the-rockwell`) and names the registry
  file; **slug_** is the same with underscores (`the_rockwell`) and names the
  custom module, the test and the fixture folder.
- **Request budget per venue.** Detection is about 5 requests: `cal detect`
  spends at most 5 by design (the page plus up to four probes), so run it once
  per URL and do not probe further by hand; robots.txt is the one addition.
  After that: one saved copy of the listing if you write a custom scraper, one
  recording run (step 2 or 3), up to 3 spot-check requests, up to 2 coverage
  requests. Develop against saved copies, never by re-running against the
  venue. A 403, 401, bot check or 429 means stop, not retry: Cambridge tripped
  an IP-keyed block by testing repeatedly from one machine.
- To fetch a page by hand, always with the calendar's honest user-agent:

  ```bash
  .venv/bin/python - <<'EOF'
  import os, requests
  from src.config import USER_AGENT
  r = requests.get("<url>", headers={"User-Agent": USER_AGENT}, timeout=30)
  print(r.status_code, r.headers.get("content-type"), len(r.content))
  os.makedirs("tmp", exist_ok=True)                    # tmp/ is gitignored
  open("tmp/<slug_>-listing.html", "wb").write(r.content)   # keep it; never fetch twice
  EOF
  ```

  Never curl or fetch with a browser's user-agent string.

## 0. Is it new?

```bash
ls registry/
cal sources --json        # each source's name and url
```

Same venue already registered (same site and calendar, even under another
URL): **duplicate**, stop. A listings site that already carries this venue does
not make it a duplicate: the venue's own source wins deduplication and is
usually more complete. Social media pages (Facebook, Instagram) and ticketing
marketplaces are **skipped**: they need logins or forbid it, and the venue's own
site or feed is the source to ask for.

## 1. Detect

```bash
cal detect "<url>" --json
```

Each match has `adapter`, `score`, `evidence` and `suggested_url`; the best is
first. No match exits 1.

Unless the top match is `blocked`, check `https://<host>/robots.txt` for the
paths you will read before going on (one request, part of the budget;
`docs/access-policy.md#robotstxt`). If the events pages or the feed are
disallowed for all user-agents or for ours, do not read them: the venue is
**blocked**, with robots.txt as the evidence. A `Crawl-delay` is the minimum
pause between requests. Anything but "allowed" goes in notes.

| Top match | Do |
|---|---|
| `blocked` | the page refused us: go to **Blocked** below and stop |
| an adapter | step 2 with it; use `suggested_url` as `--url` when it differs |
| several adapters | read the evidence; prefer the one reading a data endpoint (an API, a feed) over page markup; `cal adapters` and `docs/platform-cheatsheet.md` |
| none | step 3, a custom scraper |

### Blocked

1. Do not retry, change the user-agent, or try a browser to see whether it gets
   through: getting around a refusal defeats protection the venue chose, and
   repeated probing is what turns a soft block into a hard one. At onboarding
   one refusal is enough to record; the policy's "confirm it over several runs"
   is for a source that was working and starts failing.
2. A sanctioned door is the exception: if the hint names a feed, or the venue
   publicly offers an iCal link or a documented public API, try that one URL
   once. If it answers, go to step 2 with the adapter for that format (`ical`,
   or whichever `cal detect "<feed url>"` names). Do not go looking through the
   site for one.
3. Write `registry/<slug>.yaml` by hand (`cal add` requires an adapter):

   ```yaml
   name: <Name>
   url: <url>
   status: blocked
   runs_in_ci: false
   notes: >-
     <YYYY-MM-DD>: <what was refused and how: "HTTP 403 with a Cloudflare 'Just a
     moment...' page to plain HTTP with our user-agent">. Not retried. Outreach
     email drafted.
   ```

4. `cal sources | grep -i "<Name>"` must list it as `! blocked`.
5. Keep for the outreach email: the venue, its events page, the date, what was
   refused (or what robots.txt says), and a contact address if a page you
   already fetched shows one. Run alone, draft the email from
   `docs/venue-outreach-email.md` and show it; under `/onboard`, the
   orchestrator drafts it.

## 2. Register with an adapter

```bash
cal adapters                                   # the adapter's params and what they mean
cal add --name "<Name>" --adapter <adapter> --url "<url>" \
        --param key=value \
        --venue-name "<Name>" --street "<street>" --city "<City>" --zip "<zip>"
```

`--param` repeats and takes YAML values (`--param days=60`). `--no-ci` and
`--notes` exist too; venue and CI are decided in steps 8 and 9, so leave them
off if unsure and edit the file later. `cal add` validates the file and refuses
to overwrite. A listings site covering many venues: pass `--kind aggregator`
and leave out the venue, so every venue's own listing outranks its copy.

Then run it. This is `cal scrape "<Name>"`, recording as it goes, so the one
live run also gives the test its fixture:

```bash
.venv/bin/python -m tests.sources.record "<Name>"
```

Re-record (`--force`) only when a params change makes the scraper ask for
different URLs. If no param makes the adapter right for this venue, do not
edit the adapter when other workers may be running: it is shared. Report the
gap ("tribe: needs a param to skip members-only events") and either write a
custom scraper (step 3) or return needs-human. Run alone, extending the
adapter with a new param and a test in `tests/adapters/` is the better fix: it
fixes every venue on that platform.

## 3. No adapter: a custom scraper

Save the listing page once with the fetch snippet above (and later, once, any
data endpoint you find), then look for the data in the saved copy, best first:

1. **The JSON the page itself fetches.** grep the page and its scripts for
   `/wp-json/`, `/api/`, `?format=json`, `__NEXT_DATA__`, `admin-ajax`,
   `evo-ajax`, `.json`, `fetch(`. It is what the venue's own page trusts.
2. **Structured data**: an iCal feed (`.ics`, `webcal:`, an embedded Google
   Calendar), JSON-LD with `"@type": "Event"`, microdata. Check completeness: a
   list page's JSON-LD often holds only the first ten events.
3. **The visible HTML.**

If 1 or 2 exists and an adapter reads that format (`ical`, `jsonld`), go back
to step 2 with that adapter and the feed's URL. Otherwise:

```bash
cp src/scrapers/custom/_template.py src/scrapers/custom/<slug_>.py
cal add --name "<Name>" --adapter custom --url "<url>" \
        --venue-name "<Name>" --street "<street>" --city "<City>" --zip "<zip>"
```

`cal add` sets `module: src.scrapers.custom.<slug_>` and `class: Scraper`. In
the copy: set `SOURCE_NAME` to the registry name exactly, replace the docstring
with this venue's story (where its data is and why), and replace the selectors
and parsing while keeping every rule the template demonstrates. Develop against
the saved page, not the venue:

```bash
.venv/bin/python - <<'EOF'
from datetime import date
from pathlib import Path
from src.scrapers.custom.<slug_> import parse_listing
html = Path("tmp/<slug_>-listing.html").read_text()
events, next_url = parse_listing(html, "<url>", venue={}, as_of=date.today(), source_name="<Name>")
for e in events: print(e.start_datetime, "ALL-DAY" if e.all_day else "", e.title, "|", e.venue_name)
print(len(events), "events; next page:", next_url)
EOF
```

Then one recording run, as in step 2.

A page that renders its events only in a browser, with no JSON behind it:
extend `BasePlaywrightScraper`, register it with `--kind playwright`, and
wait with `wait_for_stable_count()`. If only a *visible* browser gets through,
that is a human decision (`docs/access-policy.md`): return needs-human.

## 4. Capture and test

The capture is in `tests/fixtures/<slug_>/<YYYY-MM-DD>/` (step 2 or 3).

```bash
cp tests/sources/_template.py tests/sources/test_<slug_>.py
```

Fill in every `REPLACE`: `SOURCE`, `CAPTURE`, `CAPTURED_ON`, the count and the
first and last start from the recording run, and the docstring (capture date,
count, span, what the venue's site listed). The spot-check test is filled in at
step 6. Add a test for any quirk you handled: an exclusion, an all-day case, a
skipped notice. For a custom scraper reading yearless dates, pin `as_of` as the
template shows. A playwright source has no capture: save the rendered page and
test the parse step on it.

```bash
.venv/bin/python -m pytest tests/sources/test_<slug_>.py -q -p no:cacheprovider
```

## 5. Invariants

Read every section of the `cal scrape` output the recording run printed:

- **invariants** must say `✓ clean`. Anything else is a scraper bug; never
  ship it. `clock_stamped`, `timestamp_pileup`, `uniform_timestamp`: a date
  filled in from somewhere. `date_out_of_range`: a year bug. `nav_title`: the
  selector is on page chrome. `tz_aware`: an offset was not converted.
- **`valid N, rejected {...}`**: every rejection reason is a finding. "Description
  is too short": build one from facts ("<title> at <venue>"). "Event date is too
  far in the past": are you reading an archive? A source whose events are all
  old looks alive and contributes nothing. "Start datetime looks like a scrape
  timestamp": fabrication.
- **the rows**: everything at 19:00 or 00:00 (an invented default?), dates or
  times left in titles ("10/9 Late Jam 12-1am"), "TBA" published as a time.
- **shape**: `events` and `date_span_days` against the venue (step 7);
  `max_events_one_timestamp` above 1 only if the venue really runs parallel
  sessions; `null_venue_rate` (step 8).

## 6. Spot-check against the venue's own page

Pick two or three events: **one in the evening** (a start at 18:00 or later:
a time-zone error moves those to the next day first), one all-day if there are
any, and one after the next daylight-saving change if the listing reaches it
(fixed offsets that ignore DST are a real failure). Fetch each event's
`source_url` (one request each) and compare the date, the start time and the
place with what the venue prints.

Any disagreement stops everything else until explained: doors versus show,
an offset written wrong by the platform, the time read from the wrong element,
a different year. "Close enough" is a wrong date. If the event page has no time
in its HTML (rendered by script), compare against the JSON it fetches or the
listing, and say which.

Write each check into the test's spot-check docstring and assertions, and into
`notes`: `2026-10-06: "Late Set" Fri Oct 9 8:30 PM matches <url>`.

## 7. Coverage: did we get everything?

| Signal | Usually |
|---|---|
| a round count: 10, 12, 20, 25, 30, 50, 100 | a page size or a cap |
| the furthest date is weeks out; the venue lists months | page one only |
| "Next", "Load more", `?page=2`, `/page/2/`, `?p=2`, infinite scroll | paging not followed (a "Load more" is often a JSON or HTML endpoint taking an offset) |
| `total_pages`, `total`, `page.total`, `X-WP-Total`, `next` in a payload | paging not followed to the end |
| tabs or categories on the listing | one tab read |
| a recurring series appears once | recurrences not expanded, or only `occurrences[0]` read |

Look one level deeper (at most 2 requests): page 2, the "load more" endpoint,
the API's total. Record "venue lists about N through <date>; we read N" in the
notes and the test docstring.

Then the other direction. Are we publishing what a reader cannot attend? Past
events, members-only or internal events, closures ("Holiday:", "Closed"),
deadlines ("Apps due"), placeholders ("Example Event", "TBD vs TBD"),
cancelled or "RESCHEDULED:" notices, events in other cities. Filter per event
from the event's own fields, and test it.

## 8. City and venue defaults

- **One venue**: the registry `venue:` block (name, street, city, zip), with the
  city spelled as `region.cities` in `calendar.config.yaml` spells it.
- **Many venues** (a listings site, a library system, a university, a city
  calendar): no `venue:` block. Each event's own place must come from the
  event; check `null_venue_rate` and the city of a sample of events.
- An event's own place replaces the defaults entirely. A venue that names rooms
  ("Studio 7") would lose its street; the tribe adapter has a `rooms` param, and
  a custom scraper should keep the street and city for a room.
- Edit `registry/<slug>.yaml` directly; `cal add` will not overwrite.

## 9. Can it run in CI?

Leave `runs_in_ci: true` unless the source needs a visible browser or there is
evidence the venue blocks datacenter IP ranges. You cannot test GitHub's IPs
from here: after the first CI run, a source that fails only in CI while
`cal scrape` works locally gets `runs_in_ci: false` and a note. Such a source
only refreshes when the weekly local job runs (`docs/operations.md`).

## 10. Notes

`notes:` in the registry file, dated: why this endpoint and not the page;
what is excluded and why; the venue's own data errors that are published as
given; the spot checks; the coverage numbers. Write for whoever opens this file
the day the source breaks.

## Red flags from Cambridge's history

| Red flag | What happened | Instead |
|---|---|---|
| **page one only** | The Middle East published 20 of ~227 shows; The Sinclair 20 of 67 ("Load More" was an endpoint); Mad Monkfish 10 of 37 (its forward pager was labelled "Previous"); MIT Events read homepage JSON-LD, about 5% of the calendar; Mount Auburn's JSON-LD held its first 10 events | follow the platform's paging to the end (step 7) |
| **`[:30]` and page caps** | Comedy Studio kept an arbitrary 30 of 163 shows; Lamplighter, The Lily Pad and Portico were capped at 30; The Dance Complex's 20-page cap was 87% full and would have truncated in silence | no slice caps; a sanity cap logs an error when reached |
| **dateutil fuzzy parsing** | `parse(text, fuzzy=True)` gave midnight for "no time" and took a missing year from the clock; whole-sentence parsing dropped 4 of 11 Museum of Science events | match the date and the time explicitly; unreadable means skip |
| **`fromtimestamp` without a zone** | First Parish's 10:30 services went out at 14:30 from the UTC runner; Brattle's showtimes would move a day west of Pacific | `datetime.fromtimestamp(s, tz=timezone.utc)` then `on_the_minute()` |
| **the AM/PM attribute** | Cambridge.gov's `<time datetime>` was a 12-hour clock with no meridiem (5 PM written `05:00:00`); EventON writes `-4:00` all year, putting winter shows an hour early; one API's "timestamp" was wall clock labelled UTC | cross-check any attribute against the visible text; skip on disagreement |
| **doors vs show** | "Doors open at 7:30 pm; performance starts at 8:00 pm" was published at 7:30; an Assabet `doorTime` happened to be the start | the start is the show; cross-check a field named doors |
| **rollover years** | "today's" events rolled a year ahead; A.R.T. published summer 2027 dates in 2026; a show listed "Wednesday, September 23" went out on a Thursday the following year | the printed year; else `year_for_weekday`; else skip |
| **placeholder and closure rows** | "Holiday:" office closures at 12:00 am, an "Example Meeting Event", "All Closed" cards, an application deadline, TBD-sited playoff games, "RESCHEDULED:" notices left on the old date, a "CANCELLED" show, private bookings | skip them per event, and test the skip |
| **aggregator vs venue** | a listings site registered as an ordinary source took credit for about 50 venues' events and their links | `kind: aggregator`; the venue's own source wins |
| **`wait_for_selector` first match** | Longfellow House gave 54 tours run alone and 4 under load | read the JSON the page fetches, or `wait_for_stable_count()` |
| **"ok, 0 events"** | six sources sat for weeks returning a challenge page parsed as an empty listing; two returned `[]` on a failed load | raise on refusal; and confirm a real zero on the venue's site (First Parish's own JSON said `upcoming: 0`) |
| **one place for every event** | every A.R.T. performance was put in one building (wrong for 82 of 125); a national organisation published eleven other cities; alumni receptions in Miami and Seattle | the event's own place; filter out-of-region events |
| **descriptions** | "Description is too short" dropped a theater's whole run; `&#038;`, page-builder shortcodes and a ticket button's tracking script appeared in descriptions | build a description from facts; decode entities, strip markup |
| **a clock filter in the scraper** | `if start < datetime.now()` made saved fixtures lose events every week until tests failed | leave staleness to `EventValidator` |

## Result

Return or show this block, one per URL; `/onboard` builds its report from it.

```
url:        <url as given>
status:     added | blocked | needs-human | duplicate | skipped
source:     <Name>  (registry/<slug>.yaml)       or -
adapter:    <adapter> | custom | -
kind:       requests | playwright | aggregator
events:     <returned> returned, <valid> valid after validation
dates:      <first YYYY-MM-DD> to <last YYYY-MM-DD>
runs_in_ci: true | false
coverage:   <venue lists about N through <date>; we read N>
spot_checks:
  - <title>, <date time>: matches | differs (<how>)  <url>
test:       tests/sources/test_<slug_>.py passed | none (<why>)
requests:   <about how many requests this venue received from you>
notes:      <one or two sentences: anything unusual, anything a human must know>
outreach:   <blocked only: date, what was refused and how>
needs:      <needs-human only: the decision or work required, and what you learned>
```

A **needs-human** URL leaves no registry file behind. If a draft scraper is
worth keeping, park it as `src/scrapers/custom/_draft_<slug_>.py` (the leading
underscore keeps it unregistered and untested) and say so in `needs`; delete
its test. Typical reasons: only a visible browser gets through; a 429 or a
login; events only in images or PDFs; a spot check or coverage gap you could
not explain after three attempts; an adapter gap you were not free to fix.
