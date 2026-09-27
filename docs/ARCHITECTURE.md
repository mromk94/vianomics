# VAIIP Architecture Decision Record

**Status:** Proposed baseline — validated against greenfield repository (no prior stack to preserve).
**Decision driver:** CHOREDATA SOW 2.0 + 35-part framework + goals.md engineering rules.

---

## 1. Context

The repository contains only `LICENSE` — this is a greenfield build. goals.md proposes Next.js/TypeScript + Python/FastAPI + PostgreSQL (+ optional TimescaleDB) + Redis/workers. This document evaluates and confirms that stack, with the specific boundary rules the financial domain demands.

## 2. The one architectural principle that outranks all others

**Deterministic engines decide; AI agents interpret.**

Every number that gates money — Green Zone score, Sticker Price, ATR, position size, exposure, margin, risk limits — is produced by tested Python functions. LLM agents receive computed results plus evidence and return structured recommendations. Agents can never emit orders, override limits, or fabricate data. The Risk Manager's veto is enforced in code, not in a prompt.

## 3. Stack decision

| Layer | Choice | Rationale | Alternatives considered |
|---|---|---|---|
| Frontend | Next.js 15 + TypeScript + Tailwind + shadcn/ui + Recharts/TradingView Lightweight Charts | Dense institutional workstation UI; SSR for performance; matches mockup's information density | Streamlit (too weak for product UI), plain React SPA (fine but Next adds API routes/SSR) |
| Backend | Python 3.12 + FastAPI + Pydantic v2 | Same language as quant engines — engines are imported, not wrapped in a second service | Node backend (would force quant logic into JS or extra service) |
| DB | PostgreSQL 16 + TimescaleDB extension | Transactions for portfolio/order state; hypertables for OHLCV/macro when volume justifies | Plain Postgres (acceptable fallback; Timescale is optional at day 1) |
| Cache/queue | Redis + arq (or RQ) workers | Rate-limit windows, async ingestion, agent runs, alerts | Celery (heavier); APScheduler acceptable for Phase 1 |
| ORM/migrations | SQLAlchemy 2 + Alembic | Standard, async-capable | — |
| LLM | Provider-agnostic client (litellm-style interface or thin internal abstraction) + structured outputs | Swap models without rewriting agents; schema-validated outputs | LangChain (too heavy for our needs) |
| Charts | TradingView Lightweight Charts (free) first; full TradingView if licensed | Candlesticks + overlays required | Recharts for non-price viz |
| Auth | FastAPI JWT + RBAC; `tenant_id` column reserved on owned tables from day 1 | Phase-2-ready without paying multi-tenant cost now | Auth0/Clerk later if SaaS needs it |
| Broker | `BrokerAdapter` interface; `paper` + `ibkr` implementations behind feature flag | Execution isolation is a hard requirement | Alpaca/Tradier as additional adapters |

**Verdict:** Adopt proposed stack. No competing stack is justified.

## 4. System shape — modular monolith

```
vianomics/
├── apps/
│   ├── web/                    # Next.js frontend
│   └── api/                    # FastAPI application
│       ├── main.py
│       ├── core/               # config, security, RBAC, audit
│       ├── mandate/            # D1.1
│       ├── universe/           # D1.2
│       ├── data/               # D1.3
│       │   ├── providers/      # edgar, fred, yahoo, fmp, tiingo, ibkr-data
│       │   ├── ingestion/      # jobs, scheduling, throttling
│       │   ├── normalization/
│       │   └── quality/        # freshness, missing-value policy
│       ├── engines/            # DETERMINISTIC — no LLM calls allowed
│       │   ├── screening/      # D1.4 green zone
│       │   ├── valuation/      # D1.5 sector screens, D1.7 rule#1, DCF
│       │   ├── quant/          # D1.8
│       │   ├── macro/          # D1.9 regime, D1.11 overlay
│       │   ├── technical/      # D1.10 indicators + signals
│       │   ├── trading/        # D1.12 pyramid state machine
│       │   ├── risk/           # D1.13–D1.15
│       │   ├── portfolio/      # D1.17 engine-side, D1.18 allocation
│       │   ├── exits/          # D1.24
│       │   ├── monitoring/     # D1.25
│       │   ├── governance/     # D1.20 confidence, D1.21 conflicts,
│       │   │                   # D1.23 entry, D1.34 ten questions
│       │   └── learning/       # D1.32 attribution
│       ├── agents/             # LLM layer — consumes engine outputs
│       │   ├── llm/            # provider abstraction
│       │   ├── schemas.py      # Part-28 AgentOutput
│       │   ├── registry.py     # Part-27 matrix
│       │   ├── fundamental/ valuation/ rule_one/ quant/ macro/ technical/
│       │   ├── risk_manager/   # veto = engine check + narrative
│       │   ├── portfolio_manager/
│       │   ├── cio/
│       │   └── execution/      # Phase 2
│       ├── pipeline/           # D1.22 decision tree orchestrator
│       ├── execution/          # D1.30 broker adapters, order SM, reconciliation
│       ├── backtest/           # D1.31 — imports engines/, never reimplements
│       └── approvals/          # D1.29 human gate
├── packages/ (web shared) / tests/
├── infra/ (docker-compose, deploy)
└── docs/
```

