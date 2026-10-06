# Operations

Runbooks for running, diagnosing, repairing and deploying the calendar. Each
rule mentioned is listed with its test in
[rules-and-edge-cases.md](rules-and-edge-cases.md); the reasons behind the
shape of the system are in [architecture.md](architecture.md).

Almost everything runs through `cal` (`src/cli.py`), one verb per layer:

```bash
alias cal='.venv/bin/python -m src.cli'

cal doctor [--live]       # what is wrong right now                       all layers
cal sources               # the registry: counts, furthest-out date, age   layer 0
cal adapters              # the platform adapters and their params         layer 1
cal detect <url>          # which adapter fits a venue's page              layer 1
cal add --name ... ...    # register a source (writes registry/<slug>.yaml) layer 0
cal scrape <name>         # run one source, show what it would publish     layer 1
cal check [<name>]        # invariants and drift on current data           layer 2
cal runs / cal run <id>   # recent runs; one run in full                   layer 3
cal diff [--live]         # what changed vs HEAD, or vs production         layer 5
cal repair <name>         # re-scrape one source and splice it in          layer 5
```

**Start with `cal doctor`.** It is the only command you need to remember.

Use `.venv/bin/python` explicitly rather than an activated shell; activation
does not survive between Claude Code tool calls.

---

## What runs where

| What | Where | When |
|---|---|---|
| Daily scrape | GitHub Actions, `.github/workflows/scrape.yml` | daily (the `cron` is UTC; set it to a small hour in your region); also by hand |
| Reader submissions | GitHub Actions, `.github/workflows/sync-submissions.yml` | daily, two hours after the scrape |
| Tests | GitHub Actions, `.github/workflows/tests.yml` | every push to `main` and every pull request |
| Local-only sources | `scripts/weekly_local_scrape.sh` on a Mac, via launchd | weekly |
| API | Railway, from `main` | redeploys on every push |
| Frontend | Vercel, from `main` (`frontend/`) | on push, and on the deploy hook after each published scrape |
| Event data | `data/events.json`, committed | written by the scrape and the sync, served by the API |

**Pushing to `main` deploys to production.** There is no staging. The daily job
commits to `main`, so pull before you work: your checkout is probably stale.

## The daily CI scrape

The workflow installs Python and Playwright's Chromium, runs `python scrape.py`,
and continues only if the gate passed:

1. every source with `status: active` and `runs_in_ci: true` runs, cheapest
   first, aggregators last;
2. events are validated, deduplicated, enriched and given ids;
3. the publish set adds user submissions, CI-skipped sources' stored events, and
   the still-upcoming events of any source that failed or returned nothing;
4. the gate decides; a block quarantines the run, opens an issue and exits
   non-zero, which stops the job before its commit step;
5. on a pass, everything changed under `data/` (events, fingerprints, the run
   record, the geocoder cache) is committed and pushed, rebasing and retrying
   up to three times if `main` moved; Railway redeploys, then the Vercel deploy
   hook rebuilds the frontend;
6. the run record, any quarantine and the log are uploaded as a workflow
   artifact (kept 30 days), pass or fail.

Inputs for a manual run (Actions → Daily scrape → Run workflow): `force`
(publish past a failing gate) and `drift_mode` (`report` or `enforce`; this is
`GATE_DRIFT`, default `report`).

## The weekly local job

Sources registered `runs_in_ci: false` never refresh in CI: the venue blocks
GitHub's IP ranges, or the source needs a visible browser window.
`scripts/weekly_local_scrape.sh` pulls, runs `scrape_local.py`, and commits and
pushes `data/events.json` (and the geocoder cache) only if they changed. It
refuses to run off `main` or over uncommitted changes in `data/`, `src/` or
`registry/`, drops its own commit rather than leave a conflict if CI pushed
meanwhile, and posts a macOS notification on failure. `scrape_local.py`
replaces only the sources that produced events, deduplicates against the rest
of the file, and refuses to write on an invariant error.

