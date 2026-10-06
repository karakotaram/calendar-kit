# CLAUDE.md

Venue websites go in. One trustworthy JSON file comes out, an API serves it,
and the site in `frontend/` shows it as this calendar. Everything that makes the
calendar yours (name, region, time zone, cities, theme) is in
`calendar.config.yaml`, read through `src/config.py`.

**Every scraper will break, and most breakage is silent.** The system's job is
not to scrape correctly; it is to make breakage loud and non-shipping. Read that
sentence before any structural change: the design follows from it.

## Start here

```bash
alias cal='.venv/bin/python -m src.cli'
cal doctor          # what is wrong right now: run this first, always
```

One verb per layer: `cal sources` (what we scrape), `cal adapters` (the platform
readers), `cal detect <url>` (which one fits a page), `cal add ...` (register a
source), `cal scrape <name>` (run one, write nothing), `cal check` (invariants and
drift), `cal runs` / `cal run <id>` (what a scrape did), `cal diff [--live]`,
`cal repair <name>` (re-scrape one source and splice it in).

Skills: `/new-calendar` (write the config), `/onboard <urls-file>` (many venues
in parallel), `/add-source <url>` (one venue), `/audit` (monthly, read-only).

## Where to look

| Question | Document |
|---|---|
| Why is the system shaped this way? What are the layers? | [docs/architecture.md](docs/architecture.md) |
| Every rule, the incident behind it, and its test | [docs/rules-and-edge-cases.md](docs/rules-and-edge-cases.md) |
| How do I add a venue by hand? | [docs/onboarding-sources.md](docs/onboarding-sources.md) |
| Where does platform X keep its events? | [docs/platform-cheatsheet.md](docs/platform-cheatsheet.md) |
| How do I diagnose, repair, run locally, roll back? | [docs/operations.md](docs/operations.md) |
| How do I deploy? | [docs/deployment.md](docs/deployment.md) |
| What may we fetch, and what when a venue says no? | [docs/access-policy.md](docs/access-policy.md), [docs/venue-outreach-email.md](docs/venue-outreach-email.md) |
| What went wrong before? | [docs/incident-log.md](docs/incident-log.md) |
| The frontend | [frontend/CLAUDE.md](frontend/CLAUDE.md) |

## Rules that must not be broken

Each exists because of a specific failure in Cambridge Calendar, which this kit
was built from. The full list, with tests, is
[docs/rules-and-edge-cases.md](docs/rules-and-edge-cases.md).

1. **Never fabricate a date or a time.** No date: skip the event. A date with
   no time: `all_day=True` at 00:00. Time text you cannot read ("7:30", no AM or
   PM): skip. A missing year comes from the printed weekday
   (`year_for_weekday`), never from comparing with the clock.

   ```python
   # never
   start_datetime = parsed if parsed else datetime.now()
   start_datetime = parsed_date.replace(hour=19)        # "shows are usually at 7"
   # always
   if parsed is None:
       logger.warning(f"Skipping '{title}' - no parseable date ({url})")
       continue
   ```

   A missing event costs one reader one listing; a wrong date costs every reader
   that day's trust. `EventValidator` rejects a start carrying seconds: that
   precision only ever comes from a clock reading, and it is what put 117 events
   on one day in Cambridge.

2. **All times are naive wall clock in `region.timezone`.** Convert an instant
   explicitly: `on_the_minute(datetime.fromtimestamp(s, tz=timezone.utc))` or
   `to_local_naive(aware)`. Never `datetime.fromtimestamp(s)` without `tz=`: it
   uses the machine's zone, which is UTC in CI (10:30 services went out at
   14:30). Never hard-code a zone; `src/config.py` has it. When a page states a
   time twice (an attribute and the text), both must agree or the event is
   skipped: attributes have carried 12-hour clocks with no meridiem and offsets
   that ignore daylight saving.

3. **Scrapers are pure.** URL in, `EventCreate[]` out. No file writes, no
   globals, no clock (`if start < datetime.now()` filters belong to
   `EventValidator`; in a scraper they make saved fixtures parse differently
   each day). Purity is what makes offline tests possible.

4. **Never mint an event id by hand.** `Event.from_create()` only; ids are a
   content hash of `source_name | source_url | start | normalized title`.
   `uuid4()` would break the click history join, the diff, and the changelog.

5. **Add a source in exactly one place: its registry file.**
   `registry/<slug>.yaml`, written by `cal add`. `scrape.py`, the monitors, `cal`
   and the tests all derive from the registry. Two hand-kept lists drifted once
   and left four scrapers unmonitored; a scraper nothing registered ran for
   nobody for months.

6. **Identify honestly, and take no for an answer.** Every request carries
   `config.USER_AGENT` (it names this calendar and how to reach it); a browser
   sends its own user-agent. When a venue refuses (403, a bot check, 429), stop:
   register it `status: blocked` with dated notes and draft the outreach email
   ([docs/venue-outreach-email.md](docs/venue-outreach-email.md)). Visible-browser
   mode only on an explicit human decision, recorded in the notes
   ([docs/access-policy.md](docs/access-policy.md)).

