"use client";

import { AlertTriangle } from "lucide-react";

export function ErrorState({
  title = "Failed to load",
  detail,
  onRetry,
}: {
  title?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-2 px-6 py-8 text-center"
    >
      <AlertTriangle className="size-6 text-neg" aria-hidden />
      <div className="text-sm font-medium text-neg">{title}</div>
      {detail && <div className="max-w-sm text-xs text-dim">{detail}</div>}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-1 rounded border border-border bg-surface-2 px-3 py-1.5 text-xs font-medium text-text hover:bg-surface-3"
        >
          Retry
        </button>
      )}
    </div>
  );
}
