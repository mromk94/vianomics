# Vesturs — VAIIP Trader OS

**Vesturs AI Investment Intelligence Platform** — an institutional-style
investment operating system: one auditable decision chain from mandate to
execution, monitoring, and attribution.

## License

**Vesturs is proprietary software.**

The source code is publicly available for inspection and evaluation only.
It is **not open-source software** and may not be copied, modified, redistributed,
commercially used, deployed, or incorporated into competing products without
prior written permission.

See [LICENSE](./LICENSE) for the full terms.

---

## The operating chain

Every module is a stage in a single pipeline — nothing acts on a signal
that hasn't passed the stages before it:

```
Investment Mandate → Universe → Data → Screening → Research → Valuation
→ Quant → Macro → Technical Timing → Trade Construction → Risk Veto
→ Portfolio Allocation → CIO Synthesis → Human Approval → Execution
→ Monitoring → Attribution → Validation
```

Core invariants:

- **No fake data.** A number is real or the UI says why it isn't.
  Honest `insufficient_data`/`unconfigured` states everywhere — never
  demo values presented as live.
- **Point-in-time provenance.** Fundamentals, quotes, and observations
  carry source + published_at; restatements supersede, never overwrite.
- **The risk veto is not advisory.** Signals propose; the risk engine
  disposes. Nothing executes without passing it.
- **Human approval gates execution.** Agents research and recommend;
  humans approve. `EXECUTION_ENABLED` is off by default.
- **Idempotent ingestion.** Every feed can be re-run safely; dedupe is
  structural, not best-effort.

## Repository layout

```
apps/
  api/            FastAPI + SQLAlchemy(async) + Alembic — the engine
    app/
      routers/    REST surface (26 modules, /api/v1/*)
      services/   business logic: engines, sync, metrics, regime
      models/     security master, market, fundamentals, portfolio,
                  execution, governance, ops
      providers/  external adapters: tiingo|yahoo|alpaca|edgar|fred
      ingestion/  job runner, dedupe upserts, backfills
      agents/     analyzer registry + committee reports
    tests/        260+ pytest tests (sqlite in-memory, mocked HTTP)
    migrations/   alembic
  web/            Next.js 15 + React 19 + TypeScript — the workstation
    src/
      app/        routes: /[module] shell + /symbol/[ticker]
      components/ one component per workstation page + ui/ primitives
docs/             local-only runbooks (incl. the MT4 push EA) —
                  intentionally untracked
new docs/         local-only canonical specs — SOW, ATR/pyramid
                  workbook, state machines — intentionally untracked
```

## Workstation modules

| Page | What it does |
|---|---|
| Command Center | Portfolio strip (internal + external accounts), provider health, alerts, regime banner |
| Mandate | Investment mandate, constraints, policy defaults |
| Universe | Tiered universes (global → eligible → approved), memberships, watchlist |
| Screener | **Green Zone** — 20-criterion equity screen; asset-class-aware ETF variant (macro/rotation channel replaces fundamentals) |
| Research | Orchestrated dossier generation |
| Valuation | Intrinsic-value engine, margin of safety vs price |
| Quant | Factor exposures, quant verdicts |
| Macro | Regime classifier (expansion/slowdown/recession/recovery), real CNN Fear & Greed index, sector rotation preferences |
| Technical | Full-universe technical scan → persisted verdicts (entry/pullback/continuation/wait/avoid) |
| Trading | Signal search, ATR sheet, trade risk sheet, pyramid trades, order tickets |
| Risk | Risk center: book metrics, concentration, vetoes, pyramid candidates, calculator |
| Portfolio / Allocation | Holdings, sleeve targets, drift |
| Committee | Multi-agent analysis runs — each agent's verdict persisted and auditable |
| Monitoring | Alert rules + watch items (stale feeds, denied risk checks, drawdown) |
| Backtest | Historical simulation harness |
| Journal | Decision journal — what was decided, why, outcome |
| DataOps | Provider registry, job runs, manual ingestion triggers, quarantine review |
| `/symbol/{ticker}` | Per-instrument dossier: market overview, TradingView chart, price-history table (D/2D/3D/W/M/Y), market intelligence, financials (key ratios + statement tables, annual/quarterly), risk & pyramid, position, news |

## Key engines

### Green Zone screening
20-criterion equity screen (moat, profitability, balance sheet,
valuation vs IV, technicals, liquidity). Verdicts: `pass` / `review` /
`fail`. ETFs get an honest variant — fundamentals marked
`not_applicable`, replaced by macro sector alignment (regime rotation
map), relative strength vs SPY, trend, liquidity; a failed macro thesis
caps at `review` — never a silent pass.

### Macro regime
Classifier over FRED series + market context: regime, risk-on/off,
real **CNN Fear & Greed** index (dataviz endpoint, all 7 components,
proxy fallback labeled honestly), sector-rotation preferences persisted
per run. Persisted runs serve reads; recompute is explicit.

