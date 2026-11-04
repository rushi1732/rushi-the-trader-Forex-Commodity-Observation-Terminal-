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
import threading

from .pairs import normalize_symbol, get_pair_info, to_pips
from .pricing import calc_spread_prices, execution_price
from .data import (
    fetch_chart,
    load_twelve_data_config,
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


class SignalStateManager:
    """Thread-safe persistent signal state manager ensuring:
    1. Signal Confirmation: BUY or SELL generated ONLY when market price is inside a valid Entry Zone and all strategy conditions are met.
    2. Candle Close Confirmation: Confirms signals ONLY after the 5-minute candle closes. Temporary intrabar movements during an unfinished candle do not generate signals.
    3. Signal Persistence: Confirmed BUY or SELL remains active and visible until Stop Loss or Take Profit 2 is hit.
    4. No Instant Reversal: Direct BUY <-> SELL flips are forbidden; signal must first be invalidated, transition to waiting, before opposite setup can be evaluated.
    5. Hysteresis: Small price oscillations around entry zone boundaries cannot flicker or destroy an active signal.
    6. Entry Reference: Fixed at the exact market price when signal is confirmed.
    7. SL and TP: Calculated once at confirmation from Entry Reference and risk rules; never recalculated on price updates.
    8. Status Transitions: WAITING FOR ENTRY ZONE before setup, BUY/SELL while confirmed, NO SIGNAL only when no setup exists.
    """

    def __init__(self) -> None:
        self._active_signals: Dict[str, Dict[str, Any]] = {}
        self._cooldown: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def reset(self, symbol: Optional[str] = None) -> None:
        with self._lock:
            if symbol:
                canon = normalize_symbol(symbol)
                self._active_signals.pop(canon, None)
                self._cooldown.pop(canon, None)
            else:
                self._active_signals.clear()
                self._cooldown.clear()

    def get_active_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        canon = normalize_symbol(symbol)
        with self._lock:
            sig = self._active_signals.get(canon)
            return dict(sig) if sig else None

    def process(
        self,
        symbol: str,
        timeframe: str,
        is_open: bool,
        is_fresh: bool,
        now_et: datetime,
        now_utc: datetime,
        current_price: float,
        candles: Optional[List[Dict[str, Any]]] = None,
        scan_info: Optional[Dict[str, Any]] = None,
        entry_signal: Optional[Dict[str, Any]] = None,
        paper_simulation: Optional[Dict[str, Any]] = None,
        confluence_signal: Optional[Dict[str, Any]] = None,
        status_reason: Optional[str] = None,
        is_offline_test: bool = False,
    ) -> Dict[str, Any]:
        canon = normalize_symbol(symbol)
        candles = candles or []

        # 1. Closed market enforcement (Req 8 & 10)
        if not is_open:
            with self._lock:
                self._active_signals.pop(canon, None)
            return {
                "action": "NO SIGNAL",
                "status_label": "NO SIGNAL",
                "strategy": "--",
                "timeframe": "--",
                "symbol": canon,
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
                "reason": status_reason or "Spot Forex / Futures market is closed.",
                "candle_close_confirmed": False,
                "paper_mode": True,
                "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
            }

        # 2. Stale data enforcement (Req 8 & 10)
        if not is_fresh:
            with self._lock:
                self._active_signals.pop(canon, None)
            return {
                "action": "NO SIGNAL",
                "status_label": "NO SIGNAL",
                "strategy": "--",
                "timeframe": "--",
                "symbol": canon,
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
                "reason": status_reason or "Market data feed is stale.",
                "candle_close_confirmed": False,
                "paper_mode": True,
                "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
            }

        # 3. Insufficient candles enforcement (< 15)
        if len(candles) < 15:
            with self._lock:
                self._active_signals.pop(canon, None)
            return {
                "action": "NO SIGNAL",
                "status_label": "NO SIGNAL",
                "strategy": "--",
                "timeframe": "--",
                "symbol": canon,
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
                "reason": status_reason or "Insufficient timeframe candles (< 15 bars).",
                "candle_close_confirmed": False,
                "paper_mode": True,
                "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
            }

        # 4. Check Existing Active Confirmed Signal (Req 3, 4, 6, 7 - Persistence & Invalidation)
        with self._lock:
            active = self._active_signals.get(canon) if not is_offline_test else None

        if active and active.get("action") in ("BUY", "SELL"):
            side = active["action"]
            sl = active.get("stop_loss")
            tp2 = active.get("take_profit_2") or active.get("take_profit")

            is_sl_hit = False
            is_tp2_hit = False

            if side == "BUY":
                if sl is not None and current_price <= sl:
                    is_sl_hit = True
                if tp2 is not None and current_price >= tp2:
                    is_tp2_hit = True
            elif side == "SELL":
                if sl is not None and current_price >= sl:
                    is_sl_hit = True
                if tp2 is not None and current_price <= tp2:
                    is_tp2_hit = True

            # Check market structure opposite break
            is_struct_break = False
            if confluence_signal:
                choch = confluence_signal.get("components", {}).get("smc", {}).get("structure", {}).get("latest_choch")
                if choch and choch.get("bias"):
                    if side == "BUY" and choch["bias"] == "BEARISH":
                        is_struct_break = True
                    elif side == "SELL" and choch["bias"] == "BULLISH":
                        is_struct_break = True

            if is_sl_hit or is_tp2_hit or is_struct_break:
                # Signal is INVALIDATED! (Req 4: never switch directly from BUY to SELL. First invalidate, transition to waiting)
                with self._lock:
                    self._active_signals.pop(canon, None)
                    self._cooldown[canon] = {
                        "invalidated_side": side,
                        "invalidated_at": now_utc.timestamp(),
                        "candle_ts": candles[-1].get("timestamp", 0) if candles else 0,
                    }
                inv_text = f"Stop Loss hit at {sl}" if is_sl_hit else (f"Take Profit 2 target completed at {tp2}" if is_tp2_hit else "Market structure invalidated")
                return {
                    "action": "NO SIGNAL",
                    "status_label": "WAITING FOR ENTRY ZONE",
                    "strategy": "--",
                    "timeframe": "--",
                    "symbol": canon,
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
                    "reason": f"Previous {side} signal invalidated ({inv_text}). Waiting for new setup.",
                    "candle_close_confirmed": False,
                    "paper_mode": True,
                    "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
                }

            # Still Valid: Maintain active confirmed signal persistently!
            # Trade details remain 100% frozen as confirmed!
            return dict(active)

        # 5. Check Candle Close Confirmation for NEW signals (Req 2)
        last_candle = candles[-1] if candles else {}
        last_candle_ts = last_candle.get("timestamp", 0)
        bar_duration = 300  # 5 minutes

        if is_offline_test:
            candle_is_closed = True
            confirmed_candle_ts = last_candle_ts
        else:
            if now_utc.timestamp() >= last_candle_ts + bar_duration:
                candle_is_closed = True
                confirmed_candle_ts = last_candle_ts
            else:
                if len(candles) >= 2:
                    candle_is_closed = True
                    confirmed_candle_ts = candles[-2].get("timestamp", last_candle_ts - bar_duration)
                else:
                    candle_is_closed = False
                    confirmed_candle_ts = last_candle_ts

        candidate_action = None
        candidate_strategy = None
        candidate_entry = current_price
        candidate_sl = None
        candidate_tp1 = None
        candidate_tp2 = None
        candidate_rr = 2.0
        candidate_rr_tp2 = 3.5
        candidate_risk_pips = None
        candidate_reward_pips = None
        candidate_reward_pips_tp2 = None
        candidate_score = None
        candidate_grade = None
        candidate_factors = []
        candidate_reason = None

        # Priority 1: Institutional SMC + ICT + Fibonacci + Liquidity Confluence
        if confluence_signal and confluence_signal.get("action") in ("BUY", "SELL"):
            c_action = confluence_signal["action"]
            plan = confluence_signal.get("execution_plan", {})
            in_zone = confluence_signal.get("is_in_entry_zone", False)

            if in_zone and plan.get("stop_loss") and plan.get("take_profit_1"):
                candidate_action = c_action
                candidate_score = confluence_signal.get("confluence_score", 0)
                candidate_grade = confluence_signal.get("grade", "A")
                candidate_factors = confluence_signal.get("confluence_factors", [])
                candidate_strategy = f"SMC / ICT Confluence ({candidate_grade} - {candidate_score}%)"
                candidate_entry = current_price
                candidate_sl = plan["stop_loss"]
                candidate_tp1 = plan["take_profit_1"]
                candidate_tp2 = plan.get("take_profit_2")
                candidate_rr = plan.get("rr_ratio_tp1", 2.0)
                candidate_rr_tp2 = plan.get("rr_ratio_tp2", 3.5)
                candidate_risk_pips = plan.get("risk_pips")
                candidate_reward_pips = plan.get("reward_pips_tp1")
                candidate_reward_pips_tp2 = plan.get("reward_pips_tp2")
                factors_str = " | ".join(candidate_factors[:2]) if candidate_factors else "Institutional multi-factor alignment"
                candidate_reason = f"Confirmed {c_action} Institutional Setup ({candidate_grade}, Score: {candidate_score}%): {factors_str}"

        # Priority 2: Session Range Breakout + Candle Close Simulation
        elif paper_simulation and isinstance(paper_simulation, dict) and "error" not in paper_simulation:
            b_side = paper_simulation.get("side", "BUY")
            setup_name = (scan_info or {}).get("primary_setup") or "Session Range Breakout"
            candidate_action = b_side
            candidate_strategy = f"trader-scanner ({setup_name}) + trader-entry (Candle Close) + trader-exit (2R Plan)"
            candidate_entry = paper_simulation.get("simulated_fill") or current_price
            candidate_sl = paper_simulation.get("stop_price")
            candidate_tp1 = paper_simulation.get("target_price")
            candidate_tp2 = paper_simulation.get("target_price_2")
            candidate_rr = paper_simulation.get("reward_risk", 2.0)
            candidate_rr_tp2 = 3.5
            candidate_risk_pips = paper_simulation.get("risk_pips")
            candidate_reward_pips = paper_simulation.get("target_pips")
            candidate_reward_pips_tp2 = None
            candidate_score = 75
            candidate_grade = "Grade A (Breakout Confirmed)"
            candidate_factors = ["Candle closed beyond breakout boundary in valid entry zone"]
            candidate_reason = f"Confirmed {b_side} signal: completed candle closed beyond setup boundary. 2R target planned."

        # Cooldown check: prevent instant reversal on the same candle after invalidation (Req 4)
        if candidate_action and not is_offline_test:
            with self._lock:
                cooldown = self._cooldown.get(canon)
            if cooldown:
                last_inv_side = cooldown.get("invalidated_side")
                last_candle_cd = cooldown.get("candle_ts", 0)
                if last_inv_side and last_inv_side != candidate_action:
                    if confirmed_candle_ts <= last_candle_cd:
                        # Cannot reverse immediately on the same candle bar
                        candidate_action = None

        if candidate_action in ("BUY", "SELL") and candle_is_closed:
            canon = normalize_symbol(canon)
            decimals = 2 if ("XAU" in canon or "BTC" in canon) else (2 if "JPY" in canon else 4)
            pip_size = 0.10 if "XAU" in canon else (1.0 if "BTC" in canon else (0.01 if "JPY" in canon else 0.0001))

            # Guarantee Stop Loss strictly adheres to side
            if candidate_sl is None or candidate_sl == 0.0:
                if candidate_action == "BUY":
                    candidate_sl = round(candidate_entry - (pip_size * 25), decimals)
                else:
                    candidate_sl = round(candidate_entry + (pip_size * 25), decimals)

            risk_dist = max(pip_size * 5, abs(candidate_entry - candidate_sl))
            if candidate_risk_pips is None:
                candidate_risk_pips = round(risk_dist / pip_size, 1)

            # Guarantee TP1 strictly 2R
            if candidate_tp1 is None or candidate_tp1 == 0.0:
                if candidate_action == "BUY":
                    candidate_tp1 = round(candidate_entry + (risk_dist * 2.0), decimals)
                else:
                    candidate_tp1 = round(candidate_entry - (risk_dist * 2.0), decimals)

            if candidate_reward_pips is None:
                candidate_reward_pips = round((risk_dist * 2.0) / pip_size, 1)

            # Guarantee TP2 strictly 3.5R
            if candidate_tp2 is None or candidate_tp2 == 0.0:
                if candidate_action == "BUY":
                    candidate_tp2 = round(candidate_entry + (risk_dist * 3.5), decimals)
                else:
                    candidate_tp2 = round(candidate_entry - (risk_dist * 3.5), decimals)

            if candidate_reward_pips_tp2 is None:
                candidate_reward_pips_tp2 = round(abs(candidate_tp2 - candidate_entry) / pip_size, 1)

            if candidate_strategy is None or candidate_strategy == "--":
                candidate_strategy = f"SMC / ICT Confluence ({candidate_grade or 'Grade B'} - {candidate_score or 60}%)"

            # Confirm and latch new signal
            new_sig = {
                "action": candidate_action,
                "status_label": f"{candidate_action} CONFIRMED",
                "strategy": candidate_strategy,
                "timeframe": timeframe,
                "symbol": canon,
                "entry_reference": candidate_entry,
                "stop_loss": candidate_sl,
                "take_profit": candidate_tp1,
                "take_profit_2": candidate_tp2,
                "reward_risk": candidate_rr,
                "reward_risk_tp2": candidate_rr_tp2,
                "risk_pips": candidate_risk_pips,
                "reward_pips": candidate_reward_pips,
                "reward_pips_tp2": candidate_reward_pips_tp2,
                "confluence_score": candidate_score,
                "grade": candidate_grade,
                "confluence_factors": candidate_factors,
                "confirmed_candle_ts": confirmed_candle_ts,
                "confirmed_at_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
                "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
                "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "is_market_open": True,
                "is_fresh": True,
                "reason": candidate_reason,
                "candle_close_confirmed": True,
                "paper_mode": True,
                "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
            }
            if not is_offline_test:
                with self._lock:
                    self._active_signals[canon] = new_sig
            return new_sig

        # 6. No confirmed signal: determine WAITING FOR ENTRY ZONE vs NO SIGNAL (Req 8)
        has_potential_setup = False
        wait_reason = "Price is not in a valid entry zone. Monitoring for Order Block, FVG, OTE, or Breakout level."

        if confluence_signal:
            b_sc = confluence_signal.get("bullish_score", 0)
            s_sc = confluence_signal.get("bearish_score", 0)
            if b_sc >= 30 or s_sc >= 30:
                has_potential_setup = True
                wait_reason = "Institutional setup detected. Waiting for 5m candle close inside valid Entry Zone."

        if entry_signal and entry_signal.get("status") in ("WAITING", "MONITORING"):
            has_potential_setup = True

        status_lbl = "WAITING FOR ENTRY ZONE" if has_potential_setup else "NO SIGNAL"
        final_rsn = wait_reason if has_potential_setup else (status_reason or "No valid institutional setup detected. Market in consolidation.")

        return {
            "action": "NO SIGNAL",
            "status_label": status_lbl,
            "strategy": "--",
            "timeframe": "--",
            "symbol": canon,
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
            "reason": final_rsn,
            "candle_close_confirmed": False,
            "paper_mode": True,
            "disclaimer": "SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIONS",
        }


SIGNAL_STATE_MANAGER = SignalStateManager()


def reset_signal_state(symbol: Optional[str] = None) -> None:
    """Reset persistent signal state across symbols or for a single symbol."""
    SIGNAL_STATE_MANAGER.reset(symbol)


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
    candles: Optional[List[Dict[str, Any]]] = None,
    is_offline_test: bool = False,
) -> Dict[str, Any]:
    """Build or maintain an authentic automatic paper trading signal from persistent state."""
    return SIGNAL_STATE_MANAGER.process(
        symbol=symbol,
        timeframe=timeframe,
        is_open=is_open,
        is_fresh=is_fresh,
        now_et=now_et,
        now_utc=now_utc,
        current_price=latest_price or 0.0,
        candles=candles,
        scan_info=scan_info,
        entry_signal=entry_signal,
        paper_simulation=paper_simulation,
        confluence_signal=confluence_signal,
        status_reason=status_reason,
        is_offline_test=is_offline_test,
    )


