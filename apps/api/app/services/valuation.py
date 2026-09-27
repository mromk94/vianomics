"""Part 5 — Sector-aware valuation.

Each sector archetype declares allowed metrics + a compute function.
Inappropriate metrics are rejected unless `override=True` with a
recorded `override_reason` — never silently substituted.
"""

from decimal import Decimal

from app.services import finmetrics as fm

# sector name → archetype
ARCHETYPES = {
    "Financials": "financial",
    "Real Estate": "real_estate",
    "Energy": "commodity",
    "Materials": "commodity",
    "Utilities": "commodity",
}

# allowed metrics per archetype — everything else needs override
ALLOWED = {
    "financial": {"pb", "roe", "bvps_quality", "capital_adequacy", "rotce"},
    "real_estate": {"nav", "p_ffo", "debt_ratio", "occupancy", "affo"},
    "commodity": {"normalized_eps", "cycle_margin", "capex_intensity",
                  "scenario_sensitivity"},
    "growth": {"pe", "ev_ebitda", "p_fcf", "rev_growth", "eps_cagr",
               "fwd_pe", "revisions"},
}
DEFAULT_ARCHETYPE = "growth"


def archetype_for(sector_name: str | None) -> str:
    return ARCHETYPES.get(sector_name or "", DEFAULT_ARCHETYPE)


def allowed_metrics(sector_name: str | None) -> set[str]:
    return ALLOWED[archetype_for(sector_name)]


def check_metric(
    metric: str,
    sector_name: str | None,
    override: bool = False,
    override_reason: str | None = None,
) -> dict:
    """Gate: refuse inappropriate metrics unless explicitly overridden."""
    arch = archetype_for(sector_name)
    ok = metric in ALLOWED[arch]
    if not ok and not (override and override_reason):
        return {
            "allowed": False,
            "metric": metric,
            "archetype": arch,
            "reason": (
                f"'{metric}' is not a standard {arch} metric — "
                "requires explicit override + reason"
            ),
        }
    return {
        "allowed": True,
        "metric": metric,
        "archetype": arch,
        "overridden": not ok,
        "override_reason": override_reason if not ok else None,
    }


def compute(
    sector_name: str | None,
    *,
    price=None,
    shares=None,
    equity=None,
    eps=None,
    ebitda=None,
    net_debt=None,
    ni_series=None,
    fcf_ps=None,
) -> dict:
    """Sector-appropriate metrics from available inputs — None-safe."""
    arch = archetype_for(sector_name)
    out: dict = {"archetype": arch, "metrics": {}, "missing": []}

    if arch == "financial":
        bvps = fm.per_share(equity, shares)
        pb = fm.price_ratio(price, bvps)
        out["metrics"]["bvps"] = float(bvps) if bvps is not None else None
        out["metrics"]["pb"] = float(pb) if pb is not None else None
        if ni_series:
            out["metrics"]["roe_latest"] = (
                float(fm.roe(ni_series[-1], equity)) if equity else None
            )
        for k in ("bvps", "pb"):
            if out["metrics"][k] is None:
                out["missing"].append(k)
        out["metrics"]["capital_adequacy"] = None
        out["missing"].append("capital_adequacy — needs regulatory filings")
        out["metrics"]["bvps_quality"] = None
        out["missing"].append("bvps_quality — needs AOCI/RWA decomposition")

    elif arch == "real_estate":
        out["metrics"]["nav"] = None
        out["metrics"]["p_ffo"] = None
        out["metrics"]["debt_ratio"] = (
            float(net_debt / Decimal(str(equity)))
            if net_debt is not None and equity and Decimal(str(equity)) > 0
            else None
        )
        out["missing"] += ["nav — property valuations not ingested",
                           "p_ffo — FFO requires NOI/depreciation detail",
                           "occupancy — property-level data not ingested"]

    elif arch == "commodity":
        # normalized EPS = average NI / shares across full cycle
        if ni_series and shares:
            avg_ni = sum(Decimal(str(v)) for v in ni_series) / len(ni_series)
            norm = fm.per_share(avg_ni, shares)
            out["metrics"]["normalized_eps"] = float(norm) if norm else None
            out["metrics"]["normalized_pe"] = (
                float(fm.price_ratio(price, norm)) if norm else None
            )
        else:
            out["metrics"]["normalized_eps"] = None
            out["metrics"]["normalized_pe"] = None
        out["metrics"]["cycle_margin"] = None
        out["metrics"]["capex_intensity"] = None
        out["missing"] += ["cycle_margin — needs commodity price deck",
                           "capex_intensity — needs capex detail"]

    else:  # growth
        pe = fm.price_ratio(price, eps)
        out["metrics"]["pe"] = float(pe) if pe is not None else None
        if ebitda is not None and net_debt is not None and Decimal(str(ebitda)) > 0 \
                and price is not None and shares is not None:
            ev = Decimal(str(price)) * Decimal(str(shares)) + Decimal(str(net_debt))
            out["metrics"]["ev_ebitda"] = float(ev / Decimal(str(ebitda)))
        else:
            out["metrics"]["ev_ebitda"] = None
        pfcf = fm.price_ratio(price, fcf_ps)
        out["metrics"]["p_fcf"] = float(pfcf) if pfcf is not None else None
        out["metrics"]["fwd_pe"] = None
        out["metrics"]["eps_cagr"] = None
        out["metrics"]["revisions"] = None
        out["missing"] += [
            "fwd_pe — consensus estimates not ingested",
            "eps_cagr — needs EPS history",
            "revisions — estimate revisions not ingested",
        ]
    return out
