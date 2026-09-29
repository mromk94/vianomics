"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { ChatWidget } from "./chat-widget";
import { CommandPalette } from "./command-palette";
import { SymbolDrawer } from "./symbol-drawer";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

const PUBLIC_PATHS = new Set(["/login", "/chart"]);

export function Shell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.has(pathname);
  const [authed, setAuthed] = useState<boolean | null>(null);

  // auth gate — every module requires a session token
  useEffect(() => {
    if (isPublic) return;
    const check = () => {
      const t = window.localStorage.getItem("vaiip-token");
      if (!t) router.replace("/login");
      else setAuthed(true);
    };
    check();
    window.addEventListener("vaiip:unauth", check);
    return () => window.removeEventListener("vaiip:unauth", check);
  }, [isPublic, router]);

  // bare surfaces — no chrome, no gate
  if (isPublic) return <>{children}</>;
  // gate: render nothing while checking / redirecting
  if (!authed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0b0f1a]">
        <div className="size-6 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }
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
      <SymbolDrawer />
      <ChatWidget />
    </div>
  );
}
