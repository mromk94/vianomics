export function SectionCard({
  title,
  badge,
  actions,
  children,
  padded = true,
  className = "",
}: {
  title?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  padded?: boolean;
  className?: string;
}) {
  return (
    <section
      className={`overflow-hidden rounded-md border border-border bg-surface ${className}`}
    >
      {(title || badge || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2">
            {title && (
              <h2 className="text-[13px] font-semibold tracking-wide text-dim uppercase">
                {title}
              </h2>
            )}
            {badge}
          </div>
          {actions}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function DemoBadge() {
  return (
    <span
      title="Demonstration fixture — not live brokerage data"
      className="rounded border border-warn/40 bg-warn-bg px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-warn uppercase"
    >
      Demo
    </span>
  );
}
