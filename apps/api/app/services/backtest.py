"""Part 31 — Event-driven backtester. Point-in-time by construction:
signals at close(t) execute at open(t+1); indicators computed only
on bars[:i]. Costs & slippage explicit. Backtest output is never
labeled live performance.

Engine version: backtest/v1.0
Conventions: daily bars, close-signal → next-open fill, commission +
slippage bps per side, compounding equity.
"""

import random
from dataclasses import dataclass, field
from math import sqrt

from app.services import technical as ti

ENGINE_VERSION = "backtest/v1.0"


@dataclass
class BTParams:
    strategy: str = "mr_rsi"            # mr_rsi | tf_aroon | buy_hold
    initial_capital: float = 100_000.0
    commission_per_trade: float = 1.0
    slippage_bps: float = 10.0
    risk_per_trade_pct: float = 0.02
    atr_stop_mult: float = 1.5
    entry_rsi: float = 30.0
    exit_rsi: float = 55.0
    aroon_entry: float = 99.0
    adx_min: float = 25.0
    max_positions: int = 5
    adv_participation: float = 0.10


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_px: float
    qty: float
    stop_px: float
    exit_date: str | None = None
    exit_px: float | None = None
    reason: str | None = None
    pnl: float | None = None
    costs: float = 0.0


def _slip(px: float, side: str, bps: float) -> float:
    adj = px * bps / 10_000
    return px + adj if side == "buy" else px - adj


def _signal(hist: list[ti.Bar], p: BTParams) -> bool:
    """Entry condition on trailing data only."""
    if p.strategy == "mr_rsi" and len(hist) > 11:
        r = ti.rsi([b.c for b in hist], 10)
        return r is not None and r < p.entry_rsi
    if p.strategy == "tf_aroon" and len(hist) > 26:
        ar = ti.aroon(hist, 25)
        return bool(ar and ar["up"] > p.aroon_entry)
    return False


def _exit(hist: list[ti.Bar], cur: ti.Bar, pos: Trade,
          p: BTParams) -> tuple[float | None, str | None]:
    """Returns (exit_px, reason) or (None, None). Gap-through-stop
    fills at open when open < stop."""
    if cur.l <= pos.stop_px:
        return min(cur.o, pos.stop_px), "stop"
    if p.strategy == "mr_rsi" and len(hist) > 11:
        r = ti.rsi([b.c for b in hist], 10)
        if r is not None and r >= p.exit_rsi:
            return cur.o, "rsi_recovery"
    if p.strategy == "tf_aroon" and len(hist) > 26:
        ar = ti.aroon(hist, 25)
        if ar and ar["up"] < 50:
            return cur.o, "trend_loss"
    return None, None


