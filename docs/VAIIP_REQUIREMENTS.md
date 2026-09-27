# VAIIP Requirements Traceability Matrix

**Project:** Vianomics AI Investment Intelligence Platform (VAIIP)
**Source documents:**
- `docs/CHOREDATA OS- SOW 2.0 FINAL VERSION.docx` — contract, deliverables D1.1–D3.2, acceptance criteria, milestones
- `docs/Institutional Investment Operating System Revised (1).txt` — 35-part / 8-layer target framework
- `docs/ChoreData_Vianomics_Investment_Operating_System.pdf` — original framework with concrete thresholds/formulas
- `docs/deepseek_html_20260907_80b228 (1).html` — UI mockup (static, simulated data)
- `goals.md` — build brief and engineering rules

**Status legend:** `NOT STARTED` · `IN PROGRESS` · `IMPLEMENTED` · `VERIFIED` (tests pass) · `BLOCKED` (decision/dependency required)

**Global engineering rules (from goals.md, binding on all deliverables):**
1. Never replace real functionality with mock data to make a screen look complete.
2. Never invent financial values, market data, agent findings, or API responses.
3. All financial calculations live in tested, versioned, deterministic functions — AI agents consume results, they do not compute them.
4. Every external data point retains provenance, timestamp, freshness, and licensing metadata.
5. All agent outputs conform to a validated structured schema (Part 28).
6. Every task ends with a working build, tests, change summary, and remaining-limitations list.
7. No secrets in frontend code, logs, or source control.
8. No live trade execution without backend risk approval AND recorded human authorization.

---

## Spec conflicts requiring a decision (do NOT silently resolve)

| # | Conflict | Sources | Default assumption until decided |
|---|---|---|---|
| C1 | Green Zone pass threshold | PDF: ≥18/20. SOW + revised txt: ≥15/20. Revised txt gate diagram leaves 15–17 undefined (`<18/20 → REJECT` vs `≥15/20 → RESEARCH`) | Treat threshold as **config** `green_zone.pass_score` (default 15 per SOW); 15–17 band = "conditional → watchlist pending review" unless owner decides otherwise |
| C2 | Options Agent / Part 13 (Options Intelligence) | Present in PDF (agent + CIO input + conflict example) but absent from revised 35-part structure | Deferred to backlog as D1.X-OPT; keep agent interface slot so it can be added without refactor |
| C3 | Investment universe scope | PDF: fixed 24 tickers. Revised txt: all US small–large caps. SOW: US equities + equity futures | Phase 1 seeds the 24 named tickers as the default Approved Universe; schema supports full-market scan |
| C4 | Month-4 acceptance "20 live trades" | SOW milestone vs. human-approval + Phase-2 execution rules | Interpret as **paper trades** unless the Trader confirms live capital |
| C5 | Aroon trigger threshold | PDF: `Aroon(25) > 100` (impossible; max is 100). Revised: `> 99` | Use `> 99`, configurable |
| C6 | ADX < 20 trend confirmation | PDF explicitly flags it as unconventional but says preserve-and-flag | Implement as specified, emit a `spec_anomaly` flag on the agent output |
| C7 | Trial dataset | SOW trial requires "provided dataset" for F&G vs put/call divergence | **BLOCKED** — dataset not present in repo/docs |
| C8 | Part 6 dossier section count | PDF: 16 sections. Revised txt + SOW: 18 sections (adds 6.15 Management, 6.16 Industry/Competitive) | Implement 18-section version |
| C9 | Trading risk parameters | Numeric table exists only in PDF Part 14 (max capital/trade 20%, risk/trade 1–2%, daily loss 3%, weekly loss 6%, position alloc 10–15%, position DD 20%, correlation ≤30%) | Port these values into the risk-limits config as documented defaults |

---

## SECTION I — STRATEGIC FOUNDATION (Parts 1–3)

### D1.1 | Part 1 — Investment Mandate & Objectives Engine
- **Required:** Config module: philosophy (fundamentals-first, MOS, technical confirmation), portfolio split (70% Investment / 30% Trading), targets (15–20% return, <15% DD, Sharpe >1.5, win rate >60%, trading R/R ≥1.5:1). Mandate must constrain every downstream engine.
- **Dependencies:** None (root config).
- **Modules:** `core/mandate` — `MandateConfig` Pydantic model, versioned, DB-backed.
- **DB:** `mandate_config` (version, effective_from, payload JSONB).
- **API:** `GET/PUT /api/mandate` (admin only).
- **UI:** Settings → Mandate panel.
- **Acceptance:** Mandate constraints are injected into screening, risk, and allocation calls; changing config creates a new version, not a mutation.
- **Tests:** Config validation; downstream engines receive injected limits; version immutability.
- **Status:** IMPLEMENTED — versioned `mandates` (immutable rows, effective_from, change_note, changed_by); `GET/POST /api/v1/mandate/*`; validation (split=100, ranges); `as_of()` history; `decision_records.mandate_version` snapshots; `/settings` UI (viewer + editor + version history, `mandate:write` gated). Engine consumption wired as engines land.

