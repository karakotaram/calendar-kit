# Deployment

Accounts, secrets, domains and a first-deploy checklist. Prices change; for
every service below, check current pricing before you commit to a plan.

## The shape of it

```
GitHub repo ── Actions: daily scrape ── commits data/events.json to main
     │                                         │
     ├── Railway: the API (repo root) ◄────────┘ redeploys on every push to main
     │
     └── Vercel: the frontend (frontend/) ◄──── rebuilt by the deploy hook
                                                after each published scrape
```

One repository holds the scrapers, the data, the API and the frontend. The data
is committed, so data and code deploy together and rollback is a `git revert`.

## Accounts

| Account | Needed for | Required? | Notes |
|---|---|---|---|
| **GitHub** | the repository, the daily scrape (Actions), issues opened by the gate | yes | a private repository works. Actions minutes are metered on private repos; the daily scrape is the main consumer |
| **Railway** | hosting the API | yes | one service, deployed from `main`. Any host that runs a Python web process will do; Railway is what Cambridge used |
| **Vercel** | hosting the frontend | yes | one project with root directory `frontend/` |
| Mapbox | the map (`features.map`) | optional | a public access token; restrict it to your domains in Mapbox's settings |
| Google Cloud | reader submissions from a Google Form (`features.submissions`) | optional | a service account with the Google Sheets API enabled |
| Anthropic | the chat assistant (`features.chat`) | optional | an API key from the Anthropic Console |

Everything optional degrades rather than fails: no Mapbox token hides the map,
no service account skips the submissions sync, no Anthropic key turns chat off.
Each skip is logged; treat it as a finding, not a non-event.

## Environment variables and secrets

Nothing secret goes in `calendar.config.yaml` or anywhere in git. For local
work, copy `.env.example` to `.env` (gitignored) and fill in what you need.

### API (Railway service variables)

| Variable | Required | Purpose |
|---|---|---|
| `ADMIN_TOKEN` | for Editor's Picks | a long random string. The admin page sends it as a bearer token; unset, nobody can choose picks. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `FEATURED_PATH` | recommended with Editor's Picks | where picks are written, e.g. a path on a Railway volume. Railway's filesystem is wiped on every redeploy, and the daily scrape redeploys daily, so picks written elsewhere are lost |
| `ANTHROPIC_API_KEY` | for chat | enables `/chat` when `features.chat` is true |
| `CHAT_MODEL` | no | the model `/chat` uses; has a default |
| `CALENDAR_CONFIG` | no | path to a config file other than `calendar.config.yaml` |
| `PORT` | set by Railway | the start command binds to it |

### Daily scrape and sync (GitHub: Settings → Secrets and variables → Actions)

Two workflows write data: `.github/workflows/scrape.yml` (the daily scrape) and
`.github/workflows/sync-submissions.yml` (reader submissions, two hours later).
They share a concurrency group, so they never push at the same time.
`.github/workflows/tests.yml` runs the tests on every push and pull request.

