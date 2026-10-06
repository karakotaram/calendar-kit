/**
 * calendar.config.yaml → src/site.config.json + vercel.json
 *
 * The kit's one config file lives at the repo root, beside the API that reads
 * it. The frontend cannot read YAML at runtime, and Vercel needs its rewrites
 * in vercel.json, so this script turns the YAML into those two files. It runs
 * before every `dev`, `build` and `build:spa` (see package.json), and by hand
 * as `npm run sync-config`.
 *
 * It validates as it goes and stops on the first bad config rather than
 * shipping a site with a wrong time zone, a missing theme or a city page that
 * shadows a real route. Every problem found is listed, not just the first.
 *
 * Both outputs are committed:
 *
 *   src/site.config.json  imported by the app, index.html (vite.config.ts) and
 *                         scripts/generate-static.mjs
 *   vercel.json           Vercel reads this when a deploy *starts*, before any
 *                         build step runs, so regenerating it during the build
 *                         is too late to change the proxies. On Vercel this
 *                         script therefore refuses to build when the committed
 *                         file is out of date.
 *
 * Usage: node scripts/sync-config.mjs [path/to/calendar.config.yaml]
 *        (or set CALENDAR_CONFIG; default ../calendar.config.yaml)
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

import { RESERVED_SLUGS, buildFacets, homeMeta, slugify } from '../src/lib/facets.mjs';
import { isValidTimeZone } from '../src/lib/time.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CONFIG_PATH = path.resolve(
  ROOT,
  process.argv[2] || process.env.CALENDAR_CONFIG || '../calendar.config.yaml',
);
const SITE_JSON = path.join(ROOT, 'src', 'site.config.json');
const VERCEL_JSON = path.join(ROOT, 'vercel.json');
const THEMES_DIR = path.join(ROOT, 'src', 'themes');

/**
 * Paths on the site that Vercel proxies to the API, so the admin pages the API
 * serves share the site's origin. /admin/:path* carries the admin sign-in
 * (/admin/verify) and anything else under /admin.
 */
const API_PROXIES = [
  '/admin',
  '/admin/featured',
  '/admin/:path*',
  '/events',
  '/events/:path*',
  '/featured',
];

const fail = (lines) => {
  console.error(`[sync-config] ${path.relative(process.cwd(), CONFIG_PATH)} is not valid:`);
  for (const line of lines) console.error(`  - ${line}`);
  process.exit(1);
};

const isHttpUrl = (value) => {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
};

const trimSlash = (value) => value.replace(/\/+$/, '');

function readYaml() {
  if (!existsSync(CONFIG_PATH)) {
    console.error(`[sync-config] ${CONFIG_PATH} not found.`);
    console.error('  The frontend reads the kit\'s calendar.config.yaml, one directory up.');
    console.error('  On Vercel, keep "Include files outside the root directory" switched on');
    console.error('  (Project Settings → General → Root Directory).');
    process.exit(1);
  }
  try {
    return yaml.load(readFileSync(CONFIG_PATH, 'utf8')) || {};
  } catch (error) {
    console.error(`[sync-config] could not parse ${CONFIG_PATH}: ${error.message}`);
    process.exit(1);
  }
}