### D1.2 | Part 2 — Investment Universe Manager
- **Required:** Configurable universe (US small–large cap + equity futures per SOW; expand-ready for commodities/crypto). Hierarchy: Global → Eligible → Approved.
- **Dependencies:** D1.3 (instrument master data).
- **Modules:** `core/universe`; instrument master + membership tables.
- **DB:** `instruments`, `universe_tiers`, `universe_membership`.
- **API:** `GET/POST /api/universe`, `GET /api/universe/{tier}`.
- **UI:** Universe manager (tier tree, membership editor).
- **Acceptance:** Hierarchical filtering works; 24-ticker seed set loads; expansion asset classes modeled.
- **Tests:** Tier filter logic; seed integrity (24 tickers).
- **Status:** IMPLEMENTED — 3-tier hierarchy (global/eligible/approved + securities); eligibility engine (listing status, asset class, mcap, liquidity rules — configurable per-universe); exclusion reasons on memberships; `GET/POST /api/v1/universe/*` (search, detail, membership, watchlists); `/universe` UI (hierarchy counts, search, filters, eligibility badges, detail drawer, watchlists). Delisted instruments stay queryable, exit eligibility. Extensible to futures/commodities/crypto via `asset_class`.

### D1.3 | Part 3 — Universal Data & Research Infrastructure
- **Required:** ETL: ingest, clean, normalize, store — Fundamental (SEC EDGAR first-class; Yahoo/FMP/Tiingo adapters), Market (OHLCV multi-timeframe, ATR inputs), Macro (FRED), Alternative (interface stub only). **Point-in-time correctness mandatory** (no look-ahead, no silent restatement leakage). Provenance + freshness on every record.
- **Dependencies:** Postgres schema, provider key management, Redis/queue for async ingestion.
- **Modules:** `data/` — `providers/` (adapter per source), `ingestion/` (jobs), `normalization/`, `quality/` (staleness, missing-value policy).
- **DB:** `fundamentals` (point-in-time, source, period, published_at), `ohlcv` (hypertable-ready), `macro_series`, `data_provenance`, `provider_entitlements`.
- **API:** Internal first: `GET /api/data/fundamentals/{ticker}`, `/api/data/ohlcv`, `/api/data/macro`.
- **Acceptance:** ≥1 fundamentals source (EDGAR) + 1 market source + FRED live; provenance complete; missing values never silently zeroed.
- **Tests:** PIT correctness (restatement scenario), freshness decay, provenance completeness, adapter contract tests.
- **Status:** IN PROGRESS — provenance/PIT schema live; adapters: EDGAR ✅ **live-verified** (all 24 tickers ingested via CIK auto-resolution, ~47K facts), FRED + Tiingo implemented (awaiting keys), IBKR/paper stubs; idempotent ingestion + quarantine + job tracking + as-of retrieval tested. Remaining: scheduler daemon, news/alt adapters, market data (needs key).

---

## SECTION II — INVESTMENT INTELLIGENCE (Parts 4–7)

### D1.4 | Part 4 — Green Zone Screening Engine
- **Required:** 20-criteria scorecard (Revenue growth, NI growth, OCF growth, margin expansion, ROE, ROIC, Rev/AR, cash conversion, share count, FCF/share, P/FCF, BV/share, dividend growth, moat, current ratio, Debt/EBITDA, interest coverage, DSCR, IV discount, technical setup). Pass = ≥15/20 (see C1); critical risk-control failures block regardless.
- **Dependencies:** D1.2, D1.3.
- **Modules:** `engines/screening/green_zone` — deterministic pure functions, criteria pluggable.
- **DB:** `screening_runs`, `criterion_results` (per-criterion value, threshold, pass/fail, provenance ref).
- **API:** `POST /api/screening/run`, `GET /api/screening/{ticker}`.
- **UI:** Screener grid with per-criterion drilldown.
- **Acceptance:** Screens whole universe; correct categorization pass/watchlist/reject; blocking criteria enforced.
- **Tests:** Golden dataset per criterion; boundary values; block-override case.
- **Status:** IMPLEMENTED — `green_zone.py` (20 criteria, score pass=1/review=0.5, verdicts pass|fail|review|insufficient_data|blocked_by_risk, moat = deterministic evidence + requires_review, financials N/A variants, dividend non-payer N/A); `screening_policies` versioned; `screening_runs`/`screening_results` persist policy+mandate versions + per-criterion evidence; `POST /api/v1/screener/run` (JobRun-tracked batch), `/latest`, `/results/{ticker}`, `/policies`; `/screener` UI (statuses, filters, drill-down w/ evidence + freshness). Live run: ASML 15.5/20 REVIEW (moat unconfirmed → not auto-qualified), NVDA 13.5 FAIL — all real EDGAR data.

