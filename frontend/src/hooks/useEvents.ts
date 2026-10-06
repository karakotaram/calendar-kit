import { useQuery } from '@tanstack/react-query';
import { Event, APIEventSlim } from '@/types/event';
import { fetchApi } from '@/lib/config';

interface UseEventsParams {
  familyFriendly?: boolean;
}

export const useEvents = (params: UseEventsParams = {}) => {
  return useQuery({
    queryKey: ['events', params],
    queryFn: async () => {
      // Build query parameters - slim endpoint defaults to upcoming_only=true
      const queryParams = new URLSearchParams();
      queryParams.append('limit', '5000');
      queryParams.append('ranked', 'true'); // Sort by relevance score

      if (params.familyFriendly) {
        queryParams.append('family_friendly', 'true');
      }

      // Use slim endpoint for faster loading
      const apiEvents = await fetchApi<APIEventSlim[]>(`/events/slim?${queryParams.toString()}`);

      // Transform slim API data to match our Event type
      // Note: description, fee, sourceName are not available in slim endpoint
      // These will be fetched on-demand when user clicks an event
      const events: Event[] = apiEvents.map((event) => {
        const startDate = new Date(event.start_datetime);
        
        // Format day (e.g., "Monday")
        const day = startDate.toLocaleDateString('en-US', { weekday: 'long' });
        
        // Format date (YYYY-MM-DD) using local time to avoid timezone shifts
        const date = `${startDate.getFullYear()}-${String(startDate.getMonth() + 1).padStart(2, '0')}-${String(startDate.getDate()).padStart(2, '0')}`;
        // Display form (e.g., "Oct 6")
        const dateLabel = startDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        
        // Format time (e.g., "7:00 PM"). All-day events are dated but have
        // no time; their 00:00 start must not be shown as "12:00 AM".
        const time = event.all_day ? '' : startDate.toLocaleTimeString('en-US', {
          hour: 'numeric',
          minute: '2-digit',
          hour12: true
        });

        // The API geocodes venues. An event it could not place stays off the
        // map rather than being pinned to the region's center, where it would
        // claim a location it does not have.
        const coordinates: [number, number] | null =
          event.latitude != null && event.longitude != null
            ? [event.longitude, event.latitude]
            : null;

        return {
          id: event.id,
          title: event.title || 'Untitled Event',
          day,
          date,
          dateLabel,
          time,
          location: event.venue_name || 'Location TBD',
          address: event.city || 'Location TBD',
          coordinates,
          fee: event.cost || '',
          description: '', // Will be loaded on-demand
          type: event.category?.toLowerCase() || 'other',
          familyFriendly: event.family_friendly || false,
          sourceUrl: event.source_url || '',
          sourceName: event.source_name || '',
          imageUrl: event.image_url,
          startDateTime: event.start_datetime,
          endDateTime: event.end_datetime,
          allDay: event.all_day || false,
          featured: event.featured || false,
        };
      });

      return events;
    },
  });
};
