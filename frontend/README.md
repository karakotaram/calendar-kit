# Calendar kit: frontend

The public site for a calendar built with the kit: a searchable list of every
upcoming event in the region, with optional map, editor's picks, "Submit an
event" link and chat assistant, plus a prerendered, indexable page for every
event, venue and quick view.

It began as a copy of Cambridge Calendar's frontend. Everything that was
specific to Cambridge now comes from the kit's one config file, and the look
comes from a theme. React, Vite, TypeScript, Tailwind, Radix (shadcn/ui),
TanStack Query and Mapbox GL. Deployed on Vercel.

## Quick start

```bash
cd frontend
npm install
cp .env.example .env.local     # then add VITE_MAPBOX_TOKEN if the map is on
npm run dev                    # http://localhost:8080
```

To develop against a local API instead of the deployed one:

```bash
VITE_API_BASE_URL=http://localhost:8199 npm run dev
```

## How config flows in

```
calendar.config.yaml (kit root)
        │  scripts/sync-config.mjs  (runs before dev, build and build:spa)
        ├──► src/site.config.json ──► src/lib/site.ts ──► the app
        │                         ├─► vite.config.ts  ──► index.html <head>, theme alias
        │                         └─► scripts/generate-static.mjs ──► prerendered pages
        └──► vercel.json  (API proxies, read by Vercel before the build starts)
```

The frontend reads these keys from
[calendar.config.yaml](../calendar.config.yaml):

| Key | Used for |
|---|---|
| `site.name`, `site.tagline` | Header, page titles, meta tags, homepage copy |
| `site.url` | Canonical links, Open Graph URLs, `sitemap.xml`, `robots.txt` |
| `region.name`, `region.state` | Copy on the quick-view pages ("Free Bay Area events", "Events in Oakland, CA") |
| `region.timezone` | What "today", "this weekend" and "past" mean; UTC offsets in structured data and calendar links |
| `region.default_city` | Location fallback for events whose own address names no city |
| `region.cities` | One quick-view page per city, at `/<city-slug>`, in this order |
| `region.map_center`, `region.map_zoom` | Where the map opens |
| `features.map`, `features.chat`, `features.editors_picks`, `features.submissions` | Show or hide each feature |
| `submissions.form_url` | Target of "Submit an event" |
| `brand.theme` | Which folder under `src/themes/` styles the site |
| `api.base_url` | The API the site reads, and the target of the Vercel proxies |

`sync-config` validates as it goes and stops with a list of every problem: an
unknown time zone, a missing theme, a `map_center` in the wrong order, a city
whose page would shadow a real route such as `/free` or `/embed`. It also warns
about settings that leave the site incomplete, such as an empty
`api.base_url`.

**Commit both generated files.** `src/site.config.json` lets a fresh checkout
type-check, and Vercel reads `vercel.json` when a deploy starts, before any
build step runs, so regenerating it during the build cannot change that
deploy's proxies. On Vercel, `sync-config` therefore fails the build when the
committed `vercel.json` does not match the YAML. After changing `api.base_url`,
run `npm run sync-config` and commit `vercel.json`.

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `VITE_MAPBOX_TOKEN` | `.env.local`, Vercel | Public Mapbox token (`pk.…`). The map appears only when `features.map` is true **and** this is set. Restrict the token to your domain in the Mapbox dashboard. |
| `VITE_API_BASE_URL` | Optional | Overrides `api.base_url`, e.g. a local API. |
| `CALENDAR_CONFIG` | Optional, build | Path to a different config file for `sync-config`. |
| `STATIC_LIMIT` | Optional, build | Caps how many pages `generate-static` writes, for quick local runs. |
| `VERCEL_PROJECT_PRODUCTION_URL` | Set by Vercel | Fallback for `site.url` in canonical links until a domain is configured. |

`VITE_` variables are compiled into the public JavaScript. Never put a secret
in one.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Sync config, then start Vite on port 8080 |
| `npm run build:spa` | Sync config, then build the app only |
| `npm run build` | Sync config, build, then prerender every event, venue and quick-view page plus `sitemap.xml` (what Vercel runs) |
| `npm run sync-config` | Regenerate `src/site.config.json` and `vercel.json` |
| `npm run typecheck` | `tsc --noEmit -p tsconfig.app.json` |
| `npm run lint` | ESLint |

`npm run build` needs a reachable API and a site URL. With `api.base_url`
empty it stops at the prerender step and says so; use `build:spa` until the
API is deployed.

## Deploying on Vercel

1. Import the kit's repository as a new Vercel project.
2. **Root Directory: `frontend`.** Keep "Include files outside the root
   directory in the Build Step" switched on (Project Settings → General), which
   is the default: the build reads `../calendar.config.yaml`.
3. Framework, build command and output directory come from `vercel.json`
   (Vite, `npm run build`, `dist`).
4. Add `VITE_MAPBOX_TOKEN` under Environment Variables if the map is on.
5. Deploy the API first, set `api.base_url` and `site.url`, run
   `npm run sync-config`, and commit `vercel.json` and `src/site.config.json`.
6. Add your domain. Pushing to the production branch redeploys.

`vercel.json` proxies these paths to the API, so the admin pages it serves
appear on the site's own domain: `/admin`, `/admin/featured`, `/admin/*`
(including the `/admin/verify` sign-in), `/events`, `/events/*` and
`/featured`. Everything else is the app, except the prerendered pages, which
Vercel serves from disk first.

The browser calls the API directly at `api.base_url`, not through the proxy,
so the API must allow CORS from the site's origin.

## Time zones

The API sends every time as naive wall clock in `region.timezone`:
`2026-10-06T19:00:00` means 7 pm where the event is. The app displays those
digits unchanged in any browser. What needs the zone is handled by
`src/lib/time.mjs` through `Intl`, so any IANA zone works:

