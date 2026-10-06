export interface Event {
  id: string;
  day: string;
  /** YYYY-MM-DD, for filtering */
  date: string;
  /** "Oct 6", for display */
  dateLabel: string;
  title: string;
  time: string;
  location: string;
  address: string;
  /** [longitude, latitude], or null when the API could not place the venue */
  coordinates: [number, number] | null;
  fee: string;
  description: string;
  type: string;
  familyFriendly: boolean;
  sourceUrl?: string;
  sourceName?: string;
  imageUrl?: string;
  startDateTime: string;
  endDateTime?: string;
  // A date with no time (a multi-day run, a TBA game): show no clock time
  allDay?: boolean;
  featured?: boolean;
}

// Slim API response - optimized for list/map views.
// GET /events/slim?limit=5000&ranked=true. Times are naive wall clock in the
// calendar's zone (region.timezone); display them as they are.
export interface APIEventSlim {
  id: string;
  title: string;
  start_datetime: string;
  end_datetime?: string;
  all_day?: boolean;
  venue_name: string;
  city: string;
  latitude?: number | null;
  longitude?: number | null;
  category: string;
  family_friendly?: boolean;
  image_url?: string;
  source_url?: string;
  source_name?: string;
  cost?: string | null;
  /** Relevance score behind ranked=true ordering */
  score?: number;
  featured?: boolean;
}

// Full API response - includes all details
export interface APIEvent {
  id: string;
  title: string;
  description: string;
  start_datetime: string;
  end_datetime?: string;
  all_day?: boolean;
  venue_name: string;
  street_address: string;
  city: string;
  category: string;
  source_url: string;
  source_name?: string;
  image_url?: string;
  family_friendly?: boolean;
  cost?: string;
  latitude?: number | null;
  longitude?: number | null;
}

export type EventType = 'all' | 'music' | 'arts and culture' | 'food and drink' | 'theater' | 'lectures' | 'sports' | 'community' | 'other';
export type FeeFilter = 'all' | 'free' | 'paid';
