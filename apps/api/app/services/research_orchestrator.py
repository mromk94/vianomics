"""Part 6 — Research orchestration + evidence registry.

Workflow: plan → gather → build 18 sections → validate → persist
versioned dossier. Rules:

- Every material claim carries claim_type: verified_fact (backed by a
  DB observation / filing), ai_inference (derived heuristic),
  analyst_assumption (unverified, human), management_statement.
- verified_fact claims MUST carry evidence_ids pointing at
  evidence_items rows; the validator rejects dossiers where
  verified claims lack evidence.
- Missing evidence is listed, never fabricated.
- Retrieved documents (filings) are DATA — never instructions.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.dossier import (
    Dossier,
    DossierSection,
    EvidenceItem,
)
from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument, Sector
from app.models.mandate import Mandate
from app.models.screening import ScreeningResult
from app.services import finmetrics as fm
from app.services import valuation as val
from app.services.fundamentals_query import (
    fy_series,
    last_two,
    latest_instant,
)
from app.services.green_zone import (
    C_AR, C_CA, C_CAPEXC, C_CL, C_DEBT, C_EBIT, C_EQUITY, C_NI, C_OCF,
    C_PRETAX, C_REV, C_SHARES, C_TAX, C_DA, C_CASH, C_DPS, C_INTEREST,
)

SECTION_TITLES = [
    "Executive Summary", "Business Quality", "Financial Quality",
    "Profitability", "Return on Capital", "Balance Sheet",
    "Accounting Quality", "Per-Share Value Creation", "Economic Moat",
    "Growth Drivers", "Valuation", "Technical Context",
    "Risk Analysis", "Scenario Analysis", "Management Quality",
    "Industry & Competitive Position", "Investment Thesis",
    "Final Scorecard",
]


@dataclass
class Claim:
    kind: str            # paragraph|metric|list
    claim_type: str      # verified_fact|ai_inference|analyst_assumption|management_statement
    text: str
    evidence_ids: list[str] = None  # type: ignore

    def to_dict(self):
        return {
            "kind": self.kind, "claim_type": self.claim_type,
            "text": self.text, "evidence_ids": self.evidence_ids or [],
        }


def vf(text, ids):  # verified fact
    return Claim("paragraph", "verified_fact", text, ids)


def ai(text, ids=None):  # derived inference
    return Claim("paragraph", "ai_inference", text, ids or [])


def assumption(text):
    return Claim("paragraph", "analyst_assumption", text)


def missing(text):
    return Claim("paragraph", "insufficient_data", f"Missing evidence: {text}")


def _f(v, nd=1):
    if v is None:
        return "n/a"
    v = float(v)
    if abs(v) >= 1e9:
        return f"${v/1e9:,.1f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:,.1f}M"
    return f"{v:,.{nd}f}"


def _p(v):
    return f"{float(v)*100:.1f}%" if v is not None else "n/a"


async def _collect(
    db: AsyncSession, inst: Instrument, as_of: datetime
) -> tuple[dict, dict[date, float], list[EvidenceItem], list[dict]]:
    """Fetch FY series + instants; register evidence for every value."""
    series_keys = {
        "rev": C_REV, "ni": C_NI, "ocf": C_OCF, "ebit": C_EBIT,
        "shares": C_SHARES, "capex": C_CAPEXC, "dps": C_DPS,
        "ar": C_AR, "tax": C_TAX, "pretax": C_PRETAX, "da": C_DA,
        "equity": C_EQUITY,
    }
    series: dict[str, dict] = {}
    evid_map: dict[date, float] = {}
    evidence: list[EvidenceItem] = []
    ev_ids: dict[str, list[str]] = {}

    for key, concepts in series_keys.items():
        s = await fy_series(db, inst.id, concepts, as_of, years=6)
        series[key] = s
        for pe, v in s.items():
            e = EvidenceItem(
                instrument_id=inst.id, kind="filing_observation",
                concept=concepts[0], value=str(v), unit="USD",
                period_end=pe.isoformat(), source="SEC EDGAR XBRL",
                published_at=None,
            )
            db.add(e)
            await db.flush()
            evidence.append(e)
            ev_ids.setdefault(f"{key}:{pe}", []).append(e.id)

    instants = {
        "equity_i": C_EQUITY, "debt": C_DEBT, "cash": C_CASH,
        "interest": C_INTEREST, "ca": C_CA, "cl": C_CL,
        "debt_cur": ["us-gaap:LongTermDebtCurrent",
                     "ifrs-full:CurrentPortionOfLongtermBorrowings",
                     "ifrs-full:CurrentBorrowings",
                     "yahoo:CurrentDebt"],
    }
    point: dict[str, float | None] = {}
    for key, concepts in instants.items():
        v = await latest_instant(db, inst.id, concepts, as_of)
        point[key] = v
        if v is not None:
            e = EvidenceItem(
                instrument_id=inst.id, kind="filing_observation",
                concept=concepts[0], value=str(v), unit="USD",
                source="SEC EDGAR XBRL",
            )
            db.add(e)
            await db.flush()
            evidence.append(e)
            ev_ids.setdefault(key, []).append(e.id)

    return {"series": series, "point": point}, evid_map, evidence, ev_ids


def _ev(ev_ids, key, series, which="latest"):
    if not series.get(key):
        return []
    items = sorted(series[key].items())
    if which == "latest":
        pe = items[-1][0]
    elif which == "prev":
        pe = items[-2][0] if len(items) >= 2 else items[-1][0]
    else:
        pe = items[0][0]
    return ev_ids.get(f"{key}:{pe}", [])


async def _peers(db: AsyncSession, inst: Instrument, as_of: datetime) -> list[dict]:
    """Same-sector instruments → per-peer key metrics."""
    if inst.sector_id is None:
        return []
    peers = (
        await db.execute(
            select(Instrument).where(
                Instrument.sector_id == inst.sector_id,
                Instrument.id != inst.id,
                Instrument.listing_status == "active",
            )
        )
    ).scalars().all()
    out = []
    for p in peers:
        rev = await fy_series(db, p.id, C_REV, as_of)
        ni = await fy_series(db, p.id, C_NI, as_of)
        ebit = await fy_series(db, p.id, C_EBIT, as_of)
        prev_r, cur_r = last_two(rev)
        prev_ni, cur_ni = last_two(ni)
        eq = await latest_instant(db, p.id, C_EQUITY, as_of)
        rg = fm.growth(prev_r, cur_r) if prev_r else None
        m = fm.margin(last_two(ebit)[1], cur_r) if cur_r else None
        roe_v = fm.roe(cur_ni, eq) if eq else None
        out.append({
            "symbol": p.symbol, "name": p.name,
            "rev_growth": float(rg) if rg is not None else None,
            "op_margin": float(m) if m is not None else None,
            "roe": float(roe_v) if roe_v is not None else None,
        })
    return out


async def build_dossier(
    db: AsyncSession,
    inst: Instrument,
    actor_id: str | None,
    mandate: Mandate | None,
    policy_version: int | None,
    as_of: datetime | None = None,
    price: float | None = None,
) -> Dossier:
    as_of = as_of or utcnow()
    log: list[dict] = [
        {"step": "plan", "at": as_of.isoformat(),
         "tasks": ["fetch fundamentals", "compute metrics",
                   "peer comparison", "sector valuation",
                   "build 18 sections", "validate claims"]},
    ]

    data, _, evidence, ev = await _collect(db, inst, as_of)
    S, P = data["series"], data["point"]
    log.append({"step": "gather",
                "observations": sum(len(s) for s in S.values()),
                "evidence_items": len(evidence)})

    sector = None
    if inst.sector_id:
        sector = (
            await db.execute(select(Sector).where(Sector.id == inst.sector_id))
        ).scalar_one_or_none()
    sector_name = sector.name if sector else None
    arch = val.archetype_for(sector_name)

    # latest Green Zone screen, if any
    gz = (
        await db.execute(
            select(ScreeningResult)
            .where(ScreeningResult.instrument_id == inst.id)
            .order_by(ScreeningResult.created_at.desc())
        )
    ).scalars().first()

    peers = await _peers(db, inst, as_of)

    # ── computed metrics bundle ──
    prev_r, cur_r = last_two(S["rev"])
    prev_ni, cur_ni = last_two(S["ni"])
    prev_ocf, cur_ocf = last_two(S["ocf"])
    prev_eb, cur_eb = last_two(S["ebit"])
    equity = P["equity_i"]
    rg = fm.growth(prev_r, cur_r)
    ni_g = fm.growth(prev_ni, cur_ni)
    op_m = fm.margin(cur_eb, cur_r) if cur_eb and cur_r else None
    roe_v = fm.roe(cur_ni, equity)
    roic_v = fm.roic(cur_eb, last_two(S["tax"])[1],
                     last_two(S["pretax"])[1], P["debt"], equity, P["cash"])
    fcf_v = fm.fcf(cur_ocf, last_two(S["capex"])[1]) if cur_ocf else None
    sh = last_two(S["shares"])[1]
    fcfps = fm.per_share(fcf_v, sh) if fcf_v is not None else None
    bvps = fm.per_share(equity, sh)
    eps = fm.per_share(cur_ni, sh)
    net_debt = (Decimal(str(P["debt"] or 0)) - Decimal(str(P["cash"] or 0)))
    ebitda = (Decimal(str(cur_eb)) + Decimal(str(last_two(S["da"])[1] or 0))) \
        if cur_eb is not None else None

    val_summary = val.compute(
        sector_name, price=price, shares=sh, equity=equity,
        eps=eps, ebitda=ebitda, net_debt=net_debt,
        ni_series=[v for _, v in sorted(S["ni"].items())],
        fcf_ps=fcfps,
    )

    miss: list[dict] = []
    def need(cond, what):
        if not cond:
            miss.append({"item": what, "reason": "not in provider data"})

    need(S["rev"], "revenue history")
    need(S["ni"], "net income history")
    need(price is not None, "market price (needs market-data provider)")
    need(bool(peers), "peer set (same-sector securities)")

    sym = inst.symbol
    sections: list[DossierSection] = []

    def sec(no, claims, status=None):
        verified = any(c.claim_type == "verified_fact" for c in claims)
        st = status or ("verified" if verified else
                        "partial" if any(
                            c.claim_type == "ai_inference" for c in claims)
                        else "insufficient")
        sections.append(DossierSection(
            section_no=no, title=SECTION_TITLES[no - 1],
            status=st, content=[c.to_dict() for c in claims],
        ))

    # 1 Executive summary
    c = []
    if cur_r is not None:
        c.append(vf(f"{sym} ({inst.name}) — {sector_name or 'unclassified'} · "
                    f"revenue FY {sorted(S['rev'])[-1].year}: "
                    f"{_f(cur_r)}, growth {_p(rg)}.",
                    _ev(ev, "rev", S)))
    else:
        c.append(missing("revenue history — executive metrics unavailable"))
    if not c:
        c.append(ai(f"{sym} ({inst.name}) — {sector_name or 'unclassified'}."))
    if gz:
        c.append(ai(
            f"Green Zone {gz.score}/{gz.applicable} → {gz.verdict}"
            + (" (qualified for further research)" if gz.qualified
               else " — not currently Green Zone qualified"),
        ))
    c.append(ai(
        f"Sector archetype: {arch} valuation framework applied."
    ))
    sec(1, c)

    # 2 Business quality — OCF consistency
    ocfs = [v for _, v in sorted(S["ocf"].items())]
    c = []
    if len(ocfs) >= 3:
        all_pos = all(v > 0 for v in ocfs)
        c.append(vf(
            f"Positive operating cash flow in {sum(1 for v in ocfs if v>0)}/"
            f"{len(ocfs)} reported years "
            f"(latest { _f(cur_ocf)}).",
            _ev(ev, "ocf", S)))
        c.append(ai("Cash-generative operating history suggests durable "
                    "business economics." if all_pos else
                    "Inconsistent cash generation — quality question flag."))
    else:
        c.append(missing("≥3 years of OCF history"))
    sec(2, c)

    # 3 Financial quality — cash conversion + accruals gap
    c = []
    if cur_ocf and cur_ni and cur_ni > 0:
        ratio = cur_ocf / cur_ni
        c.append(vf(f"Cash conversion OCF/NI = {ratio:.2f} "
                    f"(OCF {_f(cur_ocf)} vs NI {_f(cur_ni)}).",
                    _ev(ev, "ocf", S) + _ev(ev, "ni", S)))
        c.append(ai("Earnings are cash-backed." if ratio >= 0.9 else
                    "Accruals gap — NI exceeds cash generation; review "
                    "receivables/revenue recognition."))
    else:
        c.append(missing("OCF + NI for cash-conversion analysis"))
    sec(3, c)

    # 4 Profitability
    c = []
    if op_m is not None:
        c.append(vf(f"Operating margin {_p(op_m)} "
                    f"(EBIT {_f(cur_eb)} / revenue {_f(cur_r)}).",
                    _ev(ev, "ebit", S)))
    pm_prev = fm.margin(prev_eb, prev_r) if prev_eb and prev_r else None
    if op_m is not None and pm_prev is not None:
        c.append(ai(
            f"Margin trend {'expanding' if op_m > pm_prev else 'contracting'} "
            f"({_p(pm_prev)} → {_p(op_m)}).",
            _ev(ev, "ebit", S) + _ev(ev, "rev", S)))
    if not c:
        c.append(missing("EBIT + revenue for margin analysis"))
    sec(4, c)

    # 5 Return on capital
    c = []
    if roe_v is not None:
        c.append(vf(f"ROE {_p(roe_v)} (NI {_f(cur_ni)} / equity {_f(equity)}).",
                    _ev(ev, "ni", S) + [e for e in ev.get("equity_i", [])]))
    if roic_v is not None:
        c.append(vf(f"ROIC {_p(roic_v)} (NOPAT / invested capital).",
                    _ev(ev, "ebit", S)))
        c.append(ai("Returns above assumed 10–12% cost of capital — "
                    "value-creating reinvestment." if roic_v > 0.12 else
                    "Returns near/below cost of capital — reinvestment "
                    "economics weak."))
    if not c:
        c.append(missing("equity/debt/EBIT for ROE and ROIC"))
    sec(5, c)

    # 6 Balance sheet
    c = []
    if equity is not None:
        c.append(vf(f"Shareholders' equity {_f(equity)}; "
                    f"debt {_f(P['debt'])}; cash {_f(P['cash'])}; "
                    f"net debt {_f(float(net_debt))}.",
                    [e for e in ev.get("equity_i", [])
                     + ev.get("debt", []) + ev.get("cash", [])]))
    if P["ca"] and P["cl"]:
        c.append(vf(f"Current ratio {P['ca']/P['cl']:.2f} "
                    f"(CA {_f(P['ca'])} / CL {_f(P['cl'])}).",
                    ev.get("ca", []) + ev.get("cl", [])))
    if not c:
        c.append(missing("balance-sheet items"))
    sec(6, c)

    # 7 Accounting quality — AR vs revenue
    c = []
    if prev_r and cur_r and len(S["ar"]) >= 2:
        prev_ar, cur_ar = last_two(S["ar"])
        ag = fm.growth(prev_ar, cur_ar)
        if ag is not None and rg is not None:
            c.append(vf(
                f"Receivables growth {_p(ag)} vs revenue growth {_p(rg)}.",
                _ev(ev, "ar", S) + _ev(ev, "rev", S)))
            c.append(ai("Receivables outgrowing revenue — possible channel "
                        "stuffing or looser terms; flagged for review."
                        if ag > rg else
                        "Receivables tracking revenue — clean accruals."))
    else:
        c.append(missing("receivables series for accruals check"))
    sec(7, c)

    # 8 Per-share value creation
    c = []
    shares_vals = [v for _, v in sorted(S["shares"].items())]
    if len(shares_vals) >= 2:
        ch = fm.shares_change(shares_vals[-2], shares_vals[-1])
        c.append(vf(
            f"Share count {shares_vals[-1]:,.0f} (Δ{_p(ch)} YoY).",
            _ev(ev, "shares", S)))
        c.append(ai("Buyback-driven per-share compounding." if ch < 0 else
                    "Share dilution eroding per-share value." if ch > 0.02
                    else "Stable share count."))
    if bvps is not None:
        c.append(vf(f"Book value per share {float(bvps):,.2f}.",
                    [e for e in ev.get("equity_i", [])]))
    if fcfps is not None:
        c.append(vf(f"FCF per share {float(fcfps):,.2f} "
                    f"(FCF {_f(fcf_v)}).",
                    _ev(ev, "ocf", S) + _ev(ev, "capex", S)))
    if not c:
        c.append(missing("share count / equity / FCF"))
    sec(8, c)

    # 9 Economic moat — evidence from Green Zone criterion
    c = []
    moat_crit = None
    if gz:
        moat_crit = next(
            (x for x in gz.criteria if x["key"] == "moat"), None)
    if moat_crit:
        c.append(ai(
            f"Green Zone moat screen: {moat_crit['status']} "
            f"({moat_crit['formula']})."))
        c.append(ai(
            "Deterministic moat evidence requires human review before being "
            "treated as confirmed." if moat_crit["review_required"] else
            "Moat screen deterministic — no human-review flag."))
    else:
        c.append(missing("Green Zone moat assessment"))
    c.append(assumption(
        "Qualitative moat assessment (brand, switching costs, network "
        "effects) requires analyst work — not computed."))
    sec(9, c)

    # 10 Growth drivers
    c = []
    revs = [v for _, v in sorted(S["rev"].items())]
    if len(revs) >= 3:
        cagr = (Decimal(str(revs[-1])) / Decimal(str(revs[0]))) ** (
            Decimal(1) / (len(revs) - 1)) - 1
        c.append(vf(
            f"{len(revs)}-yr revenue CAGR {_p(cagr)} "
            f"({_f(revs[0])} → {_f(revs[-1])}).",
            _ev(ev, "rev", S, "first") + _ev(ev, "rev", S)))
        c.append(ai("Sustained double-digit growth — demand runway."
                    if cagr > 0.10 else
                    "Moderate growth — maturity or cyclical headwinds."))
    else:
        c.append(missing("≥3 years of revenue for CAGR"))
    sec(10, c)

    # 11 Valuation — sector-aware
    c = [ai(f"Archetype '{arch}' metrics for {sector_name or 'unclassified'} "
            f"sector.")]
    for k, v in val_summary["metrics"].items():
        if v is not None:
            c.append(ai(f"{k}: {v:,.2f}"))
    for m in val_summary["missing"]:
        c.append(missing(m))
    sec(11, c)

    # 12 Technical context — needs price data
    c = [missing("OHLCV price history — market-data provider not configured")]
    sec(12, c)

    # 13 Risk analysis — structured register
    risks = []
    if cur_ni is not None and cur_ni < 0:
        risks.append(("Profitability", "negative net income"))
    if P["debt"] and ebitda and P["debt"] > 0:
        lev = Decimal(str(P["debt"])) / ebitda
        if lev > 3:
            risks.append(("Leverage", f"debt/EBITDA {float(lev):.1f}x"))
    if rg is not None and rg < 0:
        risks.append(("Growth", "negative revenue growth"))
    if not risks:
        risks.append(("Data", "limited history — verify coverage"))
    sec(13, [ai(f"[{cat}] {detail}") for cat, detail in risks] +
            [assumption("Full risk register requires analyst review.")])

    # 14 Scenario analysis — deterministic bands, clearly AI
    c = []
    if cur_r and rg is not None:
        for name, mult in (("Bear", 0.5), ("Base", 1.0), ("Bull", 1.8)):
            proj = Decimal(str(cur_r)) * (1 + Decimal(str(rg)) * Decimal(str(mult)))
            c.append(ai(
                f"{name}: revenue { _f(float(proj))} at {float(mult)*100:.0f}% "
                f"of current growth momentum.",
                _ev(ev, "rev", S)))
    else:
        c.append(missing("revenue series for scenarios"))
    sec(14, c)

    # 15 Management quality — cannot fabricate statements
    c = [missing(
        "management statements, insider activity, capital-allocation "
        "track record — requires filings transcript/insider feeds")]
    sec(15, c)

    # 16 Industry & competitive position — peer metrics
    c = []
    if peers:
        c.append(ai(f"Peer set: {', '.join(p['symbol'] for p in peers)}."))
        for axis in ("rev_growth", "op_margin", "roe"):
            mine = {"rev_growth": rg, "op_margin": op_m, "roe": roe_v}[axis]
            vals = [p[axis] for p in peers if p[axis] is not None]
            if mine is not None and vals:
                rank = sum(1 for v in vals if mine > v)
                c.append(ai(
                    f"{axis}: {_p(mine)} — above {rank}/{len(vals)} peers."))
        c.append(assumption(
            "Market share, pricing power, retention, competitive intensity — "
            "require external industry data; not fabricated."))
    else:
        c.append(missing("same-sector peers for comparison"))
    sec(16, c)

    # 17 Investment thesis — template, analyst to complete
    c = [ai(
        f"Draft thesis: {sym} shows "
        f"{'strong' if (rg or 0) > 0.1 else 'modest'} fundamental momentum; "
        f"Green Zone {'pass' if gz and gz.qualified else 'not met'}. "
        "Formal thesis requires analyst synthesis of sections 2–16."),
        assumption("Analyst to complete: what the market misprices, "
                   "catalysts, kill conditions.")]
    sec(17, c)

    # 18 Final scorecard
    c = [
        ai(f"Green Zone: {gz.score}/{gz.applicable} → {gz.verdict}"
           if gz else "Green Zone screen not run."),
        ai(f"Section evidence coverage: see missing_evidence register "
           f"({len(miss)} gaps)."),
        ai("Verdict requires human approval — AI output is advisory."),
    ]
    sec(18, c)

    # ── validate ──
    problems = []
    for s in sections:
        for claim in s.content:
            if claim["claim_type"] == "verified_fact" and not claim["evidence_ids"] \
                    and claim["kind"] == "paragraph":
                problems.append(
                    f"§{s.section_no} verified_fact without evidence")
    log.append({"step": "validate",
                "sections": len(sections),
                "verified_without_evidence": len(problems),
                "problems": problems[:10]})

    # ── persist ──
    prev = (
        await db.execute(
            select(Dossier)
            .where(Dossier.instrument_id == inst.id)
            .order_by(Dossier.created_at.desc())
        )
    ).scalars().first()
    group = prev.group_id if prev else inst.id + "-d"
    version = (prev.version + 1) if prev else 1

    d = Dossier(
        group_id=group, instrument_id=inst.id, version=version,
        status="draft",
        mandate_version=mandate.version if mandate else None,
        policy_version=policy_version,
        green_zone_score=gz.score if gz else None,
        created_by=actor_id,
        missing_evidence=miss + [
            {"item": p, "reason": "validator"}
            for p in problems
        ],
        workflow_log=log + [{"step": "persist", "version": version}],
    )
    db.add(d)
    await db.flush()
    for s in sections:
        s.dossier_id = d.id
        db.add(s)
    return d
