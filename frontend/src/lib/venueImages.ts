// Fallback images for events that carry no image of their own.
//
// Cambridge Calendar also kept a table of photos for its own venues, hotlinked
// from the venues' sites. The kit drops it: those venues are not this region's,
// and hotlinking another site's images breaks without warning. A station can
// add its own venue photos here, hosted on its own domain.

// Generic category fallbacks (Unsplash, which permits hotlinking)
const CATEGORY_FALLBACKS: Record<string, string> = {
  music: 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=400&h=300&fit=crop',
  theater: 'https://images.unsplash.com/photo-1503095396549-807759245b35?w=400&h=300&fit=crop',
  'arts and culture': 'https://images.unsplash.com/photo-1561214115-f2f134cc4912?w=400&h=300&fit=crop',
  comedy: 'https://images.unsplash.com/photo-1585699324551-f6c309eedeca?w=400&h=300&fit=crop',
  lectures: 'https://images.unsplash.com/photo-1524178232363-1fb2b075b655?w=400&h=300&fit=crop',
  community: 'https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=400&h=300&fit=crop',
  'food and drink': 'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=400&h=300&fit=crop',
  other: 'https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=400&h=300&fit=crop',
};

// Specific venue images, keyed by venue name as the API spells it
const VENUE_IMAGES: Record<string, string> = {};

/**
 * Get a fallback image for an event based on venue name or category
 */
export function getVenueImage(venueName: string, category?: string): string | undefined {
  // Try exact venue match first
  if (VENUE_IMAGES[venueName]) {
    return VENUE_IMAGES[venueName];
  }

  // Try partial venue match (for variations like "The Lily Pad - Main Room")
  const venueKey = Object.keys(VENUE_IMAGES).find(key =>
    venueName.toLowerCase().includes(key.toLowerCase()) ||
    key.toLowerCase().includes(venueName.toLowerCase())
  );
  if (venueKey) {
    return VENUE_IMAGES[venueKey];
  }

  // Fall back to category image
  if (category && CATEGORY_FALLBACKS[category.toLowerCase()]) {
    return CATEGORY_FALLBACKS[category.toLowerCase()];
  }

  // Default fallback
  return CATEGORY_FALLBACKS.other;
}
