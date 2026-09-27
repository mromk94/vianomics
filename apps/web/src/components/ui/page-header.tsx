export function PageHeader({
  title,
  subtitle,
  description,
  meta,
  actions,
  demo = false,
}: {
  title: string;
  subtitle?: string;
  description?: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  demo?: boolean;
}) {
  const sub = subtitle ?? description;
  return (
    <div className="mb-1 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="flex items-center gap-2.5 text-lg font-semibold tracking-tight">
          {title}
          {demo && (
            <span className="rounded-full border border-warn/40 bg-warn-bg px-2 py-px text-[10px] font-bold tracking-wider text-warn">
              DEMO
            </span>
          )}
        </h1>
        {sub && <p className="mt-0.5 max-w-2xl text-[13px] text-dim">{sub}</p>}
      </div>
      <div className="flex items-center gap-2 text-[12px] text-faint">
        {meta}
        {actions}
      </div>
    </div>
  );
}
