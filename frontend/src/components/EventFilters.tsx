import { EventType, FeeFilter } from '@/types/event';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { CalendarIcon, ChevronDown, List, Map } from 'lucide-react';
import { format } from 'date-fns';
import { cn } from '@/lib/utils';
import { MAP_ENABLED, siteNow } from '@/lib/site';

const EVENT_TYPES: { value: EventType; label: string }[] = [
  { value: 'music', label: 'Music' },
  { value: 'arts and culture', label: 'Arts & Culture' },
  { value: 'food and drink', label: 'Food & Drink' },
  { value: 'theater', label: 'Theater' },
  { value: 'lectures', label: 'Lectures' },
  { value: 'sports', label: 'Sports' },
  { value: 'community', label: 'Community' },
  { value: 'other', label: 'Other' },
];

interface EventFiltersProps {
  selectedEventTypes: EventType[];
  feeFilter: FeeFilter;
  familyFriendly: boolean;
  selectedDate: Date | undefined;
  selectedSources: string[];
  sources: string[];
  view: 'list' | 'map';
  onViewChange: (view: 'list' | 'map') => void;
  onEventTypesChange: (types: EventType[]) => void;
  onFeeFilterChange: (fee: FeeFilter) => void;
  onFamilyFriendlyChange: (value: boolean) => void;
  onDateChange: (date: Date | undefined) => void;
  onSourcesChange: (sources: string[]) => void;
  onReset: () => void;
}

