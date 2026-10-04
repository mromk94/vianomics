"""Statement-style fundamentals for the symbol page — the investing.com
layout: Key Ratios + Income Statement / Balance Sheet / Cash Flow
tables with fiscal periods as columns.

Observations are grouped per concept, latest filings first; each
concept keeps up to MAX_PERIODS points tagged FY or Q1-Q4 so the UI
can render an Annual/Quarterly toggle without re-querying.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument

MAX_PERIODS = 6
MAX_CONCEPTS_PER_STATEMENT = 24

# XBRL concept keyword → statement bucket. Compact map — unmatched
# concepts land in "other" rather than being mislabeled.
_CASHFLOW = ("NetCashProvidedByUsedIn", "PaymentsToAcquire",
             "PaymentsOfDividends", "PaymentsForRepurchase",
             "ProceedsFrom", "DepreciationDepletionAndAmortization",
             "ShareBasedCompensation", "PaymentsRelatedTo")
_BALANCE = ("Assets", "Liabilities", "StockholdersEquity",
            "LongTermDebt", "ShortTermBorrowings", "Debt",
            "CashAndCashEquivalents", "Inventory", "Goodwill",
            "RetainedEarnings", "AccountsReceivable",
            "AccountsPayable", "MinorityInterest",
            "CommonStockShares", "EntityCommonStockShares",
            "IntangibleAssets", "PropertyPlantAndEquipment")
_INCOME = ("Revenue", "Income", "Earnings", "CostOf", "Expense",
           "Profit", "Interest", "Tax", "Dividends", "ShareBased",
           "Operating", "Research", "SellingGeneral",
           "WeightedAverage")


def _statement(concept: str) -> str:
    c = concept.split(":")[-1]
    if any(k in c for k in _CASHFLOW):
        return "cashflow"
    if any(k in c for k in _INCOME):
        return "income"
    if any(k in c for k in _BALANCE):
        return "balance"
    return "other"


def _latest(points: dict[str, list[dict]], *concepts: str) -> float | None:
    """First concept (by alias preference) with any point."""
    for c in concepts:
        hits = points.get(c) or points.get(f"us-gaap:{c}")
        if hits:
            return hits[0]["value"]
    return None


async def statement_map(db: AsyncSession, inst: Instrument,
                        close: float | None = None) -> dict:
    """{statements: {income|balance|cashflow|other: [concept rows]},
        ratios: derived key ratios, periods: column headers}."""
    rows = (await db.execute(
        select(FO.concept, FO.period_end, FO.fiscal_period,
               FO.value, FO.unit, FO.published_at, FO.quality)
        .where(FO.instrument_id == inst.id,
               FO.quality != "quarantined")
        .order_by(FO.period_end.desc(), FO.published_at.desc().nulls_last())
    )).all()

    by_concept: dict[str, list[dict]] = {}
    units: dict[str, str | None] = {}
    for r in rows:
        pts = by_concept.setdefault(r.concept, [])
        # dedupe same period_end — latest published wins (rows sorted)
        if any(p["end"] == r.period_end.isoformat() for p in pts):
            continue
        pts.append({
            "end": r.period_end.isoformat(),
            "fp": r.fiscal_period or "?",
            "value": float(r.value),
        })
        units.setdefault(r.concept, r.unit)

    statements: dict[str, list[dict]] = {
        "income": [], "balance": [], "cashflow": [], "other": []}
    for concept, pts in sorted(by_concept.items()):
        statements[_statement(concept)].append({
            "concept": concept,
            "label": (concept.split(":")[-1]
                      .replace("ExcludingAssessedTax", "")
                      .replace("FromContractWithCustomer", "")),
            "unit": units[concept],
            "points": pts[:MAX_PERIODS],
        })
    for k in statements:
        statements[k] = statements[k][:MAX_CONCEPTS_PER_STATEMENT]

    # ── derived key ratios (investing.com grid) — only where the
    # facts exist; nulls render as —
    ni = _latest(by_concept, "NetIncomeLoss", "ProfitLoss",
                 "us-gaap:NetIncomeLoss")
    eq = _latest(by_concept, "StockholdersEquity",
                 "StockholdersEquityIncludingPortionAttributableTo"
                 "NoncontrollingInterest")
    debt = _latest(by_concept, "LongTermDebt",
                   "LongTermDebtAndCapitalLeaseObligations",
                   "LongTermDebtNoncurrent")
    ebitda = _latest(by_concept, "EBITDA")
    eps = _latest(by_concept, "EarningsPerShareDiluted",
                  "EarningsPerShareBasic")
    shares = _latest(by_concept, "EntityCommonStockSharesOutstanding",
                     "CommonStockSharesOutstanding",
                     "WeightedAverageNumberOfDilutedSharesOutstanding")
    div_ps = _latest(by_concept,
                     "CommonStockDividendsPerShareDeclared",
                     "CommonStockDividendsPerShareCashPaid")

    ratios = {
        "pe": (close / eps) if close and eps else None,
        "pb": (close / (eq / shares))
        if close and eq and shares else None,
        "debt_equity": (debt / eq) if debt is not None and eq else None,
        "roe": (ni / eq) if ni is not None and eq else None,
        "ebitda": ebitda,
        "dividend_yield": (div_ps / close) if div_ps and close else None,
        "shares": shares,
        "eps_diluted": eps,
    }

    # union of all period columns (for the table header) — newest first
    cols: dict[str, str] = {}
    for bucket in statements.values():
        for c in bucket:
            for p in c["points"]:
                cols[p["end"]] = p["fp"]

    return {
        "statements": statements,
        "ratios": ratios,
        "periods": [{"end": e, "fp": fp}
                    for e, fp in sorted(cols.items(), reverse=True)
                    [:MAX_PERIODS * 2]],
        "facts": sum(len(b) for b in statements.values()),
    }
