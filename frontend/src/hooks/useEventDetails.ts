import { useQuery } from '@tanstack/react-query';
import { APIEvent } from '@/types/event';
import { fetchApi } from '@/lib/config';

export interface EventDetails {
  description: string;
  fee: string;
  sourceName: string;
  streetAddress: string;
}

export const useEventDetails = (eventId: string | null) => {
  return useQuery({
    queryKey: ['eventDetails', eventId],
    queryFn: async (): Promise<EventDetails> => {
      if (!eventId) throw new Error('No event ID provided');
      
      const event = await fetchApi<APIEvent>(`/events/${eventId}`);

      return {
        description: event.description || '',
        fee: event.cost || '',
        sourceName: event.source_name || '',
        streetAddress: event.street_address || '',
      };
    },
    enabled: !!eventId,
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });
};
