from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.instruments import Instrument, Sector
from app.models.mandate import Mandate
from app.routers.risk import _portfolio_ctx

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/positions")
async def positions(db: AsyncSession = Depends(get_db)):
    """Positions w/ weights — real DB state; empty book is empty."""
    ctx = await _portfolio_ctx(db)
    nav = ctx["nav"]
    return {
        "nav": nav if ctx["positions"] else None,
        "cash": ctx["cash"],
        "positions": [
            {**p, "weight_pct": round(p["market_value"] / nav * 100, 2)
             if nav else None}
            for p in ctx["positions"]],
        "as_of": ctx["as_of"],
        "note": "broker positions reconcile separately — this is the "
                "ledger book",
    }


@router.get("/allocation")
async def allocation(db: AsyncSession = Depends(get_db)):
    """Mandate target split vs actual sector/position weights."""
    m = (
        await db.execute(
            select(Mandate).order_by(Mandate.version.desc()).limit(1))
    ).scalars().first()
    ctx = await _portfolio_ctx(db)
    nav = ctx["nav"]
    by_sector: dict[str, float] = {}
    for p in ctx["positions"]:
        by_sector[p["sector"]] = (by_sector.get(p["sector"], 0)
                                + p["market_value"])
    return {
        "mandate_targets": {
            "investment_pct": m.investment_split_pct if m else 70,
            "trading_pct": m.trading_split_pct if m else 30,
            "max_stocks_investment": m.inv_max_stocks if m else 15,
            "max_stocks_trading": m.trading_max_stocks if m else 5,
            "max_drawdown_pct": m.max_drawdown_pct if m else 15,
        },
        "actual": {
            "nav": nav if ctx["positions"] else None,
            "sectors": [
                {"sector": s, "weight_pct": round(v / nav * 100, 2)}
                for s, v in sorted(by_sector.items(),
                                   key=lambda kv: -kv[1])],
            "largest_position_pct": max(
                (p["market_value"] / nav * 100
                 for p in ctx["positions"]), default=None),
        },
        "note": "splits target mandate policy; trading vs investment "
                "cash is tracked by order_tickets.side routing",
    }
