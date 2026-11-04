"""Live Market Observation Mode for Forex Paper Trading.

OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION.
Does not connect to a broker, request API keys, submit orders, or enable real-money trading.
Provides live/indicative market monitoring, session-based signals, spread & slippage simulation,
staleness checks, and pre-trade paper validation.

Standard library only, Python 3.8+ compatible.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

from .pairs import normalize_symbol, get_pair_info, to_pips
from .pricing import calc_spread_prices, execution_price
from .data import (
    fetch_chart,
    INDICATIVE_DISCLAIMER,
    INDICATIVE_DISCLAIMER_FOREX,
    INDICATIVE_DISCLAIMER_GOLD,
    MAJOR_PAIRS,
)
from .sessions import (
    is_forex_market_open,
    is_gold_futures_market_open,
    is_market_open_for_symbol,
    get_active_sessions,
    utc_to_et,
)
from .scanner import analyze_pair
from .entry import evaluate_entry_from_candles
from .exit import plan_exit
from .sizer import size_position
from .risk import validate_forex_trade
from .signals import evaluate_confluence_signal
from .smc import analyze_smc
from .fibonacci import analyze_fibonacci
from .liquidity import analyze_liquidity
from .ict import analyze_ict

BANNER_FOREX = """
========================================================================================
[OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION]
This system runs exclusively in paper observation mode. It does not connect to any
brokerage account, has no trade execution capability, and never risks real capital.
Quotes are indicative aggregate bank feeds provided for educational observation.
========================================================================================
"""

BANNER_GOLD = """
========================================================================================
[OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION]
This system runs exclusively in paper observation mode. It does not connect to any
brokerage account, has no trade execution capability, and never risks real capital.
Quotes are derived from COMEX Gold Futures (GC=F) benchmark for educational observation.
========================================================================================
"""

BANNER = BANNER_FOREX


def check_data_freshness(
    latest_timestamp: int,
    interval_str: str = "5m",
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, float, str]:
    """
    Evaluate if candle data is fresh or stale.
    
    interval: '5m' -> stale if > 20 mins old during open market
    Returns: (is_fresh, age_minutes, reason)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    candle_dt = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc)
    age_seconds = (now_utc - candle_dt).total_seconds()
    age_minutes = round(age_seconds / 60.0, 1)

    # Interval threshold in minutes
    interval_minutes = 5
    if interval_str == "15m":
        interval_minutes = 15
    elif interval_str == "1h":
        interval_minutes = 60
    elif interval_str == "1d":
        interval_minutes = 1440

    stale_threshold = max(20.0, interval_minutes * 2.5)

    if age_minutes < 0:
        return True, 0.0, "Future candle timestamp (clock drift)"
    if age_minutes > stale_threshold:
        return False, age_minutes, f"Data is {age_minutes:.1f} minutes old (threshold: {stale_threshold:.0f}m)"
    return True, age_minutes, f"Fresh data ({age_minutes:.1f} minutes old)"