### D1.5 | Part 5 — Special Valuation Screens (sector adjustment)
- **Required:** Sector-aware metric selection: Banks/Insurance/RE → P/B, NAV; commodities → cycle valuation; growth → fwd P/E, EV/EBITDA, FCF, EPS CAGR, revisions.
- **Dependencies:** D1.3 (sector classification), D1.4.
- **Modules:** `engines/valuation/sector_screens`.
- **API:** folded into valuation service responses.
- **Acceptance:** Correct metric per sector; flags inappropriate metric usage.
- **Tests:** Sector→metric mapping matrix.
- **Status:** IMPLEMENTED — `valuation.py` archetypes (financial→P/B+ROE+BVPS-quality, real_estate→NAV/FFO honest-missing, commodity→normalized EPS + cycle, growth→P/E+EV/EBITDA+P/FCF); `check_metric()` blocks inappropriate metrics without explicit override+reason; `GET /research/valuation/{ticker}` (NVDA live: P/E 36.5, EV/EBITDA 33.0, P/FCF 42.7).

### D1.6 | Part 6 — Institutional Research Dossier Engine
- **Required:** 18-section dossier (6.1 Exec Summary → 6.18 Final Scorecard incl. Management Quality, Industry/Competitive Position, peer comparison on 10 axes).
- **Dependencies:** D1.3, D1.4, agents (D1.26) for narrative sections; deterministic scoring for scorecard.
- **Modules:** `engines/research/dossier` + Fundamental Agent prompt/schema.
- **DB:** `dossiers`, `dossier_sections` (versioned per ticker).
- **API:** `POST /api/research/{ticker}/generate`, `GET /api/research/{ticker}`.
- **UI:** Research workbench — sectioned dossier view, peer table.
- **Acceptance:** Structured dossier for e.g. NVDA matching the 18-section format; every quantitative field sourced.
- **Tests:** Schema validation; section completeness; scorecard arithmetic.
- **Status:** IMPLEMENTED — `research_orchestrator.py` (plan→gather→build→validate→persist); `dossiers` versioned (group_id+version) + `dossier_sections` (18 claims-typed blocks: verified_fact|ai_inference|analyst_assumption|management_statement) + `evidence_items` registry (every verified claim → XBRL observation) + `dossier_reviews` (approve/request_changes/attest); peer comparison vs same-sector instruments (rev growth/margin/ROE ranks); validator flags verified claims lacking evidence; missing evidence disclosed. `POST /research/dossier/{t}`, `GET .../versions`, `POST .../review`; `/research` workbench UI (search, accordion w/ provenance chips, missing-evidence register, versions, review actions). Live: NVDA v2 — 13 verified/27 AI/4 assumptions/5 insufficient, peers ranked (65.5% growth >9/9).

### D1.7 | Part 7 — Rule #1 Engine
- **Required:** Four Ms (Meaning/Moat/Management/MOS) + Five Numbers (Revenue, EPS, Equity, FCF growth >10%; ROIC >15%). Outputs Sticker Price → MOS → Buy Price.
- **Dependencies:** D1.3 (10yr fundamentals).
- **Modules:** `engines/valuation/rule_one` (deterministic) + Rule #1 Agent (interpretation).
- **Acceptance:** Sticker Price & Buy Price computed with visible assumptions; MOS formula `(IV − Price)/IV`.
- **Tests:** Formula correctness against known examples; missing-growth-data handling.
- **Status:** IMPLEMENTED — `valuation_engine.py` (pure Decimal: sticker/buy w/ configurable MOS & required return, Gordon-growth DCF w/ EV→equity bridge, reverse-DCF implied growth via bisection, sensitivity grids, Five Numbers); `valuation_runs` versioned (inputs+outputs+methodology `rule1-dcf/v1.0` → reproducible); `GET/POST /valuation-engine/*`; `/valuation` Lab UI (anchors auto-load, bear/base/bull, editable assumptions, sensitivity heatmap, Five Numbers table, history, versions). Live NVDA: sticker $301.76 / buy $211.23 / DCF $207.32 / MOS 13.2% / implied-growth 18.1%.

---

## SECTION III — MARKET & QUANTITATIVE INTELLIGENCE (Parts 8–11)

### D1.8 | Part 8 — Quantitative Intelligence Engine
- **Required:** Factor suite (momentum, value, quality, growth, vol, size, revisions, FCF yield, ROIC, beta, corr, Sharpe, Sortino, DD) + event studies, earnings analysis, walk-forward, Monte Carlo.
- **Dependencies:** D1.3 time series; backtesting core (shared with D1.31).
- **Modules:** `engines/quant/` — factors, events, stats.
- **Acceptance:** Factor scores + statistical summary per ticker/portfolio; demo event study on a historical earnings release.
- **Tests:** Factor math vs reference calcs; MC determinism with seeded RNG.
- **Status:** NOT STARTED