```bash
scripts/weekly_local_scrape.sh --dry-run    # scrape, leave the result uncommitted
scripts/weekly_local_scrape.sh              # the real thing
scripts/weekly_local_scrape.sh --install    # schedule it: Mondays at 10:00, via launchd
scripts/weekly_local_scrape.sh --uninstall  # stop that
```

Install it on a Mac that someone stays logged in to (a visible browser needs a
session), with the repo's `.venv` already created. The launchd label is
`org.<short_name>.scrape-local`, from `site.short_name` in lower case
(`org.kqedevents.scrape-local` for the shipped config); the log is
`logs/weekly_local_scrape.log`. If the Mac is asleep at 10:00, launchd runs the
job at the next wake.

```bash
launchctl kickstart gui/$(id -u)/org.<short_name>.scrape-local   # run it now
launchctl print gui/$(id -u)/org.<short_name>.scrape-local       # status
```

Check `cal sources` for each local-only source's last-scraped age. At
Cambridge, nothing ran this job for months and one source went 96 days without
a refresh.

## `cal doctor`

Prints every finding, errors first, and exits non-zero if there are errors:

- invariant violations and drift in `data/events.json`;
- a checkout behind `origin/main`, or uncommitted changes to the data;
- registered sources with no monitoring;
- registered sources contributing no events;
- the last run's gate decision, failed scrapers, and its age;
- with `--live`: whether `/health`, `/stats`, `/events/slim` and a filtered
  `/events` answer, and whether production serves the same event count as the
  local file. `--live` (here and in `cal diff --live`) probes `api.base_url`
  from `calendar.config.yaml`, so set that once the API is deployed.

During Cambridge's 2026-08-31 incident it would have printed, in about two
seconds, what took an hour to find by hand:

```
✗ City of Cambridge: 117 start times carry seconds/microseconds — a clock reading, not a listing
✗ City of Cambridge: 117 events share the exact start 2026-09-14T13:29:26.288025
✗ City of Cambridge: date_span_days 16 vs baseline 63 — reaching less far ahead
✗ GET /stats -> HTTPError: 500
! local checkout is 26 commits behind origin/main
```

## Reading a run record

```bash
cal runs              # newest first: run id, gate, events, scrapers ok/total, time, diff
cal run <run-id>      # one run in full
```

`data/runs/<run-id>.json` holds:

| Field | Tells you |
|---|---|
| `run_id`, `git_sha`, `is_ci`, `duration_s` | which code ran, where, how long |
| `scrapers[]` | per source: `status` (`ok`/`failed`), `returned`, `duration_s`, `error` |
| `counts` | `scraped` → `validated` → `deduplicated` → `final` |
| `rejected` | validation rejections by reason ("Event date is too far in the past": 7) |
| `fingerprints` | each source's shape this run |
| `gate` | decision, mode, reasons, violations, drifts |
| `diff` | added, removed, changed against what was published |

A source can go quiet three ways, and `cal run` tells them apart:

