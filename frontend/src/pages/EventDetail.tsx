import { useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CalendarPlus, ExternalLink, MapPin } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { SiteHeader } from '@/components/SiteHeader';
import { API_BASE_URL, fetchApi } from '@/lib/config';
import { slugify } from '@/lib/facets.mjs';
import { SITE, siteNow } from '@/lib/site';
import { APIEvent } from '@/types/event';
import { getVenueImage } from '@/lib/venueImages';

/**
 * A shareable page for one event.
 *
 * These URLs are prerendered at build time by scripts/generate-static.mjs, so a
 * crawler sees the event without running any JavaScript. This component is what
 * a visitor gets once the bundle loads, and what client-side navigation renders.
 *
 * The URL is stable because event ids are a content hash rather than a fresh
 * uuid per scrape — a link shared today still resolves next week.
 */
const EventDetail = () => {
  const { id } = useParams<{ id: string }>();

  const { data: event, isLoading, error } = useQuery({
    queryKey: ['event', id],
    queryFn: () => fetchApi<APIEvent>(`/events/${id}`),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });

  // Keep the tab title in step during client-side navigation. The prerendered
  // <head> is correct on first load; this covers arriving from another route.
  useEffect(() => {
    if (event?.title) document.title = `${event.title} | ${SITE.site.name}`;
    return () => {
      document.title = SITE.home.title;
    };
  }, [event?.title]);

  const start = event ? new Date(event.start_datetime) : null;
  // The API flags listings with no published time; a midnight start is real
  const hasTime = start && !event?.all_day;
  // Pages outlive their events by 90 days so old links land somewhere useful
  // rather than on a 404 — say so, and point at what is on next.
  // Both sides are wall clock in the calendar's zone, wherever the reader is
  const hasPassed = start ? start < siteNow() : false;
  const venueSlug = event?.venue_name ? slugify(event.venue_name) : null;
  const image = event?.image_url || getVenueImage(event?.venue_name || '', event?.category || '');

  return (
    <div className="min-h-screen bg-background">
      <SiteHeader compact />

      <main className="max-w-[900px] mx-auto px-4 py-10">
        {isLoading && <p className="font-serif text-muted-foreground">Loading event…</p>}

        {error && (
          <div>
            <h1 className="font-display font-black text-3xl mb-3">Event not found</h1>
            <p className="font-serif text-muted-foreground mb-6">
              This event may have already happened, or the listing was removed by the venue.
            </p>
            <Button asChild><Link to="/">Browse the calendar</Link></Button>
          </div>
        )}

        {event && (
          <article>
            {hasPassed && (
              <p className="font-sans text-xs uppercase tracking-[0.12em] border border-foreground px-3 py-2 mb-4 inline-block">
                This event has already taken place
              </p>
            )}
            <p className="font-sans text-xs uppercase tracking-[0.15em] text-primary mb-2">
              {event.category || 'Event'}
            </p>
            <h1 className="font-display font-black text-4xl md:text-5xl leading-tight mb-4">
              {event.title}
            </h1>

            <p className="font-serif text-lg mb-1">
              <time dateTime={event.start_datetime}>
                {start?.toLocaleDateString('en-US', {
                  weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
                })}
                {hasTime && ` at ${start?.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`}
              </time>
            </p>

            {event.venue_name && (
              <p className="font-sans text-sm text-muted-foreground flex items-start gap-1.5 mb-6">
                <MapPin className="h-4 w-4 mt-0.5 shrink-0" />
                <span>
                  {event.venue_name}
                  {event.street_address && <>, {event.street_address}</>}
                  {event.city && <>, {event.city}</>}
                </span>
              </p>
            )}

            {image && (
              <img
                src={image}
                alt={event.title}
                className="w-full max-h-[420px] object-cover mb-6"
                loading="lazy"
              />
            )}

            {event.cost && (
              <p className="font-sans text-sm mb-4">
                <span className="uppercase tracking-[0.12em] text-muted-foreground">Admission </span>
                {event.cost}
              </p>
            )}

            {event.description && (
              <div className="font-serif text-base leading-relaxed whitespace-pre-line mb-8">
                {event.description}
              </div>
            )}

            <div className="flex flex-wrap gap-3 border-t border-border pt-6">
              {hasPassed && venueSlug && (
                <Button asChild variant="default">
                  <Link to={`/venue/${venueSlug}`}>
                    What's on at {event.venue_name}
                  </Link>
                </Button>
              )}
              {event.source_url && (
                <Button asChild variant="default">
                  <a href={event.source_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="h-4 w-4 mr-2" />
                    {event.source_name ? `More at ${event.source_name}` : 'More information'}
                  </a>
                </Button>
              )}
              {!hasPassed && (
                <Button asChild variant="outline">
                  <a href={`${API_BASE_URL}/events/${event.id}/calendar.ics`}>
                    <CalendarPlus className="h-4 w-4 mr-2" />
                    Add to calendar
                  </a>
                </Button>
              )}
            </div>
          </article>
        )}
      </main>
    </div>
  );
};

export default EventDetail;
