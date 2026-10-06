The goal is to turn your Vesturs investment framework into a working, institutional-style Trader OS, with a coherent architecture, reliable financial calculations, auditable AI decisions, and a polished interface.

I've received the guide, and the material available to me includes the complete 35-part VAIIP framework, its eight architectural layers, and the investment governance and execution principles. I'll use that as the product's foundation rather than inventing a different investment methodology.

The central principle for this build: we're not building a chatbot that happens to analyze stocks. We're building an investment operating system in which data, deterministic financial engines, specialized AI agents, risk controls, portfolio decisions, and human approval work together.

1. What I found in the documents

I read the full 33-page CHOREDATA OS Scope of Work, Revision 2.0, including its deliverables, acceptance criteria, trial project, milestones, Phase 2 and Phase 3 scope, and traceability matrix. I also reviewed the accompanying 35-part Institutional Investment Operating System framework.

The two documents serve different purposes, and both matter.

Document 1 — CHOREDATA OS SOW 2.0

33 pages reviewed

Defines the contracted product, 35 technical deliverables, acceptance criteria, trial prototype, four-month internal-engine milestone, SaaS productization, and strategy marketplace.

Document 2 — Institutional Investment Operating System Revised

35-part framework reviewed

Defines the actual investment methodology, eight layers, financial screening rules, valuation logic, technical indicators, risk controls, agent hierarchy, and investment decision process.

The build has three distinct product horizons
Phase 1 · Internal OS

Vesturs institutional investment engine

A complete, private investment workstation for the founding trader: universe screening, research, valuation, portfolio management, risk, AI-assisted decisions, trade records, and eventually broker execution.

Primary objective: prove that all 35 parts work together in a complete investment workflow.

Phase 2 · SaaS

Multi-tenant commercial platform

Isolated trader accounts, subscription billing, Analyst, Quant and Fund tiers, self-service onboarding, paper trading, and product entitlements.

Phase 3 · Marketplace

Data and strategy ecosystem

Opt-in anonymized intelligence, institutional data feeds, vetted third-party strategies, and strategy licensing.

One important implementation decision: Phase 1 should be architected so Phase 2 does not require rebuilding the entire application. However, multi-tenant billing and a public strategy marketplace should not delay the first working internal investment engine.

The SOW's month-by-month milestones are useful planning targets, not a reason to sacrifice correctness in financial calculations or data integrity.

2. Research findings that change how we should build it

I researched the external technical dependencies, with particular attention to the SEC, Interactive Brokers, market-data access, and the requirements for a credible financial research and trading system.

These findings directly affect the prompts I'm going to give you.

A. Data infrastructure must be built before AI agents

The SEC offers public company submissions and extracted XBRL data through its EDGAR APIs. This gives us an authoritative source for a substantial part of US-company fundamentals. The architecture should ingest, normalize, timestamp, and retain source information before an agent interprets it. 
data.sec.gov
+1

Financial values need a source, reporting period, publication date, currency, unit, and freshness status. A missing value must not silently become zero, and a current restatement must not leak into an older backtest.

B. Interactive Brokers is not a limitless market-data provider

IBKR's API depends on account permissions, market-data subscriptions, and concurrent market-data lines. Historical-data requests also have pacing and availability constraints. Its paper-trading environment has simulated execution behavior that may differ from live trading. 
Documentation
+2

Therefore, the OS needs a provider abstraction, request throttling, caching, entitlement checks, stale-data indicators, retry handling, and a separate paper-trading validation stage.

It must not assume every symbol has live data or that an API connection automatically grants redistribution rights.

C. Financial calculations must be deterministic

AI can interpret filings, explain a business, identify contradictions, formulate a thesis, and synthesize research. It should not be the authority for arithmetic involving position sizes, exposure, ATR stops, margin, valuation formulas, or risk-limit enforcement.

Those calculations belong in tested, versioned software functions. AI agents receive the results and supporting evidence, not permission to invent or override them.

D. Broker execution needs a separate security boundary

IBKR's own documentation describes its API as an interface to TWS or IB Gateway, with the broker responsible for its own order validations. Our application must implement its own pre-trade risk checks, approval records, order-state reconciliation, and emergency controls. 
Documentation
+1

