"use client";

import { useState } from "react";
import { CircleHelp } from "lucide-react";

/** ⓘ hover/click explanation — for non-technical users. `text` is
 * plain language; `link` optionally points at /docs#anchor. */
export function InfoTip({ text, link }: { text: string; link?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex items-center align-middle">
      <button
        type="button"
        aria-label="What is this?"
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="ml-1.5 inline-flex text-faint transition-colors hover:text-accent"
      >
        <CircleHelp className="size-3.5" />
      </button>
      {open && (
        <span className="absolute left-0 top-5 z-40 w-64 rounded-lg border border-border-strong bg-surface-2 p-3 text-left text-[11.5px] font-normal normal-case leading-relaxed tracking-normal text-dim shadow-xl">
          {text}
          {link && (
            <a href={link} className="mt-1 block text-accent hover:underline">
              Docs →
            </a>
          )}
        </span>
      )}
    </span>
  );
}