- "today", "this weekend" and "has this passed?" use `siteNow()`, the wall
  clock in the calendar's zone, not the reader's or the build server's
- schema.org dates on prerendered pages carry the zone's offset for that date
  (`-07:00` in summer, `-08:00` in winter for Pacific time)
- Google Calendar links pass the zone (`ctz`); Outlook links carry the offset

Events flagged `all_day` show no clock time anywhere: not in the list, the
detail page, the map popup or the prerendered pages, where schema.org gets a
bare date.

## Themes

A theme is a folder in `src/themes/`, chosen by `brand.theme`. Only the chosen
theme is bundled: `vite.config.ts` points the `@site-theme` import at its
`index.ts`, so one station's logo never ships in another station's site.

| File | Holds |
|---|---|
| `theme.css` | CSS variables: colors as HSL channels (`--primary: 342 100% 42%`), `--radius`, and `--font-sans`, `--font-serif`, `--font-display`. Tailwind maps its tokens (`bg-primary`, `text-muted-foreground`, `font-display`, `bg-masthead`) to these. |
| `theme.json` | What `index.html` needs before any script runs: the font stylesheet URL, the favicon file and the browser theme color. |
| `index.ts` | Imports `theme.css`; exports the masthead treatment and the logo. |
| assets | The logo, favicon and any fonts, saved in the folder. Never hotlinked. |

Masthead treatments, drawn by `src/components/SiteHeader.tsx`:

- `rule`: centered site name over a double rule, newspaper style
- `band`: a solid `--masthead` bar carrying the logo; the page title sits
  below it

Shipped themes:

- **`default`**: neutral. Inter, slate grays, one blue accent, `rule`
  masthead, no logo.
- **`kqed`**: KQED's navy, red, wordmark and a Tiempos stand-in, with a `band`
  masthead. **Provisional**: assets were taken from kqed.org and must be
  confirmed with KQED's brand team. See
  [src/themes/kqed/README.md](src/themes/kqed/README.md) for sources,
  substitutions and contrast checks.

To add a theme, copy `src/themes/default/`, change the values, and set
`brand.theme` to the folder name. Every text color must reach 4.5:1 against
the background it sits on.

## Features and routes

| Route | Page |
|---|---|
| `/` | The calendar |
| `/embed` | The calendar without header or footer, for iframes on other sites |
| `/event/:id` | One event (prerendered) |
| `/venue/:slug` | One venue's upcoming events (prerendered when it has 4 or more) |
| `/today`, `/this-weekend`, `/this-week`, `/free`, `/family`, `/music`, … | Time, price and category quick views (prerendered) |
| `/<city-slug>` | One per `region.cities` entry (prerendered) |

The header links the time, price and city quick views.

- **Map** (`features.map` and `VITE_MAPBOX_TOKEN`): a List/Map toggle, and
  venue names in expanded cards open the map there. Events the API could not
  geocode stay off the map rather than being pinned to the region's center.
- **Editor's Picks** (`features.editors_picks`): four events above the
  list, homepage only: those marked featured in the API's `/admin/featured`,
  or the first four in ranked order when none are.
- **Submit an event** (`features.submissions` and `submissions.form_url`): a
  link to the station's Google Form, opening in a new tab.
- **Chat** (`features.chat`): an assistant over upcoming events, at the
  bottom of the page.

## What the API must provide

| Request | Used by | Fields |
|---|---|---|
| `GET /events/slim?limit=5000&ranked=true` (`&family_friendly=true` when filtered) | Event list, map | `id, title, start_datetime, end_datetime, all_day, venue_name, city, latitude, longitude, category, family_friendly, image_url, source_url, source_name, cost, featured` (`score` accepted, unused) |
| `GET /events/{id}` | Expanded cards, event page | The above plus `description, street_address` |
| `GET /events/{id}/calendar.ics` | "Add to calendar" | — |
| `GET /sources` | Source filter | `{"sources": {"<name>": <count>}}` |
| `GET /events?limit=5000` | `generate-static` at build time | Full events; `state`, `zip_code`, `last_updated` or `scraped_at` used when present |
| `POST /chat` `{message, conversation_history}` | Chat, if on | `{response}`; 404 when chat is off |
| `/admin`, `/admin/featured`, `/admin/*`, `/featured` | Proxied admin pages | — |

All times naive wall clock in `region.timezone`, with `all_day` on slim and
full events.

## What was removed relative to Cambridge Calendar

- **Supabase**: the client package, the generated client and types, the edge
  functions and migrations. The submit form that posted to a Supabase function
  is now a link to `submissions.form_url`; the map's Mapbox token now comes
  from `VITE_MAPBOX_TOKEN` instead of a Supabase function.
- **Analytics**: the `/analytics` page and route, interaction tracking (the
  `useTrackInteraction` hook and its `POST /track` calls), and the PostHog
  snippet in `index.html`.
- **Cambridge content**: the hard-coded masthead, tagline, footer, page
  titles and meta tags; the Cambridge and Somerville facets; the table of
  Cambridge venue coordinates (and the map's Cambridge default center); the
  hotlinked Cambridge venue photos; the "Letters to the Editor" chat persona;
  Eastern-time offsets in the static generator.
- **The Broadsheet look** as hard-coded Tailwind fonts and `index.css`
  variables. It is now a theme's job.
- **Cambridge project files**: the Lovable README, `docs/`, one-off data
  scripts and their sample data, the Google Search Console verification file,
  Lighthouse reports and `bun.lockb`. Cambridge's `.env` (Supabase keys) and
  `.vercel` link (its Vercel project) were never copied.
