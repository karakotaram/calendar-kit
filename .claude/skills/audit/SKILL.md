---
name: audit
description: Audit every registered source against the venue's own website - coverage (page-one-only reads, caps), a spot check of times including an evening event, CI-versus-local gaps and staleness - and write a read-only report with a verdict per source (healthy, degraded or broken) and a proposed fix for each problem. Fans out to subagents by source kind. Recommended monthly and after any reader report of a wrong event. Use for /audit, "audit the sources", "check the scrapers against the venues", "are any sources silently dead?".
argument-hint: "[source name or kind, to audit only part]"
---

# /audit

Monitoring compares each source with its own past, so a scraper that has always
read page one has a perfectly stable baseline of being wrong. Only comparing
with the venue's own site finds it. Cambridge's audit on 2026-10-05 did that and
found six sources that had sat for weeks returning a refusal page read as "ok,
0 events", a dozen page-one-only scrapers (one publishing 20 of about 227
shows), times that depended on the machine's time zone, deduplication merging
back-to-back sessions, and a listings site taking credit for venues' events.
None of it had raised an alarm.

**This audit is read-only.** It changes no registry file, scraper, test or data,
records no baseline, and commits nothing. `cal scrape` writes nothing. It
writes only its report, `audits/<YYYY-MM-DD>.md`, and scratch files in the
gitignored `tmp/`. Fixes come afterwards,
one at a time, each with a fixture, a test and an entry in
`docs/incident-log.md`, once the owner has read the report.

