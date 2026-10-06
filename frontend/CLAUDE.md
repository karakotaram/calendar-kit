# CLAUDE.md

The calendar kit's public site: React + Vite + TypeScript + Tailwind, deployed
on Vercel with `frontend/` as the root directory. Full guide:
[README.md](README.md).

## Rules

1. **Nothing site-specific in code.** Name, region, time zone, cities, map,
   features, theme and API URL come from
   [calendar.config.yaml](../calendar.config.yaml) through
   [scripts/sync-config.mjs](scripts/sync-config.mjs), which writes
   `src/site.config.json` and `vercel.json`. Read them via `src/lib/site.ts`.
   Never edit the two generated files by hand.
2. **Event times are naive wall clock in `region.timezone`.** Display them as
   they are. For "today", "upcoming" and "past" use `siteNow()`; for offsets use
   `src/lib/time.mjs`. Never use `new Date()` alone to decide what has passed.
3. **One facet list.** Landing pages come from `buildFacets()` in
   `src/lib/facets.mjs`, which both the app and
   [scripts/generate-static.mjs](scripts/generate-static.mjs) read.
4. **Themes own the look.** Colors, fonts, logo and masthead live in
   `src/themes/<theme>/`. Components use the Tailwind tokens (`bg-primary`,
   `font-display`, `bg-masthead`), never literal colors or font names. Text
   must clear 4.5:1 contrast.
5. **Keep `/embed` working.** It is the calendar without header or footer, for
   iframes.

## Commands

```bash
npm run dev          # sync config, then Vite on :8080
npm run build:spa    # sync config, then Vite only
npm run build        # also prerenders pages; needs a live API and site.url
npm run typecheck
npm run lint
```

## Themes

The KQED theme is provisional; see
[src/themes/kqed/README.md](src/themes/kqed/README.md) for where each asset
came from and what KQED's brand team must confirm.
