import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";

export function EmptyState({
  icon: Icon = Inbox,
  title = "No data",
  hint,
  action,
}: {
  icon?: LucideIcon;
  title?: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-8 text-center">
      <Icon className="size-6 text-faint" aria-hidden />
      <div className="text-sm font-medium text-dim">{title}</div>
      {hint && <div className="max-w-sm text-xs text-faint">{hint}</div>}
      {action}
    </div>
  );
}
