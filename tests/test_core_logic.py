from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import daily_option_report as dor
import dashboard_renderer
import option_unusual_monitor as oum


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class CoreLogicTests(unittest.TestCase):
    def test_build_signals_includes_reversal_bonus(self) -> None:
        rows = [
            {
                "snapshot_date": "2026-06-01",
                "underlying": "US.TEST",
                "call_volume": "800",
                "put_volume": "200",
                "total_volume": "1000",
                "call_share": "0.8",
                "put_share": "0.2",
                "put_call_ratio": "0.25",
            },
            {
                "snapshot_date": "2026-06-02",
                "underlying": "US.TEST",
                "call_volume": "900",
                "put_volume": "300",
                "total_volume": "1200",
                "call_share": "0.75",
                "put_share": "0.25",
                "put_call_ratio": "0.33",
            },
            {
                "snapshot_date": "2026-06-03",
                "underlying": "US.TEST",
                "call_volume": "700",
                "put_volume": "300",
                "total_volume": "1000",
                "call_share": "0.7",
                "put_share": "0.3",
                "put_call_ratio": "0.43",
            },
            {
                "snapshot_date": "2026-06-04",
                "underlying": "US.TEST",
                "call_volume": "2500",
                "put_volume": "15000",
                "total_volume": "17500",
                "call_share": "0.1429",
                "put_share": "0.8571",
                "put_call_ratio": "6.0",
            },
        ]
        latest = [
            row for row in dor.build_signals(rows, min_total=10_000, min_history_days=3)
            if row["snapshot_date"] == "2026-06-04"
        ][0]

        self.assertEqual(latest["direction"], "PUT")
        self.assertEqual(latest["prior_direction"], "CALL")
        self.assertGreater(float(latest["reversal_bonus"]), 0)
        self.assertIn("方向反转", latest["reason"])

    def test_top_contract_rows_uses_turnover_top5_plus_volume_fill(self) -> None:
        amount_rows = [
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": f"US.TEST260619C{i:05d}",
                "volume": str(100 + i),
                "turnover": str(1000 - i),
            }
            for i in range(1, 8)
        ]
        volume_rows = [
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00001",
                "volume": "9999",
                "turnover": "10",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00008",
                "volume": "8000",
                "turnover": "20",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00009",
                "volume": "7000",
                "turnover": "30",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00010",
                "volume": "6000",
                "turnover": "40",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00011",
                "volume": "5000",
                "turnover": "50",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00012",
                "volume": "4000",
                "turnover": "60",
            },
        ]

        rows = dashboard_renderer.top_contract_rows(
            amount_rows,
            "2026-06-05",
            "US.TEST",
            volume_contract_rows=volume_rows,
        )
        codes = [row["option_code"] for row in rows]

        self.assertEqual(codes[:5], [f"US.TEST260619C{i:05d}" for i in range(1, 6)])
        self.assertEqual(codes[5:], [f"US.TEST260619C{i:05d}" for i in range(8, 13)])

    def test_matched_unusual_rows_filters_and_sorts(self) -> None:
        contracts = [
            {"option_code": "US.TEST260619C00200", "option_type": "CALL", "strike": "200"},
            {"option_code": "US.TEST260619C00210", "option_type": "CALL", "strike": "210"},
            {"option_code": "US.TEST260626P00180", "option_type": "PUT", "strike": "180"},
        ]
        unusual = [
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00200",
                "option_type": "CALL",
                "strike": "200",
                "expiry": "2026-06-19",
                "turnover": "300",
                "direction": "BUY",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00210",
                "option_type": "CALL",
                "strike": "210",
                "expiry": "2026-06-19",
                "turnover": "100",
                "direction": "SELL",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260626P00180",
                "option_type": "PUT",
                "strike": "180",
                "expiry": "2026-06-26",
                "turnover": "500",
                "direction": "BUY",
            },
            {
                "snapshot_date": "2026-06-05",
                "underlying": "US.TEST",
                "option_code": "US.TEST260619C00205",
                "option_type": "CALL",
                "strike": "205",
                "expiry": "2026-06-19",
                "turnover": "999",
                "direction": "BUY",
            },
        ]

        rows = dashboard_renderer.matched_unusual_rows(contracts, unusual, "2026-06-05", "US.TEST")
        self.assertEqual([row["strike"] for row in rows], ["210", "200", "180"])

    def test_option_unusual_parser_reports_failures(self) -> None:
        content = (
            "6.6 03:57，出现一笔买入看涨期权交易，成交量为3486张，"
            "未平仓数为668张，V/OI值为8.2，交易金额为1945188USD，"
            "合约行权价是307.5，到期日为2026/06/12\n"
            "6.6 04:00，出现一笔无法识别的期权文本"
        )
        rows, stats = oum.parse_unusual_content_with_stats(content, "2026-06-05", "US.AAPL")

        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["raw_records"], 2)
        self.assertEqual(stats["parsed_records"], 1)
        self.assertEqual(stats["excluded_neutral_records"], 0)
        self.assertEqual(stats["unparsed_records"], 1)
        self.assertEqual(rows[0]["direction"], "BUY")
        self.assertEqual(rows[0]["option_type"], "CALL")

    def test_option_unusual_parser_excludes_neutral_records(self) -> None:
        content = (
            "6.8 22:15，出现一笔中性看涨期权交易，成交量为3000张，"
            "未平仓数为53718张，V/OI值为0.5，交易金额为420000USD，"
            "合约行权价是30，到期日为2027/01/15\n"
            "6.8 22:16，出现一笔买入看涨期权交易，成交量为2000张，"
            "未平仓数为53718张，V/OI值为0.5，交易金额为280000USD，"
            "合约行权价是30，到期日为2027/01/15"
        )
        rows, stats = oum.parse_unusual_content_with_stats(content, "2026-06-08", "US.NOK")

        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["raw_records"], 2)
        self.assertEqual(stats["parsed_records"], 1)
        self.assertEqual(stats["excluded_neutral_records"], 1)
        self.assertEqual(stats["unparsed_records"], 0)
        self.assertEqual(rows[0]["direction"], "BUY")

    def test_replace_rows_for_date_symbols_preserves_other_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            columns = ["snapshot_date", "underlying", "value"]
            dor.write_csv(
                path,
                columns,
                [
                    {"snapshot_date": "2026-06-08", "underlying": "US.A", "value": "old-a"},
                    {"snapshot_date": "2026-06-08", "underlying": "US.B", "value": "old-b"},
                    {"snapshot_date": "2026-06-05", "underlying": "US.A", "value": "old-date"},
                ],
            )

            dor.replace_rows_for_date_symbols(
                path,
                columns,
                [{"snapshot_date": "2026-06-08", "underlying": "US.A", "value": "new-a"}],
                "2026-06-08",
                ["US.A"],
            )

            rows = read_csv(path)
            values = {(row["snapshot_date"], row["underlying"]): row["value"] for row in rows}
            self.assertEqual(values[("2026-06-08", "US.A")], "new-a")
            self.assertEqual(values[("2026-06-08", "US.B")], "old-b")
            self.assertEqual(values[("2026-06-05", "US.A")], "old-date")

    def test_render_existing_data_keeps_cards_and_unusual_section(self) -> None:
        agg_rows = read_csv(DATA / "option_screen_underlying_snapshot.csv")
        signal_rows = read_csv(DATA / "daily_option_signals.csv")
        contract_rows = read_csv(DATA / "option_screen_contract_snapshot.csv")
        volume_rows = read_csv(DATA / "option_screen_volume_contract_snapshot.csv")
        unusual_rows = read_csv(DATA / "option_unusual_snapshot.csv")
        status = {
            "snapshot_date": "2026-06-05",
            "trade_date": "2026-06-05",
            "snapshot_type": "complete",
        }
        current_symbols = sorted(
            {row["underlying"] for row in agg_rows if row.get("snapshot_date") == "2026-06-05"}
        )

        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "report.html"
            dashboard_renderer.render_html(
                html_path,
                agg_rows,
                signal_rows,
                contract_rows,
                "2026-06-05",
                dor.trailing_weekdays("2026-06-05", 7),
                volume_contract_rows=volume_rows,
                option_unusual_rows=unusual_rows,
                report_groups={"All": current_symbols},
                quote_map={},
                snapshot_status=status,
            )
            text = html_path.read_text(encoding="utf-8")

        self.assertGreaterEqual(text.count('class="scan-row"'), len(current_symbols))
        self.assertIn("unusual-matches", text)


if __name__ == "__main__":
    unittest.main()
