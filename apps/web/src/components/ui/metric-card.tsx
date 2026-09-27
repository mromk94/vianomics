export function MetricCard({
  label,
  value,
  sub,
  tone,
  loading = false,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "pos" | "neg" | "warn" | "default";
  loading?: boolean;
}) {
  const toneClass =
    tone === "pos"
      ? "text-pos"
      : tone === "neg"
        ? "text-neg"
        : tone === "warn"
          ? "text-warn"
          : "text-text";
  return (
    <div className="rounded-md border border-border bg-surface px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-faint">
        {label}
      </div>
      {loading ? (
        <div className="mt-2 h-7 w-24 animate-pulse rounded bg-surface-3" />
      ) : (
        <div className={`num mt-1 text-xl font-semibold ${toneClass}`}>
          {value}
        </div>
      )}
      {sub && !loading && (
        <div className="num mt-0.5 text-xs text-dim">{sub}</div>
      )}
    </div>
  );
}