/** Validate the YAML and reshape it into what the frontend reads. */
function normalize(raw) {
  const errors = [];
  const section = (name) => {
    const value = raw[name];
    if (value == null) return {};
    if (typeof value !== 'object' || Array.isArray(value)) {
      errors.push(`${name}: must be a mapping`);
      return {};
    }
    return value;
  };
  const text = (obj, key, where, { required = false } = {}) => {
    const value = obj[key] ?? '';
    if (typeof value !== 'string') {
      errors.push(`${where}.${key}: must be text`);
      return '';
    }
    if (required && !value.trim()) errors.push(`${where}.${key}: is required`);
    return value.trim();
  };
  const url = (obj, key, where) => {
    const value = text(obj, key, where);
    if (value && !isHttpUrl(value)) errors.push(`${where}.${key}: "${value}" is not an http(s) URL`);
    return trimSlash(value);
  };
  const flag = (obj, key) => {
    const value = obj[key] ?? false;
    if (typeof value !== 'boolean') errors.push(`features.${key}: must be true or false`);
    return value === true;
  };

  const site = section('site');
  const region = section('region');
  const features = section('features');
  const submissions = section('submissions');
  const brand = section('brand');
  const api = section('api');

  const timezone = text(region, 'timezone', 'region', { required: true });
  if (timezone && !isValidTimeZone(timezone)) {
    errors.push(`region.timezone: "${timezone}" is not an IANA time zone (e.g. America/Los_Angeles)`);
  }

  let cities = region.cities ?? [];
  if (!Array.isArray(cities) || cities.some((c) => typeof c !== 'string' || !c.trim())) {
    errors.push('region.cities: must be a list of city names');
    cities = [];
  }
  cities = cities.map((c) => c.trim());

  const center = region.map_center;
  const centerOk = Array.isArray(center) && center.length === 2 &&
    center.every((n) => typeof n === 'number' && Number.isFinite(n)) &&
    Math.abs(center[0]) <= 180 && Math.abs(center[1]) <= 90;
  if (!centerOk) errors.push('region.map_center: must be [longitude, latitude], e.g. [-122.27, 37.80]');

  const zoom = region.map_zoom ?? 11;
  if (typeof zoom !== 'number' || zoom < 0 || zoom > 22) errors.push('region.map_zoom: must be a number from 0 to 22');

  const theme = text(brand, 'theme', 'brand') || 'default';
  if (!/^[a-z0-9-]+$/.test(theme)) {
    errors.push(`brand.theme: "${theme}" must be a folder name under src/themes (lowercase letters, digits, hyphens)`);
  } else {
    for (const file of ['index.ts', 'theme.css', 'theme.json']) {
      if (!existsSync(path.join(THEMES_DIR, theme, file))) {
        errors.push(`brand.theme: src/themes/${theme}/${file} does not exist`);
      }
    }
  }

  const config = {
    $comment: 'Generated from calendar.config.yaml by scripts/sync-config.mjs. Edit the YAML, not this file.',
    site: {
      name: text(site, 'name', 'site', { required: true }),
      tagline: text(site, 'tagline', 'site'),
      url: url(site, 'url', 'site'),
    },
    region: {
      name: text(region, 'name', 'region', { required: true }),
      timezone,
      state: text(region, 'state', 'region'),
      defaultCity: text(region, 'default_city', 'region'),
      cities,
      mapCenter: centerOk ? center : [0, 0],
      mapZoom: typeof zoom === 'number' ? zoom : 11,
    },
    features: {
      map: flag(features, 'map'),
      chat: flag(features, 'chat'),
      submissions: flag(features, 'submissions'),
      editorsPicks: flag(features, 'editors_picks'),
    },
    submissions: {
      formUrl: url(submissions, 'form_url', 'submissions'),
    },
    brand: { theme },
    api: { baseUrl: url(api, 'base_url', 'api') },
  };

  // A city page lives at /<slug>. It must not shadow a route, a time or
  // category page, or another city.
  const facetSlugs = new Map();
  for (const facet of buildFacets({ region: { ...config.region, cities: [] } })) {
    facetSlugs.set(facet.slug, `the "${facet.heading}" page`);
  }
  for (const reserved of RESERVED_SLUGS) facetSlugs.set(reserved, `the /${reserved} route`);
  for (const city of cities) {
    const slug = slugify(city);
    if (!slug) errors.push(`region.cities: "${city}" has no letters or digits to build a URL from`);
    else if (facetSlugs.has(slug)) errors.push(`region.cities: "${city}" would live at /${slug}, which is ${facetSlugs.get(slug)}`);
    else facetSlugs.set(slug, `the page for "${city}"`);
  }

  if (errors.length) fail(errors);
  return { ...config, home: homeMeta(config) };
}

function vercelConfig(config) {
  const base = config.api.baseUrl;
  const proxies = base
    ? API_PROXIES.map((source) => ({ source, destination: `${base}${source}` }))
    : [];
  return {
    $schema: 'https://openapi.vercel.sh/vercel.json',
    buildCommand: 'npm run build',
    outputDirectory: 'dist',
    framework: 'vite',
    rewrites: [
      ...proxies,
      // Everything else is the SPA, except files Vercel finds on disk first —
      // the prerendered /event/*, /venue/* and facet pages.
      { source: '/(.*)', destination: '/index.html' },
    ],
  };
}

/** Write only on change, so an unchanged config does not trigger a dev reload. */
function writeIfChanged(file, content) {
  const current = existsSync(file) ? readFileSync(file, 'utf8') : null;
  if (current === content) return false;
  writeFileSync(file, content, 'utf8');
  return true;
}

const config = normalize(readYaml());
const siteJson = `${JSON.stringify(config, null, 2)}\n`;
const vercelJson = `${JSON.stringify(vercelConfig(config), null, 2)}\n`;

if (process.env.VERCEL) {
  const committed = existsSync(VERCEL_JSON) ? readFileSync(VERCEL_JSON, 'utf8') : '';
  if (committed !== vercelJson) {
    console.error('[sync-config] vercel.json does not match calendar.config.yaml.');
    console.error('  Vercel read the committed vercel.json before this build started, so this');
    console.error('  deploy would proxy /events, /featured and /admin to the wrong place.');
    console.error('  Run `npm run sync-config` in frontend/ and commit vercel.json.');
    process.exit(1);
  }
}

const wroteSite = writeIfChanged(SITE_JSON, siteJson);
const wroteVercel = writeIfChanged(VERCEL_JSON, vercelJson);

console.log(
  `[sync-config] ${config.site.name} · theme "${config.brand.theme}" · ${config.region.timezone}` +
  ` · ${config.region.cities.length} cities` +
  (wroteSite || wroteVercel
    ? ` · wrote ${[wroteSite && 'src/site.config.json', wroteVercel && 'vercel.json'].filter(Boolean).join(', ')}`
    : ' · up to date'),
);
if (!config.api.baseUrl) {
  console.warn('[sync-config] api.base_url is empty: the site has no API to read events from,');
  console.warn('  and vercel.json proxies nothing. `npm run build` will stop at the static step.');
}
if (config.features.submissions && !config.submissions.formUrl) {
  console.warn('[sync-config] features.submissions is on but submissions.form_url is empty;');
  console.warn('  "Submit an event" stays hidden until it is set.');
}
if (!config.site.url) {
  console.warn('[sync-config] site.url is empty: canonical URLs and the sitemap need it');
  console.warn('  (on Vercel the production domain is used as a fallback).');
}
