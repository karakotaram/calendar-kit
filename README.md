# Calendar kit

A kit for building a public events calendar for a region: every venue's events,
read from the venues' own websites, checked, deduplicated and published on one
site. It is a generalized copy of the engine and site behind
[cambridgecalendar.com](https://cambridgecalendar.com), which reads the venues
in and around Cambridge, Massachusetts.

You give Claude Code a directory and a list of venue URLs. It works out which
platform each venue's website runs on, reads the events with an existing
adapter or writes a scraper for the ones that need it, tests each one against
the venue's own page, and builds your version of the calendar.

What comes with it:

- **Platform adapters** for the systems most venue sites run on (WordPress's The
  Events Calendar, Squarespace, Localist, EventON, iCal feeds, schema.org data
  and more; `cal adapters` lists them), so most venues need one registry file
  and no code.
- **A quality gate** that refuses to publish a scrape with fabricated dates,
  time-zone errors or a collapsed source. Every scraper eventually breaks; the
  gate makes that loud instead of letting it reach readers.
- **An API and a site**, themed and configured from one file,
  `calendar.config.yaml`.
- **Claude Code skills** that onboard venues, audit them monthly, and carry
  Cambridge's experience of how scrapers go wrong.

## Quickstart

You need git, Python 3.12, Node 18 or later, and
[Claude Code](https://docs.anthropic.com/en/docs/claude-code).

1. **Clone it.**

   ```bash
   git clone <this repository's URL> my-calendar
   cd my-calendar
   ```

2. **Set up Python and the browser** some venues need.

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m playwright install chromium
   npm --prefix frontend install
   ```

3. **Open Claude Code in the directory.**

   ```bash
   claude
   ```

4. **Run `/new-calendar`.** It asks for your calendar's name, region, time
   zone, cities, map centre, features, submissions form, theme and contact
   email, writes `calendar.config.yaml`, and syncs the frontend. The contact
   email goes into the user-agent every request carries, so venues can reach
   you.

5. **Put the venue URLs in `urls.txt`**, one per line. Start from the example:

   ```bash
   cp urls.example.txt urls.txt
   ```

   `#` starts a comment. After a URL, ` | ` adds a hint for Claude: "a listings
   site, not a venue", "public concerts only", "has an iCal feed".

6. **Run `/onboard urls.txt`.** Claude splits the list among parallel
   subagents, about five URLs each. Each one detects the venue's platform,
   registers it, writes and tests a scraper where no adapter fits, and checks a
   few events against the venue's own page. Then it runs the tests, scrapes
   everything into `data/events.json`, and starts a local preview of the API
   and the site.

   Claude Code asks before running commands, and several workers at once ask
   often. Allowing `.venv/bin/python -m src.cli`, `.venv/bin/python -m pytest`
   and `.venv/bin/python -m tests.sources.record` for this project when first
   asked saves most of the prompts.

7. **Review `onboarding-report.md`.** One row per URL: added, blocked,
   needs-human, duplicate or skipped, with the adapter, the number of events,
   their date span and notes. Venues that refused access have an outreach email
   drafted for you to send, asking for a feed. Items marked needs-human say what
   decision is needed.

8. **Deploy.** See [docs/deployment.md](docs/deployment.md).

After that: `cal doctor` whenever something looks wrong, `/add-source <url>`
for a new venue, and `/audit` once a month.

## Working with it

```bash
alias cal='.venv/bin/python -m src.cli'
cal doctor              # what is wrong right now
cal sources             # every source: events, furthest date, last scraped
cal scrape "<Source>"   # run one source and show what it would publish
```

| In Claude Code | Does |
|---|---|
| `/new-calendar` | interviews you and writes `calendar.config.yaml` |
| `/onboard <file>` | onboards a list of venue URLs in parallel and writes `onboarding-report.md` |
| `/add-source <url>` | onboards one venue |
| `/audit` | compares every source with the venue's own site; read-only report with a verdict per source. Run monthly |

## Map of the repository

| Path | What it is |
|---|---|
| `calendar.config.yaml` | everything that makes the calendar yours: name, region, time zone, cities, features, theme |
| `registry/` | one YAML file per source, the only list of sources; [registry/_example.yaml](registry/_example.yaml) shows every field |
| `src/adapters/` | one reader per website platform, configured per venue by its registry file |
| `src/scrapers/custom/` | hand-written scrapers for venues no adapter fits; start from `_template.py` |
| `src/quality/` | invariants, drift fingerprints, run records and the gate |
| `src/cli.py` | the `cal` tool |
| `src/api/` | the API that serves the events |
| `frontend/` | the public site (React and Vite) |
| `scrape.py`, `scrape_local.py` | the full scrape, and the sources that must run on a local machine |
| `data/` | `events.json` (what the site shows), run records, fingerprints |
| `tests/` | offline tests: `tests/sources/` one per source, `tests/adapters/` one per adapter |
| `docs/` | how it works and how to run it, below |
| `.claude/` | the skills and the onboarding subagent Claude Code uses |
| [CLAUDE.md](CLAUDE.md) | the rules Claude Code follows in this repository |

## Documentation

| Document | Read it for |
|---|---|
| [docs/architecture.md](docs/architecture.md) | why the system is shaped the way it is: layers, data flow, failure taxonomy |
| [docs/rules-and-edge-cases.md](docs/rules-and-edge-cases.md) | every rule, the incident behind it, and the test that holds it |
| [docs/onboarding-sources.md](docs/onboarding-sources.md) | adding a venue by hand |
| [docs/platform-cheatsheet.md](docs/platform-cheatsheet.md) | where each website platform keeps its events |
| [docs/operations.md](docs/operations.md) | diagnosing, repairing, the daily and weekly jobs, rolling back |
| [docs/deployment.md](docs/deployment.md) | deploying the API and the site |
| [docs/access-policy.md](docs/access-policy.md) | what the scrapers may fetch, and what to do when a venue says no |
| [docs/venue-outreach-email.md](docs/venue-outreach-email.md) | the email asking a venue for a feed |
| [docs/incident-log.md](docs/incident-log.md) | what has gone wrong before, and what changed because of it |