def run_backtest(
    bars_by_symbol: dict[str, list[ti.Bar]], params: BTParams,
) -> dict:
    p = params
    all_dates = sorted({b.t for bars in bars_by_symbol.values()
                        for b in bars})
    idx = {s: {b.t: b for b in bars}
           for s, bars in bars_by_symbol.items()}
    pos_of = {s: {b.t: i for i, b in enumerate(bars)}
              for s, bars in bars_by_symbol.items()}

    cash = p.initial_capital
    positions: dict[str, Trade] = {}
    pending: list[dict] = []          # signals → fill next open
    closed: list[Trade] = []
    curve: list[dict] = []
    turnover = 0.0
    total_costs = 0.0
    liq_skips = 0

    for d in all_dates:
        # 1. fill pending entries at THIS bar's open
        for pend in list(pending):
            sym = pend["symbol"]
            i = pos_of[sym].get(d)
            if i is None:
                continue
            cur = bars_by_symbol[sym][i]
            fill = _slip(cur.o, "buy", p.slippage_bps)
            max_q = int(cur.v * p.adv_participation) if cur.v else \
                pend["qty"]
            qty = min(pend["qty"], max_q)
            if qty < pend["qty"]:
                liq_skips += 1
            cost = qty * fill + p.commission_per_trade
            if qty > 0 and cost <= cash:
                cash -= cost
                total_costs += p.commission_per_trade
                turnover += cost
                positions[sym] = Trade(
                    symbol=sym, entry_date=d.isoformat(), entry_px=fill,
                    qty=qty, stop_px=pend["stop"],
                    costs=p.commission_per_trade)
            pending.remove(pend)

        # 2. exits — evaluate on data ≤ d
        for sym, pos in list(positions.items()):
            i = pos_of[sym].get(d)
            if i is None:
                continue
            bars = bars_by_symbol[sym]
            cur = bars[i]
            if pos.entry_date == d.isoformat():
                continue            # can't exit same bar as entry
            exit_px, reason = _exit(bars[: i + 1], cur, pos, p)
            if exit_px is not None:
                fill = _slip(exit_px, "sell", p.slippage_bps)
                proceeds = pos.qty * fill - p.commission_per_trade
                cash += proceeds
                total_costs += p.commission_per_trade
                turnover += proceeds
                pos.exit_date, pos.exit_px = d.isoformat(), fill
                pos.reason = reason
                pos.pnl = pos.qty * fill - pos.qty * pos.entry_px \
                    - pos.costs
                pos.costs += p.commission_per_trade
                closed.append(pos)
                del positions[sym]

        # 3. signals at close(d) → pending for open(d+1)
        equity = cash + sum(
            t.qty * (idx[s].get(d).c if idx[s].get(d) else t.entry_px)
            for s, t in positions.items())
        curve.append({"t": d.isoformat(), "equity": round(equity, 2)})
        if len(positions) + len(pending) >= p.max_positions:
            continue
        for sym, bars in bars_by_symbol.items():
            if sym in positions or any(x["symbol"] == sym
                                       for x in pending):
                continue
            i = pos_of[sym].get(d)
            if i is None:
                continue
            hist = bars[: i + 1]
            atr = ti.atr_sma(hist)
            if atr is None or not _signal(hist, p):
                continue
            close = hist[-1].c
            stop = close - p.atr_stop_mult * atr
            qty = int(equity * p.risk_per_trade_pct /
                      max(close - stop, 1e-9))
            if qty > 0:
                pending.append({"symbol": sym, "qty": qty,
                                "stop": stop,
                                "signal_date": d.isoformat()})
            if len(positions) + len(pending) >= p.max_positions:
                break

    # end: close remaining at last close
    if all_dates:
        last = all_dates[-1]
        for sym, pos in list(positions.items()):
            b = idx[sym].get(last)
            if b is None:
                continue
            fill = _slip(b.c, "sell", p.slippage_bps)
            pos.exit_date, pos.exit_px = last.isoformat(), fill
            pos.reason = "end_of_backtest"
            pos.pnl = pos.qty * (fill - pos.entry_px) - pos.costs
            closed.append(pos)

    return {
        "engine": ENGINE_VERSION,
        "params": {k: getattr(p, k) for k in vars(p)},
        "equity_curve": curve,
        "trades": [
            {"symbol": t.symbol, "entry": t.entry_date,
             "entry_px": round(t.entry_px, 2),
             "exit": t.exit_date,
             "exit_px": round(t.exit_px, 2) if t.exit_px else None,
             "qty": t.qty, "stop": round(t.stop_px, 2),
             "pnl": round(t.pnl, 2) if t.pnl is not None else None,
             "reason": t.reason, "costs": t.costs}
            for t in closed],
        "metrics": compute_metrics(curve, closed, p.initial_capital,
                                   turnover, total_costs),
        "skipped_for_liquidity": liq_skips,
        "bias_controls": {
            "signal_timing": "close(t) signal → open(t+1) fill",
            "point_in_time": "indicators on bars[:i] only",
            "lookahead": "none",
            "survivorship": "LIMITATION: current universe members only — "
                            "delisted names absent",
        },
    }


