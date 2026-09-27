export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded bg-surface-3 ${className}`}
    />
  );
}

export function SkeletonRows({ n = 4 }: { n?: number }) {
  return (
    <div className="space-y-2 p-3" aria-label="Loading">
      {Array.from({ length: n }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}
