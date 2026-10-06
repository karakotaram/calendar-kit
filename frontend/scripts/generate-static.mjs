/**
 * Post-build static generation: sitemap, per-event pages, and facet pages.
 *
 * Why this exists
 * ---------------
 * The site is a Vite SPA. Without this script a crawler fetching the homepage
 * receives a few dozen characters of text, no <h1>, no structured data, and a
 * handful of routes — for a calendar carrying thousands of upcoming events.
 * Nothing about any individual event would be indexable, so no search for a
 * particular event, venue or "free events this weekend" could ever reach it.
 *
 * Rather than migrate to a full SSR framework, this clones the built
 * `index.html` once per URL and injects three things:
 *
 *   1. real <head> metadata — title, description, canonical, Open Graph
 *   2. schema.org JSON-LD — Event for detail pages, ItemList for facet pages.
 *      This is what feeds Google's events experience, which is the highest
 *      intent surface a local calendar can appear on.
 *   3. crawlable body content inside #root, which React replaces on mount.
 *      A crawler that never runs the JS still sees the event; a visitor gets
 *      the app.
 *
 * This is only viable because event ids are a content hash of
 * (source, url, start, title) rather than a fresh uuid4 per scrape. Under a
 * random-id scheme most ids rotate nightly, so every URL indexed today would
 * 404 tomorrow — we would be feeding Google dead links.
 *
 * Everything site-specific — name, URL, region, time zone, cities, API — comes
 * from src/site.config.json, which scripts/sync-config.mjs writes from the
 * kit's calendar.config.yaml before `vite build` runs.
 *
 * Run automatically by `npm run build`. Set STATIC_LIMIT to cap page count
 * while iterating locally.
 */

// Event times from the API are naive wall clock in the calendar's zone. Run
// this process in UTC so `new Date("2026-10-06T19:00:00")` and every local-time
// getter read those digits back unchanged, whatever zone the build machine is
// in, with no daylight-saving gap to fall into. Offsets for schema.org come
// from the configured zone via src/lib/time.mjs. Set before any Date is made.
process.env.TZ = 'UTC';

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  buildFacets,
  isFreeCost,
  placeName,
  slugify,
  windowRange,
} from '../src/lib/facets.mjs';
import { wallClockNow, wallClockToIso } from '../src/lib/time.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, 'dist');
const CONFIG_FILE = path.join(ROOT, 'src', 'site.config.json');
const LIMIT = Number(process.env.STATIC_LIMIT || 0);

/** Stop with a message a person can act on, not a stack trace. */
function stop(...lines) {
  for (const line of lines) console.error(`[static] ${line}`);
  process.exit(1);
}

if (!existsSync(CONFIG_FILE)) {
  stop('src/site.config.json is missing — run `npm run sync-config` first.');
}
const CONFIG = JSON.parse(await readFile(CONFIG_FILE, 'utf8'));

const SITE_NAME = CONFIG.site.name;
const TIMEZONE = CONFIG.region.timezone;
const STATE = CONFIG.region.state;
const REGION = CONFIG.region.name;
const DEFAULT_CITY = CONFIG.region.defaultCity || REGION;
const FACETS = buildFacets(CONFIG);

const API = (process.env.VITE_API_BASE_URL || CONFIG.api.baseUrl || '').replace(/\/+$/, '');
if (!API) {
  stop(
    'api.base_url is empty in calendar.config.yaml, so there are no events to prerender.',
    'Set it to the deployed API (e.g. https://<app>.up.railway.app) and run `npm run sync-config`,',
    'or build the app alone with `npm run build:spa`.',
  );
}

// Canonical URLs and the sitemap must be absolute. On Vercel, fall back to the
// project's production domain until site.url is set.
const SITE_URL = (
  CONFIG.site.url ||
  (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : '')
).replace(/\/+$/, '');
if (!SITE_URL) {
  stop(
    'site.url is empty in calendar.config.yaml; canonical links and sitemap.xml need the public address.',
    'Set it (e.g. https://events.example.org) and run `npm run sync-config`.',
  );
}

