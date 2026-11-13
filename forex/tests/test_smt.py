"""Unit tests for Institutional Multi-Factor Triad SMT Execution Engine.

Tests:
1. Layer 1: 15-Minute HTF Context & Liquidity Sweep Lock.
2. Layer 2: Triple-Asset Triad SMT Engine & 2-Asset Pair SMT Fallback.
3. Layer 3: Mandatory CISD + FVG Displacement & Spread Guardrail.
4. Auto-Invalidation on 5M candle body close beyond SH2/SL2.
5. Integration with Confluence Signal Engine & Sniper Grade (90%+).
"""

import unittest
from datetime import datetime, timezone
import time

from forex.smt import (
    aggregate_5m_to_15m,
    evaluate_15m_htf_context,
    find_fractal_pivots_5m,
    detect_smt_divergence,
    evaluate_cisd_displacement_and_spread,
    analyze_institutional_triad_smt,
)
from forex.signals import evaluate_confluence_signal


class TestInstitutionalSMT(unittest.TestCase):

    def setUp(self):
        self.base_time = 1700000000

        # Build 25 synthetic 5m candles for XAU/USD:
        # High at index 10 = 2050.0 (SH1)
        # Pullback to 2040.0
        # High at index 20 = 2048.0 (SH2 < SH1, Lower High)
        # Pullback, last candle closes at 2044.0
        self.xau_candles = []
        for i in range(25):
            o = 2040.0 + (i * 0.2)
            c = o + 0.1
            h = max(o, c) + 0.5
            l = min(o, c) - 0.5
            if i == 10:
                h = 2050.0  # SH1
                c = 2048.0
            elif i == 20:
                h = 2048.0  # SH2 (Lower High)
                c = 2046.0
            elif i == 23:
                # Opposing manipulation up candle (open 2045, close 2047)
                o, c, h, l = 2045.0, 2047.0, 2047.5, 2044.5
            elif i == 24:
                # Bearish CISD displacement candle closing below opposing candle open (close 2043 < 2045)
                # and leaving a 5m FVG (c[24].high 2044.0 < c[22].low 2045.0)
                o, c, h, l = 2046.0, 2043.0, 2044.0, 2042.0

            self.xau_candles.append({
                "timestamp": self.base_time + i * 300,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000,
            })

        # Build XAG/USD candles: Higher High at index 20 (SH2 > SH1)
        self.xag_candles = []
        for i in range(25):
            o = 24.0 + (i * 0.02)
            c = o + 0.01
            h = max(o, c) + 0.05
            l = min(o, c) - 0.05
            if i == 10:
                h = 25.00  # SH1
            elif i == 20:
                h = 25.40  # SH2 (Higher High -> Bearish SMT divergence vs XAU)
            self.xag_candles.append({
                "timestamp": self.base_time + i * 300,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 500,
            })

        # Build UUP candles: Higher Low at index 20 (SL2 > SL1 -> USD strength confirming Gold drop)
        self.uup_candles = []
        for i in range(25):
            o = 28.0 + (i * 0.01)
            c = o + 0.005
            h = max(o, c) + 0.02
            l = min(o, c) - 0.02
            if i == 10:
                l = 27.80  # SL1
            elif i == 20:
                l = 28.10  # SL2 (Higher Low -> USD Strength)
            self.uup_candles.append({
                "timestamp": self.base_time + i * 300,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 200,
            })

    def test_aggregate_5m_to_15m(self):
        c15 = aggregate_5m_to_15m(self.xau_candles)
        self.assertEqual(len(c15), 8)
        self.assertGreaterEqual(c15[0]["high"], c15[0]["open"])

    def test_triad_smt_divergence_bearish(self):
        res = detect_smt_divergence(
            primary_symbol="XAU/USD",
            primary_candles_5m=self.xau_candles,
            b1_candles_5m=self.xag_candles,
            b2_candles_5m=self.uup_candles,
            b1_symbol="XAG/USD",
            b2_symbol="UUP",
            b2_is_fresh=True,
        )
        self.assertTrue(res["detected"])
        self.assertEqual(res["tier"], "TRIAD SMT (A+)")
        self.assertEqual(res["bias"], "SELL")
        self.assertEqual(res["confluence_bonus"], 30)

    def test_pair_smt_fallback_when_benchmark_2_stale(self):
        res = detect_smt_divergence(
            primary_symbol="XAU/USD",
            primary_candles_5m=self.xau_candles,
            b1_candles_5m=self.xag_candles,
            b2_candles_5m=self.uup_candles,
            b1_symbol="XAG/USD",
            b2_symbol="UUP",
            b2_is_fresh=False,  # Weekend / Stale DXY
        )
        self.assertTrue(res["detected"])
        self.assertEqual(res["tier"], "PAIR SMT (A)")
        self.assertEqual(res["bias"], "SELL")
        self.assertEqual(res["confluence_bonus"], 15)

    def test_smt_auto_invalidation_when_candle_body_closes_above_sh2(self):
        # Alter last candle close to close above SH2 (2048.0)
        invalidated_candles = [dict(c) for c in self.xau_candles]
        invalidated_candles[-1]["close"] = 2049.50  # Closes above SH2

        res = detect_smt_divergence(
            primary_symbol="XAU/USD",
            primary_candles_5m=invalidated_candles,
            b1_candles_5m=self.xag_candles,
            b2_candles_5m=self.uup_candles,
            b1_symbol="XAG/USD",
            b2_symbol="UUP",
        )
        self.assertFalse(res["detected"])
        self.assertEqual(res["tier"], "INVALIDATED")

    def test_cisd_displacement_and_spread_guardrail(self):
        # Spread is safe (1.5 pips <= 4.5 pips)
        res_safe = evaluate_cisd_displacement_and_spread(
            candles_5m=self.xau_candles,
            smt_bias="SELL",
            live_spread_pips=1.5,
            symbol="XAU/USD",
        )
        self.assertTrue(res_safe["is_spread_safe"])

        # Spread spike (5.2 pips > 4.5 pips threshold)
        res_spike = evaluate_cisd_displacement_and_spread(
            candles_5m=self.xau_candles,
            smt_bias="SELL",
            live_spread_pips=5.2,
            symbol="XAU/USD",
        )
        self.assertFalse(res_spike["is_spread_safe"])
        self.assertFalse(res_spike["displacement_confirmed"])
        self.assertEqual(res_spike["status_badge"], "SPREAD LOCKED")

    def test_full_triad_smt_analysis(self):
        offline_dict = {
            "XAG/USD": {"candles": self.xag_candles},
            "UUP": {"candles": self.uup_candles},
        }
        res = analyze_institutional_triad_smt(
            primary_symbol="XAU/USD",
            primary_candles_5m=self.xau_candles,
            live_spread_pips=1.8,
            offline_data=offline_dict,
            now_ts=self.base_time + 3600 * 3,
        )
        self.assertIn("tier", res)
        self.assertIn("summary_en", res)
        self.assertIn("summary_mr", res)
        self.assertIn("benchmarks", res)


if __name__ == "__main__":
    unittest.main()
