type Tone = "pos" | "neg" | "warn" | "info" | "neutral";

const TONES: Record<Tone, string> = {
  pos: "bg-pos-bg text-pos border-pos/30",
  neg: "bg-neg-bg text-neg border-neg/30",
  warn: "bg-warn-bg text-warn border-warn/30",
  info: "bg-info-bg text-info border-info/30",
  neutral: "bg-surface-3 text-dim border-border",
};

export function StatusBadge({
  tone = "neutral",
  children,
  dot = false,
}: {
  tone?: Tone;
  children: React.ReactNode;
  dot?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${TONES[tone]}`}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function healthTone(
  status: "up" | "down" | "degraded" | "unconfigured" | string,
): Tone {
  switch (status) {
    case "up":
      return "pos";
    case "down":
      return "neg";
    case "degraded":
      return "warn";
    default:
      return "info";
  }
}

export function severityTone(s: "info" | "warning" | "critical"): Tone {
  return s === "critical" ? "neg" : s === "warning" ? "warn" : "info";
}