**Dependency direction (strict):**
```
api routes → pipeline → agents → engines → data → db
                          └──────────→ engines (direct, for veto checks)
engines NEVER import agents. agents NEVER compute financials.
execution NEVER reachable except via pipeline gate results.
```

## 5. Key subsystem decisions

### 5.1 Data layer (D1.3) — built before agents
- Every fact row carries: `source`, `fetched_at`, `period_end`, `published_at`, `unit`, `currency`, `is_restated`, `supersedes_id`.
- Point-in-time reads: `as_of(date)` query helpers; backtests may only read `published_at <= as_of`.
- Missing value → explicit `NULL` + `data_gap` flag; never zero-filled.
- Freshness states: `fresh | stale | expired` computed per source SLA; stale data lowers CIO confidence (Part 20 input) and surfaces UI badges.

### 5.2 Provider adapters
```
ProviderAdapter protocol:
  capabilities() → {asset classes, data types, rate limits, redistribution}
  fetch(...) → RawPayload (always persisted before normalization)
  health() / quota()
```
Sources: SEC EDGAR (authoritative fundamentals), FRED (macro), one market-data provider for OHLCV (Yahoo/Tiingo/FMP — pick at sprint), IBKR adapter constrained by entitlements + pacing. Per-source throttle + circuit breaker + cache TTL.

### 5.3 Engines
Pure functions + thin service wrappers. Each engine: versioned formula definitions, unit/edge-case tests, golden datasets. Engines are shared verbatim by backtester — single source of truth for production vs. simulation.

### 5.4 Agent orchestration
- `Agent.run(context) -> AgentOutput` — schema-validated (Part 28), persisted to `agent_runs`/`agent_outputs` with model name, prompt hash, input snapshot.
- Research agents run in parallel; Risk Manager runs last before PM/CIO; CIO synthesizes.
- Every agent call includes the deterministic engine results and data-quality header.
- LLM calls behind retries + timeout + output-repair pass; malformed output = failed run, never coerced.

### 5.5 Risk veto enforcement
`pipeline` computes `risk_gate = engines.risk.evaluate(...)`. If any hard limit fails → `BLOCKED` written to `gate_results`; CIO receives the block as input but cannot flip it; execution endpoint validates gate state server-side. Veto is a database-enforced invariant.

### 5.6 Execution isolation (D1.30)
`execution/` module behind `EXECUTION_ENABLED` flag. Order state machine: `proposed → risk_approved → human_approved → submitted → filled|rejected|cancelled`. Reconciliation job diffs internal orders vs broker state. Paper adapter is default; IBKR requires explicit config + entitlement check. Emergency kill switch: `orders.halt_all` flag checked in submission path.

### 5.7 Audit & records (D1.33)
Append-only `audit_events` + `investment_records` (no UPDATE/DELETE paths; enforced by DB permissions). Every gate transition, agent run, approval, and order event writes an audit row.

### 5.8 Background processing
arq workers (Redis): ingestion jobs, indicator refresh, monitoring evaluators, alert dispatch, agent runs, reconciliation. Schedules defined in code; job runs persisted.

## 6. Non-functional requirements

- **Testing:** pytest (engines: unit + golden data; pipeline: journey tests; API: integration), vitest/playwright for web, mypy strict on `engines/`.
- **Observability:** structured logs (structlog), correlation IDs across pipeline runs, agent cost tracking, provider health dashboard.
- **Security:** secrets via env/`.env` (gitignored) → later vault; no secrets client-side; RBAC roles `admin | trader | viewer` now, `tenant` scoped later.
- **Deployment (Phase 1):** docker-compose: `api`, `web`, `postgres(+timescale)`, `redis`, `worker`. Single host acceptable; IaC deferred.
- **Multi-tenancy readiness:** `tenant_id` reserved columns + `current_tenant` request context now; enforcement enabled at Phase 2.

## 7. Decisions deferred

| Decision | When | Depends on |
|---|---|---|
| Market-data provider selection (Tiingo vs FMP vs Yahoo) | Sprint 1 | licensing + coverage check |
| TimescaleDB enablement | When OHLCV volume justifies | measurable insert/query pressure |
| Full TradingView license | Phase 2 | product decision |
| Auth provider (managed) | Phase 2 | SaaS onboarding requirements |
| Broker #2 (Alpaca/Tradier) | Phase 2 | client demand |

## 8. Risks

1. **Spec conflicts C1–C9** (see REQUIREMENTS) — defaults assumed; owner sign-off needed.
2. **Data licensing** — some listed sources (TIKR, Macrotrends, GuruScreener) lack public APIs; Phase 1 uses EDGAR/FRED/one market provider and stubs the rest behind adapters.
3. **IBKR constraints** — entitlement/pacing mean live data cannot be assumed; provider abstraction absorbs this.
4. **"20 live trades" milestone** — assumed paper trades (C4).
5. **Agent determinism** — mitigated by schema validation + engine-injected numbers + replay tests with recorded responses.