7. **Three things block a publish, and only one is tunable.** Catastrophic
   collapse (the run would delete most of the calendar) and invariant
   violations are absolute. Drift is source-relative and governed by
   `GATE_DRIFT`. Do not make the first two configurable.

8. **No hand-typed counts in documentation.** A number that describes the data
   must be generated or checked by a test. Typed numbers rot.

## Politeness

Venues did not ask to be read. During detection, about 5 requests per venue at
most. Pace listing pages about a second apart; read a platform's data endpoint
rather than one page per event. Never retry against a challenge or a 429: an
Imunify360 block in Cambridge was tripped by repeated testing from one machine.
Develop against a saved capture (`python -m tests.sources.record`), not the
live site.

## Traps

Full list in [docs/operations.md](docs/operations.md#known-traps). The ones that
cost the most time:

- **Your checkout is probably stale.** The daily job commits to `main`.
  `cal doctor` checks.
- **"ok, 0 events" can be a refusal.** A challenge page parsed as an empty
  listing kept six Cambridge sources dead in CI for weeks. A refusal must raise;
  check that a zero is the venue's own zero.
- **Page one is not the listing.** Most of Cambridge's gaps were scrapers that
  read the first page, the first ten JSON-LD events, or `[:30]`. Follow the
  platform's paging to the end; a sanity cap logs an error when hit.
- **A source can look alive and contribute nothing.** Validation drops events
  over 30 days old and descriptions under 10 characters. Check `rejected` in
  `cal scrape` output and run records, not just the status.
- **A failed scrape must never delete events.** `build_publish_set` keeps the
  upcoming events of a source that failed or came back empty. Do not route
  around it with a hand-written script.
- **`kind` decides who wins deduplication.** Register a listings site
  `kind: aggregator`, or it takes credit for every venue's events.
- **`wait_for_selector` returns on the first match.** On a progressively
  rendered list it reads a partial page. Read the JSON the page fetches, or use
  `wait_for_stable_count()`.
- **`runs_in_ci: false` sources go stale silently** unless the weekly local job
  runs `scrape_local.py` ([docs/operations.md](docs/operations.md#the-weekly-local-job)).
- **`Event.category` is a `str`, not an enum.** `.value` raises.
- **Drift does not block yet** (`GATE_DRIFT=report`). Invariants do.

## Commands

Use `.venv/bin/python` explicitly; shell activation does not persist between
tool calls. Python 3.12 locally; production pins 3.11 (`runtime.txt`).

```bash
.venv/bin/python -m pytest tests/ -q                        # all tests, offline
.venv/bin/python -m pytest tests/sources/test_<slug_>.py -q # one source's tests
.venv/bin/python -m tests.sources.record "<Source Name>"    # cal scrape + save a capture for tests
.venv/bin/python scrape.py                                  # full scrape; gate decides whether it writes
.venv/bin/python scrape.py --force                          # publish past a failing gate
.venv/bin/python scrape_local.py                            # only the runs_in_ci: false sources
.venv/bin/python -m uvicorn src.api.main:app --port 8199    # API locally
VITE_API_BASE_URL=http://localhost:8199 npm --prefix frontend run dev   # site on :8080
.venv/bin/python -m flake8 --max-line-length=200 src/       # lint
```

## Structure

| Layer | Code | Data |
|---|---|---|
| Config | `calendar.config.yaml`, `src/config.py` | |
| 0 Sources | `src/sources.py` loads `registry/*.yaml` (files starting `_` are ignored) | `registry/` |
| 1 Scrapers | `src/adapters/` (one per platform), `src/scrapers/custom/` (one per venue) | `tests/fixtures/` |
| 2 Contract | `src/quality/invariants.py` (absolute), `src/quality/fingerprint.py` (drift) | `data/fingerprints.json` |
| 3 Run | `src/quality/run_record.py` | `data/runs/` |
| 4 Gate | `src/quality/gate.py` | `data/quarantine/` |
| 5 State | `src/utils/storage.py`, `src/models/event.py` | `data/events.json` |
| 6 Surface | `src/api/`, `frontend/` | |

Tests: `tests/sources/` (one module per source, replaying a capture),
`tests/adapters/` (one per adapter), engine tests at `tests/` top level, which
run against a demo copy of Cambridge's registry (`tests/fixtures/demo_registry/`)
so their meaning does not change as you add sources.

## Adding a source

Use `/add-source <url>`; it encodes the procedure and the red flags. In short:
`cal detect` → `cal add` (an adapter, or `custom` from
`src/scrapers/custom/_template.py`) → record a capture and write
`tests/sources/test_<slug_>.py` from `tests/sources/_template.py` → spot-check
two or three events, one in the evening, against the venue's own page → check
coverage against what the venue lists → venue defaults, `runs_in_ci`, notes.
Prefer an adapter: a fix to an adapter fixes every venue on that platform.