def compute_metrics(curve, trades, capital, turnover=0.0,
                    total_costs=0.0, benchmark=None) -> dict:
    if not curve:
        return {"note": "no equity curve"}
    eq = [c["equity"] for c in curve]
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    n = len(rets)
    years = max(n / 252, 1 / 252)
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if eq[-1] > 0 else -1
    mean = sum(rets) / n if n else 0
    sd = sqrt(sum((r - mean) ** 2 for r in rets) / max(1, n - 1))
    sharpe = mean / sd * sqrt(252) if sd else 0
    downside = [r for r in rets if r < 0]
    dsd = sqrt(sum(r ** 2 for r in downside) / max(1, len(downside)))
    sortino = mean / dsd * sqrt(252) if dsd else 0

    peak, max_dd, dd_dur = eq[0], 0.0, 0
    peak_i = 0
    for i, e in enumerate(eq):
        if e > peak:
            peak, peak_i = e, i
        dd = e / peak - 1
        max_dd = min(max_dd, dd)
        dd_dur = max(dd_dur, i - peak_i)

    wins = [t for t in trades if (t.pnl or 0) > 0]
    losses = [t for t in trades if (t.pnl or 0) < 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))

    out = {
        "cagr": round(cagr, 4), "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(max_dd, 4),
        "max_dd_duration_days": dd_dur,
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "profit_factor": round(gp / gl, 2) if gl else None,
        "expectancy": round(sum(t.pnl or 0 for t in trades) /
                            len(trades), 2) if trades else None,
        "trades": len(trades),
        "turnover": round(turnover, 0),
        "total_costs": round(total_costs, 2),
        "final_equity": round(eq[-1], 2),
        "conventions": {"annualization": "sqrt252 daily",
                        "returns": "equity-curve daily"},
    }
    if benchmark:
        out["benchmark_return"] = round(
            benchmark[-1].c / benchmark[0].c - 1, 4)
    return out


# ── Part C: validation stack ──

def walk_forward(bars_by_symbol: dict, params: BTParams,
                 windows: int = 4) -> dict:
    """Fixed params evaluated on consecutive windows — stability
    check, no refitting."""
    all_dates = sorted({b.t for bars in bars_by_symbol.values()
                        for b in bars})
    n = len(all_dates)
    if n < windows * 40:
        return {"error": f"insufficient bars ({n}) for {windows} "
                         "walk-forward windows", "windows": []}
    step = n // windows
    results = []
    for w in range(windows):
        lo, hi = w * step, (w + 1) * step if w < windows - 1 else n
        subset = {s: [b for b in bars
                      if all_dates[lo] <= b.t <= all_dates[hi - 1]]
                  for s, bars in bars_by_symbol.items()}
        r = run_backtest(subset, params)
        m = r["metrics"]
        results.append({"window": w,
                        "from": all_dates[lo].isoformat()[:10],
                        "to": all_dates[hi - 1].isoformat()[:10],
                        "cagr": m.get("cagr"), "sharpe": m.get("sharpe"),
                        "max_dd": m.get("max_drawdown"),
                        "trades": m.get("trades")})
    sharpes = [x["sharpe"] for x in results if x["sharpe"] is not None]
    mean_s = sum(sharpes) / len(sharpes) if sharpes else None
    return {
        "windows": results,
        "stability": {
            "mean_sharpe": round(mean_s, 3) if mean_s else None,
            "all_windows_positive": all(s > 0 for s in sharpes)
            if sharpes else None},
        "version": ENGINE_VERSION,
    }


def monte_carlo(trades: list[dict], runs: int = 500,
                capital: float = 100_000, seed: int = 42) -> dict:
    """Bootstrap resample of trade P&Ls → outcome distribution.
    Robustness check, not a forecast."""
    pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    if not pnls:
        return {"error": "no closed trades"}
    rng = random.Random(seed)
    finals = []
    for _ in range(runs):
        eq = capital
        for _ in range(len(pnls)):
            eq += rng.choice(pnls)
        finals.append(eq)
    finals.sort()
    return {
        "runs": runs, "seed": seed,
        "median_final": round(finals[len(finals) // 2], 0),
        "p5": round(finals[int(0.05 * runs)], 0),
        "p95": round(finals[int(0.95 * runs)], 0),
        "prob_loss": round(sum(1 for f in finals if f < capital)
                           / runs, 3),
        "note": "bootstrap of realized trade P&Ls — robustness, "
                "not a forecast"}


def stress_overlay(curve: list[dict], shock: float) -> dict:
    """Inject a one-day shock at each possible point; report worst
    resulting max drawdown."""
    if not curve:
        return {}
    eq = [c["equity"] for c in curve]
    worst = 0.0
    for i in range(len(eq)):
        shocked = eq[:i] + [e * (1 + shock) for e in eq[i:]]
        peak = shocked[0]
        dd = 0.0
        for e in shocked:
            peak = max(peak, e)
            dd = min(dd, e / peak - 1)
        worst = min(worst, dd)
    return {"shock": shock, "worst_max_dd": round(worst, 4)}
