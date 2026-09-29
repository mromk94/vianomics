"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";

import { ChatWidget } from "./chat-widget";
import { CommandPalette } from "./command-palette";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

export function Shell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  // /chart is a bare full-viewport surface for pop-out monitors
  if (pathname === "/chart") return <>{children}</>;
  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-accent focus:px-3 focus:py-2 focus:text-white"
      >
        Skip to content
      </a>
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenu={() => setMobileOpen(true)} />
        <main id="main" className="flex-1 p-4 md:p-6">
          {children}
        </main>
      </div>
      <CommandPalette />
      <ChatWidget />
    </div>
  );
}
