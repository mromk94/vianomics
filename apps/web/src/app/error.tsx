"use client";

import { ErrorState } from "@/components/ui/error-state";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <ErrorState
        title="Something went wrong"
        detail={error.message}
        onRetry={reset}
      />
    </div>
  );
}