### D1.8 | Part 8 — Quantitative Intelligence
- **Status:** IMPLEMENTED — `quant.py` (returns/vol/sharpe/sortino/mdd/beta/corr/12-1 momentum/event studies/factor scoring, pairwise-skip missing, documented conventions), `quant_service.py` (fundamentals+OHLCV factors, portfolio exposure, corr matrix), `/api/v1/quant/*`, `/quant` dashboard. Real data via Yahoo EOD adapter.

### D1.9 | Part 9 — Macro Regime Engine
- **Required:** Ingest FRED macro (GDP, PMI, employment, CPI, PPI, confidence, yield curve, rates, credit, dollar, commodities, liquidity) → classify Economic Regime (Recovery/Expansion/Slowdown/Recession) + Market Regime (Risk-on/Neutral/Risk-off) → sector rotation output. Constraints: ≤25% sector, ≥5 sectors, quarterly reassessment.
- **Dependencies:** D1.3 macro pipeline.
- **Modules:** `engines/macro/regime` — classifier with documented rules + confidence.
- **DB:** `macro_regimes` (asof, classification, inputs snapshot).
- **Acceptance:** Current regime classified; rotation table feeds PM agent.
- **Tests:** Regime classification on known historical periods.
- **Status:** IMPLEMENTED — `macro_regime.py` (versioned `regime-rules/v1.0`: feature set + rule hits persisted per `RegimeRun`; PIT by `observed_at ≤ as_of`; per-series staleness windows; `insufficient_data` when core inputs missing); 14 FRED series via keyless fredgraph (latest-vintage, limitation disclosed); sector-rotation map w/ mandate constraints surfaced via `/macro/sector-rotation`; `/macro` dashboard (regime track, rule chips, staleness badges, F&G/VIX overlays, run history).

### D1.10 | Part 10 — Technical Timing Engine
- **Required:** Branch A mean reversion (RSI(10) multi-timeframe <30; CMI(21)<0; Williams %R(13)/(52) <−80; S/R; reversal; volume — RSI must align with CMI+%R). Branch B trend following (Aroon(25) >99 daily/weekly/monthly — see C5; ADX, MACD, Ichimoku, pattern, volume — see C6).
- **Dependencies:** D1.3 OHLCV.
- **Modules:** `engines/technical/` — indicator library (RSI, CMI, %R, Aroon, ADX, MACD, Ichimoku, ATR) then signal rules.
- **Acceptance:** Deterministic Entry/Wait output naming every triggered indicator.
- **Tests:** Indicator correctness vs reference implementations; rule truth tables.
- **Status:** IMPLEMENTED — `technical.py` (Wilder RSI/ADX/ATR, CMI(21), %R(13/52), Aroon(25), MACD, Ichimoku, S/R, reversal patterns, volume confirm) + `technical_engine.py` (`technical/v1.0` params: MR = RSI(10)<30 trigger + CMI/%R/reversal/volume confirmations; TF = Aroon>99 → MACD→ADX>25→Ichimoku-above-cloud→breakout→volume sequence halting at first failed gate); multi-TF aggregation (2d/3d/1w/1mo) w/ provisional-last-bucket exclusion; freshness gate (bar age ≤7d, else not-live). `/technical/{signal,bars,scan}` + `/trading-desk` UI (lightweight-charts candlesticks + SMA overlays + volume + S/R lines, TF switcher, branch panels, trade-setup preview, universe scan).

### D1.11 | Part 11 — Market Timing Overlay
- **Required:** Fear & Greed bands (0–25 accumulate … 80–100 aggressive reduction) + VIX bands (<15 normal … >30 risk-off). Overlays only — never standalone signals.
- **Dependencies:** D1.3 (VIX, F&G source).
- **Modules:** `engines/macro/timing_overlay`; consumed by PM + Risk.
- **Acceptance:** Overlay modifies sizing/aggressiveness; cannot independently generate trades.
- **Tests:** Band boundaries; non-signal enforcement.
- **Status:** IMPLEMENTED — `fear_greed_overlay()` + `vix_band()` with documented lower-bound-inclusive thresholds; F&G is a transparent proxy composite (vix_inverse, spy_vs_sma200, breadth, hy_inverse — CNN index not available); overlays embedded in RegimeRun, consumed as inputs only. Boundary tests cover all edges.

---

## SECTION IV — TRADE CONSTRUCTION & RISK (Parts 12–15)

