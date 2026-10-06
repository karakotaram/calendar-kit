export const EventCardSkeleton = () => {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-4 p-3 bg-card border border-transparent animate-pulse">
      {/* Thumbnail placeholder */}
      <div className="w-[140px] h-[100px] bg-muted" />

      {/* Text content */}
      <div className="space-y-2 min-w-0">
        {/* Category */}
        <div className="h-3 bg-muted w-16" />
        {/* Title */}
        <div className="h-5 bg-muted w-3/4" />
        {/* Meta line */}
        <div className="h-4 bg-muted w-full max-w-[280px]" />
        {/* Show details */}
        <div className="h-3 bg-muted w-20" />
      </div>
    </div>
  );
};

export const EventCardSkeletonList = ({ count = 5 }: { count?: number }) => {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <EventCardSkeleton key={i} />
      ))}
    </div>
  );
};