export const EventFilters = ({
  selectedEventTypes,
  feeFilter,
  familyFriendly,
  selectedDate,
  selectedSources,
  sources,
  view,
  onViewChange,
  onEventTypesChange,
  onFeeFilterChange,
  onFamilyFriendlyChange,
  onDateChange,
  onSourcesChange,
  onReset,
}: EventFiltersProps) => {
  const handleEventTypeToggle = (type: EventType) => {
    if (selectedEventTypes.includes(type)) {
      onEventTypesChange(selectedEventTypes.filter(t => t !== type));
    } else {
      onEventTypesChange([...selectedEventTypes, type]);
    }
  };

  const handleSelectAllEventTypes = () => {
    if (selectedEventTypes.length === EVENT_TYPES.length) {
      onEventTypesChange([]);
    } else {
      onEventTypesChange(EVENT_TYPES.map(t => t.value));
    }
  };

  const getEventTypesLabel = () => {
    if (selectedEventTypes.length === 0) return 'All Types';
    if (selectedEventTypes.length === 1) {
      const found = EVENT_TYPES.find(t => t.value === selectedEventTypes[0]);
      return found?.label || selectedEventTypes[0];
    }
    if (selectedEventTypes.length === EVENT_TYPES.length) return 'All Types';
    return `${selectedEventTypes.length} types selected`;
  };

  const handleSourceToggle = (source: string) => {
    if (selectedSources.includes(source)) {
      onSourcesChange(selectedSources.filter(s => s !== source));
    } else {
      onSourcesChange([...selectedSources, source]);
    }
  };

  const handleSelectAllSources = () => {
    if (selectedSources.length === sources.length) {
      onSourcesChange([]);
    } else {
      onSourcesChange([...sources]);
    }
  };

  const getSourcesLabel = () => {
    if (selectedSources.length === 0) return 'All Sources';
    if (selectedSources.length === 1) return selectedSources[0];
    if (selectedSources.length === sources.length) return 'All Sources';
    return `${selectedSources.length} sources selected`;
  };

  return (
    <div className="bg-card border border-border p-5 space-y-5">
      {/* View toggle: only when the map is switched on and has a token */}
      {MAP_ENABLED && (
        <>
          <div className="space-y-2">
            <h4 className="font-serif font-bold text-base border-b border-border pb-1">View</h4>
            <div className="flex gap-2">
              <Button variant={view === 'list' ? 'default' : 'outline'} size="sm" onClick={() => onViewChange('list')} aria-pressed={view === 'list'} className="gap-2 font-sans flex-1">
                <List className="w-4 h-4" />
                List
              </Button>
              <Button variant={view === 'map' ? 'default' : 'outline'} size="sm" onClick={() => onViewChange('map')} aria-pressed={view === 'map'} className="gap-2 font-sans flex-1">
                <Map className="w-4 h-4" />
                Map
              </Button>
            </div>
          </div>

          <hr className="border-border" />
        </>
      )}

      {/* Date */}
      <div className="space-y-2">
        <h4 className="font-serif font-bold text-base border-b border-border pb-1">Date</h4>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className={cn(
                "w-full justify-start text-left font-normal font-sans",
                !selectedDate && "text-muted-foreground"
              )}
            >
              <CalendarIcon className="mr-2 h-4 w-4" />
              {selectedDate ? format(selectedDate, "PPP") : <span>Pick a date</span>}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="single"
              selected={selectedDate}
              onSelect={onDateChange}
              disabled={(date) => {
                // Today where the events are, not where the reader is
                const today = siteNow();
                today.setHours(0, 0, 0, 0);
                return date < today;
              }}
              today={undefined}
              initialFocus
              className="pointer-events-auto"
            />
          </PopoverContent>
        </Popover>
      </div>

      <hr className="border-border" />

      {/* Categories */}
      <div className="space-y-2">
        <h4 className="font-serif font-bold text-base border-b border-border pb-1">Categories</h4>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className="w-full justify-between text-left font-normal font-sans"
            >
              <span className="truncate">{getEventTypesLabel()}</span>
              <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-0 bg-background border-border z-50" align="start">
            <ScrollArea className="h-60" scrollbarAlwaysVisible>
              <div className="p-2">
                <div
                  className="flex items-center space-x-2 p-2 hover:bg-muted cursor-pointer"
                  onClick={handleSelectAllEventTypes}
                >
                  <Checkbox
                    checked={selectedEventTypes.length === EVENT_TYPES.length && EVENT_TYPES.length > 0}
                    onCheckedChange={handleSelectAllEventTypes}
                  />
                  <span className="text-sm font-medium font-sans">Select All</span>
                </div>
                <div className="border-t border-border my-1" />
                {EVENT_TYPES.map((type) => (
                  <div
                    key={type.value}
                    className="flex items-center space-x-2 p-2 hover:bg-muted cursor-pointer"
                    onClick={() => handleEventTypeToggle(type.value)}
                  >
                    <Checkbox
                      checked={selectedEventTypes.includes(type.value)}
                      onCheckedChange={() => handleEventTypeToggle(type.value)}
                    />
                    <span className="text-sm truncate font-sans capitalize">{type.label}</span>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </PopoverContent>
        </Popover>
      </div>

      <hr className="border-border" />

      {/* Cost */}
      <div className="space-y-2">
        <h4 className="font-serif font-bold text-base border-b border-border pb-1">Cost</h4>
        <Select value={feeFilter} onValueChange={(value) => onFeeFilterChange(value as FeeFilter)}>
          <SelectTrigger className="font-sans">
            <SelectValue placeholder="Select cost" />
          </SelectTrigger>
          <SelectContent className="bg-background border-border z-50">
            <SelectItem value="all">All Events</SelectItem>
            <SelectItem value="free">Free Only</SelectItem>
            <SelectItem value="paid">Paid Only</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <hr className="border-border" />

      {/* Source */}
      <div className="space-y-2">
        <h4 className="font-serif font-bold text-base border-b border-border pb-1">Source</h4>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className="w-full justify-between text-left font-normal font-sans"
            >
              <span className="truncate">{getSourcesLabel()}</span>
              <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-0 bg-background border-border z-50" align="start">
            <ScrollArea className="h-60" scrollbarAlwaysVisible>
              <div className="p-2">
                <div
                  className="flex items-center space-x-2 p-2 hover:bg-muted cursor-pointer"
                  onClick={handleSelectAllSources}
                >
                  <Checkbox
                    checked={selectedSources.length === sources.length && sources.length > 0}
                    onCheckedChange={handleSelectAllSources}
                  />
                  <span className="text-sm font-medium font-sans">Select All</span>
                </div>
                <div className="border-t border-border my-1" />
                {sources.map((source) => (
                  <div
                    key={source}
                    className="flex items-center space-x-2 p-2 hover:bg-muted cursor-pointer"
                    onClick={() => handleSourceToggle(source)}
                  >
                    <Checkbox
                      checked={selectedSources.includes(source)}
                      onCheckedChange={() => handleSourceToggle(source)}
                    />
                    <span className="text-sm truncate font-sans">{source}</span>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </PopoverContent>
        </Popover>
      </div>

      <hr className="border-border" />

      {/* Family Friendly */}
      <div className="flex items-center justify-between">
        <Label htmlFor="family-friendly" className="text-sm font-sans font-semibold text-foreground">
          Family Friendly
        </Label>
        <Switch
          id="family-friendly"
          checked={familyFriendly}
          onCheckedChange={onFamilyFriendlyChange}
        />
      </div>

      {/* Reset */}
      <Button variant="outline" size="sm" onClick={onReset} className="w-full font-sans text-xs uppercase tracking-wide">
        Reset All Filters
      </Button>
    </div>
  );
};
