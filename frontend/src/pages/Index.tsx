import { useState, useMemo, useDeferredValue, lazy, Suspense } from 'react';
import { EventCard } from '@/components/EventCard';
import { EventCardSkeletonList } from '@/components/EventCardSkeleton';
import { EventFilters } from '@/components/EventFilters';
import { EventSearch } from '@/components/EventSearch';
import { SubmitEventLink } from '@/components/SubmitEventLink';
import { EventChat } from '@/components/EventChat';
import { SiteFooter, SiteHeader } from '@/components/SiteHeader';
import { Pagination, PaginationContent, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious, PaginationEllipsis } from '@/components/ui/pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

// Lazy load heavy components
const EventMap = lazy(() => import('@/components/EventMap').then(m => ({ default: m.EventMap })));
import { EventType, FeeFilter } from '@/types/event';
import { Button } from '@/components/ui/button';
import { RefreshCw } from 'lucide-react';
import { useEvents } from '@/hooks/useEvents';
import { useSources } from '@/hooks/useSources';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';
import { getVenueImage } from '@/lib/venueImages';
import { isFreeCost, slugify, windowRange } from '@/lib/facets.mjs';
import { scoreEvent, searchTerms } from '@/lib/eventSearch';
import { MAP_ENABLED, SITE, siteNow } from '@/lib/site';

interface IndexProps {
  embed?: boolean;
  /** Landing-page framing, set by the routes in Facet.tsx. */
  heading?: string;
  standfirst?: string;
  initialTypes?: EventType[];
  initialFee?: FeeFilter;
  initialFamilyFriendly?: boolean;
  /** 'today' | 'tomorrow' | 'weekend' | 'week' — see src/lib/facets.mjs */
  initialWindow?: string;
  initialCity?: string;
  /** Restrict to events whose price is explicitly free. */
  initialFreeOnly?: boolean;
  /** Slug of a single venue, for /venue/:slug */
  venueSlug?: string;
}

