# VAIIP — Project Status

> Durable status file. Every work session: read this first, append a dated entry at the end, update statuses in `docs/VAIIP_REQUIREMENTS.md`.

**Current milestone:** M1 — Foundation (D1.1 mandate, D1.2 universe, D1.3 data infra) — next up
**Last updated:** 2026-09-27

## Baseline
- Repository: greenfield — only `LICENSE` (MIT) on `main` (initial commit). No manifests, no source, no CI.
- Docs present: SOW 2.0 (.docx), revised 35-part framework (.txt), original framework (.pdf, 40pp), UI mockup (.html, static/simulated), `goals.md` build brief.
- **Baseline build/tests:** nothing exists to run — no package manifests or test infra. First runnable baseline arrives with M0.

## Established documents
- `docs/VAIIP_REQUIREMENTS.md` — full D1.1–D3.2 traceability + spec conflicts C1–C9.
- `docs/ARCHITECTURE.md` — modular monolith; deterministic engines vs agent layer boundaries.
- `docs/IMPLEMENTATION_ROADMAP.md` — milestones M0–M8, dependency-ordered.

## Awaiting owner decisions (blockers)
1. **C1** Green Zone threshold 15 vs 18 (default: configurable, 15).
2. **C2** Options Agent in or out of Phase 1 (default: interface slot reserved, deferred).
3. **C3** Universe = 24 seed tickers vs full US market (default: seed 24, schema-ready for full).
4. **C4** Month-4 "20 live trades" — confirm paper trades acceptable.
5. **C7** Trial project "provided dataset" (F&G vs put/call divergence) not in repo — supply or approve public-source derivation.
6. Market-data provider pick (Tiingo/FMP/Yahoo) + API keys; LLM provider keys.

## Decisions made
- Stack: Next.js+TS frontend, FastAPI backend, Postgres(+Timescale-ready), Redis+arq workers, modular monolith.
- Agents interpret; engines compute; Risk veto is a DB-enforced invariant.
- Paper-trading default; execution behind feature flag.

## Built (M0 — scaffold + application shell)
- `docker-compose.yml`: postgres(timescale pg16) + redis; `api`/`web` images behind `app` profile.
- `apps/api` — FastAPI skeleton: `/healthz` (liveness), `/readyz` (postgres+redis readiness), `GET /api/v1/command-center` serving real system health + demo-flagged fixtures when `DEMO_FIXTURES=true`, honest empty sections otherwise. `EXECUTION_ENABLED` defaults off. 5 pytest tests green.
- `apps/web` — Next.js 15 + TS + Tailwind v4 + lucide: dark-first token theme (+light), sidebar with 19 modules in 6 groups, topbar, ⌘K command palette, theme toggle, error boundary, 404. All 18 non-CC modules route via `[module]` catch-all → honest "engine pending" placeholders (no fabricated data). Command Center renders portfolio/risk/regime/watchlist/approvals/alerts/decisions + REAL provider health; demo sections carry amber DEMO badges. Components: MetricCard, StatusBadge, DataTable, Dialog, Drawer, ConfirmDialog, Toast, SearchInput, FilterSelect, Skeleton, EmptyState, ErrorState, PageHeader, SectionCard. 9 vitest tests green; `next build` clean; `tsc --noEmit` clean.
- Known npm audit: 4 vulns (3 moderate, 1 high) in dev deps — review before Phase 2.

