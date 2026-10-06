import { useEffect } from 'react';
import { Navigate, useParams } from 'react-router-dom';

import Index from './Index';
import { FACETS_BY_SLUG, SITE, type Facet } from '@/lib/site';
import type { EventType } from '@/types/event';

/**
 * A landing page for one slice of the calendar — /this-weekend, /free, /music,
 * one per configured city — and for a single venue at /venue/:slug.
 *
 * These are prerendered by scripts/generate-static.mjs so they are indexable;
 * this renders the same slice for a visitor, by handing initial filters to the
 * normal calendar rather than reimplementing it.
 *
 * The facet list lives in src/lib/facets.mjs precisely so the generator and this
 * component cannot disagree about which URLs exist.
 */

const CATEGORY_TO_TYPE: Record<string, EventType> = {
  'music': 'music',
  'arts and culture': 'arts and culture',
  'food and drink': 'food and drink',
  'theater': 'theater',
  'lectures': 'lectures',
  'sports': 'sports',
  'community': 'community',
  'other': 'other',
};

/**
 * Each facet is mounted at its own static path (/this-weekend, /free, …), so
 * there is no :slug segment to read — the facet arrives as a prop. useParams is
 * only the fallback for a dynamic mount.
 */
export const FacetPage = ({ facet: facetProp }: { facet?: Facet }) => {
  const { slug } = useParams<{ slug: string }>();
  const facet = facetProp ?? (slug ? FACETS_BY_SLUG[slug] : undefined);

  useEffect(() => {
    if (!facet) return;
    document.title = facet.title;
    return () => {
      document.title = SITE.home.title;
    };
  }, [facet]);

  // An unknown slug is a 404, not an empty calendar.
  if (!facet) return <Navigate to="/404" replace />;

  const { filter } = facet;
  return (
    <Index
      heading={facet.heading}
      standfirst={facet.description}
      initialTypes={filter.category ? [CATEGORY_TO_TYPE[filter.category]].filter(Boolean) : undefined}
      initialFreeOnly={filter.free}
      initialFamilyFriendly={filter.familyFriendly}
      initialWindow={filter.window}
      initialCity={filter.city}
    />
  );
};

/** /venue/:slug — every upcoming event at one venue. */
export const VenuePage = () => {
  const { slug } = useParams<{ slug: string }>();

  useEffect(() => {
    if (slug) {
      const name = slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
      document.title = `${name} Events | ${SITE.site.name}`;
    }
    return () => {
      document.title = SITE.home.title;
    };
  }, [slug]);

  // The display name comes from the events themselves — Index derives it from
  // the first match rather than guessing by un-slugging the URL.
  return <Index venueSlug={slug} />;
};

export default FacetPage;
