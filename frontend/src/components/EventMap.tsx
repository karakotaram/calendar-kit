import { useEffect, useRef, useState, useMemo } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { Event } from '@/types/event';
import { toast } from 'sonner';
import { MAPBOX_TOKEN, SITE } from '@/lib/site';

interface EventMapProps {
  events: Event[];
  center?: [number, number];
  /** What the pins are currently limited to, e.g. "today's events". */
  scopeLabel?: string;
  /** Line under the label telling the reader how to widen it. */
  scopeHint?: string;
}

const EVENT_TYPE_COLORS: Record<string, string> = {
  music: '#8b5cf6',
  'arts and culture': '#f59e0b',
  theater: '#ef4444',
  lectures: '#3b82f6',
  community: '#10b981',
  sports: '#ec4899',
  'food and drink': '#f97316',
  other: '#6b7280',
};

// Popup content is built as an HTML string, and every field in it comes from a
// scraped page. Escape it so a title can never become markup.
const escapeHtml = (value: unknown) =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const safeHref = (value: unknown) => (/^https?:\/\//i.test(String(value ?? '')) ? escapeHtml(value) : '');

function eventsToGeoJSON(events: Event[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    // Events the API could not geocode have no place on the map
    features: events.filter(event => event.coordinates).map(event => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: event.coordinates,
      },
      properties: {
        id: event.id,
        title: event.title,
        day: event.day,
        date: event.dateLabel,
        time: event.time,
        location: event.location,
        fee: event.fee || '',
        description: event.description || '',
        type: event.type,
        sourceUrl: event.sourceUrl || '',
        color: EVENT_TYPE_COLORS[event.type] || EVENT_TYPE_COLORS.other,
      },
    })),
  };
}