The CIO cannot directly place an order. The Risk Manager's veto must be enforced by the backend, and the human approval must be recorded before an order can be transmitted.

The most important architecture decision

I would structure Vesturs as a modular, API-first financial platform with a Python quantitative and AI backend, a TypeScript web application, and a durable relational data foundation.

The technology should be selected based on the existing repository if one already exists. We should not let an AI coding IDE replace a working stack simply because a prompt mentions a different framework.

3. Recommended technical architecture

This is the proposed starting architecture for the coding IDE. The first prompt will require the IDE to inspect your actual environment and validate these choices before implementing anything.

Vesturs · VAIIP

Proposed modular architecture · Phase 1 designed for Phase 2 SaaS expansion

Presentation layer

Next.js · TypeScript · Tailwind · Recharts / TradingView chart integration

Investment dashboard, research workbench, portfolio, charts, alerts, approval interface

Application API and workflow

Python · FastAPI · Pydantic · RBAC

Authentication, APIs, decision workflow, authorization, audit events

Quant engine

Pandas, NumPy, SciPy, tested financial functions, backtesting

Agent orchestration

Specialized agents, structured outputs, evidence, CIO synthesis

Risk and governance

Independent veto, hard limits, approvals, immutable decision history

Provider adapters

SEC, FRED, market data, news, IBKR, future broker integrations

Persistence and background infrastructure

PostgreSQL · TimescaleDB where appropriate · Redis · Background workers

Fundamentals, time series, portfolios, agent runs, jobs, audit events, cached data

Proposed stack

Component

	

Starting choice

	

Reason




Frontend

	

Next.js + TypeScript

	

Complex, responsive institutional workstation




Backend

	

Python + FastAPI

	

Quantitative analysis, data science, AI integrations




Database

	

PostgreSQL

	

Transactional consistency and structured financial records




Time series

	

TimescaleDB extension, if justified

	

Efficient historical OHLCV and indicator data




Cache / queue

	

Redis + worker framework

	

Rate limits, asynchronous jobs, alerts, background ingestion




AI

	

Provider-agnostic LLM interface

	

Support different models without rewriting agents




Charts

	

TradingView integration or equivalent

	

Candlesticks, technical overlays, multiple timeframes




Broker

	

IBKR adapter first

	

Separated account, order and execution interfaces

These are proposed choices, not assumptions that the existing project already uses them. If the IDE discovers a working architecture, it should document and preserve it unless there is a clear, tested reason to migrate.

Critical separation: the AI research layer, deterministic financial engine, risk control service, and broker execution service should have distinct responsibilities and permissions.

4. The build marathon: sequential coding prompts

The following is the master execution plan. Each prompt is designed to be pasted into your AI coding IDE as a separate task. They are ordered by dependency: later prompts rely on foundations created and tested by earlier ones.

Do not feed the entire marathon to the IDE in one message. That would encourage it to create disconnected mock screens, placeholder calculations, and a tangled codebase.

Instead, complete one prompt, inspect the changes, run its acceptance tests, and then proceed.

The operating rules for every prompt

These rules should be included in the first IDE instruction and retained as a project-level development document.

Non-negotiable engineering rules

Inspect the repository before changing files. Never assume a blank project.

Reuse existing working components, routes, APIs, and database models.

Never replace real functionality with hardcoded mock data just to make a screen look complete.

Never invent financial values, market data, agent findings, or successful API responses.

All financial calculations must have explicit formulas, units, edge-case handling, and automated tests.

Every external data point must retain provenance, timestamp, freshness, and licensing metadata where applicable.

All AI agent outputs must follow a validated structured schema.

Every major task must end with a working build, tests, a change summary, and a list of remaining limitations.

Do not expose API keys, broker credentials, or secrets in frontend code, logs, or source control.

No live trade execution without backend risk approval and recorded human authorization.

MARATHON I — Foundation and product shell
Build order 01–04

These first four prompts establish the codebase, data architecture, investment mandate, and professional interface. Do not build advanced agents before these foundations are functional.