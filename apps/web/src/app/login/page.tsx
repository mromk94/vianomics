"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";

import { apiPost, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await apiPost<{ token: string }>(
        "/api/v1/auth/login", form);
      setToken(r.token);
      router.replace("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "login failed");
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#070a12] p-4">
      {/* ambient layers */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_-20%,#14b8a622,transparent_60%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_120%,#3b82f61a,transparent_60%)]" />
        {/* subtle grid */}
        <div className="absolute inset-0 opacity-[0.07]"
          style={{ backgroundImage:
            "linear-gradient(#38bdf833 1px,transparent 1px)," +
            "linear-gradient(90deg,#38bdf833 1px,transparent 1px)",
            backgroundSize: "44px 44px" }} />
        {/* slow conic ring behind card */}
        <div className="absolute left-1/2 top-1/2 size-[640px] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-25 animate-[spin_24s_linear_infinite]"
          style={{ background:
            "conic-gradient(from 0deg,transparent 0deg,#14b8a6 60deg,transparent 120deg,#3b82f6 200deg,transparent 260deg)",
            filter: "blur(60px)" }} />
      </div>

      {/* card */}
      <div className="relative w-full max-w-[380px] animate-[rise_.5s_ease-out]">
        <div className="rounded-2xl border border-border bg-surface/80 p-7 shadow-2xl backdrop-blur-xl">
          {/* wordmark */}
          <div className="mb-7 text-center">
            <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-xl border border-accent/30 bg-accent-soft">
              <ShieldCheck className="size-5 text-accent" />
            </div>
            <div className="text-[20px] font-bold tracking-tight">
              <span className="text-accent">VAIIP</span>
              <span className="text-dim font-normal"> · Trader OS</span>
            </div>
            <div className="mt-1 text-[11px] tracking-wide text-faint uppercase">
              Vianomics AI Investment Intelligence
            </div>
          </div>

          <form onSubmit={(e) => { e.preventDefault(); submit(); }}
            className="space-y-3">
            <label className="block text-[11px] font-medium uppercase tracking-wider text-dim">
              Email
              <input type="email" required autoComplete="username"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="mt-1 w-full rounded-lg border border-border bg-[#0b0f1a] px-3 py-2.5 text-[14px] normal-case tracking-normal outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/40"
                placeholder="you@vianomics.com" />
            </label>
            <label className="block text-[11px] font-medium uppercase tracking-wider text-dim">
              Password
              <input type="password" required autoComplete="current-password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="mt-1 w-full rounded-lg border border-border bg-[#0b0f1a] px-3 py-2.5 text-[14px] normal-case tracking-normal outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/40"
                placeholder="••••••••••" />
            </label>

            {err && (
              <div className="rounded-lg border border-neg/40 bg-neg/10 px-3 py-2 text-[12px] text-neg">
                {err}
              </div>
            )}

            <button type="submit" disabled={busy}
              className="relative w-full overflow-hidden rounded-lg bg-accent py-2.5 text-[14px] font-semibold text-[#0b0f1a] transition hover:brightness-110 disabled:opacity-60">
              {busy ? "Authenticating…" : "Sign in"}
              {busy && (
                <span className="absolute inset-x-0 bottom-0 h-0.5 animate-[scan_1.1s_linear_infinite] bg-white/70" />
              )}
            </button>
          </form>

          <div className="mt-6 text-center text-[10px] text-faint">
            Authorized access only · all sessions audited
          </div>
        </div>

        <div className="mt-4 text-center text-[10px] text-faint">
          Vianomics AI © 2026
        </div>
      </div>

      <style jsx global>{`
        @keyframes rise {
          from { opacity: 0; transform: translateY(14px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes scan {
          from { transform: translateX(-100%); }
          to { transform: translateX(100%); }
        }
      `}</style>
    </div>
  );
}