export const EventMap = ({
  events,
  center,
  scopeLabel = "today's events",
  scopeHint = 'Use date filter to see other days',
}: EventMapProps) => {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);
  const popup = useRef<mapboxgl.Popup | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isMapLoaded, setIsMapLoaded] = useState(false);

  const geoJsonData = useMemo(() => eventsToGeoJSON(events), [events]);

  const setupLayers = (mapInstance: mapboxgl.Map, data: GeoJSON.FeatureCollection) => {
    mapInstance.addSource('events', { type: 'geojson', data: data });

    mapInstance.addLayer({
      id: 'event-points',
      type: 'circle',
      source: 'events',
      paint: {
        'circle-color': ['get', 'color'],
        'circle-radius': 10,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#ffffff',
      },
    });

    mapInstance.on('click', 'event-points', (e) => {
      if (!e.features?.length) return;
      const feature = e.features[0];
      const coordinates = (feature.geometry as GeoJSON.Point).coordinates.slice() as [number, number];
      const props = feature.properties;

      if (popup.current) popup.current.remove();

      const feeDisplay = props?.fee && props.fee !== 'TBD'
        ? `<p style="font-size:0.75rem;font-weight:600;color:hsl(var(--foreground));">Admission: ${escapeHtml(props.fee)}</p>`
        : '';
      const href = safeHref(props?.sourceUrl);

      // Theme variables, so the popup follows the site's fonts and colors
      const popupContent = `
        <div style="padding:0.75rem;max-width:280px;font-family:var(--font-sans);color:hsl(var(--foreground));">
          <h3 style="font-family:var(--font-display);font-weight:700;font-size:1rem;margin-bottom:0.5rem;line-height:1.25;">${escapeHtml(props?.title)}</h3>
          <div style="margin-bottom:0.5rem;font-size:0.75rem;color:hsl(var(--muted-foreground));line-height:1.6;">
            <p>${escapeHtml(props?.day)}, ${escapeHtml(props?.date)}</p>
            ${props?.time ? `<p>${escapeHtml(props.time)}</p>` : ''}
            <p>${escapeHtml(props?.location)}</p>
            ${feeDisplay}
          </div>
          ${props?.description ? `<p style="font-size:0.75rem;line-height:1.5;border-top:1px solid hsl(var(--border));padding-top:0.5rem;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;">${escapeHtml(props.description)}</p>` : ''}
          ${href ? `<a href="${href}" target="_blank" rel="noopener noreferrer" style="display:inline-block;font-size:0.7rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:hsl(var(--primary));text-decoration:none;border-bottom:1px solid hsl(var(--primary));margin-top:0.5rem;">Read more &rarr;</a>` : ''}
        </div>
      `;

      popup.current = new mapboxgl.Popup({ offset: 15, maxWidth: '300px' })
        .setLngLat(coordinates)
        .setHTML(popupContent)
        .addTo(mapInstance);
    });

    mapInstance.on('mouseenter', 'event-points', () => {
      mapInstance.getCanvas().style.cursor = 'pointer';
    });
    mapInstance.on('mouseleave', 'event-points', () => {
      mapInstance.getCanvas().style.cursor = '';
    });
  };

  useEffect(() => {
    if (!MAPBOX_TOKEN || !mapContainer.current || map.current) return;

    // A public (pk.) token from VITE_MAPBOX_TOKEN; restrict it to the site's
    // URL in the Mapbox dashboard.
    mapboxgl.accessToken = MAPBOX_TOKEN;

    try {
      // Opened from an event's location: start on it. Otherwise frame the region.
      map.current = new mapboxgl.Map({
        container: mapContainer.current,
        style: 'mapbox://styles/mapbox/light-v11',
        center: center || SITE.region.mapCenter,
        zoom: center ? 15 : SITE.region.mapZoom,
      });

      map.current.addControl(new mapboxgl.NavigationControl(), 'top-right');

      map.current.on('load', () => {
        if (events.length > 0) {
          setupLayers(map.current!, geoJsonData);
        }
        setIsLoading(false);
        setIsMapLoaded(true);
      });
    } catch (error) {
      console.error('Error initializing map:', error);
      toast.error('Failed to load map: ' + (error as Error).message);
      setIsLoading(false);
    }
  }, [center, events, geoJsonData]);

  useEffect(() => {
    if (!map.current || !isMapLoaded) return;
    const source = map.current.getSource('events') as mapboxgl.GeoJSONSource | undefined;
    if (source) {
      source.setData(geoJsonData);
    } else if (events.length > 0) {
      setupLayers(map.current, geoJsonData);
    }
  }, [events.length, geoJsonData, isMapLoaded]);

  useEffect(() => {
    if (map.current && center) {
      map.current.flyTo({ center: center, zoom: 15, duration: 1500 });
    }
  }, [center]);

  useEffect(() => {
    return () => {
      if (popup.current) popup.current.remove();
      map.current?.remove();
      map.current = null;
    };
  }, []);

  return (
    <div className="h-full w-full relative">
      <div ref={mapContainer} className="h-full w-full" style={{ minHeight: '500px' }} />

      {/* Legend */}
      <div className="absolute top-4 left-4 bg-card border border-border p-3 z-10 shadow-sm">
        <div className="mb-3 pb-3 border-b border-border">
          <p className="text-xs text-muted-foreground font-sans">
            Showing <span className="font-semibold text-foreground">{scopeLabel}</span>
          </p>
          {scopeHint && (
            <p className="text-xs text-muted-foreground mt-1 font-sans">
              {scopeHint}
            </p>
          )}
        </div>

        <h4 className="font-serif font-bold text-sm mb-2 border-b border-border pb-1">Event Types</h4>
        <div className="space-y-1.5">
          {Object.entries(EVENT_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full border-2 border-white shadow" style={{ backgroundColor: color }} />
              <span className="text-xs text-muted-foreground font-sans capitalize">{type}</span>
            </div>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80">
          <div className="text-center space-y-2">
            <h3 className="font-serif font-bold text-lg text-foreground">Loading Map...</h3>
            <p className="text-sm text-muted-foreground font-serif italic">Initializing event map</p>
          </div>
        </div>
      )}
    </div>
  );
};
