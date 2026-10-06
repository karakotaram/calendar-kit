---
name: new-calendar
description: Set up a new calendar from this kit. Interviews the owner (name, region, time zone, cities, map centre, features, submissions form, theme, contact email), writes calendar.config.yaml, syncs the frontend config, and prints the next steps. Use when someone says "set up a new calendar", "configure the calendar", "start a calendar for <region>", "make this our events site", "change the calendar's name/time zone/cities", or runs /new-calendar.
---

# /new-calendar

Turn this kit into one region's calendar by writing `calendar.config.yaml`.
Code never hard-codes these facts; it asks `src/config.py`, which reads that
file, and the frontend reads the copy `frontend/scripts/sync-config.mjs` makes.

Nothing here touches the network, commits, or deploys.

## 1. Read what is there

Read `calendar.config.yaml`. Its current values are the defaults you offer. Also
note:

- the themes available: the directories in `frontend/src/themes/`
- whether `data/events.json` holds events (`[]` in a fresh kit). If it does and
  the time zone is about to change, say so: stored times are wall clock in the
  old zone, and the calendar needs a fresh scrape after the change.

## 2. Interview

Ask in three short rounds, not one field at a time. Offer the current value or a
sensible derived one as the default; accept "keep" and "skip". If the
AskUserQuestion tool is available, use it for the choices (time zone, features,
theme).

**Round 1: who and where**

| Key | Ask | Check |
|---|---|---|
| `site.name` | the calendar's name, as the masthead shows it | |
| `site.short_name` | a one-word name for the scrapers' user-agent | letters and digits only (`config.SHORT_NAME` strips the rest) |
| `site.tagline` | one line under the masthead | |
| `site.contact_email` | an address venues can write to | **press for one.** It goes into the user-agent on every request so a venue that wonders who is reading its site can ask, and blocked venues get outreach email from it. Blank is allowed, but say what it costs |
| `site.url` | the public site, if known | `https://...`, no trailing slash; blank until deployed |
| `region.name` | "Bay Area", "Greater Boston" | |
| `region.timezone` | propose the IANA zone for the region and confirm | must load: `pytz.timezone(name)`. Every event time is stored as wall clock in this zone |
| `region.state` | two-letter state | blank outside the US (the field holds 2 characters) |

**Round 2: the map of the region**

| Key | Ask | Check |
|---|---|---|
| `region.cities` | the cities to offer as quick views, in order | spell them as venues' addresses do ("San Francisco", not "SF"): they match each event's `city` |
| `region.default_city` | the city for an event whose address names none | usually the main city. It is a last resort: set each source's own `venue.city` instead wherever possible. Cambridge once stamped one city on every event and put neighbouring towns' venues in the wrong place |
| `region.map_center` | the centre of the region | **[longitude, latitude]**, longitude first (negative in the Americas). Derive it from the main city and confirm |
| `region.map_zoom` | how much the map shows | 9 for a metro region, 12 for one city |

**Round 3: features and look**

| Key | Ask | Needs |
|---|---|---|
| `features.map` | show a map? | a Mapbox token on the frontend (`VITE_MAPBOX_TOKEN`) |
| `features.chat` | an assistant that answers questions about upcoming events? | `ANTHROPIC_API_KEY` on the API; it costs money per question |
| `features.submissions` | let readers submit events? | a Google Form; set `submissions.form_url`. Its response sheet's id goes in `submissions.sheet_id`, read by `sync_user_events.py` |
| `features.editors_picks` | an admin page for featured events? | |
| `brand.theme` | which look | one of the directories in `frontend/src/themes/`. A new theme is a new directory there; offer to do it later, following `frontend/CLAUDE.md` |
| `api.base_url` | the deployed API, if known | blank until deployed; `docs/deployment.md` sets it |

Secrets (tokens, keys) never go in `calendar.config.yaml`; it is committed.
Say where each one goes and leave setting it to deployment.

## 3. Write the file

Edit `calendar.config.yaml` in place, value by value, keeping its comments and
key order: the comments are the documentation people read. Do not regenerate
the file. Quote strings that YAML would misread (`"02139"`, `"yes"`, anything
with a `:` or a leading `#`).

Then prove it loads the way the code will read it:

```bash
.venv/bin/python - <<'EOF'
from src import config
print("name     ", config.SITE_NAME, "|", config.TAGLINE)
print("zone     ", config.TIMEZONE_NAME, config.TZ)
print("agent    ", config.USER_AGENT)
print("cities   ", config.CITIES, "default:", config.DEFAULT_CITY or "-")
print("features ", config.FEATURES)
EOF
```

`config.USER_AGENT` must name the calendar and, after `+`, the site URL or the
contact email. If it is a bare `Name/1.0`, nobody can reach you from it.

## 4. Sync the frontend

If `frontend/scripts/sync-config.mjs` exists:

```bash
npm --prefix frontend install        # once; the script needs js-yaml
npm --prefix frontend run sync-config
```

It writes `frontend/src/site.config.json` and `frontend/vercel.json` from the
YAML and stops on a bad value (an unknown theme, an invalid time zone, a city
whose page would shadow a real route), listing every problem. Fix them in
`calendar.config.yaml`, never in the generated files, and run it again. Both
outputs are committed; a deploy refuses to build when `vercel.json` is out of
date.

## 5. Finish

Show the owner a short summary of what was set, anything left blank and what
that costs, and the next steps:

1. `cp urls.example.txt urls.txt`, then list the venues' event pages in it,
   one per line, with an optional ` | hint`.
2. `/onboard urls.txt`
3. Review `onboarding-report.md`, and send the drafted outreach emails.
4. Deploy: `docs/deployment.md`. Set `site.url` and
   `api.base_url` once the addresses exist, and run `/new-calendar` again or edit
   the two lines and re-run the sync.

Do not commit; the owner decides when.