Run it monthly (put it in the owner's calendar, or schedule it), and after any
reader report of a wrong or missing event. `$ARGUMENTS` may name one source or
one kind (`requests`, `playwright`, `aggregator`, `local`) to audit part.

`cal` means `.venv/bin/python -m src.cli`.

## Politeness

One `cal scrape` per source (its normal requests), and beyond that at most
about 5 requests per venue: the listing, one deeper page or total, two or three
event pages for spot checks. The honest user-agent always. A refusal is
recorded, not retried. Never two workers on the same host.

## 1. The state of things

```bash
git fetch -q origin && git status -sb | head -1     # behind origin? the daily job commits to main
cal doctor
mkdir -p tmp audits
cal sources --json > tmp/audit-sources.json
cal check
```

If the checkout is behind, say so in the report and audit what production
serves: pull only if the owner agrees (it changes the working tree).

What each source did in recent runs, CI and local side by side:

```bash
.venv/bin/python - <<'EOF'
from collections import defaultdict
from src.quality.run_record import recent
history = defaultdict(list)
for run in recent(30):                       # newest first
    where = "CI" if run.get("is_ci") else "local"
    for s in run.get("scrapers", []):
        history[s["source"]].append(f"{run['run_id'][:10]} {where} {s['status']} {s.get('returned', 0)}"
                                    + (f" ({(s.get('error') or '')[:60]})" if s.get("error") else ""))
for name in sorted(history):
    print(name); print("   " + "\n   ".join(history[name][:8]))
EOF
```

## 2. Split the work by kind

From `tmp/audit-sources.json`, group the active sources:

| Group | Sources | What is special |
|---|---|---|
| requests | `kind: requests`, `runs_in_ci: true` | the bulk; page-one reads and caps hide here |
| playwright | `kind: playwright` | refusals read as empty pages; `wait_for_selector` partial reads |
| aggregator | `kind: aggregator` | do they take credit for venues that have their own source? out-of-region events? |
| local | `runs_in_ci: false` | staleness: when did the weekly local job last refresh them? |

Blocked and retired sources are not fetched: list them in the report with the
date and reason from their notes, and flag any blocked one whose notes say the
venue has since replied, as a candidate for `/add-source`.

Split each group into chunks of about six sources, keeping same-host sources
in one chunk, and give each chunk to a subagent (Agent tool,
`subagent_type: "general-purpose"`). Launch them in parallel, a wave of up to
six at a time. Each one's prompt is the worker brief below, plus its sources'
rows from `tmp/audit-sources.json` and their run history from step 1.

## 3. Worker brief (pass this verbatim)

> You are auditing event sources for a community calendar, read-only. Do not
> edit, create or delete any file; do not run `scrape.py`, `scrape_local.py`,
> `cal repair`, `cal check --rebaseline` or git. Use
> `.venv/bin/python -m src.cli` for `cal`. Fetch pages only with the calendar's
> user-agent:
>
> ```python
> import requests; from src.config import USER_AGENT
> r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
> ```
>
> At most about 5 requests per venue beyond one `cal scrape`. A 403, 429 or bot
> check: record it and stop for that venue.
>
> For each source:
>
> 1. **What we produce now**: `cal scrape "<name>"`. Note returned, valid and
>    the rejection reasons, the first and last start, the invariants line, and
>    anything odd in the rows (everything at 19:00 or 00:00, dates in titles).
> 2. **What we publish**: the row you were given (events, furthest date, last
>    scraped) and the run history (failing in CI only? `ok` with 0?).
> 3. **What the venue lists**: read the scraper to see where it reads from
>    (`registry/<slug>.yaml`, then the adapter in `src/adapters/` or
>    `src/scrapers/custom/`). Fetch the venue's own listing, and one level
>    deeper where it pages (page 2, a "load more" endpoint, an API's total).
>    How many upcoming events does the venue list, and how far ahead?
> 4. **Spot check**: two events, one starting at 18:00 or later. Open each
>    event's own page and compare date, start time and place with ours.
> 5. **Verdict**, by these definitions:
>    - **broken**: contributes nothing while the venue lists events; a refusal
>      recorded as ok; any spot check with a wrong date or time; an invariant
>      violation; failing in every recent run; or a local-only source not
>      refreshed in over 30 days.
>    - **degraded**: we read clearly less than the venue lists (furthest date
>      short of the venue's, a round count, an unfollowed next page); events
>      at the wrong venue or city; unexplained rejections; failing in CI only
>      or intermittently; a local-only source not refreshed in over 7 days;
>      events a reader cannot attend (closures, deadlines, members-only,
>      other cities).
>    - **healthy**: coverage matches the venue, spot checks match, fresh. Zero
>      events is healthy when the venue itself lists none.
> 6. **Proposed fix**, concrete: "follow the API's total_pages; it reports 4
>    pages and we read 1", "convert data-time from UTC, it reads the machine's
>    zone", "set runs_in_ci: false; fails only in CI since <date>". Not "look
>    into it".
>
> Return one block per source:
>
> ```
> source:     <name> (<adapter>, <kind>, ci: <true|false>)
> verdict:    healthy | degraded | broken
> published:  <events>, furthest <date>, last scraped <age>
> scrape now: <returned> returned, <valid> valid, <first> to <last>; rejected: <reasons or none>; invariants: <clean | ...>
> venue:      lists about <N> upcoming through <date> (<how you know: page count, API total>)
> spot check: <title> <date time> ours vs venue: match | differs (<how>)  <url>
>             <title> <date time> ...
> ci/local:   <from the run history>
> evidence:   <the facts behind the verdict>
> fix:        <concrete proposal, or none>
> requests:   <about how many you made to this venue>
> ```

## 4. Cross-cutting checks (yours, from the data)

While the workers run, on `data/events.json` and the run records:

- **Aggregator credit**: events from an aggregator at a venue that has its own
  source. Some are expected (the venue's source may not list everything); many
  mean deduplication is losing to the aggregator, or the venue's source is
  short.
- **Pileups and uniform starts**: `cal check` covers these; list any.
- **Silent zeros**: active sources with no published events, and why (failed,
  rejected, or genuinely empty).
- **Staleness**: sources whose furthest published date is in the past, or
  whose last scrape is older than the daily job should allow.
- **Monitoring**: anything `cal doctor` reported.

## 5. The report

`audits/<YYYY-MM-DD>.md`. Repository paths in backticks, not relative markdown
links (`tests/test_docs.py` checks every relative link in every markdown file).
Every number from a tool or a worker block.

```markdown
# Source audit, <YYYY-MM-DD>

<N> active sources: <h> healthy, <d> degraded, <b> broken. <one-sentence headline>.

## Verdicts

| Source | Kind | CI | Published | Venue lists | Furthest (ours / venue) | Spot check | Verdict | Proposed fix |
|---|---|---|---|---|---|---|---|---|

## Broken
<per source: evidence, then the fix>

## Degraded
<per source: evidence, then the fix>

## Cross-cutting
<aggregator credit, staleness, CI-only failures, doctor findings>

## Blocked and retired
<name, since when, why; any to revisit>

## Proposed fixes, in order
1. wrong data published (wrong dates or times, events a reader cannot attend)
2. missing data (dead sources, page-one-only reads)
3. staleness and CI gaps
```

Finish by telling the owner the counts, the worst three findings and where the
report is. The fixes are theirs to approve; each one, when made, gets a
fixture, a test, `cal check "<name>" --rebaseline` after the fix lands, and an
entry in `docs/incident-log.md`.
