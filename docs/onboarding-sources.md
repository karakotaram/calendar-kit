# Onboarding sources

How a venue becomes a source. In Claude Code, `/add-source <url>` does this for
one venue (`.claude/skills/add-source/SKILL.md`) and `/onboard <file>` for a list,
in parallel (`.claude/skills/onboard/SKILL.md`). This page is the same procedure
for engineers doing it by hand, and the reference for when a step needs a
person. Where this page and the skill differ, the skill is what runs.

Read [access-policy.md](access-policy.md) first. Every source is read as an
honest, named client, politely, with a link back.

```bash
alias cal='.venv/bin/python -m src.cli'
```

Names used below: **Name** is the venue's own name ("The Rockwell"); it is
permanent in practice, because it is every event's `source_name`, part of every
id, and what monitoring watches. **slug** is its kebab case (`the-rockwell`) and
names the registry file; **slug_** is the same with underscores (`the_rockwell`)
and names the custom module, the test and the fixture folder.

## The procedure

| Step | Command | Outcome |
|---|---|---|
| 0. Is it new? | `cal sources --json` | not already registered under another URL |
| 1. Detect | `cal detect "<url>" --json`, then the site's `robots.txt` | an adapter, `blocked`, or none |
| 2. Register | `cal add --name "<Name>" --adapter <adapter> --url "<url>" ...` | `registry/<slug>.yaml` |
| 3. Or write a custom scraper | `cp src/scrapers/custom/_template.py src/scrapers/custom/<slug_>.py`, then `cal add --adapter custom ...` | a scraper developed against a saved page |
| 4. Run and record | `.venv/bin/python -m tests.sources.record "<Name>"` | `cal scrape`'s output, plus every response saved under `tests/fixtures/<slug_>/<YYYY-MM-DD>/` |
| 5. Test | `cp tests/sources/_template.py tests/sources/test_<slug_>.py` | a test replaying the capture offline |
| 6. Read the output | invariants, rejections, rows, shape | clean invariants; every rejection explained |
| 7. Spot-check | the venue's own event pages | two or three events agree on date, time and place |
| 8. Coverage | page 2, the API's total | "the venue lists about N through <date>; we read N" |
| 9. Places, CI, notes | edit `registry/<slug>.yaml` | venue defaults, `runs_in_ci`, dated notes |