// How long a page survives its own event.
//
// The generator only ever emitted upcoming events, so a page vanished the
// moment its event passed. That is a 404 cliff on a predictable schedule: of
// the ~3,700 pages live on any given day, 53% have passed within a month and
// 86% within two. Every shared link, every bookmark and every URL Google had
// just discovered would break on its own timetable.
//
// A passed event keeps its page, says so plainly, and points at what is coming
// up at the same venue — so an old link lands somewhere useful. It is dropped
// from the sitemap and marked noindex, because advertising expired listings is
// how a site accumulates thin content; `follow` is kept so the link equity
// still reaches the venue page.
const RETAIN_PAST_DAYS = 90;

// A venue needs a few events before its own page is worth indexing; a page with
// one listing is a thin page, and thin pages drag a domain down rather than up.
const MIN_EVENTS_FOR_VENUE_PAGE = 4;

const CATEGORY_LABEL = {
  'music': 'Music',
  'arts and culture': 'Arts & Culture',
  'food and drink': 'Food & Drink',
  'theater': 'Theater',
  'lectures': 'Talks & Lectures',
  'sports': 'Sports',
  'community': 'Community',
  'other': 'Events',
};

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------

const escapeHtml = (value) =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

/** JSON-LD goes inside a <script>, so "</script>" in the data must not close it. */
const jsonLd = (data) =>
  JSON.stringify(data, null, 2).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');

const truncate = (value, max) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return text.slice(0, text.lastIndexOf(' ', max - 1) || max - 1).trimEnd() + '…';
};

/** Naive wall clock from the API → ISO 8601 with the configured zone's offset. */
const isoWithOffset = (value) => (value ? wallClockToIso(value, TIMEZONE) : null);

