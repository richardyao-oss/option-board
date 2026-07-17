from __future__ import annotations

import csv
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import daily_option_report as dor
import dashboard_renderer
import git_sync_update as gsu
import option_unusual_monitor as oum
import option_screen_monitor as osm
import report_groups as rg


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class CoreLogicTests(unittest.TestCase):
    def test_theme_configuration_covers_exactly_70_symbols_in_requested_order(self) -> None:
        expected_order = [
            "风险指标",
            "超级平台",
            "AI硬件",
            "电力能源",
            "AI时代软件",
            "加密与金融",
            "中国科技",
        ]
        configured = rg.configured_theme_symbols()

        self.assertEqual(list(rg.THEME_REPORT_GROUPS), expected_order)
        self.assertEqual(len(configured), 70)
        self.assertEqual(len(set(configured)), 70)
        self.assertEqual(
            list(rg.build_theme_report_groups(list(reversed(configured)))),
            expected_order,
        )

    def test_theme_grouping_rejects_unconfirmed_symbols(self) -> None:
        with self.assertRaises(rg.UnmappedReportSymbolsError) as caught:
            rg.build_theme_report_groups(["US.AAPL", "US.NEW"])

        self.assertEqual(caught.exception.symbols, ["US.NEW"])
        self.assertIn("期权抓取和看板写入前中止", str(caught.exception))

    def test_theme_grouping_only_uses_requested_symbols(self) -> None:
        groups = rg.build_theme_report_groups([
            "US.NVDA",
            "US.MSFT",
            "US.RDDT",
            "US.AMD",
            "US.COIN",
            "US.OPEN",
        ])

        self.assertEqual(groups, {
            "超级平台": ["US.MSFT", "US.NVDA"],
            "AI硬件": ["US.AMD"],
            "AI时代软件": ["US.RDDT"],
            "加密与金融": ["US.COIN", "US.OPEN"],
        })

    def test_partial_refresh_preserves_full_dashboard_config_scope(self) -> None:
        args = Namespace(symbols=["US.NVDA"], merge_partial=True)
        scan_symbols, report_groups = dor.choose_watchlist(args)

        self.assertEqual(scan_symbols, ["US.NVDA"])
        self.assertEqual(report_groups, rg.THEME_REPORT_GROUPS)

    def test_build_report_groups_uses_independent_dashboard_config(self) -> None:
        groups = dor.build_report_groups(Namespace())

        self.assertEqual(groups, rg.THEME_REPORT_GROUPS)

    def test_full_refresh_scans_all_configured_symbols_without_futu_watchlist(self) -> None:
        scan_symbols, report_groups = dor.choose_watchlist(
            Namespace(symbols=None, scan_group_name=None)
        )

        self.assertEqual(set(scan_symbols), set(rg.configured_theme_symbols()))
        self.assertEqual(len(scan_symbols), 70)
        self.assertEqual(report_groups, rg.THEME_REPORT_GROUPS)

    def test_git_sync_full_update_does_not_pass_futu_watchlist_arguments(self) -> None:
        args = Namespace(
            deadline_bjt=None,
            mode="intraday",
            pages=1,
            page_count=200,
            volume_page_count=10,
            request_pause=3.8,
            symbols=None,
            merge_partial=False,
            snapshot_date=None,
            allow_market_hours_preopen=False,
            timeout=2400,
        )
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(gsu.subprocess, "run", return_value=completed) as run:
            gsu.run_report_update(args)

        command = run.call_args.args[0]
        self.assertNotIn("--watchlist-source", command)
        self.assertNotIn("--group-name", command)

    def test_collection_scope_uses_new_option_sources(self) -> None:
        scope = dor.collection_scope(
            Namespace(pages=1, page_count=200, volume_page_count=10, merge_partial=False),
            ["US.META"],
        )

        self.assertEqual(scope["option_unusual_source"], "get_option_event")
        self.assertEqual(scope["option_volume_source"], "get_option_underlying_overview")
        self.assertEqual(scope["aggregate_volume_basis"], "underlying_overview")
        self.assertNotIn("option_unusual_time_range_days", scope)

    def test_option_screen_default_pause_respects_rolling_limit(self) -> None:
        self.assertGreater(osm.DEFAULT_REQUEST_PAUSE * 10, 30)

    def test_overview_date_guards_cannot_be_forced_into_historical_mode(self) -> None:
        with mock.patch.object(dor, "current_us_trade_date", return_value=date(2026, 7, 17)):
            dor.ensure_overview_target_date("intraday", "2026-07-17")
            with self.assertRaisesRegex(RuntimeError, "no historical-date parameter"):
                dor.ensure_overview_target_date("intraday", "2026-07-16")
        with mock.patch.object(dor, "is_us_regular_session", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Refusing preopen collection"):
                dor.ensure_preopen_collection_window(allow_market_hours=True)

    def test_double_click_update_wrapper_keeps_error_visible(self) -> None:
        script = (ROOT / "git_sync_update.cmd").read_text(encoding="utf-8")

        self.assertIn("The previous dashboard has been preserved.", script)
        self.assertIn('type "%LOG%"', script)
        self.assertIn("pause", script)

    def test_git_validation_requires_new_sources_and_complete_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "reports").mkdir()
            target = "2026-07-17"
            dor.write_csv(
                root / "data/option_screen_underlying_snapshot.csv",
                osm.AGG_COLUMNS,
                [{
                    "snapshot_date": target,
                    "underlying": "US.META",
                    "call_volume": 10,
                    "put_volume": 5,
                    "total_volume": 15,
                    "call_share": 0.6667,
                    "put_share": 0.3333,
                    "put_call_ratio": 0.5,
                    "contracts_seen": 1,
                    "volume_basis": "underlying_overview",
                }],
            )
            dor.write_csv(
                root / "data/option_screen_contract_snapshot.csv",
                osm.CONTRACT_COLUMNS,
                [{"snapshot_date": target, "underlying": "US.META", "option_code": "US.META260717C00800"}],
            )
            dor.write_csv(
                root / "data/option_unusual_snapshot.csv",
                oum.UNUSUAL_COLUMNS,
                [{"snapshot_date": target, "underlying": "US.META", "option_code": "US.META260717C00800"}],
            )
            scope = {
                "option_unusual_source": "get_option_event",
                "option_volume_source": "get_option_underlying_overview",
                "option_event_rows_received": 1,
                "option_event_all_count": 1,
                "option_event_pages": 1,
                "option_overview_symbol_count": 1,
            }
            status_path = root / "data/option_screen_snapshot_status.json"
            status_path.write_text(json.dumps({
                "snapshot_date": target,
                "snapshot_type": "intraday",
                "collection_scope": scope,
            }), encoding="utf-8")
            (root / "reports/options_anomaly_report.html").write_text(target, encoding="utf-8")

            with mock.patch.object(gsu, "ROOT", root):
                self.assertEqual(gsu.validate_outputs("intraday", ["US.META"], True)[2], 1)
                scope["option_event_all_count"] = 2
                status_path.write_text(json.dumps({
                    "snapshot_date": target,
                    "snapshot_type": "intraday",
                    "collection_scope": scope,
                }), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "pagination is incomplete"):
                    gsu.validate_outputs("intraday", ["US.META"], True)

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

    def test_unusual_match_table_collapses_repeated_expiry_labels(self) -> None:
        rows = [
            {
                "expiry": "2026-06-19",
                "option_type": "CALL",
                "strike": "210",
                "volume": "1000",
                "turnover": "100000",
                "direction": "BUY",
            },
            {
                "expiry": "2026-06-19",
                "option_type": "CALL",
                "strike": "200",
                "volume": "900",
                "turnover": "90000",
                "direction": "SELL",
            },
            {
                "expiry": "2026-06-26",
                "option_type": "PUT",
                "strike": "180",
                "volume": "800",
                "turnover": "80000",
                "direction": "BUY",
            },
        ]

        table_html = dashboard_renderer.unusual_match_table(rows)

        self.assertEqual(table_html.count("2026-06-19"), 1)
        self.assertEqual(table_html.count("2026-06-26"), 1)
        self.assertIn("class='expiry-break'", table_html)

    def test_structured_option_events_deduplicate_filter_date_and_keep_neutral(self) -> None:
        target_ts = datetime.fromisoformat("2026-07-17T10:15:03-04:00").timestamp()
        base = {
            "option_code": "US.MDB260724C00400",
            "owner_code": "US.MDB",
            "fill_timestamp": target_ts,
            "ticker_type": "BUY",
            "price": 1.25,
            "volume": 500,
            "turnover": 62_500,
            "option_type": "CALL",
            "strike_price": 400,
            "strike_time": "2026-07-24",
            "total_open_interest": 100,
            "total_volume": 750,
            "vo_ratio": 7.5,
            "strategy_type": "SINGLE_LEG",
        }
        neutral = {
            **base,
            "option_code": "US.MDB260724P00350",
            "ticker_type": "NEUTRAL",
            "price": 2,
            "turnover": 100_000,
            "total_open_interest": None,
            "vo_ratio": None,
            "option_type": "PUT",
            "strike_price": 350,
        }
        wrong_date = {**base, "fill_timestamp": datetime.fromisoformat("2026-07-16T10:15:03-04:00").timestamp()}

        rows, stats = oum.normalize_event_records(
            [base, dict(base), neutral, wrong_date], "2026-07-17", ["US.MDB"]
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(stats["exact_duplicates_removed"], 1)
        self.assertEqual(stats["excluded_other_trade_date"], 1)
        self.assertEqual(stats["neutral_records_kept"], 1)
        self.assertEqual({row["direction"] for row in rows}, {"BUY", "NEUTRAL"})
        self.assertEqual(next(row for row in rows if row["direction"] == "BUY")["vo_ratio"], 7.5)
        self.assertEqual(next(row for row in rows if row["direction"] == "NEUTRAL")["open_interest"], "")

    def test_option_event_pagination_rejects_missing_rows_and_repeated_cursor(self) -> None:
        class Frame:
            def __init__(self, rows: list[dict[str, str]]) -> None:
                self.rows = rows

            def to_dict(self, orient: str) -> list[dict[str, str]]:
                self.assert_orient = orient
                return self.rows

        class Context:
            def __init__(self, payloads: list[dict[str, object]]) -> None:
                self.payloads = iter(payloads)

            def get_option_event(self, *_args: object, **_kwargs: object) -> tuple[int, dict[str, object]]:
                return 0, next(self.payloads)

        incomplete = Context([
            {"event_list": Frame([{"id": "one"}]), "all_count": 2, "next_page": ""}
        ])
        with self.assertRaisesRegex(RuntimeError, "pagination incomplete"):
            oum.collect_event_pages(incomplete, ["US.MDB"], "2026-07-17")

        repeated = Context([
            {"event_list": Frame([{"id": "one"}]), "all_count": 2, "next_page": "same"},
            {"event_list": Frame([{"id": "two"}]), "all_count": 2, "next_page": "same"},
        ])
        with self.assertRaisesRegex(RuntimeError, "repeated pagination cursor"):
            oum.collect_event_pages(repeated, ["US.MDB"], "2026-07-17")

    def test_underlying_overview_is_complete_and_sets_true_volume_basis(self) -> None:
        records = [
            {"code": "US.A", "call_volume": 600, "put_volume": 400},
            {"code": "US.B", "call_volume": 100, "put_volume": 300},
        ]
        contracts = [
            {"underlying": "US.A"},
            {"underlying": "US.A"},
            {"underlying": "US.B"},
        ]

        rows = osm.aggregate_overview_records(records, "2026-07-17", ["US.A", "US.B"], contracts)

        self.assertEqual(rows[0]["total_volume"], 1000)
        self.assertEqual(rows[0]["put_call_ratio"], 0.6667)
        self.assertEqual(rows[0]["contracts_seen"], 2)
        self.assertTrue(all(row["volume_basis"] == "underlying_overview" for row in rows))
        with self.assertRaisesRegex(RuntimeError, "coverage mismatch"):
            osm.aggregate_overview_records(records[:1], "2026-07-17", ["US.A", "US.B"], contracts)

    def test_signal_baseline_never_crosses_volume_basis(self) -> None:
        def row(day: str, basis: str, total: int) -> dict[str, str]:
            return {
                "snapshot_date": day,
                "underlying": "US.TEST",
                "call_volume": str(total * 3 // 4),
                "put_volume": str(total // 4),
                "total_volume": str(total),
                "call_share": "0.75",
                "put_share": "0.25",
                "put_call_ratio": "0.333",
                "volume_basis": basis,
            }

        rows = [
            row("2026-07-10", "", 100),
            row("2026-07-13", "", 100),
            row("2026-07-14", "", 100),
            row("2026-07-15", "underlying_overview", 100),
            row("2026-07-16", "underlying_overview", 100),
            row("2026-07-17", "underlying_overview", 100),
            row("2026-07-20", "underlying_overview", 200),
        ]

        signals = dor.build_signals(rows, min_total=1, min_history_days=3)
        first_new = next(item for item in signals if item["snapshot_date"] == "2026-07-15")
        fourth_new = next(item for item in signals if item["snapshot_date"] == "2026-07-20")

        self.assertEqual(first_new["history_days"], 0)
        self.assertEqual(first_new["total_x_base"], "")
        self.assertEqual(fourth_new["history_days"], 3)
        self.assertEqual(fourth_new["total_x_base"], 2.0)

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

    def test_stored_report_symbols_uses_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            dor.write_csv(
                data_dir / "option_screen_underlying_snapshot.csv",
                ["snapshot_date", "underlying"],
                [
                    {"snapshot_date": "2026-06-05", "underlying": "US.OLD"},
                    {"snapshot_date": "2026-06-08", "underlying": "US.AAPL"},
                    {"snapshot_date": "2026-06-08", "underlying": "US.NOK"},
                ],
            )

            self.assertEqual(dor.stored_report_symbols(data_dir), ["US.AAPL", "US.NOK"])

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

    def test_render_theme_sections_and_directory_cover_70_symbols_once(self) -> None:
        agg_rows = read_csv(DATA / "option_screen_underlying_snapshot.csv")
        signal_rows = read_csv(DATA / "daily_option_signals.csv")
        contract_rows = read_csv(DATA / "option_screen_contract_snapshot.csv")
        volume_rows = read_csv(DATA / "option_screen_volume_contract_snapshot.csv")
        unusual_rows = read_csv(DATA / "option_unusual_snapshot.csv")
        symbols = rg.configured_theme_symbols()
        groups = rg.build_theme_report_groups(symbols)
        status = {
            "snapshot_date": "2026-06-26",
            "trade_date": "2026-06-26",
            "snapshot_type": "complete",
        }

        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "report.html"
            dashboard_renderer.render_html(
                html_path,
                agg_rows,
                signal_rows,
                contract_rows,
                "2026-06-26",
                dor.trailing_weekdays("2026-06-26", 7),
                volume_contract_rows=volume_rows,
                option_unusual_rows=unusual_rows,
                report_groups=groups,
                quote_map={},
                snapshot_status=status,
            )
            text = html_path.read_text(encoding="utf-8")

        self.assertEqual(text.count('class="scan-row"'), 70)
        self.assertEqual(text.count("class='theme-section'"), 7)
        self.assertEqual(text.count("class='theme-directory-button'"), 7)
        heading_positions = [
            text.index(f"<h2>{group_name}</h2>")
            for group_name in rg.THEME_REPORT_GROUPS
        ]
        self.assertEqual(heading_positions, sorted(heading_positions))
        for symbol in symbols:
            self.assertEqual(text.count(f'data-symbol="{symbol}"'), 1)
        self.assertNotIn("theme-label", text)
        self.assertIn("[data-theme-grid]", text)
        self.assertNotIn("scrollIntoView", text)
        self.assertIn("@media (hover: hover) and (pointer: fine) and (min-width: 981px)", text)
        self.assertIn("@media (max-width: 980px)", text)
        self.assertIn(".theme-directory { display: none; }", text)


if __name__ == "__main__":
    unittest.main()
