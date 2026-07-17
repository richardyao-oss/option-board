from __future__ import annotations

import json
import unittest

import dashboard_analysis as analysis


def unusual(
    symbol: str,
    option_type: str,
    strike: float,
    direction: str,
    volume: int,
    turnover: float,
    event_time: str = "07-10 10:15",
    expiry: str = "2026-07-31",
) -> dict[str, str]:
    return {
        "snapshot_date": "2026-07-10",
        "underlying": symbol,
        "option_type": option_type,
        "strike": str(strike),
        "expiry": expiry,
        "volume": str(volume),
        "turnover": str(turnover),
        "direction": direction,
        "event_time": event_time,
    }


class IntentStructureTests(unittest.TestCase):
    def test_duplicate_split_prints_fold_into_bear_call_spread(self) -> None:
        rows = [
            unusual("US.MARA", "CALL", 12.5, "SELL", 100, 40_000, "07-10 10:15:01"),
            unusual("US.MARA", "CALL", 12.5, "SELL", 100, 40_000, "07-10 10:15:01"),
            unusual("US.MARA", "CALL", 12.5, "SELL", 50, 20_000, "07-10 10:15:40"),
            unusual("US.MARA", "CALL", 13.5, "BUY", 150, 15_000, "07-10 10:15:52"),
        ]

        structures, residual, parent_orders = analysis.structure_and_residual_summary(
            rows, "2026-07-10", "US.MARA"
        )

        self.assertEqual(parent_orders, 2)
        self.assertEqual([item["type"] for item in structures], ["bear_call_spread"])
        self.assertEqual(structures[0]["confidence"], "suspected")
        self.assertEqual(residual["bullish_orders"], 0)
        self.assertEqual(residual["bearish_orders"], 0)

    def test_two_and_three_leg_structures(self) -> None:
        sofi = [
            unusual("US.SOFI", "CALL", 20, "BUY", 1_000, 500_000),
            unusual("US.SOFI", "CALL", 23, "SELL", 1_000, 250_000),
        ]
        iren = [
            unusual("US.IREN", "PUT", 30, "BUY", 50_000, 20_000_000),
            unusual("US.IREN", "CALL", 110, "SELL", 50_000, 10_000_000),
        ]
        complex_rows = sofi + [
            unusual("US.SOFI", "PUT", 18, "SELL", 1_000, 100_000)
        ]

        self.assertEqual(
            analysis.structure_and_residual_summary(sofi, "2026-07-10", "US.SOFI")[0][0]["type"],
            "bull_call_spread",
        )
        self.assertEqual(
            analysis.structure_and_residual_summary(iren, "2026-07-10", "US.IREN")[0][0]["type"],
            "bearish_risk_reversal",
        )
        self.assertEqual(
            analysis.structure_and_residual_summary(complex_rows, "2026-07-10", "US.SOFI")[0][0]["type"],
            "complex",
        )

    def test_opening_support_is_conservative(self) -> None:
        def contract(code: str, oi: int, volume: int = 300) -> dict[str, str]:
            return {
                "option_code": code,
                "option_type": "CALL",
                "strike": "110",
                "volume": str(volume),
                "turnover": "12000000",
                "open_interest": str(oi),
                "implied_volatility": "0.5",
            }

        unknown = analysis.compact_contract_evidence(
            [contract("US.TEST260731C110000", 0)], {}, "2026-07-10", 100
        )[0]
        unconfirmed = analysis.compact_contract_evidence(
            [contract("US.TEST260731C110000", 100)], {}, "2026-07-10", 100
        )[0]
        strong = analysis.compact_contract_evidence(
            [contract("US.TEST260731C110000", 100)],
            {"US.TEST260731C110000": {"open_interest": "50"}},
            "2026-07-10",
            100,
        )[0]
        weak = analysis.compact_contract_evidence(
            [contract("US.TEST260731C110000", 100)],
            {"US.TEST260731C110000": {"open_interest": "100"}},
            "2026-07-10",
            100,
        )[0]

        self.assertIsNone(unknown["v_oi"])
        self.assertEqual(unknown["opening_support"], "unknown")
        self.assertEqual(unconfirmed["opening_support"], "unconfirmed")
        self.assertEqual(strong["opening_support"], "strong")
        self.assertEqual(weak["opening_support"], "weak")

    def test_new_multi_leg_has_high_confidence_and_two_distinct_voi_metrics(self) -> None:
        rows = [
            {
                **unusual("US.MARA", "CALL", 12.5, "SELL", 100, 40_000, "2026-07-10 10:15:01"),
                "option_code": "US.MARA260731C00012500",
                "open_interest": "100",
                "contract_volume": "420",
                "vo_ratio": "4.2",
                "strategy_type": "MULTI_LEG",
            },
            {
                **unusual("US.MARA", "CALL", 12.5, "SELL", 50, 20_000, "2026-07-10 10:15:40"),
                "option_code": "US.MARA260731C00012500",
                "open_interest": "100",
                "contract_volume": "420",
                "vo_ratio": "4.2",
                "strategy_type": "MULTI_LEG",
            },
            {
                **unusual("US.MARA", "CALL", 13.5, "BUY", 150, 15_000, "2026-07-10 10:15:52"),
                "option_code": "US.MARA260731C00013500",
                "open_interest": "300",
                "contract_volume": "600",
                "vo_ratio": "2.0",
                "strategy_type": "MULTI_LEG",
            },
        ]

        structures, residual, _orders = analysis.structure_and_residual_summary(
            rows, "2026-07-10", "US.MARA"
        )
        evidence = analysis.compact_event_evidence(
            rows, "2026-07-10", "US.MARA", structures
        )

        self.assertEqual(structures[0]["type"], "bear_call_spread")
        self.assertEqual(structures[0]["confidence"], "high")
        self.assertEqual(residual["bullish_orders"] + residual["bearish_orders"], 0)
        self.assertEqual(evidence["max_event_v_oi"], 1.5)
        self.assertEqual(evidence["max_contract_vo_ratio"], 4.2)
        self.assertEqual(evidence["contract_heat"], "very_hot")
        self.assertLessEqual(len(evidence["event_contracts"]), 3)

    def test_single_leg_records_are_never_forced_into_a_structure(self) -> None:
        rows = []
        for strike, direction in ((20, "BUY"), (23, "SELL")):
            row = unusual("US.SOFI", "CALL", strike, direction, 1_000, 500_000)
            row["strategy_type"] = "SINGLE_LEG"
            rows.append(row)

        structures, residual, _orders = analysis.structure_and_residual_summary(
            rows, "2026-07-10", "US.SOFI"
        )

        self.assertEqual(structures, [])
        self.assertEqual(residual["bullish_orders"], 1)
        self.assertEqual(residual["bearish_orders"], 1)

    def test_neutral_event_provides_heat_without_direction(self) -> None:
        row = unusual("US.MDB", "CALL", 400, "NEUTRAL", 2_000, 2_000_000)
        row.update(
            {
                "option_code": "US.MDB260731C00400000",
                "open_interest": "500",
                "contract_volume": "2500",
                "vo_ratio": "5",
                "strategy_type": "SINGLE_LEG",
            }
        )

        structures, residual, parent_orders = analysis.structure_and_residual_summary(
            [row], "2026-07-10", "US.MDB"
        )
        evidence = analysis.compact_event_evidence(
            [row], "2026-07-10", "US.MDB", structures
        )

        self.assertEqual(parent_orders, 0)
        self.assertEqual(structures, [])
        self.assertEqual(residual["bullish_orders"] + residual["bearish_orders"], 0)
        self.assertEqual(evidence["contract_heat"], "very_hot")
        self.assertEqual(evidence["event_contracts"][0]["direction"], "NEUTRAL")


