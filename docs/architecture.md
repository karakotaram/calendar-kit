# Architecture

How the system holds together, why it is shaped this way, and where your
configuration enters it. Read this before making a structural change. Day-to-day
procedures are in [operations.md](operations.md); every rule mentioned here is
listed with its test in [rules-and-edge-cases.md](rules-and-edge-cases.md).

## What this system is

Many hostile HTML pages and feeds go in. One trustworthy JSON file comes out,
an API serves it, and a static frontend shows it to readers.

The kit is Cambridge Calendar's engine with everything Cambridge-specific moved
into configuration: the region, the time zone, the site's name, the theme, the
optional features, and the list of sources.

## The one hard problem

**Every scraper will break, and most breakage is silent.** Venues redesign
pages, add bot checks, rename CSS classes and change date formats without
warning. A scraper that worked yesterday returning garbage today is the normal
case. No amount of care in one scraper changes that.

So the system's job is not "scrape correctly". It is:

> Make breakage **loud** and **non-shipping**.

Loud: a failure produces a specific, findable signal instead of a plausible
wrong answer. Non-shipping: a run that fails its checks does not reach readers.
When a design decision below seems arbitrary, this is why it exists.

### The failure that defined the design

On 2026-08-31 Cambridge's City of Cambridge scraper lost its browser a third of
the way through a run, fell back to a clock reading for every event it could no
longer date, and put 117 events on one day (2026-09-14) with an identical
microsecond timestamp. It shipped. A reader emailed.

The health monitor of the day saw 359 events against a recent average of 250.6
and called the run healthy. What was true of that run, next to the same source
after the fix (dated history):

| metric | shipped (broken) | after fix | would have caught it |
|---|---|---|---|
| events | 359 | 1,075 | no: the count went **up** |
| date span (days) | 16 | 63 | yes, against its own baseline |
| most events on one day | 124 | 40 | yes, against its own baseline |
| most events on one **timestamp** | 117 | 8 | yes, absolute invariant |
| clock-stamped start times | 117 | 0 | yes, absolute invariant |

Four signals were available; two needed no history. The monitor watched the one
metric that pointed the wrong way. **The system measured volume when the thing
that breaks is shape.**

## The layers

Each layer owns one kind of fact, has one source of truth for it, and can be
checked against the layer below. A question always has one place to go: "what
do we scrape?" is layer 0; "why is this event wrong?" is layer 3; "why did this
ship?" is layer 4.

| # | Layer | Owns | Source of truth | Invariant |
|---|---|---|---|---|
| — | **Config** | what makes this calendar yours | `calendar.config.yaml`, read by `src/config.py` | code never hard-codes a regional fact |
| 0 | **Registry** | which sources exist and how each is reached | `registry/*.yaml`, read by `src/sources.py` | every source appears once; everything else derives from it |
| 1 | **Scrapers and adapters** | page or feed → `EventCreate[]` | `src/adapters/`, `src/scrapers/custom/` | pure: URL in, events out; no writes, no clock |
| 2 | **Contract** | what a valid event and a plausible run look like | `src/utils/validator.py`, `src/quality/invariants.py`, `src/quality/fingerprint.py` | invariants are absolute; drift is source-relative |
| 3 | **Run records** | what one scrape actually did | `data/runs/<run-id>.json` via `src/quality/run_record.py` | written for every run, never edited |
| 4 | **Gate** | whether a run may ship | `src/quality/gate.py` | a run that breaks the contract quarantines instead of publishing |
| 5 | **State** | the current set of events | `data/events.json` via `src/utils/storage.py` | stable ids, deterministic order, diffable |
| 6 | **API** | what consumers can ask | `src/api/` | derived from state, never a second copy of it |
| 7 | **Frontend** | what readers see | `frontend/` | reads the API; shows stored wall-clock time as is |

### Config: what makes it yours

`calendar.config.yaml` holds the site name, the region (time zone, state,
default city, the cities shown as quick views, map centre), which features are
on, the submissions form, the brand theme and the API's URL. `src/config.py`
reads it once (or the file named by `CALENDAR_CONFIG`) and exposes constants;
code asks there instead of hard-coding. Cambridge had "Eastern" written into the
model, the validator, the API and the page builder; the kit has it in one line.

Secrets never go in this file. They are environment variables
([deployment.md](deployment.md)).

### Layer 0: the registry

