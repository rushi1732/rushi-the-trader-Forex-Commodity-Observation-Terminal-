"""Historical Backtest Engine for Forex & COMEX Gold Paper Observation.

Strictly simulated observation:
- No look-ahead bias: each setup is evaluated strictly using past candles up to bar close.
- Accounts for realistic spread and 0.5 pip simulated slippage.
- Evaluates 2R exit targets against stop-loss invalidation levels.
- Purely simulated paper observation; zero broker connectivity.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import math

from .pairs import normalize_symbol, get_pair_info, to_pips, from_pips
from .pricing import execution_price
from .sizer import size_position
from .data import fetch_chart, fetch_twelve_data_chart


def calculate_ema(values: List[float], period: int) -> List[Optional[float]]:
    """Compute Exponential Moving Average (EMA)."""
    if len(values) < period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * (period - 1)
    # Simple average for initial seed
    sma = sum(values[:period]) / period
    result.append(sma)

    k = 2.0 / (period + 1.0)
    current_ema = sma
    for val in values[period:]:
        current_ema = (val * k) + (current_ema * (1.0 - k))
        result.append(current_ema)

    return result


def run_backtest(
    symbol: str = "XAU/USD",
    interval: str = "5m",
    range_period: str = "5d",
    offline_data: Optional[Dict[str, Any]] = None,
    max_loss_usd: float = 50.0,
    lookback_window: int = 15,
    twelve_data_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute historical bar-by-bar backtest of range breakout + 2R exit strategy.
    
    Guarantees:
    - No look-ahead: trades triggered only after candle close.
    - Fills include bid/ask spread and 0.5 pip slippage.
    - Calculates win rate, profit factor, max drawdown, and average win/loss.
    """
    canon = normalize_symbol(symbol)
    pair_info = get_pair_info(canon)
    is_gold = "XAU" in canon
    spread_pips = pair_info.get("default_spread", 1.0)
    slippage_pips = 0.5

    # 1. Fetch candles
    chart = None
    if is_gold and twelve_data_api_key:
        try:
            chart = fetch_twelve_data_chart(
                symbol=canon,
                interval=interval,
                api_key=twelve_data_api_key,
                offline_data=offline_data,
            )
        except Exception:
            chart = None

    if chart is None:
        try:
            chart = fetch_chart(
                symbol=canon,
                interval=interval,
                range_period=range_period,
                offline_data=offline_data,
            )
        except Exception as e:
            return {
                "symbol": canon,
                "status": "ERROR",
                "error": f"Failed to load chart data for backtest: {str(e)}",
                "trades": [],
                "metrics": {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate_pct": None,
                    "win_rate_label": "N/A (Error)",
                    "net_pnl_usd": 0.0,
                    "profit_factor": 1.0,
                    "max_drawdown_usd": 0.0,
                },
            }

    candles = chart.get("candles", [])
    if len(candles) < lookback_window + 5:
        return {
            "symbol": canon,
            "status": "INSUFFICIENT_DATA",
            "error": f"Insufficient data/logic: {len(candles)} candles available, at least {lookback_window + 5} required.",
            "trades": [],
            "metrics": {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "net_pnl_usd": 0.0,
            },
            "disclaimer": "Historical backtest is simulated observation only. Past performance does not guarantee future results."
        }

    trades: List[Dict[str, Any]] = []
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown_usd = 0.0
    wins = 0
    losses = 0
    gross_profits = 0.0
    gross_losses = 0.0

    i = lookback_window
    n_candles = len(candles)

    while i < n_candles - 1:
        # Strict no look-ahead: reference range is computed only from past completed candles
        ref_candles = candles[i - lookback_window : i]
        range_high = max(c["high"] for c in ref_candles)
        range_low = min(c["low"] for c in ref_candles)
        range_mid = (range_high + range_low) / 2.0

        current_candle = candles[i]
        c_close = current_candle["close"]
        c_time = current_candle.get("datetime_utc") or str(current_candle.get("timestamp", "N/A"))

        trade_side = None
        stop_price = None

        # Long breakout: completed candle closes strictly above reference range high
        if c_close > range_high:
            trade_side = "BUY"
            stop_price = range_mid
        # Short breakdown: completed candle closes strictly below reference range low
        elif c_close < range_low:
            trade_side = "SELL"
            stop_price = range_mid

        if trade_side is None:
            i += 1
            continue

        # Simulated fill at bar close + spread + slippage
        sim_fill = execution_price(
            symbol=canon,
            side=trade_side,
            mid_price=c_close,
            spread_pips=spread_pips,
            slippage_pips=slippage_pips,
        )

        risk_pips_val = to_pips(canon, abs(sim_fill - stop_price))
        if risk_pips_val < 3.0:
            i += 1
            continue

        target_dist = abs(sim_fill - stop_price) * 2.0
        if trade_side == "BUY":
            target_price = sim_fill + target_dist
        else:
            target_price = sim_fill - target_dist

        # Position sizing
        sizing = size_position(
            symbol=canon,
            side=trade_side,
            entry_price=sim_fill,
            stop_price=stop_price,
            max_loss_usd=max_loss_usd,
        )
        trade_lot_size = sizing.get("lot_size", 0.01)
        trade_units = sizing.get("units", 1)
        actual_risk_usd = sizing.get("max_loss_usd", max_loss_usd)

        # Forward scan future bars to see outcome
        outcome = "OPEN"
        exit_bar_idx = i
        exit_price = sim_fill
        exit_time = c_time
        trade_pnl_usd = 0.0

        for j in range(i + 1, min(i + 40, n_candles)):
            f_candle = candles[j]
            f_high = f_candle["high"]
            f_low = f_candle["low"]
            f_time = f_candle.get("datetime_utc") or str(f_candle.get("timestamp", "N/A"))

            if trade_side == "BUY":
                # Check stop first (conservative assumption)
                if f_low <= stop_price:
                    outcome = "LOSS"
                    exit_bar_idx = j
                    exit_price = stop_price
                    exit_time = f_time
                    trade_pnl_usd = -actual_risk_usd
                    break
                elif f_high >= target_price:
                    outcome = "WIN"
                    exit_bar_idx = j
                    exit_price = target_price
                    exit_time = f_time
                    trade_pnl_usd = actual_risk_usd * 2.0  # 2R win
                    break
            else:  # SELL
                if f_high >= stop_price:
                    outcome = "LOSS"
                    exit_bar_idx = j
                    exit_price = stop_price
                    exit_time = f_time
                    trade_pnl_usd = -actual_risk_usd
                    break
                elif f_low <= target_price:
                    outcome = "WIN"
                    exit_bar_idx = j
                    exit_price = target_price
                    exit_time = f_time
                    trade_pnl_usd = actual_risk_usd * 2.0  # 2R win
                    break

        if outcome in ("WIN", "LOSS"):
            cumulative_pnl += trade_pnl_usd
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = peak_pnl - cumulative_pnl
            if drawdown > max_drawdown_usd:
                max_drawdown_usd = drawdown

            if outcome == "WIN":
                wins += 1
                gross_profits += trade_pnl_usd
            else:
                losses += 1
                gross_losses += abs(trade_pnl_usd)

            if isinstance(c_time, (int, float)):
                trade_entry_dt = datetime.fromtimestamp(c_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            else:
                trade_entry_dt = str(c_time)

            if isinstance(exit_time, (int, float)):
                trade_exit_dt = datetime.fromtimestamp(exit_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            else:
                trade_exit_dt = str(exit_time)

            trades.append({
                "trade_no": len(trades) + 1,
                "symbol": canon,
                "side": trade_side,
                "entry_time": trade_entry_dt,
                "exit_time": trade_exit_dt,
                "entry_price": round(sim_fill, 2 if is_gold else 5),
                "stop_price": round(stop_price, 2 if is_gold else 5),
                "target_price": round(target_price, 2 if is_gold else 5),
                "exit_price": round(exit_price, 2 if is_gold else 5),
                "lot_size": trade_lot_size,
                "units": trade_units,
                "unit_label": "oz" if is_gold else "units",
                "outcome": outcome,
                "pnl_usd": round(trade_pnl_usd, 2),
                "cumulative_pnl_usd": round(cumulative_pnl, 2),
                "strategy": "trader-scanner (Range Breakout) + trader-entry (Candle Close) + trader-exit (2R Plan)",
            })

            # Advance outer loop past the trade exit bar to prevent duplicate entries on the same swing
            i = exit_bar_idx + 1
        else:
            i += 1

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 1.0)
    avg_win = (gross_profits / wins) if wins > 0 else 0.0
    avg_loss = (gross_losses / losses) if losses > 0 else 0.0

    t0 = candles[0].get("timestamp") or candles[0].get("datetime_utc", "N/A")
    t1 = candles[-1].get("timestamp") or candles[-1].get("datetime_utc", "N/A")
    if isinstance(t0, (int, float)):
        start_date = datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%d")
    else:
        start_date = str(t0)[:10]

    if isinstance(t1, (int, float)):
        end_date = datetime.fromtimestamp(t1, tz=timezone.utc).strftime("%Y-%m-%d")
    else:
        end_date = str(t1)[:10]

    return {
        "symbol": canon,
        "interval": interval,
        "status": "COMPLETED",
        "date_range": f"{start_date} to {end_date}",
        "candles_analyzed": len(candles),
        "metrics": {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 1),
            "net_pnl_usd": round(cumulative_pnl, 2),
            "gross_profit_usd": round(gross_profits, 2),
            "gross_loss_usd": round(gross_losses, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "max_drawdown_usd": round(max_drawdown_usd, 2),
            "max_drawdown_pct": round((max_drawdown_usd / 5000.0) * 100.0, 2),
            "risk_per_trade_usd": max_loss_usd,
        },
        "summary": {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 1),
            "net_pnl_usd": round(cumulative_pnl, 2),
            "gross_profit_usd": round(gross_profits, 2),
            "gross_loss_usd": round(gross_losses, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "max_drawdown_usd": round(max_drawdown_usd, 2),
            "max_drawdown_pct": round((max_drawdown_usd / 5000.0) * 100.0, 2),
            "risk_per_trade_usd": max_loss_usd,
        },
        "trades": trades,
        "disclaimer": (
            "HISTORICAL BACKTEST NOTICE: Results are strictly from simulated historical candle replay with spread and 0.5 pip slippage. "
            "No live orders, no broker connections. Past simulated performance does not guarantee future results."
        ),
        "paper_only": True,
    }