Publishing comes after, separately ([Publishing](#publishing)).

**Request budget.** About five requests per venue for detection (`cal detect`
spends at most five by design, plus `robots.txt`), one recording run, up to
three spot-check requests and two coverage requests. Develop against saved
copies, never by re-running against the venue. A 401, 403, 429 or bot check
means stop, not retry: Cambridge tripped an IP-keyed block by testing
repeatedly from one machine. To fetch a page by hand, send the calendar's
user-agent (`src.config.USER_AGENT`), and save what you fetch under the
gitignored `tmp/` so you never fetch it twice.

### 0. Is it new?

Check `registry/` and `cal sources --json` for the same venue under another URL.
A listings site that already carries the venue does not make it a duplicate: the
venue's own source wins deduplication and is usually more complete. Social media
pages and ticketing marketplaces are not sources: they need logins or forbid
reading, and the venue's own site or feed is the thing to ask for.

### 1. Detect, then robots.txt

Start from the venue's events page, not its homepage. `cal detect` fetches it
once with the honest user-agent, scores each adapter's signatures against the
page, and confirms the likeliest with a few probe requests (five in all, at
most). Each match has `adapter`, `score`, `evidence` and `suggested_url`; the
best is first. No match exits 1.

| Top match | Do |
|---|---|
| `blocked` (a 401, 403 or 429, or a bot-check page) | register it as blocked ([below](#blocked-and-retired)) and stop |
| an adapter | step 2, with `suggested_url` as `--url` when it differs |
| several adapters | prefer the one that reads a data endpoint (an API, a feed) over page markup; `cal adapters` and [platform-cheatsheet.md](platform-cheatsheet.md) |
| none | step 3, a custom scraper |

Unless the venue is blocked, read `https://<host>/robots.txt` for the paths you
will read (one request). If the events pages or the feed are disallowed for all
user-agents or for ours, the venue is blocked, with `robots.txt` as the evidence.
A `Crawl-delay` is your minimum pause. Anything other than "allowed" goes in
`notes` ([access-policy.md](access-policy.md#robotstxt)).

### 2. Register with an adapter

```bash
cal adapters                                     # each adapter's params and their meaning
cal add --name "The Rockwell" --adapter tribe --url "https://therockwell.org" \
        --venue-name "The Rockwell" --street "255 Elm St" --city Somerville --zip 02144
```

`--param key=value` sets an adapter option (repeatable; values are YAML, so
`--param days=60` is a number). `--no-ci`, `--status` and `--notes` exist too;
leave venue and CI choices for step 9 if you are unsure, and edit the file
later. The file is validated as it is written and removed again if it does not
load; `cal add` refuses to overwrite. A listings site covering many venues:
after `cal add`, add `kind: aggregator` to the file by hand (there is no flag)
and leave out the `venue` block.

If no param makes the adapter right for this venue, the better fix is a new
param on the adapter with a test in `tests/adapters/`, because it fixes every
venue on that platform. Under `/onboard`, workers do not edit shared adapters:
they report the gap and the orchestrator decides.

### 3. No adapter: a custom scraper

Save the listing page once, then look for the data in the saved copy, best
first: the JSON the page itself fetches (`/wp-json/`, `/api/`, `?format=json`,
`__NEXT_DATA__`, `admin-ajax`, `evo-ajax`, `fetch(`); structured data (an iCal
feed, JSON-LD with `"@type": "Event"`, microdata; check it is complete, since a
list page's JSON-LD often holds only its first ten events); then the visible
HTML. If you find a feed in a format an adapter reads (`ical`, `jsonld`), go back
to step 2 with that adapter and the feed's URL.

```bash
cp src/scrapers/custom/_template.py src/scrapers/custom/<slug_>.py
cal add --name "<Name>" --adapter custom --url "<listing url>" \
        --venue-name "<Name>" --street "<street>" --city "<City>" --zip "<zip>"
```

`cal add --adapter custom` sets `module: src.scrapers.custom.<slug_>` and
`class: Scraper` unless you pass `--module` and `--class`, so keep the
template's class name. In the copy, set `SOURCE_NAME` to the registry `name:`
exactly: the scraper looks up its own `url` and `venue` defaults in the registry
by that name. Replace the docstring with this venue's story (where its data is
and why), and replace the selectors and parsing while keeping every rule the
template demonstrates:

- parsing is pure: `parse_listing()` turns one page's text into events, and all
  I/O is in `scrape_events()` through `BaseScraper`'s honest user-agent, so a
  test can replay a saved page;
- no date, no event; a date with no time is all-day at 00:00; time text that
  cannot be read ("7:30" with no AM or PM) skips the event; doors are not the
  start;
- a missing year is chosen by the printed weekday (`year_for_weekday()`); the
  template's `as_of` supplies only the candidate years, never the answer;
- every page is read; a sanity cap logs an error when reached;
- a refusal raises, so the run records a failure and keeps the source's events.

Develop against the saved page by calling `parse_listing()` on it, not by
re-running against the venue. A page that renders events only in a browser,
with no JSON behind it, extends `BasePlaywrightScraper`, adds `kind: playwright`
to the registry file, and waits with `wait_for_stable_count()`, never
`wait_for_selector()`. If only a visible browser gets through, stop: that is a
person's decision ([Visible-browser mode](#visible-browser-mode)).

Reuse `src/adapters/_common.py`: `clean()`, `strip_html()`, `on_the_minute()`,
`year_for_weekday()`, `venue_fields()`. `tests/test_docs.py` fails if a module in
`src/scrapers/custom/` is not registered; files starting with `_` (the template,
a parked `_draft_<slug_>.py`) are exempt.

### 4. Run and record

```bash
.venv/bin/python -m tests.sources.record "<Name>"
.venv/bin/python -m tests.sources.record "<Name>" --force    # replace today's capture
```

This is `cal scrape "<Name>"` (the same output, writing nothing to the calendar)
with every HTTP response the source reads saved, gzipped, under
`tests/fixtures/<slug_>/<YYYY-MM-DD>/` beside a `manifest.json`. One live run
gives you both the output to read and the test's fixture, however many pages,
APIs or feeds the source reads. Re-record only when a change makes the scraper
ask for different URLs.

The simple case: `cal scrape "<Name>" --save-fixture` saves only the registry
`url`'s page, which is enough only when that page is all the source reads. A
browser-driven source's traffic goes through the browser and is not recorded:
save the rendered page (`page.content()`, gzipped) under `tests/fixtures/<slug_>/`
and test the parse step on it with `read_fixture()`.

### 5. Test

```bash
cp tests/sources/_template.py tests/sources/test_<slug_>.py
.venv/bin/python -m pytest tests/sources/test_<slug_>.py -q
```

Fill in every `REPLACE`: the source name, the capture, its date, the count and
the first and last start from the recording run. The test replays the capture
with the `serve` fixture (`tests/sources/conftest.py`), which answers the
scraper's requests from the saved files and raises on anything it did not
record, and holds the output to the invariants. Write in its docstring what the
venue's own site listed on capture day ("about 60 shows through March, 3
pages"), so the next reader knows the count is the whole listing. Add a test for
any quirk you handled: an exclusion, an all-day case, a skipped notice. For a
custom scraper reading yearless dates, pin `as_of` as the template shows.

### 6. Read the output

From the recording run:

- **invariants** must be clean. `clock_stamped`, `timestamp_pileup`,
  `uniform_timestamp`: a date filled in from somewhere. `date_out_of_range`: a
  year bug. `nav_title`: the selector is on page chrome. `tz_aware`: an offset
  was not converted. Never ship any of them
  ([rules](rules-and-edge-cases.md#dates-and-times)).
- **rejections** (`valid N, rejected {...}`): every reason is a finding. "Too far
  in the past": an archive, or a source that looks alive and contributes
  nothing. "Description is too short": build one from facts ("<title> at
  <venue>"). "Looks like a scrape timestamp": fabrication.
- **the rows**: everything at 19:00 or 00:00 (an invented default?), dates or
  times left in titles ("10/9 Late Jam 12-1am"), "TBA" published as a time.
- **shape**: `events` and `date_span_days` against the venue (step 8);
  `max_events_one_timestamp` above 1 only if the venue really runs parallel
  sessions; `null_venue_rate` (step 9).

### 7. Spot-check against the venue

Pick two or three events: **one in the evening** (18:00 or later; a time-zone
error moves those to the next day first), one all-day if there are any, and one
after the next daylight-saving change if the listing reaches it. Fetch each
event's `source_url` once and compare the date, the start time and the place
with what the venue prints. Any disagreement stops everything until explained:
doors versus show, an offset written wrong by the platform, the wrong element, a
different year. "Close enough" is a wrong date.

Write each check into the test and into `notes`, dated:
`2026-10-06: "Late Set" Fri Oct 9 8:30 PM matches <url>`. Fixture tests prove a
parser is stable; only this proves it reads the page the way the venue means it.

### 8. Coverage

| Signal | Usually |
|---|---|
| a round count: 10, 12, 20, 25, 30, 50, 100 | a page size or a cap |
| the furthest date is weeks out; the venue lists months | page one only |
| "Next", "Load more", `?page=2`, `/page/2/`, `?p=2`, infinite scroll | paging not followed |
| `total_pages`, `total`, `page.total`, `X-WP-Total` in a payload | paging not followed to the end |
| tabs or categories on the listing | one tab read |
| a recurring series appears once | recurrences not expanded |

Look one level deeper (at most two requests) and record "the venue lists about
N through <date>; we read N" in the notes and the test. Then the other
direction: past events, members-only or internal events, closures, deadlines,
placeholders, cancelled or "RESCHEDULED:" notices, other cities. Filter per
event from the event's own fields, and test it
([S6, S7](rules-and-edge-cases.md#s6-read-every-page-and-make-any-cap-loud)).

### 9. Places, CI, notes

- **One venue**: the registry `venue` block, with the city spelled as
  `region.cities` in `calendar.config.yaml` spells it. **Many venues** (a
  listings site, a library system, a university, a city): no `venue` block; each
  event's place must come from the event, so check `null_venue_rate` and a sample
  of cities. An event's own place replaces the defaults entirely.
- **CI**: leave `runs_in_ci: true` unless the source needs a visible browser or
  there is evidence the venue blocks datacenter IP ranges. You cannot test
  GitHub's IPs from your machine: after the first CI run, a source that fails
  only in CI gets `runs_in_ci: false` and a note
  ([below](#sources-ci-cannot-reach)).
- **Notes**, dated: why this endpoint and not the page, what is excluded and
  why, the venue's own data errors published as given, the spot checks, the
  coverage numbers. Write for whoever opens the file the day the source breaks.

`cal add` will not overwrite, so edit `registry/<slug>.yaml` directly.

## Publishing

Onboarding never writes `data/events.json`. Under `/onboard`, workers run in
parallel and the events file is shared, so no worker runs `scrape.py` or
`cal repair`; the orchestrator runs `scrape.py` once, at the end, after the whole
test suite and `cal doctor` pass, and the gate decides what is written.

By hand, the same holds: finish onboarding, run the tests, then either let the
next daily scrape pick the source up, or run `.venv/bin/python scrape.py` once
yourself and read `cal runs -n 1` and `cal run <run-id>`. `cal repair` is for a
person repairing one existing source later
([operations.md](operations.md#repairing-one-source)), not for onboarding.

## Registry fields

One file per source, `registry/<slug>.yaml`, the slug in kebab case.
`registry/_example.yaml` is a working example of every field; its leading
underscore keeps it out of the registry. `tests/test_docs.py` checks that every
file parses, is named in kebab case, and that a `blocked` or `retired` source
has `notes`.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | the `source_name` every event carries; unique across the registry |
| `adapter` | yes, for a scraped source | an adapter name from `cal adapters`, or `custom` |
| `url` | yes | the venue's site or events page; adapters read from it |
| `params` | no | adapter options (see `cal adapters`) |
| `venue` | no | defaults for events that name no place: `name`, `street`, `city`, `zip`. An event's own address always wins |
| `module`, `class` | for `custom` | the dotted module path and class name |
| `kind` | no | `requests`, `playwright`, `aggregator` or `manual`. Defaults to the adapter's kind (`requests` for custom). Set `aggregator` for any listings site |
| `runs_in_ci` | no, default `true` | `false` if CI cannot run it: the venue blocks GitHub's IP ranges, or it needs a visible browser |
| `status` | no, default `active` | `active`, `blocked` or `retired`. Only `active` sources run |
| `notes` | when anything is unusual | why: the block, the quirk, who asked for an exclusion and when. `cal sources` prints it |

```yaml
# registry/the-rockwell.yaml
name: The Rockwell
adapter: tribe
url: https://therockwell.org
venue:
  name: The Rockwell
  street: 255 Elm St
  city: Somerville
  zip: "02144"
runs_in_ci: true
status: active
```

`kind` matters more than it looks: it is the run order and the deduplication
rank. Cambridge filed a listings site as `requests` and it took credit for about
50 venues' events
([U3](rules-and-edge-cases.md#u3-the-venues-own-listing-beats-an-aggregators)).

## Sources CI cannot reach

Some venues block the IP ranges GitHub Actions runs from; visible-browser
sources need a display. Register them `runs_in_ci: false`. Then:

- the daily CI scrape skips them and keeps their stored events;
- `scrape_local.py` runs exactly these, on a machine that can reach them, and
  replaces only the sources that produced events;
- `scripts/weekly_local_scrape.sh` runs `scrape_local.py` and pushes the result,
  weekly, from a launchd agent ([operations.md](operations.md#the-weekly-local-job)).

Without the weekly job they go stale silently: one Cambridge source went 96 days
without a refresh. Check `cal sources` for the last-scraped age.

Confirm a CI block before marking one: if `cal scrape` works locally but the
run record shows the source failing only in CI, it is the environment or the IP
range. A source failing everywhere is blocked, not CI-blocked.

## Blocked and retired

| Status | Meaning | Use when |
|---|---|---|
| `active` | run every scrape | normal |
| `blocked` | not run; its upcoming listings kept | the venue refuses access deliberately: a 403 to every client, a bot check that does not clear. Ask for a feed ([venue-outreach-email.md](venue-outreach-email.md)) |
| `retired` | not run; its listings dropped | the venue closed or stopped publishing, or another source already carries all its events |

Always fill `notes` with the evidence and the date ("403 on every path to every
client, from CI and a residential IP, 2026-09-01"). "Why is this one missing?"
is unanswerable six months later otherwise, and `tests/test_docs.py` fails a
`blocked` or `retired` file without notes.

**At onboarding**, one refusal is enough to record. Do not retry, change
anything, or try a browser to see whether it gets through: getting round a
refusal defeats protection the venue chose, and repeated probing turns a soft
block into a hard one. If the venue publicly offers a feed (an iCal link, a
documented API), try that one URL once; otherwise write the file by hand:

```yaml
# registry/<slug>.yaml
name: <Name>
url: <url>
status: blocked
runs_in_ci: false
notes: >-
  <YYYY-MM-DD>: HTTP 403 with a Cloudflare "Just a moment..." page to plain
  HTTP with our user-agent. Not retried. Outreach email drafted.
```

(`cal add --status blocked --notes "..."` writes an equivalent file, but it
requires an `--adapter`.) `cal sources` then lists it as blocked. Keep what the
outreach email needs: the venue, its events page, the date, what was refused
(or what `robots.txt` says), and a contact if a page you already fetched shows
one. `/onboard` drafts one email per blocked venue in its report, from
[venue-outreach-email.md](venue-outreach-email.md).

**For a source that was working and starts failing**, let the pipeline do its
job first: a source that fails keeps its still-upcoming events automatically, so
one bad night costs nothing. Mark it blocked once the failure is confirmed
deliberate and repeated, so the calendar stops sending requests a venue has said
it does not want.

What each status does to listings: a `blocked` source keeps its upcoming
listings (they age out as their dates pass) while you wait for a feed; a
`retired` source's listings are dropped at the next scrape. Drift stays quiet
about both (tests in `tests/test_gate.py`).

## Visible-browser mode

Some venues' bot protection refuses plain HTTP and any browser that announces
itself as headless, but serves an ordinary visible browser. Reading those means
opening a real window on someone's machine. **It is opt-in, per source, and an
organisational decision, not an engineering one**: see
[access-policy.md](access-policy.md#visible-browser-mode) for the terms and
Cambridge's history. Onboarding never chooses it: `/add-source` returns such a
venue as needs-human, and the owner decides.

If the decision is yes:

- an IndieCommerce store takes `params: {visible_browser: true}`; any other
  site needs a custom scraper on `BasePlaywrightScraper` with `headless=False`.
  Either way there is no `user_agent` override, so the browser sends its own,
  and no automation-hiding flags;
- the scraper calls `wait_past_challenge()` after navigating; it waits for an
  interstitial to clear on its own and raises `ScrapeRefusedError` if it does
  not. Nothing clicks, solves or interacts with a challenge;
- `runs_in_ci: false` (a window needs a display), and a `notes` line saying it
  runs in a visible browser, who decided, and when;
- read as little as possible per run. After a day of testing, both IndieCommerce
  bookstores Cambridge revived put follow-up pages behind a check that did not
  clear, so the adapter's `months` defaults to 1.

```yaml
# registry/porter-square-books.yaml (illustrative)
name: Porter Square Books
adapter: indiecommerce
url: https://www.portersquarebooks.com
params:
  visible_browser: true      # decided by <who>, <date>; see docs/access-policy.md
runs_in_ci: false
notes: Cloudflare refuses headless browsers; read in a visible window by decision of <who>, <date>
```
