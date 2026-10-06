import { useQuery } from '@tanstack/react-query';
import { fetchApi } from '@/lib/config';

interface SourcesResponse {
  sources: Record<string, number>;
}

export const useSources = () => {
  return useQuery({
    queryKey: ['sources'],
    queryFn: async () => {
      const data = await fetchApi<SourcesResponse>('/sources');

      // Return sorted array of source names
      return Object.keys(data.sources).sort((a, b) => a.localeCompare(b));
    },
  });
};