## Built (M1 — data architecture & persistence)
- **42-table schema** via Alembic `f866152d25bf` (verified on Postgres + SQLite; upgrade/downgrade clean): identity (users/roles/sessions/audit), mandates (versioned), instruments+identifiers+exchanges+sectors+corporate actions, universes/watchlists, providers+credential-meta (secret refs only), fundamental observations (PIT + supersedes), OHLCV, macro series/observations/releases, portfolios/positions/trades/ledger (Numeric — no float money), analysis runs, agent defs/runs/outputs/evidence, risk assessments/approvals/decision records, alerts/jobs/job runs/sync status/quarantine.
- **Provenance:** every observation carries source/ref/observed/published/ingested/quality/supersedes; `pit_filter`/`latest_as_of` helpers; restatements = new rows, never overwrites.
- **Providers:** `ProviderAdapter` protocol; EDGAR + FRED + Tiingo adapters; PaperBroker + IBKR (refuses until configured). Credentials env-only.
- **Ingestion:** validated→normalized→quarantined pipeline, idempotent upserts, revision chains, retry/backoff, per-provider throttle, JobRun counts, SyncStatus freshness.
- **Security:** argon2 hashes, revocable server sessions, `/api/v1/auth/{login,logout,me}`, `require()` RBAC (domain wildcards + admin:*), audit events, `tenant_id` on owned tables.
- **Seed:** 3 roles, admin@vianomics.io (env ADMIN_PASSWORD), NASDAQ/NYSE, 11 GICS sectors, 24-ticker approved universe, mandate v1 (incl. PDF risk limits — C9), 5 providers `unconfigured`.
- **Live-verified:** real SEC EDGAR ingest — NVDA XBRL, 980 records in / 980 valid / 730 persisted; provider health now DB-driven in Command Center.

