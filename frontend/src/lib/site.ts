/**
 * Everything about this particular calendar: its name, region, time zone,
 * features and theme.
 *
 * The values come from the kit's calendar.config.yaml via
 * scripts/sync-config.mjs, which writes src/site.config.json. Components read
 * them from here rather than hard-coding a place, so the same code serves any
 * station's calendar.
 */
import config from '@/site.config.json';
import { buildFacets, placeName } from '@/lib/facets.mjs';
import { wallClockNow, wallClockToIso } from '@/lib/time.mjs';

export interface SiteConfig {
  site: { name: string; tagline: string; url: string };
  region: {
    name: string;
    /** IANA zone. Every event time from the API is wall clock here. */
    timezone: string;
    state: string;
    defaultCity: string;
    cities: string[];
    mapCenter: [number, number];
    mapZoom: number;
  };
  features: { map: boolean; chat: boolean; submissions: boolean; editorsPicks: boolean };
  submissions: { formUrl: string };
  brand: { theme: string };
  api: { baseUrl: string };
  home: { title: string; description: string };
}

export interface FacetFilter {
  window?: 'today' | 'tomorrow' | 'weekend' | 'week';
  category?: string;
  city?: string;
  free?: boolean;
  familyFriendly?: boolean;
}

export interface Facet {
  slug: string;
  heading: string;
  title: string;
  description: string;
  filter: FacetFilter;
}

// JSON infers number[] for the [lng, lat] pair; sync-config has already checked the shape.
export const SITE = config as unknown as SiteConfig;

/** The landing pages, in the same order scripts/generate-static.mjs emits them. */
export const FACETS: Facet[] = buildFacets(SITE);
export const FACETS_BY_SLUG: Record<string, Facet> = Object.fromEntries(FACETS.map((f) => [f.slug, f]));

export const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN ?? '';

/** The map needs both the feature and a token; without either, the list stands alone. */
export const MAP_ENABLED = SITE.features.map && Boolean(MAPBOX_TOKEN);

if (SITE.features.map && !MAPBOX_TOKEN) {
  console.warn('features.map is on but VITE_MAPBOX_TOKEN is not set; the map is hidden.');
}

/** Where "Submit an event" points, or '' when the link should not appear. */
export const SUBMIT_URL = SITE.features.submissions ? SITE.submissions.formUrl : '';

/**
 * The calendar zone's current wall clock, comparable with `new Date(event.startDateTime)`.
 * Use it for "today", "upcoming" and "past" so they follow the events' zone.
 */
export const siteNow = (): Date => wallClockNow(SITE.region.timezone);

/** A naive wall-clock time from the API as ISO 8601 with its UTC offset. */
export const zonedIso = (value: string): string | null => wallClockToIso(value, SITE.region.timezone);

/** "Oakland, CA" */
export const place = (city: string): string => placeName(city, SITE.region.state);

/** Today's date in the calendar's zone, for mastheads: "Tuesday, October 6, 2026". */
export const dateline = (): string =>
  new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: SITE.region.timezone,
  });