One YAML file per source in `registry/`, loaded by `src/sources.py` into frozen
`Source` records. `scrape.py` runs what is listed, `scrape_local.py` runs the
subset marked `runs_in_ci: false`, the CI monitor watches it, and `cal`
enumerates it. Adding a source means adding one file (`cal add`); nothing else
keeps a copy of the list.

Cambridge kept its list twice, in the orchestrator and in the CI monitor, and
they drifted: four scrapers ran daily with no monitoring, and one complete
scraper (Longfellow House) was never wired in at all and produced nothing for
months. Nobody was careless; two hand-kept lists of one fact drift. The fix was
deleting one of them. One file per source, rather than one list, is the kit's
addition: parallel onboarding (several people, or several Claude Code workers
running `/onboard`) cannot conflict.

`kind` sets run order and deduplication rank: `requests`, then `playwright`,
then `aggregator`, then `manual`. Plain HTTP runs first; aggregators run last
and lose every merge to a venue's own listing. `User Submitted` is built in, of
kind `manual`, and always preserved.

### Layer 1: scrapers and adapters

A scraper is a function from a URL to events. It must not write files, mutate
globals, or consult the clock. Purity is what lets its tests run offline against
saved pages.

The kit adds **platform adapters** (`src/adapters/`). Most venue sites run on
a handful of platforms, and each platform publishes events the same way for
every venue on it, so a reader for The Events Calendar, Squarespace, Localist
and so on is written once and configured per venue by a registry file's
`adapter:` and `params:`. A venue on no known platform gets a custom scraper in
`src/scrapers/custom/`, registered with `adapter: custom`. `cal detect <url>`
says which applies; [platform-cheatsheet.md](platform-cheatsheet.md) describes
each platform.

The clock rule is absolute: a scraper that cannot read a date skips the event.
A missing event costs one reader one listing; a wrong date costs every reader
that day's trust.

### Layer 2: the contract

Two tiers, and the distinction matters more than any one rule.

**Invariants** are absolute (`src/quality/invariants.py`). They hold for every
event from every source, need no history, and a violation fails the run:
required fields present, ids unique, no sub-minute start times, no offsets, no
more than 20 events from one source on one exact timestamp, no source with most
of its output on one timestamp, dates within two years either side, no
navigation text as a title.

**Drift** is source-relative (`src/quality/fingerprint.py`). Each source's
output is fingerprinted on every run (count, date span, most events on one day
and one timestamp, title and image variety, venues, description length, null
venues, earliest and latest start) and compared with that source's own last 30
passing runs.

A global threshold is actively harmful here. Against Cambridge's healthy data,
naive global rules flagged 9 of 24 sources: Brattle Theatre for having one venue
(it is one cinema), A.R.T. for 11 distinct titles across 156 events (11
productions), City of Cambridge for repeating titles (weekly story times). A
monitor that cries wolf is worse than none, because it trains its reader to skip
it. Source-relative baselines flag "Brattle suddenly has 1 venue when it always
had 12" and stay quiet on "Brattle has 1 venue, as always". The longer a source
runs, the better the system knows its normal.

`EventValidator` (`src/utils/validator.py`) sits in front of both: it cleans
each scraped event, fills city and state from config, rejects short titles and
descriptions, clock-stamped times and dates too far past or ahead, and counts
every rejection by reason for the run record.

### Layer 3: run records

Every scrape writes `data/runs/<run-id>.json`: each scraper's status, duration,
yield and error; the validator's rejections by reason; every source's
fingerprint; the gate decision; the diff summary. Ninety are kept. Diagnosis is
reading, not archaeology: the 2026-08-31 root cause was recovered by doing
arithmetic on a corrupted timestamp, because nothing about the run survived it.

### Layer 4: the gate

One decision between "the scraper finished" and "readers see it". Three checks:

| check | measured against | tunable? |
|---|---|---|
| **catastrophic collapse**: under 50% of published events, or under 60% of contributing sources | the data being replaced | no |
| **invariants** | nothing; absolute | no |
| **drift** | each source's own baseline | `GATE_DRIFT=report` (default) or `enforce` |

Pass, and the run is written and its fingerprints become the new baseline.
Fail, and it is written to `data/quarantine/<run-id>/`, an issue is opened, the
process exits non-zero (which stops the workflow before its commit step), and
production stays on the last good data. `--force` overrides; `GATE_MODE=report`
evaluates without blocking.