### D1.12 | Part 12 — ATR & Pyramid Engine (Trading portfolio, 30%)
- **Required:** 5-stage method: $Risk →1.5×ATR→size; Entry+3×ATR→Target1→**double, no profit taking**; recalc Current ATR = Price×12W ATR%; stop = Price−1.0×ATR_current; Target2 → 0.75×ATR trail OR 50% profit. Implement the Pyramid **state machine** (Watchlist→Eligible→Pos1→T1/Pos2→T2→Exit).
- **Dependencies:** D1.10 (ATR/technical), D1.3.
- **Modules:** `engines/trading/pyramid` — state machine, persisted transitions.
- **DB:** `trades`, `pyramid_states`, `pyramid_events`.
- **Acceptance:** All 5 stages computed; no-profit-at-T1 enforced; state transitions auditable.
- **Tests:** State machine transitions; numeric examples; invalid-transition rejection.
- **Status:** IMPLEMENTED — `risk_engine.py` (`risk-pyramid/v1.0`): S1 sizing ($Risk→1.5×ATR, cash/lot/liquidity clamps + binding-report), S2 T1=+3×ATR doubles w/ **no profit-taking** + risk-check gate, S3 ATR_cur=price×ATR_12w%, S4 ratchet stop (price−1×ATR, never loosens), S5 trailing(0.75×ATR)|profit_50 explicit versioned policy — no silent mixing. PyramidTradeRec persists state+event log; gap-through-stop fills at observed price; rejected additions supported. API: POST /risk/pyramid, /pyramid/{id}/advance.

### D1.13 | Part 13 — Investment Risk Management Engine (Investment portfolio, 70%)
- **Required:** 5-test monitor: Fundamental deterioration → exit/review; price ≥ IV → reduce/exit; thesis invalidation → exit; oversized position → reduce/hold; normal volatility → hold.
- **Dependencies:** D1.4, D1.6, D1.7 (valuation inputs), portfolio state.
- **Modules:** `engines/risk/investment_monitor`.
- **Acceptance:** Per-position HOLD/REDUCE/EXIT signals with reason codes.
- **Tests:** Each test's trigger conditions; precedence rules.
- **Status:** IMPLEMENTED — `holding_tests()` outputs review actions (exit_or_review/reduce_or_exit/exit/review_reduce/no_action), explicitly *no* mechanical trading stops on investment positions.

### D1.14 | Part 14 — Risk Management Engine (core calculations)
- **Required:** 7 risk dimensions computed deterministically (fundamental/valuation, position/concentration, factor/correlation, leverage/margin, liquidity/financing, volatility/market-structure, feedback/path). **Calculation ≠ decision** — engine outputs metrics, Risk Manager Agent decides. Port PDF trading-risk table (see C9).
- **Dependencies:** D1.3, portfolio positions, D1.12.
- **Modules:** `engines/risk/core` — pure functions, fully tested.
- **Acceptance:** Structured risk metrics for any proposed/existing position.
- **Tests:** Numeric correctness incl. stress scenarios (position −10/20/30/40%).
- **Status:** IMPLEMENTED — `risk_dimensions()` computes all 7 dimensions; stress ladder −10/−20/−30/−40% + correlated-ρ1 shock + margin × gross + vol shock; empty data → explicit `unknown`/`degraded`, never fabricated.

### D1.15 | Part 15 — Portfolio Risk Limits
- **Required:** Configurable hard limits: DD ≤15%, sector ≤25%, single stock ≤10%, ≥5 sectors, correlation ≤30% (C9), cash floor, pyramid controls (vol-spike sizing cut, VIX reduction, F&G new-position restrictions).
- **Dependencies:** D1.1, D1.11, D1.14.
- **Modules:** `engines/risk/limits` — config + enforcement middleware used by Risk Manager.
- **Acceptance:** Limits configurable; violations alert AND block.
- **Tests:** Each limit boundary; combined violation handling.
- **Status:** IMPLEMENTED — `check_order()` gate w/ `Limits` (defaults: DD 15%, sector 25%, name 10%, ≥5 sectors, corr 0.70, cash 5%, gross ≤1.0×, VIX≥30 reduce, F&G≥80 block-new, vol≥60% halve); every breach carries rule+observed+required+remediation; RiskCheck audit row per evaluation; sells never blocked; only path to order — `research:run`/`trading:execute` gated. `/risk` Risk Center UI (7 dims, stress table, active blocks, dry-run gate, history, limits strip).

---

## SECTION V — INVESTMENT GOVERNANCE (Parts 16–25)

### D1.16 | Part 16 — Risk Manager Agent (veto)
- **Required:** Independent control; consumes all prior outputs; **absolute veto** enforced in backend, not advisory.
- **Dependencies:** D1.14, D1.15, agent framework (D1.26).
- **Modules:** `agents/risk_manager` — hybrid: deterministic limit checks + LLM risk narrative. Backend gate cannot be bypassed by CIO.
- **Acceptance:** Blocks an otherwise-perfect candidate on limit violation; veto visible in decision tree output.
- **Tests:** Veto enforcement; PASS checklist (12 pre-approval checks from PDF Part 16).
- **Status:** IMPLEMENTED — risk agent gated by check_order; missing RM response → NO_TRADE (never pass); BLOCK = absolute veto; veto reason surfaced in CIO output

