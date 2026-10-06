import { Search, X } from 'lucide-react';
import { Input } from '@/components/ui/input';

interface EventSearchProps {
  query: string;
  onQueryChange: (query: string) => void;
  /** Events matching the query and the current filters. */
  resultCount: number;
}

export const EventSearch = ({ query, onQueryChange, resultCount }: EventSearchProps) => {
  const trimmed = query.trim();

  return (
    <form
      role="search"
      className="bg-card border border-border p-5 space-y-2"
      onSubmit={(e) => e.preventDefault()}
    >
      <h4 className="font-serif font-bold text-base border-b border-border pb-1">Search</h4>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onQueryChange('');
          }}
          placeholder="Search events..."
          aria-label="Search events by keyword"
          className="pl-9 pr-9 font-sans [&::-webkit-search-cancel-button]:appearance-none"
        />
        {trimmed && (
          <button
            type="button"
            onClick={() => onQueryChange('')}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <p className="text-xs text-muted-foreground font-sans" aria-live="polite">
        {trimmed ? (
          <>
            <span className="font-bold text-foreground">{resultCount}</span>
            {resultCount === 1 ? ' match for ' : ' matches for '}
            &ldquo;{trimmed}&rdquo;
          </>
        ) : (
          'Title, venue, category or neighborhood.'
        )}
      </p>
    </form>
  );
};
