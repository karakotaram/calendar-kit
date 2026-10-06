import { Event } from '@/types/event';

/**
 * Fields a plain-text search looks at, heaviest first. A hit in the title
 * outranks one in the venue, which outranks the incidental fields, so a search
 * for "porter" puts "Porter Square Books" above an event merely held in Porter
 * Square. The slim list payload carries no description, so there is nothing to
 * match there — see src/hooks/useEvents.ts.
 */
const SEARCH_FIELDS: { of: (event: Event) => string | undefined; weight: number }[] = [
  { of: e => e.title, weight: 4 },
  { of: e => e.location, weight: 3 },
  { of: e => e.type, weight: 2 },
  { of: e => e.address, weight: 2 },
  { of: e => e.day, weight: 1 },
  { of: e => e.sourceName, weight: 1 },
  { of: e => e.fee, weight: 1 },
];

/** Split a raw query into lowercase terms. */
export const searchTerms = (query: string): string[] =>
  query.toLowerCase().split(/\s+/).filter(Boolean);

/**
 * Score an event against pre-split terms. Every term has to land somewhere
 * (AND across terms), so "jazz davis" means jazz in Davis Square rather than
 * either one alone. Returns 0 when the event does not match at all.
 */
export const scoreEvent = (event: Event, terms: string[]): number => {
  let total = 0;
  for (const term of terms) {
    let best = 0;
    for (const field of SEARCH_FIELDS) {
      if (field.weight <= best) continue;
      const value = field.of(event);
      if (value && value.toLowerCase().includes(term)) best = field.weight;
    }
    if (best === 0) return 0;
    total += best;
  }
  return total;
};
