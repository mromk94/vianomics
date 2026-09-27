import { ReactNode } from "react";

export function MetricCard({
  label,
  value,
  sub,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "neutral" | "pos" | "neg" | "warn" | "accent";
  icon?: ReactNode;
}) {
  const toneCls =
    tone === "pos"
      ? "text-pos"
      : tone === "neg"
        ? "text-neg"
        : tone === "warn"
          ? "text-warn"
          : tone === "accent"
            ? "text-accent"
            : "text-text";
  return (
    <div className="glass flex flex-col gap-1.5 p-4">
      <span className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-dim uppercase">
        {icon}
        {label}
      </span>
      <span className={`num text-xl font-semibold ${toneCls}`}>{value}</span>
      {sub && <span className="num text-[12px] text-faint">{sub}</span>}
    </div>
  );
}