## Session log
- **2026-09-27** — UI redesign to glass-terminal mockup look (`e615eef`); app on :3000 (newsend freed port).
- **2026-09-27** — Parts 1+2 implemented: versioned mandates + universe eligibility engine + `/universe` and `/settings` UI + auth. 43 pytest green.
- **2026-09-27** — Parts 3–5 implemented: `finmetrics.py` metric foundation (Decimal, documented formulas, missing→None), FY-aligned `fundamentals_query`, full 20-criterion Green Zone engine w/ versioned policies + persisted criteria evidence + sector variants + moat-review rule; full EDGAR ingestion of all 24 tickers (~47K observations via ticker-map CIK resolution); `/screener` UI w/ drill-down. Live: ASML 15.5 REVIEW, NVDA 13.5 FAIL. 65 pytest green.
- **2026-09-27** — Parts 5–6 (SOW D1.5/D1.6) implemented: sector valuation archetypes w/ override gating (`valuation.py`) + 18-section dossier engine w/ evidence registry (every verified claim → XBRL obs), typed claims (verified/AI/assumption/mgmt/missing), peer ranking, versioned dossiers + human review flow; `/research` workbench. NVDA live dossier v2: 13 verified facts w/ evidence chips. 75 pytest green.
- **2026-09-27** — Part 7 valuation engine: `valuation_engine.py` (Rule #1 sticker/buy, DCF w/ EV→equity, reverse DCF, sensitivity, Five Numbers — pure Decimal, validated inputs), versioned `valuation_runs`, `/valuation` Lab UI. NVDA live: sticker $301.76/buy $211.23/DCF $207.32. 86 pytest green.
- **2026-09-27** — Parts 8/9/11: quant engine (`quant.py` returns/sharpe/sortino/beta/corr/mdd/12-1mom/event-study/factor-scores w/ documented conventions) + macro regime classifier (`regime-rules/v1.0`, PIT as-of, staleness windows, insufficient_data gate) + F&G/VIX overlays + sector rotation w/ mandate limits. **Real data**: Yahoo EOD OHLCV adapter (501 bars × 25 instruments incl SPY), FRED fredgraph CSV (14 series, ~41K obs, no key). Live: expansion+risk_on, F&G 74→reduce, VIX 14.21, NVDA β1.78/Sharpe 0.88/MaxDD −28%. `/macro` + `/quant` dashboards. 103 pytest green.
- **2026-09-27** — Part 10 technical engine: full indicator library (RSI/CMI/%R/Aroon/ADX/MACD/Ichimoku/ATR/S-R/patterns/volume), multi-TF aggregation w/ provisional-candle handling, dual-branch signal engine (`technical/v1.0`, Entry/Wait/Invalid + explanations, freshness gate), `/trading-desk` UI w/ candlestick chart + S/R overlays + trade-setup preview (inspection only). NVDA live: RSI 56/57, Aroon 44 → wait, cloud above. 123 pytest green.
- **2026-09-27** — Parts 12–15: pyramid state machine (S1 sizing → T1 double/no-profit → ratchet stop → T2 trailing|profit_50, gap-through-stop, rejected additions), 5 investment holding tests, 7 risk dimensions + stress ladder, `check_order` hard gate (10 limit rules, audit-persisted, sells never blocked), `/risk` Risk Center. Live pyramid lifecycle verified: 370sh→740 at T1→stop 248→trail 257.5→stopped @246 (gapped). 147 pytest green.
- **2026-09-27** — Parts 16–21: strict AgentReport schema (16 required fields + execution_id/model/prompt_version), 8 bounded deterministic analyzers w/ tool-scoped ctx, conflict protocol (missing RM → NO_TRADE, BLOCK → absolute veto, splits surfaced not averaged), CIO synthesis (weighted score + explicit confidence model, never a return probability), DecisionRecord + pending human Approval + agent runs/outputs persisted. `/committee` UI: per-agent cards w/ evidence/risks/criteria detail, veto banner, approve/reject, history ledger. Live NVDA: RM blocked (min_sectors) → no_trade despite pass scores. 158 pytest green.
- **2026-09-27** — Parts 22–24: `evaluate_tree` 12-gate master tree (explicit results + rules_version + timestamps, halt propagation), `entry_protocol` 12-check trade-eligibility, OrderTicket w/ params_hash staleness + risk re-validation at approval, `exit_engine` 6 classes + pyramid exits + signal≠fill lifecycle, `/replay` full-snapshot decision reconstruction. `/journal` Decision Journal + gate ladder on committee page. Live NVDA tree: universe pass → GZ review → fundamental pass → MOS wait → risk blocked → human pending. 171 pytest green.
- **2026-09-27** — Part 30 execution layer: BrokerAdapter protocol, PaperBroker (deterministic fills/commissions/rejects/disconnects), IBKR stub failing loudly until configured, order FSM w/ idempotency-first dedupe + unknown→reconcile, kill switch, notional/qty caps, `broker_orders`/`execution_events`/`kill_switch` tables, `/execution/*` API, order blotter on Trading Desk. Live: NVDA 10sh paper-filled @225.07 + $0.05 commission, resubmit deduped, kill switch 403s. 184 pytest green.
- **2026-09-27** — Parts 25+33 + Command Center live wiring: monitoring engine (macro/trading/portfolio/valuation/data-quality checks → deduped alerts w/ cooldown, severity, observed/required/action), `/monitoring` UI, DecisionRecord enriched (ATR/stops/exposure/RM+PM verdicts, full gate tree, replayable), Command Center now real (regime, alerts, decisions, approvals, watchlist by GZ score, CIO block, open orders, job health) — demo fixtures only fill genuinely empty sections and stay labeled. 190 pytest green.
- **2026-09-27** — Parts 31+32: event-driven backtester (close(t) signal → open(t+1), PIT indicators, commission+slippage+liquidity, gap-through stops), versioned persisted runs + /backtest API + Research Sandbox UI (equity curve, trades, walk-forward, Monte Carlo); feedback engine (expected-vs-realized per decision + per-agent accuracy + paper-fill attribution). Live: 33-trade mr_rsi run over 501 real bars (CAGR −0.5%, WF×4, MC p5/p95, AAPL-missing limitation flagged). 200 pytest green.
- **2026-09-27** — Initial audit + documentation scaffold. Repo audited (empty), 3 docs created, spec conflicts catalogued.
- **2026-09-27** — M0 + app shell implemented per shell prompt. Backend verified live (healthz ok, command-center returns demo-flagged payload + real infra health). Committed + pushed `13149bd`.
- **2026-09-27** — M1 data layer implemented per core-data prompt. 28 pytest tests green; Postgres migration + seed verified on docker; live EDGAR ingestion proven. Next: scheduled-ingestion daemon + mandate/universe APIs + market-data provider key (Tiingo/FMP — needs owner).
- **2026-09-27** — UI completion pass: all placeholders real — /portfolio (positions+fill attribution), /allocation (mandate vs actual), /market (snapshot of 25 instruments), /technical (scan board), /data-ops (64 jobs, sync freshness, quarantine). Fills now book into the Position ledger. CC de-demoed: split/agents/sectors real; calendar dropped (no provider). Sidebar pill shows signed-in user. Admin → admin@vianomics.com. 200 pytest green.