const formatWhen = (event) => {
  const date = new Date(event.start_datetime);
  if (Number.isNaN(date.getTime())) return '';
  const day = date.toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
  });
  // The API says when there is no published time. Midnight used to be read
  // that way, but a 12am set at a jazz club is a real start time.
  if (event.all_day) return day;
  return `${day} at ${date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
};

const isFree = (event) => isFreeCost(event.cost);

/**
 * Reduce street_address to the street line.
 *
 * Scrapers fill this field inconsistently: some store "2025 Broadway", others
 * the whole postal address. Left alone it renders as
 * "Main Library, 2025 Broadway, Oakland, CA 94612, Oakland, CA" and puts the
 * locality in schema.org's streetAddress, where addressLocality already is.
 */
const STATE_NAMES = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California', CO: 'Colorado',
  CT: 'Connecticut', DE: 'Delaware', DC: 'District of Columbia', FL: 'Florida', GA: 'Georgia',
  HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois', IN: 'Indiana', IA: 'Iowa', KS: 'Kansas',
  KY: 'Kentucky', LA: 'Louisiana', ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts',
  MI: 'Michigan', MN: 'Minnesota', MS: 'Mississippi', MO: 'Missouri', MT: 'Montana',
  NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico',
  NY: 'New York', NC: 'North Carolina', ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma',
  OR: 'Oregon', PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota',
  TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia', WA: 'Washington',
  WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
};
const STATE_WORDS = [STATE, STATE_NAMES[STATE?.toUpperCase()]]
  .filter(Boolean)
  .map((s) => s.toLowerCase());

function streetOnly(event) {
  const raw = (event.street_address || '').trim();
  if (!raw) return '';
  const city = (event.city || '').toLowerCase();
  const parts = raw.split(',').map((p) => p.trim()).filter(Boolean);
  const street = parts.filter((part) => {
    const lower = part.toLowerCase();
    if (city && lower === city) return false;
    if (STATE_WORDS.includes(lower)) return false;
    if (/^(usa?|united states)$/i.test(part)) return false;
    if (/^\d{5}(-\d{4})?$/.test(part)) return false;
    const stateZip = /^(.+?)\s+\d{5}(-\d{4})?$/.exec(lower);
    if (stateZip && STATE_WORDS.includes(stateZip[1])) return false;
    return true;
  });
  return street.join(', ');
}

/** Human-readable location line with no repeated components. */
function locationLine(event) {
  const seen = new Set();
  return [event.venue_name, streetOnly(event), event.city, event.state]
    .filter(Boolean)
    .filter((part) => {
      const key = part.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(', ');
}

// --------------------------------------------------------------------------
// page assembly
// --------------------------------------------------------------------------

/**
 * Build one page from the Vite-built shell.
 *
 * Asset URLs in the shell are absolute (/assets/…), so a page written to a
 * nested directory still loads the same bundle.
 */
function renderPage(shell, { url, title, description, body, structuredData, image, noindex }) {
  const canonical = `${SITE_URL}${url}`;
  const head = [
    `<title>${escapeHtml(title)}</title>`,
    // follow, not none: the page is not worth ranking but its links to the
    // venue's upcoming events still are.
    noindex ? '<meta name="robots" content="noindex, follow" />' : '',
    `<meta name="description" content="${escapeHtml(truncate(description, 300))}" />`,
    `<link rel="canonical" href="${canonical}" />`,
    `<meta property="og:type" content="${structuredData?.['@type'] === 'Event' ? 'article' : 'website'}" />`,
    `<meta property="og:title" content="${escapeHtml(title)}" />`,
    `<meta property="og:description" content="${escapeHtml(truncate(description, 300))}" />`,
    `<meta property="og:url" content="${canonical}" />`,
    `<meta property="og:site_name" content="${escapeHtml(SITE_NAME)}" />`,
    image ? `<meta property="og:image" content="${escapeHtml(image)}" />` : '',
    `<meta name="twitter:card" content="${image ? 'summary_large_image' : 'summary'}" />`,
    `<meta name="twitter:title" content="${escapeHtml(title)}" />`,
    `<meta name="twitter:description" content="${escapeHtml(truncate(description, 200))}" />`,
    image ? `<meta name="twitter:image" content="${escapeHtml(image)}" />` : '',
    structuredData
      ? `<script type="application/ld+json">${jsonLd(structuredData)}</script>`
      : '',
  ].filter(Boolean).join('\n    ');

  return shell
    // Drop the shell's own title/description/canonical/OG so they cannot conflict
    .replace(/<title>[\s\S]*?<\/title>\s*/i, '')
    .replace(/<meta\s+name="description"[^>]*>\s*/gi, '')
    .replace(/<link\s+rel="canonical"[^>]*>\s*/gi, '')
    .replace(/<meta\s+property="og:[^"]*"[^>]*>\s*/gi, '')
    .replace(/<meta\s+name="twitter:[^"]*"[^>]*>\s*/gi, '')
    .replace('</head>', `  ${head}\n  </head>`)
    .replace('<div id="root"></div>', `<div id="root">${body}</div>`);
}

/** Shared crawlable chrome: gives every generated page a route back into the site. */
function siteNav(current) {
  const links = FACETS
    .filter((f) => f.slug !== current)
    .map((f) => `<a href="/${f.slug}">${escapeHtml(f.heading)}</a>`)
    .join('\n        ');
  return `
      <nav aria-label="Browse events">
        <a href="/">${escapeHtml(SITE_NAME)}</a>
        ${links}
      </nav>`;
}

function eventBody(event, { past = false, alsoAtVenue = [], venueSlug = null } = {}) {
  const when = formatWhen(event);
  const address = locationLine(event);
  const more = alsoAtVenue.length ? `
        <section>
          <h2>Coming up at ${escapeHtml(event.venue_name)}</h2>
          <ul>${alsoAtVenue.slice(0, 10).map((e) => `
            <li><a href="/event/${escapeHtml(e.id)}">${escapeHtml(e.title)}</a>
                <time datetime="${escapeHtml(isoWithOffset(e.start_datetime) || '')}">${escapeHtml(formatWhen(e))}</time></li>`).join('')}
          </ul>
          ${venueSlug ? `<p><a href="/venue/${escapeHtml(venueSlug)}">All events at ${escapeHtml(event.venue_name)}</a></p>` : ''}
        </section>` : '';
  return `
    <main>
      <article>
        ${past ? '<p><strong>This event has already taken place.</strong></p>' : ''}
        <h1>${escapeHtml(event.title)}</h1>
        <p><time datetime="${escapeHtml(isoWithOffset(event.start_datetime) || '')}">${escapeHtml(when)}</time></p>
        <p>${escapeHtml(address)}</p>
        ${event.cost ? `<p>Admission: ${escapeHtml(event.cost)}</p>` : ''}
        ${event.description ? `<p>${escapeHtml(truncate(event.description, 1200))}</p>` : ''}
        ${event.source_url ? `<p><a href="${escapeHtml(event.source_url)}" rel="nofollow noopener">More information from ${escapeHtml(event.source_name || 'the venue')}</a></p>` : ''}
      </article>
      ${more}
      ${siteNav()}
    </main>`;
}

function eventSchema(event) {
  // schema.org takes a bare date for an event with no time of day
  const start = event.all_day ? event.start_datetime?.slice(0, 10) : isoWithOffset(event.start_datetime);
  const end = event.all_day ? event.end_datetime?.slice(0, 10) : isoWithOffset(event.end_datetime);
  const address = {
    '@type': 'PostalAddress',
    addressLocality: event.city || DEFAULT_CITY,
    addressCountry: 'US',
  };
  if (event.state || STATE) address.addressRegion = event.state || STATE;
  const street = streetOnly(event);
  if (street) address.streetAddress = street;
  if (event.zip_code) address.postalCode = event.zip_code;

  const location = {
    '@type': 'Place',
    name: event.venue_name || placeName(DEFAULT_CITY, STATE),
    address,
  };
  if (event.latitude && event.longitude) {
    location.geo = { '@type': 'GeoCoordinates', latitude: event.latitude, longitude: event.longitude };
  }

  const schema = {
    '@context': 'https://schema.org',
    '@type': 'Event',
    name: event.title,
    startDate: start,
    eventStatus: 'https://schema.org/EventScheduled',
    eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
    location,
    url: `${SITE_URL}/event/${event.id}`,
  };
  if (end) schema.endDate = end;
  if (event.description) schema.description = truncate(event.description, 900);
  if (event.image_url) schema.image = [event.image_url];
  if (event.source_name) {
    schema.organizer = { '@type': 'Organization', name: event.source_name };
  }
  if (isFree(event)) {
    schema.isAccessibleForFree = true;
    schema.offers = {
      '@type': 'Offer', price: '0', priceCurrency: 'USD',
      availability: 'https://schema.org/InStock',
      url: event.source_url || `${SITE_URL}/event/${event.id}`,
    };
  } else if (event.cost) {
    schema.offers = {
      '@type': 'Offer', priceCurrency: 'USD',
      availability: 'https://schema.org/InStock',
      url: event.source_url || `${SITE_URL}/event/${event.id}`,
      description: event.cost,
    };
  }
  return schema;
}

function listBody(heading, blurb, events, slug) {
  const items = events.slice(0, 60).map((event) => `
        <li>
          <a href="/event/${escapeHtml(event.id)}">${escapeHtml(event.title)}</a>
          <time datetime="${escapeHtml(isoWithOffset(event.start_datetime) || '')}">${escapeHtml(formatWhen(event))}</time>
          <span>${escapeHtml(event.venue_name || '')}</span>
        </li>`).join('');
  return `
    <main>
      <h1>${escapeHtml(heading)}</h1>
      <p>${escapeHtml(blurb)}</p>
      <ol>${items}
      </ol>
      ${events.length > 60 ? `<p>${events.length - 60} more on the full calendar.</p>` : ''}
      ${siteNav(slug)}
    </main>`;
}

const listSchema = (name, description, url, events) => ({
  '@context': 'https://schema.org',
  '@type': 'ItemList',
  name,
  description,
  url,
  numberOfItems: events.length,
  itemListElement: events.slice(0, 60).map((event, index) => ({
    '@type': 'ListItem',
    position: index + 1,
    url: `${SITE_URL}/event/${event.id}`,
    name: event.title,
  })),
});

// --------------------------------------------------------------------------
// filtering
// --------------------------------------------------------------------------

function matchesFacet(event, filter, now) {
  if (filter.category && (event.category || '').toLowerCase() !== filter.category) return false;
  if (filter.city && (event.city || '').toLowerCase() !== filter.city.toLowerCase()) return false;
  if (filter.free && !isFree(event)) return false;
  if (filter.familyFriendly && !event.family_friendly) return false;
  if (filter.window) {
    const range = windowRange(filter.window, now);
    if (range) {
      const start = new Date(event.start_datetime);
      if (!(start >= range[0] && start < range[1])) return false;
    }
  }
  return true;
}

// --------------------------------------------------------------------------
// main
// --------------------------------------------------------------------------

async function writePage(urlPath, html) {
  const dir = path.join(DIST, urlPath.replace(/^\/+/, ''));
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, 'index.html'), html, 'utf8');
}

async function main() {
  if (!existsSync(path.join(DIST, 'index.html'))) {
    console.error('[static] dist/index.html missing — run `vite build` first');
    process.exit(1);
  }
  const shell = await readFile(path.join(DIST, 'index.html'), 'utf8');

  console.log(`[static] fetching events from ${API}`);
  let response;
  try {
    response = await fetch(`${API}/events?limit=5000`, {
      headers: { accept: 'application/json' },
    });
  } catch (error) {
    const reason = error.cause?.code || error.cause?.errors?.[0]?.code || error.cause?.message || error.message;
    stop(`could not reach ${API}/events (${reason}).`,
         'Check api.base_url in calendar.config.yaml, or build without prerendering: `npm run build:spa`.');
  }
  if (!response.ok) stop(`${API}/events returned HTTP ${response.status}.`);
  const all = await response.json();
  if (!Array.isArray(all)) stop(`${API}/events did not return a list of events.`);

  // The calendar zone's wall clock, comparable with the events' own times
  const now = wallClockNow(TIMEZONE);
  const retainFrom = new Date(now.getTime() - RETAIN_PAST_DAYS * 86400000);
  const dated = all.filter((e) => e.start_datetime);
  const byDate = (a, b) => a.start_datetime.localeCompare(b.start_datetime);

  let upcoming = dated.filter((e) => new Date(e.start_datetime) >= now).sort(byDate);
  let recentlyPast = dated
    .filter((e) => {
      const start = new Date(e.start_datetime);
      return start < now && start >= retainFrom;
    })
    .sort(byDate);
  if (LIMIT) {
    upcoming = upcoming.slice(0, LIMIT);
    recentlyPast = recentlyPast.slice(0, LIMIT);
  }

  console.log(`[static] ${all.length} events, ${upcoming.length} upcoming, ` +
              `${recentlyPast.length} passed within ${RETAIN_PAST_DAYS} days`);

  // Venue index over upcoming events, so a passed page can say what is next.
  const venueUpcoming = new Map();
  for (const event of upcoming) {
    const slug = slugify(event.venue_name);
    if (!slug) continue;
    if (!venueUpcoming.has(slug)) venueUpcoming.set(slug, []);
    venueUpcoming.get(slug).push(event);
  }

  const urls = [{ loc: `${SITE_URL}/`, changefreq: 'daily', priority: '1.0' }];

  // --- event pages ---------------------------------------------------------
  let written = 0;
  for (const event of [...upcoming, ...recentlyPast]) {
    const past = new Date(event.start_datetime) < now;
    const venueSlug = slugify(event.venue_name) || null;
    const alsoAtVenue = past
      ? (venueUpcoming.get(venueSlug) || []).filter((e) => e.id !== event.id)
      : [];
    const url = `/event/${event.id}`;
    const where = event.venue_name || event.city || DEFAULT_CITY;
    const heading = event.venue_name
      ? `${event.title} — ${event.venue_name}, ${placeName(event.city || DEFAULT_CITY, STATE)}`
      : event.title;
    const description = past
      ? `${event.title} took place at ${where} on ` +
        `${formatWhen(event)}.` +
        (alsoAtVenue.length ? ` See what is coming up at ${event.venue_name}.` : '')
      : (event.description
          ? truncate(event.description, 300)
          : `${event.title} at ${where} on ${formatWhen(event)}.`);

    await writePage(url, renderPage(shell, {
      url,
      title: past ? truncate(`${heading} (past event)`, 70) : truncate(heading, 70),
      description,
      image: event.image_url,
      noindex: past,
      body: eventBody(event, { past, alsoAtVenue, venueSlug }),
      structuredData: eventSchema(event),
    }));

    // Expired listings are served, not advertised.
    if (!past) {
      urls.push({
        loc: `${SITE_URL}${url}`,
        lastmod: (event.last_updated || event.scraped_at || '').slice(0, 10) || undefined,
        changefreq: 'weekly',
        priority: '0.6',
      });
    }
    written += 1;
  }
  console.log(`[static] ${written} event pages (${recentlyPast.length} passed, noindex, not in sitemap)`);

  // --- facet pages ---------------------------------------------------------
  for (const facet of FACETS) {
    const matching = upcoming.filter((e) => matchesFacet(e, facet.filter, now));
    if (!matching.length) {
      console.log(`[static]   skip /${facet.slug} — no matching events`);
      continue;
    }
    const url = `/${facet.slug}`;
    await writePage(url, renderPage(shell, {
      url,
      title: facet.title,
      description: facet.description,
      image: matching.find((e) => e.image_url)?.image_url,
      body: listBody(facet.heading, facet.description, matching, facet.slug),
      structuredData: listSchema(facet.title, facet.description, `${SITE_URL}${url}`, matching),
    }));
    urls.push({ loc: `${SITE_URL}${url}`, changefreq: 'daily', priority: '0.8' });
  }
  console.log(`[static] ${FACETS.length} facet pages`);

  // --- venue pages ---------------------------------------------------------
  // Group by slug, not by name. Two spellings of one venue — "Lou's" and
  // "Lou\u2019s" — must land on one page rather than racing to write the same
  // file, which silently dropped whichever group was generated first.
  const byVenue = new Map();
  for (const event of upcoming) {
    const name = event.venue_name;
    if (!name) continue;
    const slug = slugify(name);
    if (!slug) continue;
    if (!byVenue.has(slug)) byVenue.set(slug, { name, events: [] });
    const group = byVenue.get(slug);
    group.events.push(event);
    // Prefer the spelling used by the most events
    if (name !== group.name) {
      const counts = group.events.reduce((acc, e) => {
        acc[e.venue_name] = (acc[e.venue_name] || 0) + 1;
        return acc;
      }, {});
      group.name = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
    }
  }
  let venuePages = 0;
  for (const [slug, { name, events }] of byVenue) {
    if (events.length < MIN_EVENTS_FOR_VENUE_PAGE) continue;
    events.sort((a, b) => a.start_datetime.localeCompare(b.start_datetime));
    const url = `/venue/${slug}`;
    const city = placeName(events[0].city || DEFAULT_CITY, STATE);
    const title = `${name} Events & Calendar — ${city}`;
    const description =
      `Upcoming events at ${name} in ${city}. ` +
      `${events.length} listings, updated daily from the venue's own calendar.`;
    await writePage(url, renderPage(shell, {
      url,
      title: truncate(title, 70),
      description,
      image: events.find((e) => e.image_url)?.image_url,
      body: listBody(`${name} — Upcoming Events`, description, events, null),
      structuredData: listSchema(title, description, `${SITE_URL}${url}`, events),
    }));
    urls.push({ loc: `${SITE_URL}${url}`, changefreq: 'daily', priority: '0.7' });
    venuePages += 1;
  }
  console.log(`[static] ${venuePages} venue pages (>= ${MIN_EVENTS_FOR_VENUE_PAGE} events)`);

  // --- homepage ------------------------------------------------------------
  // The highest-authority page on the site would otherwise ship a few dozen
  // characters of text and no structured data. Give it this week's listings and
  // a WebSite entry so a crawler that only ever fetches "/" still learns what
  // this site is.
  {
    const [from, to] = windowRange('week', now);
    const soon = upcoming.filter((e) => {
      const start = new Date(e.start_datetime);
      return start >= from && start < to;
    });
    // Same wording as index.html and the app (homeMeta() in src/lib/facets.mjs)
    const { title, description } = CONFIG.home;
    const home = renderPage(shell, {
      url: '/',
      title,
      description,
      image: soon.find((e) => e.image_url)?.image_url,
      body: listBody(SITE_NAME, description, soon, null),
      structuredData: {
        '@context': 'https://schema.org',
        '@graph': [
          {
            '@type': 'WebSite',
            name: SITE_NAME,
            url: `${SITE_URL}/`,
            description,
            publisher: { '@type': 'Organization', name: SITE_NAME, url: `${SITE_URL}/` },
          },
          listSchema(`${REGION} events this week`, description, `${SITE_URL}/`, soon),
        ],
      },
    });
    await writeFile(path.join(DIST, 'index.html'), home, 'utf8');
    console.log(`[static] homepage with ${soon.length} listings this week`);
  }

  // --- sitemap -------------------------------------------------------------
  // A duplicate here means two pages wrote to one path and one was lost.
  const seenUrls = new Set();
  const duplicates = urls.filter(({ loc }) => (seenUrls.has(loc) ? true : (seenUrls.add(loc), false)));
  if (duplicates.length) {
    console.error('[static] duplicate URLs generated:', duplicates.map((d) => d.loc).slice(0, 10));
    process.exit(1);
  }

  const sitemap = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls.map(({ loc, lastmod, changefreq, priority }) => [
      '  <url>',
      `    <loc>${escapeHtml(loc)}</loc>`,
      lastmod ? `    <lastmod>${lastmod}</lastmod>` : '',
      changefreq ? `    <changefreq>${changefreq}</changefreq>` : '',
      priority ? `    <priority>${priority}</priority>` : '',
      '  </url>',
    ].filter(Boolean).join('\n')),
    '</urlset>',
    '',
  ].join('\n');
  await writeFile(path.join(DIST, 'sitemap.xml'), sitemap, 'utf8');
  console.log(`[static] sitemap.xml with ${urls.length} URLs`);

  // public/robots.txt cannot know the domain; point crawlers at the sitemap here.
  const robots = await readFile(path.join(DIST, 'robots.txt'), 'utf8').catch(() => 'User-agent: *\nAllow: /\n');
  await writeFile(path.join(DIST, 'robots.txt'), `${robots.trimEnd()}\n\nSitemap: ${SITE_URL}/sitemap.xml\n`, 'utf8');
}

main().catch((error) => {
  console.error('[static] failed:', error);
  process.exit(1);
});
