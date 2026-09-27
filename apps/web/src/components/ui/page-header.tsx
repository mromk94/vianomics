export function PageHeader({
  title,
  description,
  meta,
  actions,
}: {
  title: string;
  description?: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && (
          <p className="mt-0.5 max-w-2xl text-sm text-dim">{description}</p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {meta}
        {actions}
      </div>
    </div>
  );
}
