/**
 * The registry of indexable landing pages.
 *
 * One definition, read by two consumers that must never disagree:
 *
 *   scripts/generate-static.mjs   emits a prerendered HTML page + sitemap entry
 *   src/pages/Facet.tsx           renders the same page client-side
 *
 * If a facet existed in only one of them, the sitemap would advertise a URL the
 * app 404s on, or the app would serve a page Google never learns about. Keeping
 * the list here is the same reasoning as the source registry in the scraper
 * repo, which drifted the moment it was written down twice.
 *
 * The list is built from the site config (src/site.config.json, which
 * scripts/sync-config.mjs generates from calendar.config.yaml). The time and
 * category facets are the same for every calendar, with the region's name in
 * their copy; each configured city adds one place facet.
 *
 * Filters are declarative rather than predicates because the two consumers hold
 * events in different shapes: the generator sees the raw API payload, the app
 * sees the transformed `Event`. Each interprets the same spec for its own shape.
 */

/** @typedef {{window?: 'today'|'tomorrow'|'weekend'|'week', category?: string, city?: string, free?: boolean, familyFriendly?: boolean}} FacetFilter */
/** @typedef {{slug: string, heading: string, title: string, description: string, filter: FacetFilter}} Facet */

/**
 * Top-level paths the app, the API proxies or the build already own. A city
 * whose slug lands on one of these would shadow it, so sync-config refuses it.
 */
export const RESERVED_SLUGS = [
  'embed', 'event', 'venue', 'events', 'featured', 'admin',
  'assets', 'sitemap.xml', 'robots.txt', '404',
];

/** "A", "A and B", "A, B and C" */
export function listPhrase(items) {
  const list = items.filter(Boolean);
  if (list.length <= 1) return list.join('');
  return `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`;
}

/** "Oakland, CA", or just "Oakland" when the config names no state. */
export function placeName(city, state) {
  return [city, state].filter(Boolean).join(', ');
}

/**
 * The region name is written as a modifier ("Bay Area events", "free Bay Area
 * events") so the copy reads the same whether or not the name wants an article.
 *
 * @param {{region: {name: string, state: string, cities: string[]}}} config
 * @returns {Facet[]}
 */
export function buildFacets(config) {
  const region = config.region.name;
  const state = config.region.state;

  return [
    // --- when ---------------------------------------------------------------
    {
      slug: 'today',
      heading: 'Today',
      title: `${region} Events Today`,
      description:
        `Every ${region} event happening today: concerts, theater, talks, family ` +
        'events and community gatherings, updated every morning.',
      filter: { window: 'today' },
    },
    {
      slug: 'this-weekend',
      heading: 'This Weekend',
      title: `Things to Do This Weekend: ${region} Events`,
      description:
        `This weekend's ${region} events in one place: live music, theater, museum ` +
        'programs, markets and free family events.',
      filter: { window: 'weekend' },
    },
    {
      slug: 'this-week',
      heading: 'This Week',
      title: `${region} Events This Week`,
      description:
        `Every ${region} event over the next seven days, collected from local ` +
        'venues, libraries and civic calendars.',
      filter: { window: 'week' },
    },

    // --- price and audience -------------------------------------------------
    {
      slug: 'free',
      heading: 'Free Events',
      title: `Free ${region} Events`,
      description:
        `Free ${region} events: library programs, gallery openings, public ` +
        'lectures, outdoor concerts and community gatherings whose listings say they are free.',
      filter: { free: true },
    },
    {
      slug: 'family',
      heading: 'Family & Kids',
      title: `Family and Kids Events: ${region}`,
      description:
        `Family-friendly ${region} events: story times, sing-alongs, museum days, ` +
        'puppet shows and activities for children of all ages.',
      filter: { familyFriendly: true },
    },

    // --- category -----------------------------------------------------------
    {
      slug: 'music',
      heading: 'Live Music',
      title: `Live Music & Concerts: ${region} Events`,
      description:
        `${region} concerts and live music: clubs, chamber music, jazz, student ` +
        'ensembles and free outdoor performances.',
      filter: { category: 'music' },
    },
    {
      slug: 'theater',
      heading: 'Theater',
      title: `Theater & Performance: ${region} Events`,
      description:
        `Plays, musicals, improv and performance from ${region} stages, large and small.`,
      filter: { category: 'theater' },
    },
    {
      slug: 'arts-and-culture',
      heading: 'Arts & Culture',
      title: `Arts & Culture: ${region} Events`,
      description:
        `${region} exhibitions, gallery openings, film screenings and cultural programs.`,
      filter: { category: 'arts and culture' },
    },
    {
      slug: 'lectures',
      heading: 'Talks & Lectures',
      title: `Talks, Lectures & Author Events: ${region}`,
      description:
        `Author readings, public lectures, panels and seminars at ${region} venues, ` +
        'open to the public.',
      filter: { category: 'lectures' },
    },
    {
      slug: 'food-and-drink',
      heading: 'Food & Drink',
      title: `Food & Drink: ${region} Events`,
      description:
        `${region} tastings, brewery events, food festivals, pop-up dinners and markets.`,
      filter: { category: 'food and drink' },
    },
    {
      slug: 'community',
      heading: 'Community',
      title: `Community Events & Public Meetings: ${region}`,
      description:
        `${region} neighborhood events, public meetings, volunteer days and civic gatherings.`,
      filter: { category: 'community' },
    },
    {
      slug: 'sports',
      heading: 'Sports & Fitness',
      title: `Sports & Fitness: ${region} Events`,
      description:
        `${region} games, races, fitness classes and outdoor recreation.`,
      filter: { category: 'sports' },
    },

    // --- place: one per configured city ---------------------------------------
    ...config.region.cities.map((city) => ({
      slug: slugify(city),
      heading: city,
      title: `Events in ${placeName(city, state)}`,
      description:
        `What's on in ${placeName(city, state)}: concerts, theater, talks, family ` +
        'activities and community events, updated daily.',
      filter: { city },
    })),
  ];
}