### ATR / pyramid engine
Doc-canonical math: `TR = max(H−L, |H−prevC|, |L−prevC|)`,
`ATR% = SMA14(TR)/close`, stop `entry − 1.5·ATR`, target
`entry + 3·ATR`, ratchet-tightens on every bar close, never loosens.
Excel horizon windows (6/24/72/288/576d · 12/26/52/104/156w ·
6/12/24/36/60m) expose the volatility regime. Daily/weekly/monthly ATR
with editable SMA periods. The state machine: watchlist →
trade_eligible → initial_position → target_1 → position_addition →
target_2 → trailing_exit → partial_exit → closed/stopped_out.

### Fundamentals
SEC EDGAR company-facts → point-in-time `FundamentalObservation`
(~380K rows across the universe). The display layer composes
investing.com-style statements: fiscal-calendar period classification
(not filing tags), YTD→standalone-quarter derivation, taxonomy alias
merge, TTM ratios (P/E, P/B, D/E, ROE, yield, EPS).

### Execution lanes
Three lanes behind one approval gate:

- **paper** — deterministic in-process simulator (default)
- **alpaca** — full REST v2 adapter: account, positions, orders,
  fills, asset flags; paper or live by `ALPACA_BASE_URL`
- **ibkr** — adapter contract reserved for TWS/IB Gateway
  (`IBKR_HOST/PORT/CLIENT_ID`); fails loudly until configured

`EXECUTION_ENABLED=true` + configured broker are both required for any
real order path. Agents can never call adapters directly.

### External accounts
`ExternalAccount` is the "connected portfolio" concept — every external
book lands in the same row the Command Center reads:

| Source | Mechanism |
|---|---|
| MT4 | Push — `docs/mt4/VAIIP_Push.mq4` EA POSTs snapshots + Market Watch tape (quotes upsert to `market_quotes`) |
| Alpaca | Pull — `portfolio:alpaca:sync` job: account + positions + held-name snapshots |
| IBKR | Pull — `portfolio:ibkr:sync` job via **Flex Web Service** (read-only statement XML; no gateway daemon needed) |

## Data providers

| Provider | Data | Key |
|---|---|---|
| Tiingo | Daily EOD bars, IEX intraday | `TIINGO_API_KEY` |
| Alpaca | Stock bars/quotes/snapshots/news | `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` |
| Yahoo | Chart-meta quotes, bars fallback | none |
| Stooq | Bars fallback | none |
| SEC EDGAR | Company facts (XBRL), ticker→CIK map | `EDGAR_USER_AGENT` contact email |
| FRED | Macro series, release calendar | `FRED_API_KEY` |
| CNN | Fear & Greed index | none (public dataviz endpoint) |

Key status is visible at `/settings/env` — configured/missing only,
values never leave the API. Secrets live in `SecretStore` (DB) and are
loaded into env at startup; `.env` works too.

## Job system

`POST /dataops/run/{job_key}` triggers ingestion:

```
ingest:tiingo:{SYM}            ingest:tiingo:intraday:{SYM}[:{freq}]
ingest:alpaca:{SYM}[:{tf}]     ingest:yahoo:{SYM}   ingest:edgar:facts:{SYM}
ingest:fred:{CODE}             ingest:fred:calendar
market:context   market:quotes   market:intraday   market:alpaca:quotes
technical:scan   monitor:scan    pyramid:seed      pipeline:universe
portfolio:alpaca:sync            portfolio:ibkr:sync
```

`/dataops/backfill` runs the full-universe sweep in the background.

## Quick start

```bash
# everything (postgres+redis+api) — web runs separately
docker compose up -d postgres redis
cd apps/api && uvicorn app.main:app --reload --port 8000
cd apps/web && npm install && npm run dev        # :3000
```

```bash
# or full compose
docker compose up -d        # api :8000 · web :3000 · pg :5432 · redis :6379
```

Env: copy `.env.example` → `.env`. `DATABASE_URL` and
`NEXT_PUBLIC_API_URL` are the only required vars; provider keys go in
either `.env` or the `/settings` page (stored in the DB secret store).

```bash
# tests + checks
cd apps/api && pytest tests/ -q          # 260+ tests, in-memory sqlite
cd apps/web && npx tsc --noEmit          # typecheck
```

## Deployment

`render.yaml` describes the prod topology: FastAPI service on Render,
Supabase Postgres (transaction pooler — asyncpg statement cache
disabled), Redis. Frontend deploys as a Next.js service pointed at the
API URL. `DEMO_FIXTURES=false` in production — required, since the
platform never presents fixture data as live.

## Documentation

- `docs/`, `new docs/` — internal runbooks and canonical
  specifications (SOW, ATR Calculator workbook, pyramid state
  machine). **Intentionally untracked** — they are not part of the
  public repository and are gitignored.
- `PROJECT_STATUS.md` — module-level status ledger

---

Copyright © 2026 Obozunya Larry / Spintall Network Solutions Limited.
All rights reserved. See [LICENSE](./LICENSE).
