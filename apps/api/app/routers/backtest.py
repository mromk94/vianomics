from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.backtest import BacktestRun
from app.models.identity import User
from app.security import audit, require
from app.services.backtest import BTParams
from app.services.backtest_service import run_and_persist

router = APIRouter(prefix="/backtest", tags=["backtest"])


class RunIn(BaseModel):
    symbols: list[str] = Field(min_length=1)
    name: str | None = None
    strategy: str = "mr_rsi"
    initial_capital: float = 100_000
    commission_per_trade: float = 1.0
    slippage_bps: float = 10.0
    risk_per_trade_pct: float = 0.02
    entry_rsi: float = 30.0
    exit_rsi: float = 55.0
    max_positions: int = 5
    with_validation: bool = True


@router.post("/run", status_code=201)
async def run(body: RunIn,
              db: AsyncSession = Depends(get_db),
              user: User = Depends(require("research:run"))):
    p = BTParams(
        strategy=body.strategy, initial_capital=body.initial_capital,
        commission_per_trade=body.commission_per_trade,
        slippage_bps=body.slippage_bps,
        risk_per_trade_pct=body.risk_per_trade_pct,
        entry_rsi=body.entry_rsi, exit_rsi=body.exit_rsi,
        max_positions=body.max_positions)
    res = await run_and_persist(db, body.symbols, p, name=body.name,
                                with_validation=body.with_validation)
    await audit(db, action="backtest.run", actor=user,
                entity_type="backtest_run", entity_id=res["run_id"],
                detail={"strategy": p.strategy,
                        "symbols": body.symbols})
    await db.commit()
    # strip the equity curve from the response payload for size — fetch
    # via /runs/{id}
    return {k: v for k, v in res.items() if k != "equity_curve"} | \
        {"equity_points": len(res["equity_curve"])}


@router.get("/runs")
async def runs(limit: int = 25, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(BacktestRun)
            .order_by(BacktestRun.created_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {"id": r.id, "name": r.name, "universe": r.universe,
         "engine": r.engine_version,
         "params": {k: r.params.get(k) for k in
                    ("strategy", "initial_capital", "slippage_bps")},
         "metrics": {k: r.metrics.get(k) for k in
                     ("cagr", "sharpe", "max_drawdown", "win_rate",
                      "trades")},
         "data_snapshot": r.data_snapshot,
         "created_at": r.created_at.isoformat(),
         "note": r.note}
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {
        "id": r.id, "name": r.name, "universe": r.universe,
        "engine": r.engine_version, "params": r.params,
        "metrics": r.metrics, "equity_curve": r.equity_curve,
        "trades": r.trades, "validation": r.validation,
        "data_snapshot": r.data_snapshot, "created_at":
        r.created_at.isoformat(), "note": r.note,
    }