| You see | Cause | Fix |
|---|---|---|
| `failed` with an error | the venue changed or refused us | fix the adapter or scraper; if it is a deliberate block, see [access-policy.md](access-policy.md#when-a-site-blocks-you) |
| `ok` locally, `failed` only in CI | a missing browser in CI, or the venue blocks GitHub's IPs | fix the workflow, or set `runs_in_ci: false` |
| `ok` with events, but none published | validation rejected them all; read `rejected` | usually stale or misdated events |

The third is easy to miss: a source that returns only old events looks alive
and contributes nothing.

Reading drift: a fall in `events` or `date_span_days` with no code change means
the source broke (often a truncated read); a rise right after you fixed it means
the baseline is stale ([rebaseline](#rebaselining-drift-after-a-fix)); a jump
in `max_events_one_day` or `max_events_one_timestamp` means dates are collapsing,
which is usually a fabrication.

## Runbook: a reader reports a wrong date

The failure that reaches readers.

```bash
git pull
cal doctor --live
cal check "<source>"        # that source's invariants and drift
cal runs                    # find the latest run
cal run <run-id>            # what the scrape did for it
cal scrape "<source>"       # run it now, write nothing; compare with the venue's page
```

Signals:

- many events on one exact timestamp, or seconds in a start: a clock reading
  substituted for a date ([D1](rules-and-edge-cases.md#d1-never-fabricate-a-date-or-a-time));
- every event a few hours off: a time-zone conversion in the machine's zone, or
  a wrong offset ([D5](rules-and-edge-cases.md#d5-convert-instants-explicitly-never-in-the-machines-zone),
  [D6](rules-and-edge-cases.md#d6-when-a-source-states-a-time-twice-both-must-agree));
  try `TZ=UTC cal scrape "<source>"`;
- twelve hours off: a time read without its meridiem ([D9](rules-and-edge-cases.md#d9-a-time-with-no-meridiem-is-unreadable));
- a year off: a year guessed from the clock ([D8](rules-and-edge-cases.md#d8-a-missing-year-comes-from-the-printed-weekday-never-the-clock));
- a round default hour (7 PM, 8 PM, midnight): an invented time ([D7](rules-and-edge-cases.md#d7-a-date-with-no-time-is-all-day-never-an-invented-hour)).

Fix the parser so it skips rather than guesses, save a fixture of the page that
broke it, add a test that fails on the old code, then [repair](#repairing-one-source).

## Repairing one source

Re-scrape a single existing source and splice it in, leaving every other
source alone. Use it after fixing that source, without waiting for the daily
run. It is a person's tool: onboarding never runs it, because onboarding workers
run in parallel and `data/events.json` is shared (new sources are published by
one `scrape.py` run at the end of `/onboard`, or by the next daily scrape).

```bash
cal repair "<source>" --dry-run   # scrape, validate, dedupe, report; write nothing
cal repair "<source>"             # same, then write data/events.json
cal check
git add data/events.json && git commit -m "Repair <source>: ..." && git push
```

It validates, deduplicates within the source and against every other source
(a venue's own listing beats an aggregator's copy, so repairing a venue can
remove an aggregator's duplicate; user submissions always stay), runs the
invariants, and refuses to write on a violation. `--force` writes anyway; do not
use it to get past a fabricated date.

## Rebaselining drift after a fix

A source that returned 4 events while broken has a baseline of 4, and the
fixed 132 then reads as a "3300% duplicate explosion" for weeks. After any fix
that changes what a source returns:

```bash
cal check "<source>" --rebaseline
```

It forgets that source's fingerprint history; drift stays quiet for it until
three fresh passing runs accumulate. Commit `data/fingerprints.json` with the
fix. Do not rebaseline to silence drift you have not explained.

## When the gate blocks a run

Production is untouched. Everything you need was written:

```bash
cal runs                                  # the blocked run's id
cal run <run-id>                          # reasons, per-scraper detail, rejections
cat data/quarantine/<run-id>/report.txt   # the report the GitHub issue carries
```

In CI, `data/quarantine/` is in the workflow run's artifact rather than the
repo. The report names which check fired:

| Check | Tunable? | Usually means |
|---|---|---|
| catastrophic collapse | no | mass scraper failure (no browser in CI, a network problem), not bad data |
| invariant violation | no | a malformed event: a fabricated date, an offset, page chrome as a title |
| drift (only if `GATE_DRIFT=enforce`) | yes | one source's shape moved against its own baseline |

`data/quarantine/<run-id>/events.json` is the rejected output, kept as
evidence. Options, in order:

1. **Fix the cause**, then `cal repair` the affected source. The usual case.
2. **The change is real and large** (a venue posted its whole season): override
   ([below](#overriding-the-gate)).
3. **A drift threshold is wrong**: tune it in `src/quality/fingerprint.py` and
   say why in the commit. Never disable the gate.

## Overriding the gate

```bash
.venv/bin/python scrape.py --force     # locally
```

or run the workflow by hand with `force: true`. The run publishes and its
fingerprints become the new baseline.

Use it only when you have read the report and confirmed the data is right:
a drift alert about a change you made, or a venue's genuine season launch.

**Never** force past:

- **catastrophic collapse**: forcing it replaces the calendar with the few
  sources that worked;
- **`clock_stamped`, `timestamp_pileup` or `uniform_timestamp`**: these are
  fabricated dates, the one failure readers notice;
- **`tz_aware` or `date_out_of_range`**: wrong times or years;
- anything you have not read.

`GATE_MODE=report` evaluates without blocking at all. It is for testing the gate,
not for production.

## Before you push data

```bash
cal check                                  # invariants and drift
.venv/bin/python -m pytest tests/ -q       # includes invariants over data/events.json
cal diff                                   # exactly what changed vs HEAD
```

Ids are stable, so `cal diff` lists real additions, removals and field changes.
A normal day is tens of changes. Thousands means something rotated every id;
stop and find out why.

## Deploy and verify

```bash
.venv/bin/python -m pytest tests/ -q
cal check
git push origin main          # Railway redeploys in about two minutes
cal doctor --live             # production event count matches local, endpoints answer
```

`/health` reporting the old `total_events` means the deploy has not landed yet,
not that it failed. Then check the specific thing you changed on the site.

## Rolling back

Data and code deploy together from the same commit:

```bash
git revert <sha> && git push origin main
```

To roll back only the data and keep a code fix:

```bash
git checkout <good-sha> -- data/events.json
git commit -m "Restore events.json from <good-sha>" && git push origin main
```

Prefer either to hand-editing `data/events.json`; a hand edit of a file that
size is not reviewable. After restoring, the next scrape will try again; fix the
cause first or it will be blocked or repeat the bad data.

## The monthly audit

`/audit` in Claude Code, monthly or after any reader report of a wrong or
missing event (`/audit <source or kind>` audits part). Drift compares each
source with its own past, so a scraper that has always read page one has a
perfectly stable baseline of being wrong; only comparing with the venue's own
site finds it. For each source the audit runs `cal scrape`, checks coverage
against the venue (page-one-only reads, caps), spot-checks times including an
evening event, and looks for CI-versus-local gaps and staleness, then gives a
verdict (healthy, degraded or broken) with a proposed fix.

It is read-only: it changes no registry file, scraper, test or data, records no
baseline, and commits nothing. Its report is `audits/<YYYY-MM-DD>.md`. Cambridge's
2026-10-05 audit found six sources silently refused in CI, deduplication merging
back-to-back sessions, an aggregator taking credit for venues' events,
machine-zone times in CI, and a dozen page-one-only scrapers
([incident-log.md](incident-log.md)).

Fixes come afterwards, one at a time, once the owner has read the report: each
with a fixture, a test, a rebaseline and an [incident-log.md](incident-log.md)
entry.

## Known traps

Things that have cost real time. Add to this list whenever something surprises
you.

- **Your checkout is probably stale.** The daily job commits to `main`.
  `cal doctor` checks.
- **A source can look alive and contribute nothing.** Check the run record's
  `rejected` counts, not just the scraper's status.
- **A failed scrape must never delete events.** The pipeline keeps a failed
  source's upcoming events; do not route around `build_publish_set` or
  `scrape_local.py` with a hand-rolled script.
- **`kind` decides who wins deduplication.** Register every listings site as
  `kind: aggregator`.
- **`wait_for_selector` returns on the first match.** On a progressively
  rendered list it reads a partial page: Longfellow House gave 54 cards run alone
  and 4 under load. Read the JSON the page fetches, or use
  `wait_for_stable_count()`.
- **Playwright needs its browser installed in CI.** Cambridge's nine Playwright
  scrapers failed every night for weeks because the workflow never ran
  `playwright install`.
- **`Event.category` is a `str`, not an enum.** Both models set
  `use_enum_values`; `.value` raises `AttributeError`. That made `/stats` 500.
- **Mixed tz-awareness raises `TypeError` on comparison.** The model stores
  naive local time; a datetime from a query parameter needs converting first.
- **`datetime.fromtimestamp()` without a zone uses the machine's.** CI is UTC.
- **Never mint an event id by hand.** `Event.from_create()` only.
- **Never record a fingerprint for a run that failed the gate.** That is how a
  slow degradation becomes the baseline.
- **Never spoof a user-agent.** A contradiction between the user-agent and the
  browser's client hints is exactly what bot protection looks for.
- **Missing keys skip features silently.** Treat "the step was skipped" as a
  finding.
