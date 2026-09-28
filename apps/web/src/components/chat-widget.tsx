"use client";

import { useEffect, useRef, useState } from "react";
import { MessageCircle, Send, X } from "lucide-react";

import { apiPost } from "@/lib/api";

interface Msg { role: "user" | "assistant"; content: string; src?: string }

const QUICK = ["What's the regime?", "Any alerts?", "Open orders?",
               "Where is the screener?", "How do I approve a trade?"];

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: "assistant",
      content: "I'm the VAIIP assistant — ask about the market, alerts, orders, or where anything lives. Add a model in Settings → AI models for free-form chat." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, open]);

  const send = async (text?: string) => {
    const m = (text ?? input).trim();
    if (!m || busy) return;
    setInput("");
    setMsgs((x) => [...x, { role: "user", content: m }]);
    setBusy(true);
    try {
      const r = await apiPost<{ reply: string; source: string }>(
        "/api/v1/assistant/chat", { message: m, history: msgs.slice(-6) }, 45000);
      setMsgs((x) => [...x, { role: "assistant", content: r.reply, src: r.source }]);
    } catch {
      setMsgs((x) => [...x, { role: "assistant", content: "Couldn't reach the API — is it running?" }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* floating button */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="AI assistant"
        className="fixed bottom-5 right-5 z-50 flex size-12 items-center justify-center rounded-full bg-accent text-[#0b0f1a] shadow-lg shadow-accent/30 transition hover:brightness-110"
      >
        {open ? <X className="size-5" /> : <MessageCircle className="size-5" />}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex flex-col bg-surface-solid sm:inset-auto sm:bottom-20 sm:right-5 sm:h-[480px] sm:w-[360px] sm:overflow-hidden sm:rounded-2xl sm:border sm:border-border-strong sm:shadow-2xl">
          <header className="flex items-center justify-between border-b border-border bg-surface-solid px-4 py-3">
            <div>
              <div className="text-[13px] font-semibold">VAIIP Assistant</div>
              <div className="text-[10px] text-faint">answers from live system state — never fabricates</div>
            </div>
            <button onClick={() => setOpen(false)} aria-label="Close chat"
              className="rounded-full p-1.5 text-dim hover:text-text sm:hidden">
              <X className="size-5" />
            </button>
          </header>
          <div className="flex-1 space-y-2.5 overflow-y-auto p-3">
            {msgs.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] min-w-0 break-words rounded-2xl px-3 py-2 text-[12.5px] leading-relaxed ${
                  m.role === "user"
                    ? "bg-accent text-[#0b0f1a]"
                    : "bg-surface-2 text-text"}`}>
                  {m.content}
                  {m.src && m.src !== "deterministic" && (
                    <div className="mt-0.5 text-[9px] opacity-50">via {m.src}</div>
                  )}
                </div>
              </div>
            ))}
            {busy && <div className="text-[11px] text-faint">thinking…</div>}
            <div ref={bottomRef} />
          </div>
          {msgs.length <= 1 && (
            <div className="flex flex-wrap gap-1 px-3 pb-2">
              {QUICK.map((q) => (
                <button key={q} onClick={() => send(q)}
                  className="rounded-full border border-border px-2.5 py-0.5 text-[10px] text-dim hover:border-accent hover:text-accent">
                  {q}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 border-t border-border bg-surface-solid p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Ask anything…"
              className="flex-1 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-[12px] text-text placeholder:text-faint focus:border-accent focus:outline-none"
            />
            <button onClick={() => send()} disabled={busy || !input.trim()}
              className="flex size-8 items-center justify-center rounded-full bg-accent text-[#0b0f1a] disabled:opacity-40">
              <Send className="size-3.5" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
