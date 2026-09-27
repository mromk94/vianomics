import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="num text-4xl font-semibold text-faint">404</div>
      <p className="text-sm text-dim">This module doesn&apos;t exist.</p>
      <Link
        href="/"
        className="rounded border border-border bg-surface-2 px-3 py-1.5 text-xs font-medium hover:bg-surface-3"
      >
        Back to Command Center
      </Link>
    </div>
  );
}