/**
 * Title and description for the homepage. sync-config stores the result in
 * site.config.json, so index.html, the app and the generator share one wording.
 *
 * @param {{site: {name: string, tagline: string}, region: {cities: string[]}}} config
 */
export function homeMeta(config) {
  const { name, tagline } = config.site;
  const cities = listPhrase(config.region.cities);
  return {
    title: tagline ? `${name} | ${tagline}` : name,
    description:
      `${tagline ? `${tagline.replace(/[.!]+$/, '')}: ` : ''}` +
      'concerts, theater, talks, museum programs, family activities and free community events' +
      `${cities ? ` in ${cities}` : ''}, collected daily from local venues.`,
  };
}

/**
 * URL-safe slug for a venue or source name.
 *
 * Two normalisations, both because one venue reaches us under several
 * spellings and each extra spelling is a second URL splitting the same venue:
 *
 *   typographic punctuation — "Lou's" and "Lou’s"
 *   a leading article       — "Brattle Theatre" and "The Brattle Theatre"
 *
 * City facets use it too, so "San José" and "San Jose" share /san-jose.
 */
export function slugify(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')            // "é" → "e", not "e-"
    .replace(/^\s*the\s+/i, '')
    .replace(/[\u2018\u2019\u02bc]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/[\u2013\u2014]/g, '-')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

/**
 * Is this event's price explicitly free?
 *
 * Deliberately strict. Most listings carry no cost at all, and treating
 * "unrecorded" as "free" would put nearly the whole calendar on a page
 * promising free things to do — a claim the data cannot support. Many of
 * those probably are free; we just do not know, and saying so anyway is the
 * same defect as a scraper inventing a date.
 *
 * The generator and the app both call this, because they once drifted: the
 * prerendered /free listed a few hundred events while the live page showed
 * nearly the whole calendar.
 */
export function isFreeCost(cost) {
  return /^\s*(free|\$?0(\.00)?)\s*$/i.test(String(cost ?? ''));
}

/**
 * Inclusive [start, end) day range for a time-window filter, in local time.
 * Pass `now` as the calendar zone's wall clock (src/lib/time.mjs) so "today"
 * means today where the events are, not where the reader or the build is.
 * "weekend" means the coming Friday through Sunday, and stays on the current
 * weekend once it has started rather than jumping to the next one.
 */
export function windowRange(name, now = new Date()) {
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

  const today = startOfDay(now);
  switch (name) {
    case 'today':
      return [today, addDays(today, 1)];
    case 'tomorrow':
      return [addDays(today, 1), addDays(today, 2)];
    case 'week':
      return [today, addDays(today, 7)];
    case 'weekend': {
      const day = today.getDay();                 // 0 Sun … 6 Sat
      // Fri/Sat/Sun: we are already in it. Otherwise jump to Friday.
      const toFriday = day === 0 ? -2 : day >= 5 ? 5 - day : 5 - day;
      const friday = addDays(today, toFriday);
      const start = friday < today ? today : friday;
      return [start, addDays(friday, 3)];
    }
    default:
      return null;
  }
}
