import { useState } from 'react';
import { Event } from '@/types/event';
import { Calendar, Clock, MapPin, DollarSign, ChevronDown, ChevronUp, Loader2, Download } from 'lucide-react';
import { useEventDetails } from '@/hooks/useEventDetails';
import { getVenueImage } from '@/lib/venueImages';
import { API_BASE_URL } from '@/lib/config';
import { MAP_ENABLED, SITE, zonedIso } from '@/lib/site';

interface EventCardProps {
  event: Event;
  onLocationClick?: (coordinates: [number, number]) => void;
}

function getCalendarLinks(event: Event, streetAddress?: string) {
  // Event times are wall clock in the calendar's zone. Parsed with new Date()
  // they land in the reader's zone instead, so the links below name the zone
  // explicitly rather than converting through the reader's clock.
  const startDate = new Date(event.startDateTime);
  const endDate = event.endDateTime
    ? new Date(event.endDateTime)
    : new Date(startDate.getTime() + 2 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  const wall = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;

  // Google: wall-clock digits plus ctz, the event's own zone
  const formatGoogleDate = (d: Date) => wall(d).replace(/[-:]/g, '');
  // All-day events take bare dates; Google's end date is exclusive
  const ymd = (d: Date) => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
  const dayAfter = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
  const googleDates = event.allDay
    ? `${ymd(startDate)}/${ymd(dayAfter(event.endDateTime ? endDate : startDate))}`
    : `${formatGoogleDate(startDate)}/${formatGoogleDate(endDate)}&ctz=${encodeURIComponent(SITE.region.timezone)}`;
  // Outlook: ISO 8601 with the zone's offset on that date
  const outlookDate = (d: Date) => encodeURIComponent(zonedIso(wall(d)) ?? wall(d));

  const location = [event.location, streetAddress, event.address]
    .filter(Boolean).join(', ');

  const description = event.description || event.title;

  return {
    google: `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(event.title)}&dates=${googleDates}&details=${encodeURIComponent(description)}&location=${encodeURIComponent(location)}`,
    outlook: `https://outlook.live.com/calendar/0/deeplink/compose?subject=${encodeURIComponent(event.title)}&startdt=${outlookDate(startDate)}&enddt=${outlookDate(endDate)}${event.allDay ? '&allday=true' : ''}&body=${encodeURIComponent(description)}&location=${encodeURIComponent(location)}`,
    ics: `${API_BASE_URL}/events/${event.id}/calendar.ics`
  };
}

export const EventCard = ({ event, onLocationClick }: EventCardProps) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const fallbackImage = getVenueImage(event.location, event.type);
  const imageOptions = [event.imageUrl, fallbackImage].filter(Boolean) as string[];

  const [imageIndex, setImageIndex] = useState(0);
  const currentImage = imageOptions[imageIndex];

  const handleImageError = () => {
    if (imageIndex < imageOptions.length - 1) {
      setImageIndex(prev => prev + 1);
    } else {
      setImageIndex(imageOptions.length);
    }
  };

  const { data: details, isLoading: detailsLoading } = useEventDetails(
    isExpanded ? event.id : null
  );

  const displayFee = details?.fee || event.fee;
  const displayDescription = details?.description || event.description;
  const displaySourceName = details?.sourceName || event.sourceName;

  const calendarLinks = getCalendarLinks(event, details?.streetAddress);

  const showImage = imageIndex < imageOptions.length && !!currentImage;

  return (
    <div className={`grid ${showImage ? 'grid-cols-[140px_1fr] sm:grid-cols-[140px_1fr]' : 'grid-cols-1'} gap-4 p-3 bg-card border border-transparent hover:border-border hover:shadow-sm transition-all cursor-pointer`}>
      {/* Thumbnail */}
      {showImage && (
        <img
          key={currentImage}
          src={currentImage}
          alt={event.title}
          loading="lazy"
          onError={handleImageError}
          className="w-[140px] h-[100px] object-cover bg-muted"
        />
      )}

      {/* Text content */}
      <div className="space-y-2 min-w-0">
        {/* Category label */}
        <span className="text-[0.65rem] font-bold uppercase tracking-wider text-primary" style={{ fontVariant: 'small-caps' }}>
          {event.type}
        </span>

        {/* Title */}
        {event.sourceUrl ? (
          <a
            href={event.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="group block"
          >
            <h3 className="font-serif font-bold text-lg leading-tight group-hover:text-primary transition-colors">
              {event.title}
            </h3>
          </a>
        ) : (
          <h3 className="font-serif font-bold text-lg leading-tight">{event.title}</h3>
        )}

        {/* Meta line */}
        <p className="text-sm text-muted-foreground font-sans">
          {event.day}, {event.dateLabel}{event.time && <> &middot; {event.time}</>} &middot; {event.location}
          {displayFee && ` \u00B7 ${displayFee}`}
        </p>

        {event.familyFriendly && (
          <span className="inline-block text-xs font-sans text-success border border-success px-1.5 py-0.5">
            Family Friendly
          </span>
        )}

        {/* Expand/collapse */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            setIsExpanded(!isExpanded);
          }}
          aria-expanded={isExpanded}
          className="flex items-center gap-1 text-sm text-primary hover:text-primary/80 transition-colors font-sans font-semibold uppercase tracking-wide text-xs"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="w-3.5 h-3.5" />
              Hide details
            </>
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              Show details
            </>
          )}
        </button>

        {/* Expanded details */}
        {isExpanded && (
          <div className="space-y-3 pt-3 border-t border-border animate-in slide-in-from-top-2 duration-200">
            {detailsLoading ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm font-sans italic">Loading details...</span>
              </div>
            ) : (
              <>
                {displayDescription && (
                  <p className="text-sm text-muted-foreground leading-relaxed font-sans">
                    {displayDescription}
                  </p>
                )}

                {/* Detail meta with icons */}
                <div className="space-y-1.5 text-sm font-sans">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <MapPin className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                    {MAP_ENABLED && event.coordinates && onLocationClick ? (
                      <button
                        onClick={() => onLocationClick(event.coordinates)}
                        className="hover:text-primary transition-colors text-left"
                        title="Show on map"
                      >
                        {event.location}{event.address ? `, ${event.address}` : ''}
                      </button>
                    ) : (
                      <span>{event.location}{event.address ? `, ${event.address}` : ''}</span>
                    )}
                  </div>
                  {displayFee && (
                    <div className="flex items-center gap-2">
                      <DollarSign className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                      <span className={displayFee === 'Free' ? 'text-success font-semibold' : 'text-foreground'}>
                        {displayFee}
                      </span>
                    </div>
                  )}
                </div>

                {displaySourceName && (
                  <div className="text-xs text-muted-foreground font-sans pt-2 border-t border-border">
                    Source: {displaySourceName}
                  </div>
                )}

                {/* Calendar buttons */}
                <div className="flex flex-wrap gap-3 pt-2">
                  <a
                    href={calendarLinks.google}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary hover:text-foreground transition-colors font-sans border-b border-primary hover:border-foreground pb-px"
                  >
                    <Calendar className="w-3 h-3" />
                    Google Cal
                  </a>
                  <a
                    href={calendarLinks.outlook}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary hover:text-foreground transition-colors font-sans border-b border-primary hover:border-foreground pb-px"
                  >
                    <Calendar className="w-3 h-3" />
                    Outlook
                  </a>
                  <a
                    href={calendarLinks.ics}
                    download
                    className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary hover:text-foreground transition-colors font-sans border-b border-primary hover:border-foreground pb-px"
                  >
                    <Download className="w-3 h-3" />
                    .ics
                  </a>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
