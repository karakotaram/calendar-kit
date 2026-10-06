---
name: onboard
description: Onboard a list of venue URLs into this calendar in parallel. Reads a URLs file (one per line, # comments, optional "| hint"), dedupes it against itself and the registry, fans out to source-onboarder subagents in batches of about five, runs the full test suite and cal doctor, writes onboarding-report.md (a row per URL plus outreach email drafts for blocked venues), then scrapes everything into data/events.json and starts a local preview of the API and the site. Use for /onboard <file>, "onboard these venues", "add all the URLs in urls.txt", "build the calendar from this list".
argument-hint: <urls-file, default urls.txt>
---

# /onboard

A file of venue URLs in. Out: every URL accounted for in
`onboarding-report.md`, the working ones registered and tested, a fresh
`data/events.json`, and the calendar running locally.

You are the orchestrator. The per-URL work is done by `source-onboarder`
subagents (`.claude/agents/source-onboarder.md`), each following
`.claude/skills/add-source/SKILL.md`. They can run in parallel safely because
each source is its own registry file, scraper file, test and fixture folder;
your job is everything shared: the list, the batches, the suite, the report,
the scrape, the preview.

`cal` below means `.venv/bin/python -m src.cli`.

## Politeness, before anything

Every URL is a venue that did not ask to be read. These limits hold for the
whole run, and you pass them to every worker:

- **About 5 requests per venue during detection**: one `cal detect` (capped at
  5 by design) and robots.txt. Then one recording run, up to 3 spot-check and 2
  coverage requests. Development happens against saved copies.
- **One worker per host.** URLs on the same host go to the same worker, which
  handles them one at a time.
- **A 403, 401, 429 or bot check ends the requests to that venue.** No retries,
  no other user-agent, no browser to "see if it works". It is recorded as
  blocked or needs-human.
- **The honest user-agent always** (`config.USER_AGENT`). Visible-browser mode
  is never chosen during onboarding; it is a decision for the owner.
- The full scrape at the end is one more request round per venue, paced by
  each scraper. Run it again only after fixing something, never to "see if it
  changes".

## 0. Preconditions

```bash
.venv/bin/python -c "from src import config; print(config.SITE_NAME, '|', config.TIMEZONE_NAME, '|', config.USER_AGENT)"
cal adapters
```

- If the time zone is not the venues' region, or the name is not this
  calendar's, stop and run `/new-calendar` first: every event time is stored
  as wall clock in that zone.
- If `config.USER_AGENT` has no `(+...)` part, no venue can tell who is
  reading it or reach us. Ask the owner for a contact email (it goes in
  `site.contact_email`) before fetching anything.
- Read `docs/access-policy.md` and `docs/venue-outreach-email.md`.
- Note `git status --short`, so the report can say what this run added. Run no
  git commands that change anything.

## 1. Read the file, dedupe, check the registry

The file is `$ARGUMENTS`, or `urls.txt` if none was given. One URL per line; a
line starting with `#` is a comment, and so is anything after ` #` (a `#` inside
a URL is a fragment, not a comment); text after `|` is a hint for the worker.

```bash
.venv/bin/python - "<file>" > tmp/onboard-plan.json <<'EOF'
import json, re, sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from src.sources import SOURCES

SOCIAL = ("facebook.com", "fb.me", "instagram.com", "x.com", "twitter.com", "tiktok.com",
          "linkedin.com", "threads.net")
MARKETS = ("eventbrite.", "ticketmaster.", "meetup.com", "dice.fm", "axs.com", "seetickets.")
BATCH = 5

def norm(url):
    p = urlsplit(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query)
                             if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_"))))
    return urlunsplit((p.scheme.lower(), host, p.path.rstrip("/") or "/", query, "")), host

registered, by_host = {}, {}
for s in SOURCES:
    if s.url:
        key, host = norm(s.url)
        registered.setdefault(key, s.name)
        by_host.setdefault(host, []).append(s.name)

rows, first = [], {}
for n, raw in enumerate(open(sys.argv[1]), 1):
    line = re.sub(r"\s+#.*$", "", raw).strip()
    if not line or line.startswith("#"):
        continue
    url, _, hint = (part.strip() for part in line.partition("|"))
    if not re.match(r"https?://", url, re.I) and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
        url = "https://" + url
    row = {"line": n, "url": url, "hint": hint}
    rows.append(row)
    if not re.match(r"https?://[^\s/]+\.[^\s/]+", url, re.I):
        row.update(status="skipped", reason="not a URL")
        continue
    key, host = norm(url)
    row["host"] = host
    if key in first:
        row.update(status="duplicate", reason=f"same as line {first[key]['line']}")
        if hint:
            first[key]["hint"] = "; ".join(h for h in (first[key]["hint"], hint) if h)
        continue
    first[key] = row
    if key in registered:
        row.update(status="duplicate", reason=f"already registered as {registered[key]!r}")
    elif any(host == d or host.endswith("." + d) for d in SOCIAL):
        row.update(status="skipped", reason="social media: needs a login and forbids scraping; "
                                            "ask the venue for its own calendar or feed")
    elif any(m in host for m in MARKETS):
        row.update(status="needs-human", reason="a ticketing marketplace: its terms, or an API "
                                                "account and key, need the owner's decision")
    else:
        row["status"] = "todo"
        if host in by_host:
            row["possible_duplicate_of"] = by_host[host]

groups = {}
for row in rows:
    if row["status"] == "todo":
        groups.setdefault(row["host"], []).append(row["line"])
batches, current = [], []
for lines in sorted(groups.values(), key=len, reverse=True):
    if current and len(current) + len(lines) > BATCH:
        batches.append(current)
        current = []
    current += lines
if current:
    batches.append(current)
print(json.dumps({"rows": rows, "batches": batches}, indent=1))
EOF
```

(`mkdir -p tmp` first; `tmp/` is gitignored.) Every row now has a status.
`todo` rows go to workers; the rest are already settled and go straight into
the report. Show the owner the counts and the batches before launching.
Running `/onboard` again on the same file is safe: what is registered by then
comes back as `duplicate`.

## 2. Fan out

Launch one `source-onboarder` per batch with the Agent tool
(`subagent_type: "source-onboarder"`). Send a wave of up to six in a single
message so they run in parallel; when a wave finishes, send the next. More at
once mostly adds memory pressure (browsers, test runs) on this machine.

Each worker's prompt, filled in:

```
Onboard these URLs for <site.name> (<region.name>; time zone <region.timezone>).
Today is <YYYY-MM-DD>. You are the source-onboarder agent: follow
.claude/agents/source-onboarder.md and .claude/skills/add-source/SKILL.md.

1. <url>
   hint: <hint, or "none">
   <if possible_duplicate_of: "the registry already has <names> on this host;
   decide whether this is the same source">
2. ...

Registered source names (do not reuse): <comma-separated names from `cal sources --json`>

Return only the result blocks, one per URL, in this order, then any adapter gaps.
```

While workers run, do not touch the repository.

## 3. Collect and check what came back

For each URL, take its result block. Then verify, do not trust:

- every `added` has a `registry/<slug>.yaml` that `cal sources` lists as active,
  a `tests/sources/test_<slug_>.py`, and a capture in `tests/fixtures/<slug_>/`
  (playwright sources: a saved page);
- every `blocked` has its registry file with `status: blocked` and dated notes;
- every `needs-human` left no registry file behind (a parked
  `src/scrapers/custom/_draft_<slug_>.py` is fine).

A worker that crashed or returned no block for a URL: run that URL once more,
alone. If it fails again, it is needs-human ("onboarding worker failed: <error>").

**Adapter gaps.** Workers do not edit shared code. If a gap blocks several
venues and the fix is small and clearly right, make it now, one adapter at a
time, with a param and a test in `tests/adapters/`, then run `/add-source` for
the affected URLs one by one. Otherwise list the gap in the report.

## 4. The whole suite, then doctor

```bash
.venv/bin/python -m pytest tests/ -q
cal doctor
```

- A failing source test: fix it if the cause is plain. If not, that source
  becomes needs-human: park its module as `_draft_<slug_>.py`, and remove its
  registry file and test.
- A `tests/test_docs.py` failure is real: a registry name that differs from
  what the scraper emits, a custom module with no registry file, a blocked file
  with no notes, a broken link in a markdown file.
- Before the first scrape `cal doctor` warns that registered sources contribute
  no events and that there are no run records. That is expected now; anything
  else gets investigated.

## 5. Write `onboarding-report.md`

At the repository root. Every number comes from a result block or a tool's
output, never an estimate. Write repository paths in backticks, not as relative
markdown links: `tests/test_docs.py` checks every relative link in every
markdown file, including this one.

```markdown
# Onboarding report: <site.name>

<YYYY-MM-DD>. <N> URLs from `<file>`: <a> added, <b> blocked, <h> need a human,
<d> duplicates, <s> skipped.

## Sources

| # | URL | Status | Source | Adapter | Events | Dates | Notes |
|---|---|---|---|---|---|---|---|
| 1 | https://... | added | <Name> | tribe | <valid> | 2026-10-07 to 2027-05-01 | <notes; spot checks matched; runs locally only, and why> |
| 2 | https://... | blocked | <Name> | - | - | - | <date: what was refused> |
| 3 | https://... | needs-human | - | - | - | - | <the decision needed> |
| 4 | https://... | duplicate | <existing Name> | - | - | - | same as line 1 / already registered |
| 5 | https://... | skipped | - | - | - | - | <reason> |

## Needs a human

For each: what to decide, what the worker learned, and what to run after
deciding (usually `/add-source <url>`).

## Adapter gaps

<adapter: what it cannot do yet, for which venues; or "none">

## Outreach emails for blocked venues

One per blocked venue, from `docs/venue-outreach-email.md`. Fill what the
config knows: `{Calendar name}` (`site.name`), `{calendar URL}` (`site.url`),
`{region}` (`region.name`), `{user-agent}` (`config.USER_AGENT`), `{contact email}`
(`site.contact_email`), `{submission form URL}` (`submissions.form_url`; drop
that sentence if blank), and `{Venue name}`. Leave `{your name}`, `{title}`,
`{organisation}` and `{first name}` for the owner. Above each draft, one line:
the venue's events page, the date, and what turned us away. Where robots.txt,
not a refusal, was the reason, change "your site's security settings turn our
reader away" to say the site asks automated readers not to read those pages.
Address it to a contact a page already fetched showed; otherwise write "find a
contact on <site>". Drafts only: the owner sends them.

## Full scrape

(step 6)

## Next steps

1. Send the outreach emails.
2. Decide the needs-human items.
3. Deploy: `docs/deployment.md`.
```

Events is the number valid after validation; Dates the first and last start.
Notes keep what the owner should know: local-only sources, exclusions,
coverage limits, the venue's own data errors published as given.

## 6. Scrape everything

```bash
.venv/bin/python scrape.py
```

It runs every active source in turn (plain HTTP first, browsers next,
aggregators last), validates, deduplicates across sources, and lets the gate
decide whether to write `data/events.json`. It can take a long while; run it in
the background and check on it. Then:

```bash
cal runs -n 1            # the run id and the gate's decision
cal run <run-id>         # per-source status and yield, rejections, gate reasons
cal sources              # every source: events, furthest date
cal check
cal doctor
```

- **Gate blocked on an invariant**: some scraper is fabricating or misreading.
  Find it in `cal run <run-id>`, fix it, and scrape again. Never `--force` past
  an invariant violation. If it cannot be fixed now, make that source
  needs-human (park, remove its registry file and test) and scrape again.
- **A source that worked during onboarding fails here**: look at the error
  before anything else. A 429 or a block now usually means it was asked too
  often today; leave it, note it, and let the next scheduled run try.
- **A source that is `ok` but contributes nothing**: its `rejected` counts say
  why.

Add the **Full scrape** section to the report: the run id, the gate decision,
the total published, any source that failed or contributed nothing and why,
and the published count per source from `cal sources --json`.

## 7. Preview

```bash
.venv/bin/python -m uvicorn src.api.main:app --port 8199          # in the background
curl -s localhost:8199/health
npm --prefix frontend install                                     # if frontend/node_modules is missing
VITE_API_BASE_URL=http://localhost:8199 npm --prefix frontend run dev   # in the background; port 8080
```

If either fails to start, report the error as it is; do not work around it.
Give the owner both addresses: the site at http://localhost:8080 and the API at
http://localhost:8199.

## 8. Finish

Tell the owner, briefly: the counts by status, where the report is, the preview
addresses, and what is theirs to do (send the outreach emails, decide the
needs-human items, deploy). Do not commit or push; the owner decides when.