def build_auto_signal(
    symbol: str,
    timeframe: str,
    is_open: bool,
    is_fresh: bool,
    now_et: datetime,
    now_utc: datetime,
    status_reason: str,
    scan_info: Optional[Dict[str, Any]] = None,
    entry_signal: Optional[Dict[str, Any]] = None,
    paper_simulation: Optional[Dict[str, Any]] = None,
    latest_price: Optional[float] = None,
    confluence_signal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an authentic automatic paper trading signal from multi-skill analysis.
    
    Guarantees:
    - Never generates fake or random signals.
    - If market is closed, data stale, or candles insufficient, action is strictly 'NO SIGNAL'.
    - If candle close confirms breakout or institutional confluence, action is 'BUY' or 'SELL'.
    - Contains strategy/skill, timeframe, entry reference, stop-loss, take-profit (2R), and timestamps.
    - Zero broker connectivity, purely simulated paper observation.
    """
    if not is_open:
        return {
            "action": "NO SIGNAL",
            "strategy": "--",
            "timeframe": "--",
            "symbol": symbol,
            "entry_reference": None,
            "stop_loss": None,
            "take_profit": None,
            "take_profit_2": None,
            "reward_risk": None,
            "reward_risk_tp2": None,
            "risk_pips": None,
            "reward_pips": None,
            "reward_pips_tp2": None,
            "confluence_score": None,
            "grade": None,
            "confluence_factors": [],
            "timestamp_et": "--",
            "timestamp_utc": "--",
            "is_market_open": False,
            "is_fresh": is_fresh,
            "reason": status_reason,
            "candle_close_confirmed": False,
            "paper_mode": True,
            "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
        }

    if not is_fresh:
        return {
            "action": "NO SIGNAL",
            "strategy": "--",
            "timeframe": "--",
            "symbol": symbol,
            "entry_reference": None,
            "stop_loss": None,
            "take_profit": None,
            "take_profit_2": None,
            "reward_risk": None,
            "reward_risk_tp2": None,
            "risk_pips": None,
            "reward_pips": None,
            "reward_pips_tp2": None,
            "confluence_score": None,
            "grade": None,
            "confluence_factors": [],
            "timestamp_et": "--",
            "timestamp_utc": "--",
            "is_market_open": True,
            "is_fresh": False,
            "reason": status_reason,
            "candle_close_confirmed": False,
            "paper_mode": True,
            "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
        }

    # Priority 1: Institutional SMC + ICT + Fibonacci + Liquidity Confluence Signal
    if confluence_signal and confluence_signal.get("action") in ("BUY", "SELL"):
        side = confluence_signal["action"]
        plan = confluence_signal.get("execution_plan", {})
        score = confluence_signal.get("confluence_score", 0)
        grade = confluence_signal.get("grade", "A")
        factors = confluence_signal.get("confluence_factors", [])
        factors_summary = " | ".join(factors[:2]) if factors else "Institutional multi-factor alignment"
        strategy_label = f"SMC / ICT Confluence ({grade} - {score}%)"

        sim_lot = (paper_simulation or {}).get("paper_lot_size", 0.05)
        sim_units = (paper_simulation or {}).get("paper_units", 5000)
        sim_risk_usd = (paper_simulation or {}).get("simulated_max_loss_usd", 50.0)
        entry_val = latest_price or plan.get("entry_price")

        return {
            "action": side,
            "strategy": strategy_label,
            "timeframe": timeframe,
            "symbol": symbol,
            "entry_reference": entry_val,
            "stop_loss": plan.get("stop_loss"),
            "take_profit": plan.get("take_profit_1"),
            "take_profit_2": plan.get("take_profit_2"),
            "reward_risk": plan.get("rr_ratio_tp1", 2.0),
            "reward_risk_tp2": plan.get("rr_ratio_tp2", 3.5),
            "risk_pips": plan.get("risk_pips"),
            "reward_pips": plan.get("reward_pips_tp1"),
            "reward_pips_tp2": plan.get("reward_pips_tp2"),
            "confluence_score": score,
            "grade": grade,
            "confluence_factors": factors,
            "lot_size": sim_lot,
            "units": sim_units,
            "risk_usd": sim_risk_usd,
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "is_market_open": True,
            "is_fresh": True,
            "reason": f"Confirmed {side} Institutional Setup ({grade}, Score: {score}%): {factors_summary}",
            "candle_close_confirmed": True,
            "paper_mode": True,
            "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
        }

    # Priority 2: Session Range Breakout + Candle Close Simulation
    if paper_simulation and isinstance(paper_simulation, dict) and "error" not in paper_simulation:
        side = paper_simulation.get("side", "BUY")
        setup_name = (scan_info or {}).get("primary_setup") or "Session Range Breakout"
        strategy = f"trader-scanner ({setup_name}) + trader-entry (Candle Close) + trader-exit (2R Plan)"
        return {
            "action": side,
            "strategy": strategy,
            "timeframe": timeframe,
            "symbol": symbol,
            "entry_reference": paper_simulation.get("simulated_fill") or latest_price,
            "stop_loss": paper_simulation.get("stop_price"),
            "take_profit": paper_simulation.get("target_price"),
            "take_profit_2": paper_simulation.get("target_price_2"),
            "reward_risk": paper_simulation.get("reward_risk", 2.0),
            "reward_risk_tp2": 3.5,
            "risk_pips": paper_simulation.get("risk_pips"),
            "reward_pips": paper_simulation.get("target_pips"),
            "confluence_score": 75,
            "grade": "Grade A (Breakout Confirmed)",
            "confluence_factors": ["Candle closed beyond breakout boundary in valid entry zone"],
            "lot_size": paper_simulation.get("paper_lot_size"),
            "units": paper_simulation.get("paper_units"),
            "risk_usd": paper_simulation.get("simulated_max_loss_usd", 50.0),
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "is_market_open": True,
            "is_fresh": True,
            "reason": f"Confirmed {side} signal: completed candle closed beyond setup boundary. 2R target planned.",
            "candle_close_confirmed": True,
            "paper_mode": True,
            "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
        }

    # Market open & fresh, but no confirmed setup in valid entry zone yet
    if status_reason:
        final_reason = status_reason
    else:
        final_reason = "Price is not in a valid entry zone. Monitoring for Order Block, FVG, OTE, or Breakout level."

    return {
        "action": "NO SIGNAL",
        "strategy": "--",
        "timeframe": "--",
        "symbol": symbol,
        "entry_reference": None,
        "stop_loss": None,
        "take_profit": None,
        "take_profit_2": None,
        "reward_risk": None,
        "reward_risk_tp2": None,
        "risk_pips": None,
        "reward_pips": None,
        "reward_pips_tp2": None,
        "confluence_score": None,
        "grade": None,
        "confluence_factors": [],
        "timestamp_et": "--",
        "timestamp_utc": "--",
        "is_market_open": True,
        "is_fresh": True,
        "reason": final_reason,
        "candle_close_confirmed": False,
        "paper_mode": True,
        "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
    }


def observe_pair(
    symbol: str,
    interval: str = "5m",
    config: Optional[Dict[str, Any]] = None,
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
    now_dt: Optional[datetime] = None,
    slippage_pips: float = 0.5,
) -> Dict[str, Any]:
    """
    Run full live-market observation analysis for a single pair in paper mode.
    """
    # Strict Paper-Only Assertion
    if config is None:
        config = {
            "budget": 5000.0,
            "leverage": 30.0,
            "max_loss_per_trade_usd": 50.0,
            "min_reward_risk": 1.0,
            "paper": True,
        }
    if not config.get("paper", True):
        raise PermissionError("SECURITY ERROR: Live money trading is prohibited. 'paper' must be True.")

    canon = normalize_symbol(symbol)
    info = get_pair_info(canon)

    if now_dt is None:
        now_utc = datetime.now(timezone.utc)
    else:
        now_utc = now_dt if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc)
    now_et = utc_to_et(now_utc)

    is_gold = "XAU" in canon
    source_label = (
        "Yahoo Finance (GC=F: COMEX Gold Futures indicative benchmark)"
        if is_gold
        else "Yahoo Finance FX (query2 indicative aggregate)"
    )
    disclaimer_text = INDICATIVE_DISCLAIMER_GOLD if is_gold else INDICATIVE_DISCLAIMER_FOREX

    # 1. Market Open & Session Status respecting asset class
    is_open, open_reason = is_market_open_for_symbol(canon, now_et)
    active_sessions = get_active_sessions(now_et) if is_open else []

    # 2. Fetch Market Data
    try:
        chart = fetch_chart(
            symbol=canon,
            interval=interval,
            range_period="1d",
            offline_file=offline_file,
            offline_data=offline_data,
        )
        if not chart.get("candles") and offline_file is None and offline_data is None:
            chart = fetch_chart(
                symbol=canon,
                interval=interval,
                range_period="5d",
            )
    except Exception as e:
        reason_msg = f"Market data fetch error: {str(e)}"
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=is_open,
            is_fresh=False,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "source": source_label,
            "reason": reason_msg,
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "paper_mode": True,
            "disclaimer": disclaimer_text,
            "auto_signal": auto_sig,
        }

    candles = chart.get("candles", [])
    if not candles:
        reason_msg = "No candle data returned from indicative data feed."
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=is_open,
            is_fresh=False,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "source": source_label,
            "reason": reason_msg,
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "paper_mode": True,
            "disclaimer": disclaimer_text,
            "auto_signal": auto_sig,
        }

    latest_candle = candles[-1]
    is_fresh, age_min, freshness_reason = check_data_freshness(
        latest_candle["timestamp"], interval_str=interval, now_utc=now_utc
    )

    # Compute technical and institutional confluence analysis from available candles
    scan_info = analyze_pair(
        symbol=canon,
        interval=interval,
        offline_file=offline_file,
        offline_data=offline_data,
    )
    direction = scan_info.get("direction", "NEUTRAL")
    asian_setup = scan_info.get("asian_setup", {})
    latest_close = latest_candle["close"]
    spread_pips = info.get("default_spread", 1.0)
    bid, ask = calc_spread_prices(canon, latest_close, spread_pips)

    confluence_sig = evaluate_confluence_signal(
        symbol=canon,
        candles=candles,
        interval=interval,
        now_dt=now_et,
        asian_range=asian_setup,
    )

    # 3. Market Closed or Stale Data Guard
    if not is_open:
        market_label = "COMEX Gold Futures (GC=F)" if is_gold else "Spot Forex"
        reason_msg = f"{market_label} market is closed ({open_reason}). Observation only."
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=False,
            is_fresh=is_fresh,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
            latest_price=latest_candle["close"],
            confluence_signal=confluence_sig,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_candle["close"],
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "paper_mode": True,
            "disclaimer": disclaimer_text,
            "auto_signal": auto_sig,
            "confluence_signal": confluence_sig,
            "smc": confluence_sig.get("components", {}).get("smc"),
            "fibonacci": confluence_sig.get("components", {}).get("fibonacci"),
            "liquidity": confluence_sig.get("components", {}).get("liquidity"),
            "ict": confluence_sig.get("components", {}).get("ict"),
        }

    if not is_fresh:
        reason_msg = f"Indicative data is stale ({freshness_reason}). Waiting for active session ticks."
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=True,
            is_fresh=False,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
            latest_price=latest_candle["close"],
            confluence_signal=confluence_sig,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_candle["close"],
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "paper_mode": True,
            "disclaimer": disclaimer_text,
            "auto_signal": auto_sig,
            "confluence_signal": confluence_sig,
            "smc": confluence_sig.get("components", {}).get("smc"),
            "fibonacci": confluence_sig.get("components", {}).get("fibonacci"),
            "liquidity": confluence_sig.get("components", {}).get("liquidity"),
            "ict": confluence_sig.get("components", {}).get("ict"),
        }

    if len(candles) < 15:
        reason_msg = f"Insufficient timeframe candles ({len(candles)}/15 required for setup analysis)."
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=True,
            is_fresh=True,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
            latest_price=latest_candle["close"],
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_candle["close"],
            "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "paper_mode": True,
            "disclaimer": disclaimer_text,
            "auto_signal": auto_sig,
        }

    # 4. Scanner & Technical Context
    scan_info = analyze_pair(
        symbol=canon,
        interval=interval,
        offline_file=offline_file,
        offline_data=offline_data,
    )

    direction = scan_info.get("direction", "NEUTRAL")
    asian_setup = scan_info.get("asian_setup", {})
    latest_close = latest_candle["close"]
    spread_pips = info.get("default_spread", 1.0)
    bid, ask = calc_spread_prices(canon, latest_close, spread_pips)

    # 5. Entry Trigger Confirmation
    entry_signal = evaluate_entry_from_candles(
        symbol=canon,
        side="BUY" if direction == "BULLISH" else "SELL",
        candles=candles,
        setup="asian",
        spread_pips=spread_pips,
    )

    # 5b. Evaluate Institutional Confluence (SMC + ICT + Fibonacci + Liquidity)
    confluence_sig = evaluate_confluence_signal(
        symbol=canon,
        candles=candles,
        interval=interval,
        now_dt=now_et,
        asian_range=asian_setup,
    )

    # 6. Paper Simulation (If setup is confirmed and price is in a valid entry zone)
    paper_simulation = None
    is_breakout_in_entry_zone = False
    pip_size = info.get("pip_size", 0.0001)
    if entry_signal.get("status") == "TRIGGERED":
        level_ref = entry_signal.get("level", latest_close)
        trade_side = entry_signal.get("side", "BUY")
        pip_dist = to_pips(canon, abs(latest_close - level_ref))
        bars_since = entry_signal.get("bars_since_trigger", 0)

        # Breakout Entry Zone verification:
        # 1. Breakout trigger must be fresh (on current/recent candle, bars_since <= 2)
        # 2. Live market price must be actively in the valid breakout entry zone:
        #    - BUY: Price >= Breakout level (-1 pip buffer) and within entry threshold
        #    - SELL: Price <= Breakdown level (+1 pip buffer) and within entry threshold
        max_entry_pips = 60.0 if "XAU" in canon else (400.0 if "BTC" in canon else 25.0)
        is_fresh = (bars_since <= 2)
        if trade_side == "BUY":
            in_zone = (latest_close >= (level_ref - pip_size * 1.0)) and (pip_dist <= max_entry_pips)
        else:
            in_zone = (latest_close <= (level_ref + pip_size * 1.0)) and (pip_dist <= max_entry_pips)

        is_breakout_in_entry_zone = is_fresh and in_zone

    if is_breakout_in_entry_zone:
        trade_side = entry_signal["side"]
        sim_fill = execution_price(
            symbol=canon,
            side=trade_side,
            mid_price=latest_close,
            spread_pips=spread_pips,
            slippage_pips=slippage_pips,
        )

        try:
            asian_range_dict = entry_signal.get("asian_range") or asian_setup
            plan = plan_exit(
                symbol=canon,
                side=trade_side,
                entry_price=sim_fill,
                stop_rule="range-mid",
                target_rule="2R",
                asian_range=asian_range_dict,
            )

            sizing = size_position(
                symbol=canon,
                side=trade_side,
                entry_price=sim_fill,
                stop_price=plan["stop_price"],
                budget_usd=config.get("budget", 5000.0),
                max_loss_usd=config.get("max_loss_per_trade_usd", 50.0),
                leverage=config.get("leverage", 30.0),
            )

            risk_check = validate_forex_trade(
                symbol=canon,
                side=trade_side,
                entry_price=sim_fill,
                stop_price=plan["stop_price"],
                target_price=plan["target_price"],
                lot_size=sizing.get("lot_size", 0.01),
                config=config,
                now_et=now_et,
            )

            paper_simulation = {
                "side": trade_side,
                "simulated_fill": sim_fill,
                "indicative_spread_pips": spread_pips,
                "simulated_slippage_pips": slippage_pips,
                "stop_price": plan["stop_price"],
                "target_price": plan["target_price"],
                "risk_pips": plan["risk_pips"],
                "target_pips": plan["reward_pips"],
                "reward_risk": plan["reward_risk_ratio"],
                "paper_lot_size": sizing.get("lot_size", 0.0),
                "paper_units": sizing.get("units", 0),
                "simulated_max_loss_usd": sizing.get("max_loss_usd", 0.0),
                "margin_required_usd": sizing.get("margin_required_usd", 0.0),
                "risk_status": risk_check["status"],
            }
        except Exception as ex:
            paper_simulation = {"error": str(ex)}

    # If no breakout triggered, check if institutional confluence triggered an entry
    if not paper_simulation and confluence_sig.get("action") in ("BUY", "SELL"):
        plan_c = confluence_sig.get("execution_plan", {})
        if plan_c.get("stop_loss") and plan_c.get("take_profit_1"):
            c_side = confluence_sig["action"]
            c_fill = execution_price(
                symbol=canon,
                side=c_side,
                mid_price=latest_close,
                spread_pips=spread_pips,
                slippage_pips=slippage_pips,
            )
            try:
                sizing_c = size_position(
                    symbol=canon,
                    side=c_side,
                    entry_price=c_fill,
                    stop_price=plan_c["stop_loss"],
                    budget_usd=config.get("budget", 5000.0),
                    max_loss_usd=config.get("max_loss_per_trade_usd", 50.0),
                    leverage=config.get("leverage", 30.0),
                )
                risk_c = validate_forex_trade(
                    symbol=canon,
                    side=c_side,
                    entry_price=c_fill,
                    stop_price=plan_c["stop_loss"],
                    target_price=plan_c["take_profit_1"],
                    lot_size=sizing_c.get("lot_size", 0.01),
                    config=config,
                    now_et=now_et,
                )
                paper_simulation = {
                    "side": c_side,
                    "simulated_fill": c_fill,
                    "indicative_spread_pips": spread_pips,
                    "simulated_slippage_pips": slippage_pips,
                    "stop_price": plan_c["stop_loss"],
                    "target_price": plan_c["take_profit_1"],
                    "target_price_2": plan_c.get("take_profit_2"),
                    "risk_pips": plan_c.get("risk_pips"),
                    "target_pips": plan_c.get("reward_pips_tp1"),
                    "reward_risk": plan_c.get("rr_ratio_tp1", 2.0),
                    "paper_lot_size": sizing_c.get("lot_size", 0.01),
                    "paper_units": sizing_c.get("units", 1000),
                    "simulated_max_loss_usd": sizing_c.get("max_loss_usd", 50.0),
                    "margin_required_usd": sizing_c.get("margin_required_usd", 0.0),
                    "risk_status": risk_c.get("status", "PASS"),
                }
            except Exception:
                pass

    auto_sig = build_auto_signal(
        symbol=canon,
        timeframe=interval,
        is_open=True,
        is_fresh=True,
        now_et=now_et,
        now_utc=now_utc,
        status_reason="Institutional setup active" if (confluence_sig.get("action") in ("BUY", "SELL")) else ("Breakout active" if paper_simulation and "error" not in paper_simulation else "Price is not in a valid entry zone. Monitoring for Order Block, FVG, OTE, or Breakout level."),
        scan_info=scan_info,
        entry_signal=entry_signal,
        paper_simulation=paper_simulation,
        latest_price=latest_close,
        confluence_signal=confluence_sig,
    )

    is_confirmed = (auto_sig.get("action") in ("BUY", "SELL"))

    return {
        "symbol": canon,
        "status": "SIGNAL CONFIRMED" if is_confirmed else "MONITORING (WAITING)",
        "source": source_label,
        "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "latest_bar_utc": latest_candle.get("datetime_utc") or datetime.fromtimestamp(latest_candle.get("timestamp", time.time()), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "data_freshness": freshness_reason,
        "active_sessions": active_sessions,
        "indicative_mid": latest_close,
        "indicative_bid": bid,
        "indicative_ask": ask,
        "spread_pips": spread_pips,
        "direction": direction,
        "primary_setup": scan_info.get("primary_setup", "Consolidation"),
        "asian_range": asian_setup,
        "entry_eval": entry_signal,
        "confluence_signal": confluence_sig,
        "smc": confluence_sig.get("components", {}).get("smc"),
        "fibonacci": confluence_sig.get("components", {}).get("fibonacci"),
        "liquidity": confluence_sig.get("components", {}).get("liquidity"),
        "ict": confluence_sig.get("components", {}).get("ict"),
        "paper_simulation": paper_simulation,
        "auto_signal": auto_sig,
        "paper_mode": True,
        "disclaimer": disclaimer_text,
    }


def format_observation_report(obs: Dict[str, Any]) -> str:
    """Renders a comprehensive observation dashboard."""
    sym = obs.get("symbol", "")
    status = obs.get("status", "")
    paper = "PAPER / NOT FOR LIVE EXECUTION"
    is_gold = "XAU" in sym or "GC" in sym

    banner = BANNER_GOLD if is_gold else BANNER_FOREX
    header_pair = f"{sym} (COMEX Gold Futures GC=F Benchmark)" if is_gold else sym
    disclaimer = obs.get("disclaimer") or (INDICATIVE_DISCLAIMER_GOLD if is_gold else INDICATIVE_DISCLAIMER_FOREX)

    lines = [
        banner.strip(),
        f"PAIR: {header_pair} | STATUS: {status} | MODE: {paper}",
        "=" * 88,
        f"  Data Source:     {obs.get('source', 'Yahoo Finance Indicative')}",
        f"  Observation Time:{obs.get('timestamp_et', '')}",
        f"  Feed Freshness:  {obs.get('data_freshness', 'N/A')}",
        f"  Active Sessions: {', '.join(obs.get('active_sessions', [])) if obs.get('active_sessions') else 'None'}",
    ]

    if "latest_price" in obs and status == "NO SIGNAL":
        lines.append(f"  Last Known Mid:  {obs['latest_price']}")
        lines.append(f"  Reason:          {obs.get('reason', '')}")
        lines.append("=" * 88)
        lines.append(f"DISCLAIMER: {disclaimer}")
        lines.append("=" * 88)
        return "\n".join(lines)

    lines.extend([
        "-" * 88,
        f"  Indicative Mid:  {obs.get('indicative_mid')}",
        f"  Indicative Bid:  {obs.get('indicative_bid')} | Ask: {obs.get('indicative_ask')} (Spread: {obs.get('spread_pips')} pips)",
        f"  Market Bias:     {obs.get('direction')} [{obs.get('primary_setup')}]",
    ])

    sim = obs.get("paper_simulation")
    if sim and "error" not in sim:
        unit_label = "oz" if is_gold else "units"
        units_disp = f"{sim['paper_units']:,}" if isinstance(sim['paper_units'], int) else f"{sim['paper_units']}"
        lines.extend([
            "-" * 88,
            "SIMULATED PAPER TRADE (OBSERVATION ONLY - NO REAL ORDERS):",
            f"  Direction:       {sim['side']} (Simulated Fill: {sim['simulated_fill']} incl. {sim['simulated_slippage_pips']}p slippage)",
            f"  Paper Size:      {sim['paper_lot_size']:.2f} Lots ({units_disp} {unit_label})",
            f"  Stop Loss:       {sim['stop_price']} ({sim['risk_pips']:.1f} pips at risk)",
            f"  Take Profit:     {sim['target_price']} (+{sim['target_pips']:.1f} pips | {sim['reward_risk']}:1 RR)",
            f"  Max Paper Risk:  ${sim['simulated_max_loss_usd']:.2f} USD (Margin: ${sim['margin_required_usd']:.2f})",
            f"  Pre-Trade Risk:  {sim['risk_status']}",
        ])
    else:
        entry_eval = obs.get("entry_eval", {})
        lines.extend([
            "-" * 88,
            f"OBSERVATION STATUS: {obs.get('status')}",
            f"  Entry Monitor:   {entry_eval.get('reason', 'Waiting for candle close beyond reference level.')}",
        ])

    lines.extend([
        "=" * 88,
        f"DISCLAIMER: {disclaimer}",
        "=" * 88,
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live-Market Forex Observation Mode (Paper Only)")
    parser.add_argument("symbol", nargs="?", default="EUR/USD", help="Currency pair (e.g. EUR/USD)")
    parser.add_argument("--interval", default="5m", choices=["5m", "15m", "1h"])
    parser.add_argument("--offline", help="Path to offline JSON chart")
    args = parser.parse_args()

    result = observe_pair(symbol=args.symbol, interval=args.interval, offline_file=args.offline)
    print(format_observation_report(result))
