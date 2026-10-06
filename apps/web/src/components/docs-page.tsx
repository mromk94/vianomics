"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";

/** Plain-English docs — every module, every key, what it's for and
 * how to use it. Anchors: #start #pipeline #modules #keys #execution
 * #safety #glossary #faq */

type Block = { id: string; title: string; body: React.ReactNode };

const P = ({ children }: { children: React.ReactNode }) => (
  <p className="mb-2 text-[13px] leading-relaxed text-dim">{children}</p>
);
const B = ({ children }: { children: React.ReactNode }) => (
  <b className="text-text">{children}</b>
);
const Code = ({ children }: { children: React.ReactNode }) => (
  <code className="rounded bg-surface-3 px-1 py-0.5 text-[11px] text-accent">{children}</code>
);
const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
  <li className="border-b border-border/40 py-1.5 last:border-0">
    <b className="text-[12px] text-text">{k}</b>
    <span className="block text-[12px] text-dim">{v}</span>
  </li>
);

const BLOCKS: Block[] = [
  {
    id: "start",
    title: "Getting started — your first 5 minutes",
    body: (
      <>
        <P>Welcome to VAIIP — the <B>Vesturs AI Investment Intelligence Platform</B>. It turns your investment framework into a complete, auditable Trader OS: from finding a stock, through research and valuation, to a human-approved order — with every step recorded.</P>
        <P><B>1. Sign in.</B> Go to <B>Settings</B> (last item in the sidebar) and sign in. Your account email is the one the admin created — for the seeded system, <Code>admin@vesturs.com</Code>. Signing in unlocks actions like running research, approving orders and submitting paper trades.</P>
        <P><B>2. Look at the Command Center.</B> It&apos;s the landing page and shows the market regime, open alerts, pending approvals, your watchlist and the latest decisions — all from real stored data.</P>
        <P><B>3. Understand the principle.</B> Nothing here trades by itself. The pipeline is <B>analyze → risk check → human approval → execution</B>. AI can only recommend; only you can approve.</P>
      </>
    ),
  },
  {
    id: "pipeline",
    title: "How a decision becomes an order (the pipeline)",
    body: (
      <>
        <P>Every idea travels the same gauntlet. Each stage can stop it:</P>
        <ul className="mb-2 list-disc pl-5 text-[13px] text-dim">
          <li><B>Universe</B> — is the stock allowed by the mandate?</li>
          <li><B>Data</B> — are fundamentals and prices loaded and fresh?</li>
          <li><B>Screening</B> — does it pass the Green Zone (score ≥ threshold)?</li>
          <li><B>Research</B> — an 18-section dossier with sourced claims.</li>
          <li><B>Valuation</B> — what is it worth (Rule #1, DCF, margin of safety)?</li>
          <li><B>Quant</B> — momentum, volatility, correlation statistics.</li>
          <li><B>Macro</B> — is the regime (Fear & Greed, VIX, economy) favorable?</li>
          <li><B>Technical</B> — is now a good moment to enter?</li>
          <li><B>Risk</B> — position size, stop, limits. A risk block is a veto.</li>
          <li><B>CIO synthesis</B> — blends all votes into a verdict + confidence.</li>
          <li><B>Human approval</B> — you. Without it, nothing executes.</li>
          <li><B>Execution</B> — submits to the broker (paper by default).</li>
        </ul>
        <P>Result: a decision can be great research yet still not trade — and that&apos;s by design.</P>
      </>
    ),
  },
  {
    id: "modules",
    title: "Every page, explained",
    body: (
      <ul className="text-[13px] text-dim">
        <Row k="Command Center (home)" v="The dashboard. Regime, alerts, approvals, watchlist, latest decisions, data-provider health. Start here every day." />
        <Row k="Market" v="Latest daily prices for every tracked instrument — close, day change, range, volume. Delayed EOD data, not a live tape." />
        <Row k="Macro" v="Economic regime and market sentiment: Fear & Greed, VIX, FRED economic series. Drives sector preferences and risk posture." />
        <Row k="Technical" v="Scan board: per-ticker timing signals (RSI dip entries, Aroon trend) and which are fresh. Click a ticker for the Trading Desk chart." />
        <Row k="Investment Universe" v="The list of stocks the system may look at — filtered by your mandate (exchanges, sectors, exclusions). Everything starts here." />
        <Row k="Screener" v="Green Zone scoring — 20 criteria per stock. Passing = worth researching, not worth buying." />
        <Row k="Research" v="18-section dossier per company with evidence-linked claims. Run a dossier, read the claims, check the sources." />
        <Row k="Valuation" v="Rule #1 Sticker price, DCF, reverse DCF, margin of safety. 'Undervalued' means price is below computed worth — still needs timing and risk approval." />
        <Row k="Quant" v="Factor scores (momentum, quality, value, low-vol), correlations, volatility, beta. Numbers, no opinion." />
        <Row k="Backtesting" v="Strategy sandbox — run strategies on historical data with real costs and slippage. Walk-forward and Monte Carlo check robustness. Results are simulated, never promised live returns." />
        <Row k="Trading Desk" v="Candlestick chart + order blotter. See signals, open orders, fills with latency and commissions." />
        <Row k="Portfolio" v="What you own — positions, weights, unrealized P&L. Fills book here automatically." />
        <Row k="Allocation" v="Mandate targets (70% investment / 30% trading) vs actual sector and position weights — your drift view." />
        <Row k="Risk Center" v="Drawdown, concentration, correlation, VaR, stress tests. Any breached limit blocks the order." />
        <Row k="Committee" v="The AI committee — specialist votes, CIO synthesis, conflicts. CIO approval ≠ trade." />
        <Row k="Monitoring" v="Alert inbox — regime changes, stale data, stuck orders, stop breaches. Acknowledge or resolve." />
        <Row k="Journal" v="The immutable audit trail — every decision and order, replayable." />
        <Row k="Backtesting" v="(Research sandbox — see above.)" />
        <Row k="Data Operations" v="Provider jobs, sync freshness, quarantined records. Data failures alert here and in Monitoring." />
        <Row k="Settings" v="Sign-in, mandate editor, API keys status, model list." />
      </ul>
    ),
  },
  {
    id: "keys",
    title: "API keys & data sources — where to get each",
    body: (
      <>
        <P>Keys live in <Code>apps/api/.env</Code> on the server — one line each, then restart the API. The Settings page shows which are set (never the values).</P>
        <ul className="text-[13px] text-dim">
          <Row k="Tiingo — market prices" v="tiingo.com → free account → API token. Enables daily OHLCV bars. Env: TIINGO_API_KEY" />
          <Row k="FRED — economics" v="fred.stlouisfed.org → My Account → API Keys → request key. Enables CPI, rates, PMI. Env: FRED_API_KEY" />
          <Row k="SEC EDGAR — fundamentals" v="No key; SEC just wants a contact email as User-Agent. Env: EDGAR_USER_AGENT=Your Name you@you.com" />
          <Row k="Fear & Greed / VIX" v="Pulled from public indices where available; no key." />
          <Row k="Interactive Brokers — trading" v="IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID + TWS or Gateway running with API enabled (paper: port 7497, live: 7496). See the execution section before enabling." />
          <Row k="ADMIN_PASSWORD" v="The admin password set when the system was seeded. Keep it out of git." />
          <Row k="AI model providers" v="Settings → AI models: pick Anthropic, OpenAI, Gemini, DeepSeek, Kimi, local Ollama, or a custom endpoint, paste the key, choose the model, mark default. Keys stay server-side (shown masked). Today all analysis is deterministic — models power narrative features when added." />
          <Row k="Demo / live data" v="Settings → Demo/live toggle. Demo fills empty sections with labeled examples; live mode shows honest empty states. Real sections are real either way." />
        </ul>
        <P><B>If a provider fails</B>, the system raises a data-quality alert — it never pretends missing data means no risk.</P>
      </>
    ),
  },
  {
    id: "execution",
    title: "Execution & paper trading — the safety rules",
    body: (
      <>
        <P><B>Paper mode is the default and intended Phase-1 mode.</B> Orders route to the paper broker — simulated fills, real lifecycle, real records. Nothing touches real money.</P>
        <P><B>Going live requires all of these, deliberately:</B></P>
        <ul className="mb-2 list-disc pl-5 text-[13px] text-dim">
          <li><Code>EXECUTION_ENABLED=true</Code> in .env (off by default)</li>
          <li>IBKR host/port/client-id set + TWS or Gateway running with API enabled</li>
          <li>Market-data subscriptions active in TWS for what you trade</li>
          <li>Human approval on the exact order parameters — any change requires re-approval</li>
          <li>Kill switch off; notional and quantity limits pass</li>
        </ul>
        <P>Even then, every order re-validates risk right before submission and is deduplicated — a repeat click can never double-fire. If a submission's result is uncertain (disconnect/timeout), the system reconciles with the broker instead of retrying blindly.</P>
        <P><B>Kill switch:</B> <Code>POST /api/v1/execution/kill-switch</Code> — halts all new submissions instantly.</P>
      </>
    ),
  },
  {
    id: "safety",
    title: "What the AI can and cannot do",
    body: (
      <>
        <P><B>Agents can:</B> analyze, score, flag risks, propose nothing — they write reports, not orders.</P>
        <P><B>Agents cannot:</B> submit orders, approve trades, change the mandate, change risk limits, or modify each other's authority. The CIO synthesizes a verdict; it is not an executable instruction.</P>
        <P><B>Humans can:</B> approve orders, edit the mandate (versioned, audited), set risk limits, flip the kill switch.</P>
        <P>Every action writes an audit event — the Journal is replayable and immutable.</P>
      </>
    ),
  },
  {
    id: "glossary",
    title: "Plain-language glossary",
    body: (
      <ul className="text-[13px] text-dim">
        <Row k="Green Zone" v="A 20-point screening score. Higher = stronger candidate for research." />
        <Row k="Margin of safety" v="How far price sits below estimated worth. Positive = cheap vs value." />
        <Row k="Rule #1 / Sticker" v="Phil Town's valuation — the price a great business is worth, plus the MOS-adjusted buy price." />
        <Row k="ATR" v="Average True Range — typical daily movement; sets stop distance and pyramid size." />
        <Row k="Fear & Greed" v="0–100 sentiment gauge. ≥80 = extreme greed → new buys restricted." />
        <Row k="Regime" v="Economic backdrop (recovery/expansion/slowdown/recession) + market risk appetite (risk-on/neutral/risk-off)." />
        <Row k="Pyramid" v="Adding to winners in tranches, each smaller, each with a higher trailing stop." />
        <Row k="Walk-forward" v="Backtest across consecutive periods — a strategy that only works in one period is suspect." />
        <Row k="Monte Carlo" v="Reshuffles past trades 500× — shows the range of outcomes, not a forecast." />
        <Row k="Idempotency key" v="Fingerprint on an order — resubmitting returns the same order, never duplicates." />
        <Row k="Reconciliation" v="Asking the broker what actually happened instead of assuming." />
      </ul>
    ),
  },
  {
    id: "faq",
    title: "Common questions",
    body: (
      <>
        <P><B>"Why does my page say DEMO?"</B> — That section has no real data yet (e.g., an empty portfolio). It&apos;s filled with a clearly-labeled example so you see the layout. Real data replaces it automatically.</P>
        <P><B>"The committee says approve — why no order?"</B> — CIO approval is a recommendation. You must approve the order ticket, then execution submits it. Two separate human steps.</P>
        <P><B>"Why can't I submit to IBKR?"</B> — Check Settings → API keys: IBKR_HOST/PORT/CLIENT_ID must be set and TWS running. Then EXECUTION_ENABLED=true. Unconfigured IBKR refuses loudly rather than pretending.</P>
        <P><B>"Is backtest performance real?"</B> — No — it's simulated with realistic costs and labeled as such everywhere. Past simulated performance never promises live results.</P>
        <P><B>"Data looks stale?"</B> — Check Data Operations — failed jobs appear there and as alerts in Monitoring.</P>
      </>
    ),
  },
];

export function DocsPage() {
  const [open, setOpen] = useState<string | null>("start");
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PageHeader title="Docs & Help"
        subtitle="how VAIIP works, where data comes from, and how to operate it — plain language"
        info="This page explains every feature for non-technical users. Collapse any section when you know it." />

      {/* quick nav */}
      <div className="glass flex flex-wrap gap-1.5 p-2.5">
        {BLOCKS.map((b) => (
          <a key={b.id} href={`#${b.id}`}
            onClick={() => setOpen(b.id)}
            className={`rounded-full px-3 py-1 text-[11px] transition ${
              open === b.id ? "bg-accent font-semibold text-[#0b0f1a]"
              : "border border-border text-dim hover:text-text"}`}>
            {b.title.split(" — ")[0]}
          </a>
        ))}
      </div>

      {BLOCKS.map((b) => (
        <SectionCard key={b.id} id={b.id} className="rise scroll-mt-16"
          title={b.title}
          action={
            <button onClick={() => toggle(b.id)} className="text-faint hover:text-text">
              {open === b.id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            </button>
          }>
          {open === b.id && <div className="pt-1">{b.body}</div>}
        </SectionCard>
      ))}

      <p className="pb-6 text-center text-[11px] text-faint">
        VAIIP · Vesturs AI Investment Intelligence Platform — every
        decision auditable, every number sourced, every trade yours.
      </p>
    </div>
  );
}