### D1.17 | Part 17 — Portfolio Manager Agent
- **Required:** "Best use of capital now?" — construction, allocation, opportunity cost vs marginal holding, rebalancing, sector exposure, Sharpe, correlations, F&G awareness, pyramid capacity.
- **Dependencies:** D1.9, D1.11, D1.14, D1.15, D1.18.
- **Modules:** `agents/portfolio_manager` + `engines/portfolio/`.
- **Acceptance:** Allocation/reduce/watchlist recommendation vs current portfolio.
- **Tests:** Opportunity-cost comparison logic.
- **Status:** IMPLEMENTED — PM agent evaluates allocation lens (MOS proxy vs marginal holding, sector fit, gate constraints); score never overrides gate

### D1.18 | Part 18 — Capital Allocation Engine
- **Required:** Candidate vs marginal holding on expected risk-adjusted return → Portfolio or Watchlist.
- **Dependencies:** D1.14, portfolio state.
- **Modules:** `engines/portfolio/capital_allocation` — deterministic comparator.
- **Acceptance:** Correct accept/relegate decision with comparison record.
- **Tests:** Comparator math; tie handling.
- **Status:** IMPLEMENTED — candidate-vs-portfolio via check_order + expected-return proxy (MOS); allocation ≠ investment quality

### D1.19 | Part 19 — CIO Agent
- **Required:** Synthesizes all agent outputs → Recommendation, Rating (Strong Buy→Sell), Confidence, Expected Return, Downside, MOS, R/R, Horizon, Catalysts, Thesis, Risks, Invalidation + Kill Conditions.
- **Dependencies:** All research agents, D1.16, D1.17.
- **Modules:** `agents/cio` — structured-output LLM synthesis over Part-28 inputs.
- **Acceptance:** Coherent structured recommendation under conflicting inputs; cannot emit EXECUTE.
- **Tests:** Schema validation; veto propagation; contradiction handling.
- **Status:** IMPLEMENTED — cio_synthesize: weighted score (fund/val/r1/quant/macro/tech/pm), verdict ladder approve_pending_human→watchlist→reject, invalidation conditions + sizing + portfolio fit; cannot emit EXECUTE

### D1.20 | Part 20 — CIO Confidence Model
- **Required:** Confidence = f(agent agreement, evidence quality, data freshness, model uncertainty, valuation sensitivity, contradictions, risk) — **not** probability of return.
- **Dependencies:** D1.19, Part-28 fields.
- **Modules:** `engines/governance/confidence` — deterministic scoring function.
- **Acceptance:** High contradiction/stale data → measurably lower confidence.
- **Tests:** Monotonicity on each factor.
- **Status:** IMPLEMENTED — confidence = 0.35 + 0.25×agreement + 0.20×data_quality − 0.05×contradictions − 0.03×gaps, clamped 0–1; output labeled NOT a probability of returns

### D1.21 | Part 21 — Agent Conflict Resolution Protocol
- **Required:** Formal rules: Risk BLOCK → NO TRADE regardless of other votes; thesis-vs-eligibility distinction preserved in output text.
- **Dependencies:** D1.16, D1.19.
- **Modules:** `engines/governance/conflict_resolver`.
- **Acceptance:** The documented 7-agent example resolves to NO TRADE with correct explanation.
- **Tests:** Truth table of agent combinations.
- **Status:** IMPLEMENTED — resolve_conflict: missing RM → no_trade; BLOCK → no_trade; any rec split → recorded disagreement (not averaged)

### D1.22 | Part 22 — Master Investment Decision Tree
- **Required:** Single orchestrating gate: Universe→GreenZone→Research→Rule#1→MOS→Quant→Macro→Technical→ATR/Risk→PortfolioFit→CIO→Human→Execution. Each gate emits REJECT/WATCHLIST/WAIT/REVIEW/BLOCK semantics per spec.
- **Dependencies:** All engines + agents above.
- **Modules:** `pipeline/decision_tree` — workflow orchestrator, per-gate persisted results.
- **DB:** `decision_runs`, `gate_results`.
- **Acceptance:** Candidate cannot reach execution with any gate failed; full audit trail.
- **Tests:** End-to-end candidate journeys for each gate outcome.
- **Status:** NOT STARTED