| Name | Kind | Used by | Purpose |
|---|---|---|---|
| `VERCEL_DEPLOY_HOOK_URL` | secret | scrape | rebuilds the frontend after a published scrape ([below](#the-vercel-deploy-hook)). Absent, the step warns and continues |
| `ANTHROPIC_API_KEY` | secret | scrape | optional: Claude categorises uncategorised events and suggests causes when a source drops to zero. Absent, those steps are skipped |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | secret | sync | the service account's whole JSON key, pasted as one value. Absent, the sync does nothing |
| `GEOCODER` | variable | scrape, sync | `nominatim` lets the run look up unknown venues on OpenStreetMap (cached in `data/geocode-cache.json`, one request a second). Unset, only `data/venues.yaml` and the cache are used |
| `SUBMISSIONS_REQUIRE_APPROVAL` | variable | sync | `1` publishes a submission only after someone marks it approved in the sheet |
| `GITHUB_TOKEN` | automatic | both | provided by Actions and passed to the scrape as `GH_TOKEN`; the scrape job needs `contents: write` (to commit data) and `issues: write` (to report a blocked run) |

`GATE_DRIFT` is not a repository setting: it comes from the scrape workflow's
`drift_mode` input on a manual run and defaults to `report`. To enforce drift on
the schedule, change the default in `scrape.yml`. `GATE_MODE`, `AGENT_MODEL` and
`CALENDAR_CONFIG` are read by the code but not passed by the workflows; set them
locally (`.env`) if you need them.

### Frontend (Vercel: Project → Settings → Environment Variables)

| Variable | Required | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | yes, unless `api.base_url` is set in config | the API's public URL |
| `VITE_MAPBOX_TOKEN` | for the map | the Mapbox public token |

`VITE_` variables are compiled into the public JavaScript. Never put a secret in
one.

## The API on Railway

1. New project → Deploy from GitHub repo → this repository, branch `main`, root
   directory the repo root.
2. `railway.json` tells Railway to build the `Dockerfile` (the API only:
   Playwright and the test tools are left out, because scrapes never run in
   this container) and to health-check `/health`. The container runs
   `uvicorn src.api.main:app` on `$PORT`.
3. Add the variables above. For Editor's Picks, add a volume (e.g. mounted at
   `/state`) and set `FEATURED_PATH=/state/featured.json`.
4. Generate a domain (Settings → Networking), or add your own (below).
5. Check `https://<api>/health`: it returns `total_events` and `last_updated`.

Every push to `main` redeploys, including the daily data commit. A redeploy
takes a couple of minutes; `/health` shows the old count until it lands.

## The frontend on Vercel

1. New project → import the repository → **Root Directory: `frontend`**.
   Vercel detects Vite; `frontend/vercel.json` sets the build.
2. Add `VITE_API_BASE_URL` and, for the map, `VITE_MAPBOX_TOKEN`.
3. Deploy, and check that the home page lists events.

The build prerenders a page per event and a sitemap, so the frontend must be
rebuilt whenever the data changes. That is the deploy hook's job.

### The Vercel deploy hook

Vercel builds when the repository changes in a way it is watching, not when the
API's data changes. Without a rebuild, the prerendered event pages and the
sitemap freeze on the day they were generated while the data moves on. Cambridge
added the hook on 2026-09-01 for exactly that reason.

1. Vercel → the frontend project → Settings → Git → Deploy Hooks.
2. Create a hook named e.g. `daily-scrape` for branch `main`; copy its URL.
3. GitHub → Settings → Secrets and variables → Actions → New repository secret:
   `VERCEL_DEPLOY_HOOK_URL` = that URL.

The scrape workflow POSTs to it after a published run. Treat the URL as a
secret: anyone holding it can trigger builds.

## Domains

Decide the public URL first, then:

1. **Frontend**: Vercel → Settings → Domains → add `events.example.org` (and the
   apex or `www` if you want them). Vercel shows the DNS records to create at
   your DNS provider: typically a `CNAME` for a subdomain, or an `A` record for
   an apex. HTTPS is automatic once DNS resolves.
2. **API**: either keep Railway's generated domain, or add a custom one
   (`api.events.example.org`) in Railway's networking settings with the `CNAME`
   it shows. Alternatively, proxy API paths through the frontend's domain with
   rewrites in `frontend/vercel.json`, as Cambridge did, so readers only ever
   see one domain.
3. Update `calendar.config.yaml`: `site.url` (it goes into the scrapers'
   user-agent and the sitemap) and `api.base_url`; update `VITE_API_BASE_URL`
   in Vercel; redeploy the frontend.

If your organisation's DNS is managed by another team, send them the exact
records Vercel and Railway display; do not guess them.

## First-deploy checklist

**Configure**

- [ ] `calendar.config.yaml` filled in: `site` (name, short_name, url,
      contact_email), `region` (timezone, state, default_city, cities, map
      centre), `features`, `brand.theme`. `/new-calendar` in Claude Code walks
      through it.
- [ ] `site.short_name` and `site.url` or `site.contact_email` set: they form the
      user-agent venues see ([access-policy.md](access-policy.md)).
- [ ] At least one source registered and published
      ([onboarding-sources.md](onboarding-sources.md)); `cal sources` lists it.

**Verify locally**

- [ ] `.venv/bin/python -m pytest tests/ -q` passes.
- [ ] `cal doctor` reports no errors.
- [ ] `.venv/bin/python -m uvicorn src.api.main:app --port 8199` serves
      `/health` and `/events/slim`.
- [ ] The frontend runs against it (`npm run dev` in `frontend/` with
      `VITE_API_BASE_URL=http://localhost:8199`).

**Deploy**

- [ ] Railway service live; `/health` answers with your event count.
- [ ] Vercel project live with `VITE_API_BASE_URL` set; the home page lists
      events with correct times.
- [ ] `VERCEL_DEPLOY_HOOK_URL` saved as a GitHub secret.
- [ ] Optional features: `ADMIN_TOKEN` and `FEATURED_PATH` (Editor's Picks),
      `ANTHROPIC_API_KEY` (chat), `VITE_MAPBOX_TOKEN` (map),
      `GOOGLE_SERVICE_ACCOUNT_JSON` plus `submissions.sheet_id` shared with the
      service account (submissions).
- [ ] Domains attached; `site.url` and `api.base_url` updated (`cal doctor --live`
      and `cal diff --live` probe `api.base_url`).

**First runs**

- [ ] Set the scrape's schedule: the `cron` in `.github/workflows/scrape.yml`
      is UTC; pick a small hour in your region.
- [ ] Run the scrape by hand (Actions → Daily scrape → Run workflow). It should
      pass the gate, commit, and trigger a frontend build.
- [ ] `cal doctor --live` after it lands: production count equals local,
      endpoints answer.
- [ ] Open three events on the live site and compare each with the venue's own
      page: date, time, place, link.
- [ ] If any source is `runs_in_ci: false`, install the weekly local job
      ([operations.md](operations.md#the-weekly-local-job)).
- [ ] Watch the next three daily runs (`cal runs`). Drift stays silent until each
      source has three runs of history; that is expected.