const Index = ({
  embed = false,
  heading,
  standfirst,
  initialTypes,
  initialFee,
  initialFamilyFriendly,
  initialWindow,
  initialCity,
  initialFreeOnly,
  venueSlug,
}: IndexProps) => {
  const [view, setView] = useState<'list' | 'map'>('list');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedEventTypes, setSelectedEventTypes] = useState<EventType[]>(initialTypes ?? []);
  const [feeFilter, setFeeFilter] = useState<FeeFilter>(initialFee ?? 'all');
  const [familyFriendly, setFamilyFriendly] = useState(!!initialFamilyFriendly);
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(undefined);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [mapCenter, setMapCenter] = useState<[number, number] | undefined>(undefined);
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(20);
  const queryClient = useQueryClient();

  const {
    data: events,
    isLoading,
    error
  } = useEvents({ familyFriendly });

  const {
    data: sources = []
  } = useSources();

  const allEvents = useMemo(() => events || [], [events]);

  // Featured events: use featured flag if any are marked, else fall back to first 4 with images.
  // Off entirely unless features.editors_picks is on in calendar.config.yaml.
  const featuredEvents = useMemo(() => {
    if (!SITE.features.editorsPicks) return [];
    const marked = allEvents.filter(e => e.featured);
    if (marked.length > 0) return marked.slice(0, 4);
    return allEvents
      .filter(e => e.imageUrl || getVenueImage(e.location, e.type))
      .slice(0, 4);
  }, [allEvents]);

  // A landing page scopes the calendar before any user filter applies. These
  // are fixed for the route — clearing the filters must not escape them.
  const scopedEvents = useMemo(() => {
    if (!initialWindow && !initialCity && !venueSlug && !initialFreeOnly) return allEvents;
    // "Today" and "this weekend" are reckoned in the calendar's own time zone
    const range = initialWindow ? windowRange(initialWindow, siteNow()) : null;
    return allEvents.filter(event => {
      if (range) {
        const start = new Date(event.startDateTime);
        if (!(start >= range[0] && start < range[1])) return false;
      }
      if (initialCity && (event.address || '').toLowerCase() !== initialCity.toLowerCase()) return false;
      if (venueSlug && slugify(event.location) !== venueSlug) return false;
      if (initialFreeOnly && !isFreeCost(event.fee)) return false;
      return true;
    });
  }, [allEvents, initialWindow, initialCity, venueSlug, initialFreeOnly]);

  // On /venue/:slug the page title is whatever the venue calls itself in the
  // data, not a guess reconstructed from the URL.
  const venueName = useMemo(
    () => (venueSlug ? scopedEvents[0]?.location : undefined),
    [venueSlug, scopedEvents]);
  // A landing page is a slice of the calendar, so homepage-wide furniture
  // (Editor's Picks) does not belong on it — on /venue/lou-s it was listing
  // events at other venues entirely.
  const isScoped = Boolean(
    initialWindow || initialCity || venueSlug || initialFreeOnly ||
    initialTypes?.length || initialFee || initialFamilyFriendly);
  const pageHeading = heading ?? venueName;
  const pageStandfirst = standfirst ?? (venueName
    ? `Upcoming events at ${venueName}.`
    : undefined);

  // Keep typing responsive while several thousand events are re-filtered.
  const deferredQuery = useDeferredValue(searchQuery);
  const terms = useMemo(() => searchTerms(deferredQuery), [deferredQuery]);

  // Filter ALL events
  const filteredEvents = useMemo(() => {
    const filtered = scopedEvents.filter(event => {
      if (selectedEventTypes.length > 0 && !selectedEventTypes.includes(event.type as EventType)) return false;
      if (feeFilter === 'free' && event.fee && event.fee !== 'Free') return false;
      if (feeFilter === 'paid' && (event.fee === 'Free' || !event.fee)) return false;

      const shouldFilterBySource = selectedSources.length > 0 &&
        sources.length > 0 &&
        selectedSources.length < sources.length;
      if (shouldFilterBySource) {
        if (!selectedSources.includes(event.sourceName || '')) return false;
      }

      if (selectedDate) {
        const [year, month, day] = event.date.split('-').map(Number);
        const [selYear, selMonth, selDay] = [selectedDate.getFullYear(), selectedDate.getMonth() + 1, selectedDate.getDate()];
        if (year !== selYear || month !== selMonth || day !== selDay) {
          return false;
        }
      }
      return true;
    });
    if (terms.length === 0) return filtered;
    // A keyword search reorders the calendar by how well each event matches
    // (title beats venue beats category); with no search the API's own
    // relevance ranking stands. Sort is stable, so ties keep that order.
    return filtered
      .map(event => ({ event, score: scoreEvent(event, terms) }))
      .filter(({ score }) => score > 0)
      .sort((a, b) => b.score - a.score)
      .map(({ event }) => event);
  }, [scopedEvents, selectedEventTypes, feeFilter, familyFriendly, selectedDate, selectedSources, sources, terms]);

  // Map events: same filters but defaults to today if no date selected
  const mapEvents = useMemo(() => {
    const today = siteNow();
    const dateToFilter = selectedDate || today;
    const [filterYear, filterMonth, filterDay] = [dateToFilter.getFullYear(), dateToFilter.getMonth() + 1, dateToFilter.getDate()];

    return filteredEvents.filter(event => {
      // A search reaches across the whole calendar, so pinning the map to
      // today would hide most of what the user just asked for.
      if (selectedDate || terms.length > 0) return true;
      const [year, month, day] = event.date.split('-').map(Number);
      return year === filterYear && month === filterMonth && day === filterDay;
    });
  }, [filteredEvents, selectedDate, terms]);

  // The map legend has to describe what is actually pinned, which is no
  // longer always today.
  const mapScope = useMemo(() => {
    if (terms.length > 0) {
      return {
        label: `matches for “${deferredQuery.trim()}”`,
        hint: 'Across all upcoming dates',
      };
    }
    if (selectedDate) {
      return {
        label: selectedDate.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }),
        hint: 'Use date filter to see other days',
      };
    }
    return { label: "today's events", hint: 'Use date filter to see other days' };
  }, [terms, deferredQuery, selectedDate]);

  // Paginate
  const totalPages = Math.ceil(filteredEvents.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedEvents = filteredEvents.slice(startIndex, startIndex + itemsPerPage);

  // Reset to page 1 when filters change
  useMemo(() => {
    setCurrentPage(1);
  }, [selectedEventTypes, feeFilter, familyFriendly, selectedDate, selectedSources, itemsPerPage, terms]);

  const handleReset = () => {
    setSearchQuery('');
    setSelectedEventTypes([]);
    setFeeFilter('all');
    setFamilyFriendly(false);
    setSelectedDate(undefined);
    setSelectedSources([]);
    setCurrentPage(1);
  };

  const handleLocationClick = (coordinates: [number, number]) => {
    if (!MAP_ENABLED) return;
    setMapCenter(coordinates);
    setView('map');
  };

  return (
    <div className="min-h-screen bg-background">
      {!embed && <SiteHeader heading={pageHeading} standfirst={pageStandfirst} />}

      <div className="max-w-[1280px] mx-auto px-4">
        {/* Editor's Picks */}
        {!isLoading && featuredEvents.length > 0 && !isScoped && (
          <>
            <hr className="border-t border-border my-8" />
            <h2 className="font-serif font-bold text-2xl border-b-2 border-foreground pb-1 mb-5">
              Editor's Picks
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-2">
              {featuredEvents.map(event => {
                const fallbackImg = getVenueImage(event.location, event.type);
                const img = event.imageUrl || fallbackImg;
                return (
                  <a
                    key={event.id}
                    href={event.sourceUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group relative min-h-[260px] bg-foreground overflow-hidden cursor-pointer hover:-translate-y-[3px] transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    {img && (
                      <img
                        src={img}
                        alt=""
                        className="absolute inset-0 w-full h-full object-cover opacity-60 group-hover:opacity-45 transition-opacity"
                        onError={(e) => {
                          if (fallbackImg && e.currentTarget.src !== fallbackImg) {
                            e.currentTarget.src = fallbackImg;
                          } else {
                            e.currentTarget.style.display = 'none';
                          }
                        }}
                      />
                    )}
                    {/* Keeps white text legible over any photo */}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/40 to-transparent" aria-hidden="true" />
                    <div className="relative z-10 h-full flex flex-col justify-end items-start p-5 text-white">
                      {/* A solid chip: accent-colored text on a dark photo fails contrast */}
                      <span className="text-[0.65rem] font-bold uppercase tracking-wider bg-primary text-primary-foreground px-1.5 py-0.5 mb-2">
                        {event.type}
                      </span>
                      <h3 className="font-serif font-bold text-xl leading-tight mb-1 text-white">
                        {event.title}
                      </h3>
                      <span className="text-sm text-white/85">
                        {event.day}, {event.dateLabel}{event.time && <> &middot; {event.time}</>} &middot; {event.location}
                      </span>
                    </div>
                  </a>
                );
              })}
            </div>
          </>
        )}

        <hr className="border-t border-border my-8" />

        {/* Main Grid: Content + Sidebar */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-8">
          {/* Content area */}
          <main>
            <div className="flex items-center justify-between mb-5">
              <h2 className="font-serif font-bold text-2xl border-b-2 border-foreground pb-1">
                All Upcoming Events
              </h2>
              <div className="flex items-center gap-3">
                <SubmitEventLink />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Refresh events"
                  onClick={() => {
                    queryClient.invalidateQueries({ queryKey: ['events'] });
                    toast.info('Refreshing events...');
                  }}
                  className="h-7 px-2"
                >
                  <RefreshCw className="w-3 h-3" />
                </Button>
              </div>
            </div>

            {isLoading ? (
              <EventCardSkeletonList count={5} />
            ) : error ? (
              <div className="text-center py-12">
                <p className="text-destructive font-sans">Error loading events. Please try again.</p>
              </div>
            ) : view === 'list' || !MAP_ENABLED ? (
              <>
                <div className="space-y-3">
                  {paginatedEvents.length > 0 ? (
                    paginatedEvents.map(event => (
                      <EventCard
                        key={event.id}
                        event={event}
                        onLocationClick={MAP_ENABLED ? handleLocationClick : undefined}
                      />
                    ))
                  ) : (
                    <div className="text-center py-12">
                      <p className="text-muted-foreground font-sans">
                        {deferredQuery.trim()
                          ? `No events match “${deferredQuery.trim()}”. Try a different search or clear your filters.`
                          : 'No events match your filters. Try adjusting them!'}
                      </p>
                    </div>
                  )}
                </div>

                {totalPages > 1 && (
                  <div className="mt-8">
                    <Pagination>
                      <PaginationContent>
                        <PaginationItem>
                          <PaginationLink
                            onClick={() => setCurrentPage(1)}
                            className={currentPage === 1 ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          >
                            First
                          </PaginationLink>
                        </PaginationItem>
                        <PaginationItem>
                          <PaginationPrevious
                            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                            className={currentPage === 1 ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          />
                        </PaginationItem>

                        {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                          let pageNum;
                          if (totalPages <= 5) {
                            pageNum = i + 1;
                          } else if (currentPage <= 3) {
                            pageNum = i + 1;
                          } else if (currentPage >= totalPages - 2) {
                            pageNum = totalPages - 4 + i;
                          } else {
                            pageNum = currentPage - 2 + i;
                          }
                          return (
                            <PaginationItem key={pageNum}>
                              <PaginationLink
                                onClick={() => setCurrentPage(pageNum)}
                                isActive={currentPage === pageNum}
                                className="cursor-pointer"
                              >
                                {pageNum}
                              </PaginationLink>
                            </PaginationItem>
                          );
                        })}

                        {totalPages > 5 && currentPage < totalPages - 2 && (
                          <>
                            <PaginationItem>
                              <PaginationEllipsis />
                            </PaginationItem>
                            <PaginationItem>
                              <PaginationLink
                                onClick={() => setCurrentPage(totalPages)}
                                className="cursor-pointer"
                              >
                                {totalPages}
                              </PaginationLink>
                            </PaginationItem>
                          </>
                        )}

                        <PaginationItem>
                          <PaginationNext
                            onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                            className={currentPage === totalPages ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          />
                        </PaginationItem>
                        <PaginationItem>
                          <PaginationLink
                            onClick={() => setCurrentPage(totalPages)}
                            className={currentPage === totalPages ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          >
                            Last
                          </PaginationLink>
                        </PaginationItem>
                      </PaginationContent>
                    </Pagination>
                  </div>
                )}
              </>
            ) : (
              <div className="h-[calc(100vh-12rem)] overflow-hidden">
                <Suspense fallback={
                  <div className="h-full flex items-center justify-center bg-muted/50">
                    <p className="text-muted-foreground font-serif italic">Loading map...</p>
                  </div>
                }>
                  <EventMap
                    events={mapEvents}
                    center={mapCenter}
                    scopeLabel={mapScope.label}
                    scopeHint={mapScope.hint}
                  />
                </Suspense>
              </div>
            )}
          </main>

          {/* Sidebar */}
          {/* The search card makes this column taller than a short viewport, so
              the sticky rail scrolls rather than hiding the reset button.
              Popovers inside it portal out, so nothing gets clipped. */}
          <aside className="lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto order-first lg:order-last space-y-4">
            <EventSearch
              query={searchQuery}
              onQueryChange={setSearchQuery}
              resultCount={filteredEvents.length}
            />

            <EventFilters
              selectedEventTypes={selectedEventTypes}
              feeFilter={feeFilter}
              familyFriendly={familyFriendly}
              selectedDate={selectedDate}
              selectedSources={selectedSources}
              sources={sources}
              view={view}
              onViewChange={setView}
              onEventTypesChange={setSelectedEventTypes}
              onFeeFilterChange={setFeeFilter}
              onFamilyFriendlyChange={setFamilyFriendly}
              onDateChange={setSelectedDate}
              onSourcesChange={setSelectedSources}
              onReset={handleReset}
            />

            <div className="p-4 border border-border bg-card space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground font-sans">
                  <span className="font-bold text-foreground">{filteredEvents.length}</span> events found
                </p>
              </div>

              {view === 'list' && filteredEvents.length > 0 && (
                <div className="pt-2 border-t border-border">
                  <label className="text-sm text-muted-foreground font-sans mb-2 block">
                    Events per page
                  </label>
                  <Select
                    value={itemsPerPage.toString()}
                    onValueChange={(value) => setItemsPerPage(Number(value))}
                  >
                    <SelectTrigger className="bg-background border-border">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-background border-border">
                      <SelectItem value="20">20</SelectItem>
                      <SelectItem value="50">50</SelectItem>
                      <SelectItem value="100">100</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
          </aside>
        </div>

        {/* Ask-about-events chat: features.chat in calendar.config.yaml */}
        {SITE.features.chat && <EventChat />}

        {!embed && <SiteFooter />}
      </div>
    </div>
  );
};

export default Index;
