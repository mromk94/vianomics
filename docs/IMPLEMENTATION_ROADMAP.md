# VAIIP Implementation Roadmap

**Ordering rule:** dependency-first. Data before engines, engines before agents, agents before pipeline, pipeline before execution. Never build a screen whose data doesn't exist yet.

**Sprint completion contract (every item):**
1. Working build (`api` starts, `web` builds).
2. Tests for new logic pass (`pytest`, `vitest` where applicable).
3. Change summary + remaining limitations appended to `PROJECT_STATUS.md`.
4. Requirements matrix status updated.

---

## Milestone M0 — Project skeleton & rules
**Deliverables:** repo hygiene, `.gitignore`, `.env.example`, docker-compose (api/web/postgres/redis/worker), FastAPI skeleton with health + versioned router, Next.js shell with dark institutional theme matching mockup direction, pytest/vitest wired, CI-ready lint/typecheck, `tenant_id`-ready base models, Alembic init.
**Acceptance:** `docker compose up` → api `/healthz` 200, web renders shell, tests run green.
**Risks:** none material.

## Milestone M1 — Foundation (D1.1, D1.2, D1.3-core)
**Deliverables:** Mandate config engine + admin API; instrument master + universe tiers seeded with the 24 PDF tickers (C3); provider adapter framework; SEC EDGAR fundamentals ingestion; one OHLCV provider; FRED macro ingestion; provenance/PIT schema; data-quality flags.
**Acceptance:** fundamentals+prices+macro stored for universe tickers with full provenance; `as_of` reads correct under restatement test.
**Tests:** adapter contracts, PIT correctness, freshness.
**Risks:** provider choice + licensing (pick Tiingo/FMP — needs owner API keys); rate limits → throttle from day 1.

## Milestone M2 — Trial prototype parallel track (D0) *(can interleave with M1)*
**Deliverables:** macro regime classifier v1 (D1.9 subset), F&G vs put/call divergence dashboard (D1.11 subset), configurable alerts.
**Acceptance per SOW:** 3 historical divergence events surfaced; live alert; correct current regime.
**BLOCKER:** divergence "provided dataset" absent (C7) — supply dataset or derive from alternative.me F&G + CBOE put/call via public endpoints.

## Milestone M3 — Investment intelligence engines (D1.4, D1.5, D1.7, D1.10, D1.11)
**Deliverables:** Green Zone engine (20 criteria, configurable pass score — C1), sector valuation screens, Rule #1 engine (sticker/buy price), full technical indicator library + both signal branches (C5, C6), F&G/VIX overlay service.
**Acceptance:** Universe screen produces ranked candidates with per-criterion drilldown; Rule #1 outputs on golden dataset match reference; indicators verified vs TA reference values.
**Risks:** C1 threshold decision; some criteria need data that cheap providers don't give (DSCR, moat is qualitative → agent-assisted with human confirm).

## Milestone M4 — Risk & trade engines (D1.12–D1.15)
**Deliverables:** ATR engine + pyramid state machine (5 stages, persisted), investment 5-test monitor, 7-dimension risk calculator, limits config + enforcement (incl. PDF numeric defaults — C9).
**Acceptance:** pyramid transitions auditable; limit violations deterministically block.
**Risks:** margin/exposure math needs portfolio accounting primitives — keep minimal double-entry positions table.

## Milestone M5 — Agent framework (D1.26–D1.28)
**Deliverables:** LLM provider abstraction, Part-28 `AgentOutput` schema + validation, agent registry (Part-27 questions), run persistence, mock-LLM test harness.
**Acceptance:** Fundamental→Risk→CIO test workflow coherent; 100% schema conformance.
**Risks:** LLM cost/keys; nondeterminism → recorded-response tests.

## Milestone M6 — Governance agents & pipeline (D1.6, D1.16–D1.23, D1.33)
**Deliverables:** research dossier generator (18 sections — C8), Risk Manager with backend-enforced veto, PM agent + capital allocation comparator, CIO + confidence model (D1.20), conflict resolver (D1.21), decision tree orchestrator (D1.22), entry protocol (D1.23), investment decision record (D1.33), human approval gate + UI (D1.29).
**Acceptance:** candidate journeys: full-pass → human gate; risk-blocked → NO TRADE; every step in audit record.
**Risks:** largest LLM surface area — mitigate via engine-first inputs, strict schemas.

## Milestone M7 — Monitoring, exits, execution-paper (D1.24, D1.25, D1.30-paper)
**Deliverables:** monitoring evaluators + alert dispatcher + dashboard; exit engine 6 classes + pyramid exits; paper broker adapter + order state machine + reconciliation scaffold behind `EXECUTION_ENABLED`.
**Acceptance:** paper order lifecycle end-to-end; exit signals fire with reason codes; alerts visible in UI.
**Risks:** none beyond integration breadth.

## Milestone M8 — Validation & learning (D1.31, D1.32, D1.34, D1.35)
**Deliverables:** event-driven backtester reusing production engines (bias controls enforced), feedback/attribution engine, Ten Questions report, full master-cycle demo run.
**Acceptance:** 10-yr backtest report on Green Zone+Rule#1+mean-reversion strategy; look-ahead test proves PIT correctness; end-to-end demo on the 24-ticker universe.
**Risks:** survivorship-free historical fundamentals coverage — EDGAR depth may limit backtest window.

## Phase 2 (after Phase-1 acceptance)
D2.1 multi-tenancy + billing · D2.2 tier entitlements · D2.3 onboarding/NPS — schema hooks already reserved.

## Phase 3 (post-traction)
D3.1 anonymized feed · D3.2 marketplace.

---

## Next implementation task (exact)
**M0 scaffold** — repository hygiene + docker-compose + FastAPI `/healthz` + Next.js shell + test harness. Then **M1.1**: mandate config + instrument/universe schema + EDGAR adapter.

## Cross-cutting risks register
| Risk | Mitigation |
|---|---|
| Spec conflicts C1–C9 unresolved | Defaults documented; owner decision queue |
| Data source coverage vs 20 Green Zone criteria | Adapter gaps flagged as `data_gap`, not fabricated |
| Timeline (SOW 4-month) aggressive for solo build | Dependency-ordered milestones over calendar promises |
| Trial dataset missing | Blocker raised; public-source fallback proposed |
| Agent nondeterminism in audit | Persist prompts/inputs/outputs; replay tests |
| Broker execution prematurely enabled | `EXECUTION_ENABLED` flag + paper default + kill switch |