class IntentReplayTests(unittest.TestCase):
    def test_july_10_compact_replay(self) -> None:
        report = analysis.build_intent_report("2026-07-10", 15)
        candidates = {item["symbol"]: item for item in report["candidates"]}

        self.assertEqual(report["coverage"]["symbols"], 70)
        self.assertEqual(report["coverage"]["unusual_rows"], 514)
        self.assertEqual(len(report["groups"]), 7)
        self.assertLessEqual(len(candidates), 15)
        self.assertTrue(
            {"US.SOFI", "US.GOOGL", "US.TSLA", "US.IREN", "US.META", "US.NVDA", "US.CRCL"}
            <= candidates.keys()
        )

        expected_types = {
            "US.SOFI": "bull_call_spread",
            "US.GOOGL": "bull_call_spread",
            "US.TSLA": "bullish_risk_reversal",
            "US.IREN": "bearish_risk_reversal",
            "US.META": "bearish_risk_reversal",
            "US.NVDA": "bear_call_spread",
            "US.CRCL": "bearish_risk_reversal",
        }
        for symbol, structure_type in expected_types.items():
            item = candidates[symbol]
            self.assertIn(structure_type, {row["type"] for row in item["unusual"]["structures"]})
            self.assertLessEqual(len(item["unusual"]["structures"]), 5)
            self.assertLessEqual(len(item["contracts"]), 3)
            self.assertLessEqual(len(item["contradictions"]), 3)

        iren_types = {row["type"] for row in candidates["US.IREN"]["unusual"]["structures"]}
        self.assertTrue({"bearish_risk_reversal", "short_straddle"} <= iren_types)
        meta_biases = {row["bias"] for row in candidates["US.META"]["unusual"]["structures"]}
        self.assertTrue({"bullish", "bearish"} <= meta_biases)
        self.assertIn(
            "opposing_directional_structures", candidates["US.META"]["contradictions"]
        )

        compact_json = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("raw_text", compact_json)

    def test_july_8_mdb_uses_voi_when_direction_is_missing(self) -> None:
        report = analysis.build_intent_report("2026-07-08", 70)
        mdb = next(item for item in report["candidates"] if item["symbol"] == "US.MDB")

        self.assertEqual(mdb["unusual"]["rows"], 0)
        self.assertIn("no_explicit_unusual_direction", mdb["contradictions"])
        self.assertTrue(
            any(
                item["v_oi"] is not None
                and item["v_oi"] >= 1
                and item["opening_support"] in {"supportive", "strong"}
                for item in mdb["contracts"]
            )
        )
        self.assertIsNone(mdb["price"])


if __name__ == "__main__":
    unittest.main()
