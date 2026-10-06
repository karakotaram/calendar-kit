import { useRef, useCallback, useState, useEffect } from 'react';
import { FixedSizeList as List, ListChildComponentProps } from 'react-window';
import { Event } from '@/types/event';
import { EventCard } from './EventCard';

interface VirtualizedEventListProps {
  events: Event[];
  onLocationClick?: (coordinates: [number, number]) => void;
}

// Height for EventCard (increased to accommodate expanded content)
const ITEM_HEIGHT = 450;
// Gap between items
const ITEM_GAP = 16;

export function VirtualizedEventList({
  events,
  onLocationClick,
}: VirtualizedEventListProps) {
  const listRef = useRef<List>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [listHeight, setListHeight] = useState(600);

  // Dynamically calculate height based on container
  useEffect(() => {
    const updateHeight = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const availableHeight = window.innerHeight - rect.top - 40;
        setListHeight(Math.max(400, availableHeight));
      }
    };

    updateHeight();
    window.addEventListener('resize', updateHeight);
    return () => window.removeEventListener('resize', updateHeight);
  }, []);

  const Row = useCallback(
    ({ index, style }: ListChildComponentProps) => {
      const event = events[index];
      return (
        <div
          style={{
            ...style,
            paddingBottom: ITEM_GAP,
            paddingRight: 8,
          }}
        >
          <div className="h-full overflow-auto">
            <EventCard event={event} onLocationClick={onLocationClick} />
          </div>
        </div>
      );
    },
    [events, onLocationClick]
  );

  if (events.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">
          No events match your filters. Try adjusting them!
        </p>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full h-full">
      <List
        ref={listRef}
        height={listHeight}
        itemCount={events.length}
        itemSize={ITEM_HEIGHT + ITEM_GAP}
        width="100%"
        overscanCount={3}
        className="scrollbar-thin"
      >
        {Row}
      </List>
    </div>
  );
}

export default VirtualizedEventList;