The collapse check exists because a rehearsal caught the gate passing a run that
would have replaced 2,974 events with 232: every scraper had failed, and the
"source disappeared" findings were drift, which only reported. Losing most of
the calendar must never depend on a tunable. Drift reports by default because a
mistuned baseline blocks good data; it is also self-tuning, silent until a source
has three runs of history.

Stale data is a much smaller harm than wrong data. A calendar a day behind is
mildly annoying; a calendar confidently showing the wrong day is what makes
someone stop using it.

### Layer 5: state

`data/events.json`, written only through `src/utils/storage.py`. Two properties:

**Stable identity.** An id is a hash of `source_name | source_url |
start_datetime | normalised title`, minted only by `Event.from_create()`. Venue,
description, image and category are excluded on purpose: those are fields we
expect to get better at extracting, and including them would rotate every id the
next time a scraper improved. A rescheduled event gets a new id, which is
correct: it is a different occurrence. Cambridge's random ids churned 88% a day,
which broke click history, Editor's Picks and the git diff.

**Deterministic order**, by `(start_datetime, id)`. With stable ids, the daily
diff is a changelog and `git log -S<id>` answers "when did this event change?".

The file is committed. Data and code deploy together from one commit, so
rollback is a git operation ([operations.md](operations.md#rolling-back)).

### Layer 6: the API

`src/api/` (FastAPI) loads `data/events.json` and serves it: the full and slim
event lists, one event, search, a per-event `.ics`, sources, categories, stats,
health and Editor's Picks. Optional pieces switch on from config and the
environment: chat over upcoming events (`features.chat`, `ANTHROPIC_API_KEY`) and
the Editor's Picks admin (`features.editors_picks`, `ADMIN_TOKEN`). The API is a
projection of state and must never become a second place a fact lives.

### Layer 7: the frontend

`frontend/` is a React and Vite site, themed per `brand.theme`, deployed to
Vercel. It reads the API and displays times exactly as stored: local wall clock
for the region, never converted to the viewer's zone, with no clock time shown
for all-day events. Vercel rebuilds when the scrape commits new data, through a
deploy hook ([deployment.md](deployment.md#the-vercel-deploy-hook)).

## Data flow

```
calendar.config.yaml ── src/config.py ─────────────────── zone, region, user-agent
        │
registry/*.yaml ── src/sources.py ─────────────────────── layer 0
        │
        ▼
adapters / custom scrapers ── EventCreate[] ───────────── layer 1  (USER_AGENT, zone)
        │
        ▼
validate ── clean, fill city/state, reject and count ──── layer 2  (zone, cities, state)
        │
        ▼
deduplicate ── within a source by identity,
        │       across sources by similarity, venue beats aggregator
        ▼
enrich ── categories, family-friendly, geocoding (non-fatal)
        │
        ▼
identify ── content-hash ids ──────────────────────────── layer 5
        │
        ▼
publish set ── + submissions, CI-skipped and failed sources' kept events,
        │        reconciled against this run
        ▼
fingerprint + invariants ──────────────────────────────── layer 2
        │
        ▼
GATE ── vs. the data being replaced and each source's baseline ── layer 4
        │                                │
      pass                             fail
        │                                │
        ▼                                ▼
data/events.json (sorted)          data/quarantine/<run-id>/ + issue
record fingerprints                production unchanged
        │
        ▼                          every run: data/runs/<run-id>.json ── layer 3
git push ── Railway redeploys the API ─────────────────── layer 6
        │
        ▼
Vercel deploy hook ── frontend rebuilds ───────────────── layer 7  (theme, features)
```

User submissions enter separately: `sync_user_events.py` reads the Google Form's
response sheet (`submissions.sheet_id`) and adds them as `User Submitted`, which
every scrape preserves.

## Where config enters

| Setting | Read by | Effect |
|---|---|---|
| `site.short_name`, `site.url`, `site.contact_email` | `src/config.py` → `USER_AGENT` | every request names the calendar and how to reach you |
| `site.name`, `site.tagline` | API, frontend, logs | masthead, page titles |
| `region.timezone` | `src/models/event.py`, validator, API, frontend | the zone every time is stored and shown in |
| `region.state`, `region.default_city` | validator | filled only when an event's own address names none |
| `region.cities`, `map_center`, `map_zoom` | frontend | quick views and map |
| `features.*` | API, frontend | map, chat, submissions, Editor's Picks on or off |
| `submissions.form_url`, `sheet_id` | frontend, `sync_user_events.py` | "Submit an event" link and its sheet |
| `brand.theme` | frontend | `frontend/src/themes/<theme>` |
| `api.base_url` | frontend | where the site fetches events |
| `registry/*.yaml` | `src/sources.py` | what is scraped and how |
| `GATE_DRIFT`, `GATE_MODE` (env) | `src/quality/gate.py` | whether drift blocks; whether anything blocks |

## Failure taxonomy

Every way the system has broken or can break, and what notices.

| # | Failure | Volume signal | Caught by |
|---|---|---|---|
| 1 | Silent partial collapse: a source loses a subset (pagination, a crashed browser) | drops, not to zero | drift: `events`, `date_span_days` |
| 2 | Date fabrication: clock substituted for a missing date | flat or **up** | invariants: `clock_stamped`, `timestamp_pileup`, `uniform_timestamp` |
| 3 | Field collapse: descriptions become boilerplate, titles become navigation | flat | invariant `nav_title`; drift on distinct ratios and description length |
| 4 | Time-zone drift: mixed aware and naive, or machine-zone conversion | flat | invariant `tz_aware`; the model's converter; fixture tests under other zones |
| 5 | Duplicate explosion | up | drift: `events` up, `distinct_titles_ratio` down |
| 6 | Total source death | zero | drift: "returned nothing"; `ScrapeRefusedError`; collapse if widespread |
| 7 | Staleness: a source stops publishing new events | flat | drift: `latest_start` moving backwards; `cal sources` last-scraped age |
| 8 | Semantic drift: selectors match but mean something else | flat | partly: fixtures catch structural change; the monthly audit's live spot checks catch the rest |

Six of eight are invisible to a count, which is why the contract measures shape.

## Design principles

Each exists because something happened.

1. **Never fabricate.** A missing value is a missing value.
2. **Measure shape, not volume.** The metric that moves in a real failure is
   rarely the count.
3. **Identity is content-derived and stable.** Nothing accumulates on a value
   that changes daily for no reason.
4. **Every assertion is executable or derived.** No hand-typed counts describing
   data in prose; a document's claim is tested or generated.
5. **One registry per fact.** Two hand-kept lists of one thing drift.
6. **Runs are immutable and inspectable.** Debugging is reading.
7. **Failing loud beats shipping quiet.** A blocked run costs a day of
   staleness; not having a gate cost a reader's trust.
8. **Repair is a first-class verb.** `cal repair` exists because the first
   repair was a throwaway script that the next person would have rewritten.
9. **Scrapers are pure.** That is what makes offline tests possible.
10. **Degrade visibly.** Graceful and silent is how a broken run passes every
    check it has.
11. **Configuration, not code, makes it yours.** A regional fact written into
    code is a fact the next calendar has to find and change.

### The accretion loop

The system should end every incident smarter than it started.

| after an incident | keep | so that |
|---|---|---|
| a scraper broke | a fixture of the page that broke it, and a test | the bug cannot return silently |
| a run was bad | its run record and quarantine | the diagnosis is reading |
| a source was fixed | a reset baseline | drift knows its new normal |
| a fix was found | a runbook entry in [operations.md](operations.md) | the next person executes instead of re-deriving |
| a rule was learned | an entry in [rules-and-edge-cases.md](rules-and-edge-cases.md) and [incident-log.md](incident-log.md), with its test | the fact cannot rot into a lie |

## Where each layer lives

| Layer | Code | Data | CLI |
|---|---|---|---|
| Config | `src/config.py` | `calendar.config.yaml` | — |
| 0 Registry | `src/sources.py` | `registry/` | `cal sources`, `cal add` |
| 1 Scrapers | `src/adapters/`, `src/scrapers/`, `src/detect.py` | `tests/fixtures/` | `cal scrape`, `cal detect`, `cal adapters` |
| 2 Contract | `src/utils/validator.py`, `src/quality/invariants.py`, `src/quality/fingerprint.py` | `data/fingerprints.json` | `cal check` |
| 3 Run | `src/quality/run_record.py` | `data/runs/` | `cal runs`, `cal run <id>` |
| 4 Gate | `src/quality/gate.py` | `data/quarantine/` | runs inside `scrape.py` |
| 5 State | `src/utils/storage.py`, `src/models/event.py` | `data/events.json` | `cal diff`, `cal repair` |
| 6 API | `src/api/` | — | `cal doctor --live` |
| 7 Frontend | `frontend/` | — | — |

`cal doctor` spans all of them, which is why it is the first thing to run.
