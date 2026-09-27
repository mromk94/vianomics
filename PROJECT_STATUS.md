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

## Session log
- **2026-09-27** — Initial audit + documentation scaffold. Repo audited (empty), 3 docs created, spec conflicts catalogued.
- **2026-09-27** — M0 + app shell implemented per shell prompt. Backend verified live (healthz ok, command-center returns demo-flagged payload + real infra health). Nothing committed to git yet — awaiting owner review. Next: M1.1 mandate/universe schema + EDGAR adapter.