### D1.23 | Part 23 — Entry Protocol
- **Required:** TRADE-ELIGIBLE only when all 12 conditions pass (Green Zone, MOS, Rule#1, technical, market, ATR risk, margin, net/gross exposure, pyramid params, portfolio limits, CIO positive, human approval).
- **Dependencies:** D1.22.
- **Modules:** `engines/governance/entry_protocol` — checklist evaluation over gate results.
- **Acceptance:** All 12 enforced; status displayed.
- **Tests:** Missing-condition cases.
- **Status:** NOT STARTED

### D1.24 | Part 24 — Exit Engine
- **Required:** 6 exit classes (fundamental, valuation, technical, risk, market, thesis) + pyramid exit (stop→EXIT ALL; T1→DOUBLE; T2→TRAIL/50%).
- **Dependencies:** D1.12, D1.13, monitoring (D1.25).
- **Modules:** `engines/exits/` — signal evaluators per class.
- **Acceptance:** Per-position exit signals with class + reason.
- **Tests:** Each class trigger; pyramid exit rules.
- **Status:** NOT STARTED

### D1.25 | Part 25 — Monitoring Engine
- **Required:** Continuous monitoring: company, valuation, macro, trading (margin, pyramid state, ATR, stop, R/R), portfolio (weight, corr, sector, DD, exposure, cash, P&L, VaR, stress), technical, pyramid metrics. Threshold alerts.
- **Dependencies:** D1.3 refresh jobs, D1.12, D1.13, D1.24.
- **Modules:** `engines/monitoring/` + alert dispatcher; background workers.
- **UI:** Monitoring dashboard + alerts feed (per HTML mockup).
- **Acceptance:** Metrics refresh on schedule; threshold breach → alert with context.
- **Tests:** Alert rule evaluation; freshness-driven warnings.
- **Status:** NOT STARTED

---

## SECTION VI — AI OPERATING ARCHITECTURE (Parts 26–30)

### D1.26 | Part 26 — AI Multi-Agent Architecture
- **Required:** Framework hosting 10 agents: Research (Fundamental, Valuation, Rule#1, Quant, Macro, Technical [+ Options slot, C2]), Control (Risk), Capital (PM), Executive (CIO), Execution (Phase 2). Provider-agnostic LLM interface; structured outputs; deterministic-engine inputs injected.
- **Dependencies:** Engines of Sections II–IV (agents interpret, engines compute).
- **Modules:** `agents/` — base `Agent` class, `llm/` provider abstraction, tool registry, run persistence.
- **DB:** `agent_runs`, `agent_outputs`.
- **Acceptance:** Test workflow Fundamental→Risk→CIO produces coherent end-to-end result.
- **Tests:** Schema conformance; deterministic input injection; mock-LLM replay tests.
- **Status:** NOT STARTED

### D1.27 | Part 27 — Agent Responsibility Matrix
- **Required:** Each agent's primary question encoded and enforced in prompts/schemas.
- **Dependencies:** D1.26.
- **Modules:** `agents/registry` — declarative agent definitions.
- **Acceptance:** Registry is the single source of truth surfaced in UI/docs.
- **Status:** NOT STARTED

### D1.28 | Part 28 — Agent Output Standard
- **Required:** Uniform schema: TICKER, AGENT, DATE, DATA QUALITY, RECOMMENDATION, SCORE, CONFIDENCE, KEY EVIDENCE, PASS/FAIL CRITERIA, KEY RISKS, CONTRADICTIONS, ASSUMPTIONS, DATA GAPS, REQUIRED FOLLOW-UP, FINAL CONCLUSION.
- **Dependencies:** D1.26.
- **Modules:** `agents/schemas.py` — Pydantic `AgentOutput`; validated at runtime.
- **Acceptance:** 100% schema conformance; CIO ingests all outputs.
- **Tests:** Validation incl. malformed LLM output handling.
- **Status:** NOT STARTED

### D1.29 | Part 29 — Human-in-the-Loop Governance
- **Required:** AI = decision support only. Hierarchy: Research→Synthesis→Risk→Portfolio→**Human Approval**→Execution. Workflow PLAN→EXECUTE→LEARN→HUMAN REVIEW.
- **Dependencies:** D1.22, auth/RBAC, audit (D1.33).
- **Modules:** `core/approvals` — approval entity, signed record, UI gate.
- **UI:** Approval interface (per HTML mockup CIO panel).
- **Acceptance:** Execution blocked until recorded approval; approval is immutable.
- **Tests:** Bypass attempts rejected; audit completeness.
- **Status:** NOT STARTED

### D1.30 | Part 30 — Execution Architecture (Phase 2 productization, built Phase 1 for internal use)
- **Required:** Execution separated from intelligence: CIO→Risk→PM→Human→Execution Agent→Brokers (IBKR first, adapter-based; Alpaca/Tradier behind same interface). Handles orders, routing, status, fills, slippage, partial fills, rejections, reconciliation. Paper-trading stage mandatory.
- **Dependencies:** D1.29, provider abstraction, risk limits.
- **Modules:** `execution/` — `BrokerAdapter` interface, `ibkr/`, `paper/`, order state machine, reconciliation job.
- **DB:** `orders`, `order_events`, `fills`, `reconciliation_reports`.
- **Acceptance:** Paper order lifecycle end-to-end; IBKR adapter behind feature flag; reconciliation report.
- **Tests:** Order state machine; simulated fills; failure/retry paths.
- **Status:** NOT STARTED

---

## SECTION VII — VALIDATION & LEARNING (Parts 31–32)

### D1.31 | Part 31 — Backtesting & Research Sandbox
- **Required:** Pipeline-wide backtests: historical → OOS → walk-forward → Monte Carlo → costs → slippage → liquidity → regime → stress → scenarios. Bias controls: no look-ahead, survivorship control, point-in-time, corporate actions.
- **Dependencies:** D1.3 PIT data, engines II–IV.
- **Modules:** `backtest/` — event-driven engine reusing the same engine functions as production (no parallel implementation).
- **Acceptance:** 10-yr backtest of a defined strategy → CAGR/DD/Sharpe/win-rate report; bias controls verified.
- **Tests:** Look-ahead detection test; reproducibility; cost modeling.
- **Status:** NOT STARTED

### D1.32 | Part 32 — Feedback & Learning Engine
- **Required:** Signal→Decision→Trade→Outcome→Attribution→Model update→Backtest→Production. Track prediction/agent/CIO accuracy, win rate, expected vs realized (return/vol/DD), slippage, false ±.
- **Dependencies:** D1.22 records, D1.25 outcomes, D1.31.
- **Modules:** `engines/learning/` — attribution + accuracy scoring; model registry versioning.
- **Acceptance:** Per-trade attribution; accuracy dashboard; versioned model updates.
- **Tests:** Attribution math; versioned updates logged.
- **Status:** NOT STARTED

---

## SECTION VIII — INSTITUTIONAL RECORD & DOCTRINE (Parts 33–35)

### D1.33 | Part 33 — Investment Decision Record
- **Required:** Immutable audit record per investment: all agent scores, IV, price, MOS, ATR, entry/stop/targets, sizes, margin, exposures, CIO rating/confidence, RM/PM verdicts, human approval, full trail.
- **Dependencies:** D1.22, D1.29.
- **DB:** `investment_records` (append-only), `audit_events`.
- **Acceptance:** Complete auditable record per decision (NVDA-style example).
- **Tests:** Immutability (no UPDATE path); completeness validator.
- **Status:** NOT STARTED

### D1.34 | Part 34 — Ten Investment Questions
- **Required:** Validation module proving the system answers all 10 questions; generates "Ten Questions Report" per candidate.
- **Dependencies:** Whole pipeline.
- **Modules:** `engines/governance/ten_questions` — maps questions→deliverables D1.4–D1.22.
- **Acceptance:** Report generated; each question traceable to its answering module.
- **Status:** NOT STARTED

### D1.35 | Part 35 — Master Operating Model
- **Required:** Full cycle operational end-to-end, distinguishing: good company / good investment / good trade today / appropriately sized trade / good allocation.
- **Dependencies:** Everything.
- **Acceptance:** Live full-pipeline run universe→execution→feedback; the 5 distinctions observable in output.
- **Status:** NOT STARTED

---

## Phase 2 — SaaS Productization (deferred, Phase-1-compatible design required)

| ID | Scope | Notes | Status |
|---|---|---|---|
| D2.1 | Multi-tenant architecture & billing | Tenant isolation model must be designed into schema now (`tenant_id` on owned tables); Stripe-class billing later | DEFERRED (schema-ready) |
| D2.2 | Tiered packaging: Analyst / Quant / Fund | Entitlement layer in RBAC design | DEFERRED |
| D2.3 | Self-service onboarding, NPS | Post-MVP | DEFERRED |

## Phase 3 — Marketplace (deferred)

| ID | Scope | Status |
|---|---|---|
| D3.1 | Anonymized opt-in intelligence feed | DEFERRED — consent model prerequisite |
| D3.2 | Algo marketplace, 20–30% rev share | DEFERRED |

## Trial project (D0) — pre-SOW condition

| Item | Detail |
|---|---|
| Scope | Macro Regime Engine (D1.9 subset) + Market Timing Overlay (D1.11 subset) |
| Deliverables | FRED pipeline (GDP, CPI, PMI, yield curve); F&G-vs-put/call divergence dashboard w/ configurable alerts; regime classifier |
| Acceptance | 3 historical divergence events surfaced, real-time alerts, correct current regime |
| **Blocker** | "Provided dataset" for divergence events is not in the repo — **must be supplied or generated from public sources** (C7) |
| Status | NOT STARTED |

---

## Requirements coverage check
- Parts 1–35: all mapped to D1.1–D1.35 above.
- Open spec conflicts: C1–C9 (top of document).
- Nothing omitted; deferred items are explicitly marked DEFERRED, not dropped.
