"use client";

import { Suspense, useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";

const INTERVALS = ["1", "15", "60", "240", "D", "W"] as const;

function ChartInner() {
  const params = useSearchParams();
  const [symbol, setSymbol] = useState(
    (params.get("symbol") || "NVDA").toUpperCase());
  const [input, setInput] = useState(symbol);
  const [interval, setIv] = useState<string>(params.get("interval") || "D");
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  const go = useCallback(() => {
    const s = input.trim().toUpperCase();
    if (s) setSymbol(s);
  }, [input]);

  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen();
  };

  return (
    <div className="flex h-screen w-screen flex-col bg-[#0b0f1a]">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-[13px] font-semibold text-accent">VAIIP</span>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go()}
          className="w-28 rounded border border-border bg-transparent px-2 py-1 text-[13px] uppercase outline-none focus:border-accent"
          placeholder="SYMBOL"
        />
        <button onClick={go}
          className="rounded bg-accent px-2.5 py-1 text-[12px] font-semibold text-[#0b0f1a]">
          Load
        </button>
        {INTERVALS.map((i) => (
          <button key={i} onClick={() => setIv(i)}
            className={`rounded-full px-2.5 py-0.5 text-[11px] transition ${
              interval === i ? "bg-accent text-[#0b0f1a] font-semibold"
                             : "text-dim hover:text-text"}`}>
            {i}
          </button>
        ))}
        <div className="flex-1" />
        <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="rounded-full border border-border px-2.5 py-0.5 text-[11px] text-dim hover:text-text">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button onClick={toggleFs}
          className="rounded-full border border-border px-2.5 py-0.5 text-[11px] text-dim hover:text-text">
          Fullscreen
        </button>
      </div>
      <iframe
        key={`${symbol}:${interval}:${theme}`}
        title={`${symbol} chart`}
        src={`https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(symbol)}&interval=${interval}&theme=${theme}&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=1&studies=%5B%22RSI%40tv-basicstudies%22,%22MACD%40tv-basicstudies%22%5D&withindicators_access=1&withstudies_overrides=1&withdetails=1`}
        className="min-h-0 flex-1 border-0"
        allow="fullscreen"
      />
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense fallback={<div className="h-screen bg-[#0b0f1a]" />}>
      <ChartInner />
    </Suspense>
  );
}