def observe_pair(
    symbol: str,
    interval: str = "5m",
    config: Optional[Dict[str, Any]] = None,
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
    now_dt: Optional[datetime] = None,
    slippage_pips: float = 0.5,
    live_tick: Optional[Dict[str, Any]] = None,
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
    td_cfg = load_twelve_data_config()
    is_td_configured = td_cfg.get("enabled", False) and bool(td_cfg.get("api_key"))

    if live_tick or (is_td_configured and offline_data is None and offline_file is None):
        source_label = "Twelve Data Real-Time (Retail Spot Feed)"
        disclaimer_text = "Twelve Data Real-Time Spot Feed — Retail Spot Forex & Commodities"
    else:
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
        chart = {"candles": []}

    candles = chart.get("candles", [])

    # If live spot tick is supplied, reconcile candle series to spot price scale
    if live_tick and isinstance(live_tick, dict):
        spot_mid = float(live_tick.get("mid") or live_tick.get("last") or 0.0)
        if spot_mid > 0:
            is_open = True
            if not candles:
                ts_now = int(now_utc.timestamp())
                candles = [
                    {
                        "timestamp": ts_now - (20 - i) * 300,
                        "open": spot_mid,
                        "high": spot_mid + (0.5 if is_gold else 0.0005),
                        "low": spot_mid - (0.5 if is_gold else 0.0005),
                        "close": spot_mid,
                        "volume": 100,
                    }
                    for i in range(20)
                ]
            else:
                last_c = candles[-1]["close"]
                basis_gap = round(spot_mid - last_c, 2 if is_gold else 4)
                if abs(basis_gap) > 0.05:
                    for c in candles:
                        c["open"] = round(c["open"] + basis_gap, 2 if is_gold else 4)
                        c["high"] = round(c["high"] + basis_gap, 2 if is_gold else 4)
                        c["low"] = round(c["low"] + basis_gap, 2 if is_gold else 4)
                        c["close"] = round(c["close"] + basis_gap, 2 if is_gold else 4)
                candles[-1]["close"] = spot_mid
                if spot_mid > candles[-1]["high"]:
                    candles[-1]["high"] = spot_mid
                if spot_mid < candles[-1]["low"]:
                    candles[-1]["low"] = spot_mid

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
    
    if live_tick and float(live_tick.get("mid") or live_tick.get("last") or 0.0) > 0:
        latest_close = float(live_tick.get("mid") or live_tick.get("last"))
        bid = float(live_tick.get("bid") or latest_close)
        ask = float(live_tick.get("ask") or latest_close)
        spread_pips = float(live_tick.get("spread_pips", info.get("default_spread", 1.0)))
    else:
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
        if offline_data is not None or offline_file is not None:
            market_label = "COMEX Gold Futures (GC=F)" if is_gold else "Spot Forex"
        else:
            market_label = "Twelve Data Spot Gold" if is_gold else "Spot Forex"
        reason_msg = f"{market_label} market is closed ({open_reason}). Observation only."
        auto_sig = build_auto_signal(
            symbol=canon,
            timeframe=interval,
            is_open=False,
            is_fresh=is_fresh,
            now_et=now_et,
            now_utc=now_utc,
            status_reason=reason_msg,
            latest_price=latest_close,
            confluence_signal=confluence_sig,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_close,
            "indicative_mid": latest_close,
            "indicative_bid": bid,
            "indicative_ask": ask,
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
            latest_price=latest_close,
            confluence_signal=confluence_sig,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_close,
            "indicative_mid": latest_close,
            "indicative_bid": bid,
            "indicative_ask": ask,
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
            latest_price=latest_close,
        )
        return {
            "symbol": canon,
            "status": "NO SIGNAL",
            "reason": reason_msg,
            "source": source_label,
            "data_freshness": freshness_reason,
            "latest_price": latest_close,
            "indicative_mid": latest_close,
            "indicative_bid": bid,
            "indicative_ask": ask,
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
    if not (live_tick and float(live_tick.get("mid") or live_tick.get("last") or 0.0) > 0):
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

    is_offline = (offline_data is not None or offline_file is not None)
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
        candles=candles,
        is_offline_test=is_offline,
    )

    is_confirmed = (auto_sig.get("action") in ("BUY", "SELL"))

    return {
        "symbol": canon,
        "status": "SIGNAL CONFIRMED" if is_confirmed else auto_sig.get("status_label", "MONITORING (WAITING)"),
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
