from __future__ import annotations

import json
import shutil
import argparse
from pathlib import Path

import dashboard_renderer
import daily_option_report as dor
import option_screen_monitor as osm
import report_groups as rg
import sync_settings
from runtime_env import ROOT, configure_runtime


def merge_snapshot_csv(
    local_path: Path,
    shared_path: Path,
    columns: list[str],
    snapshot_date: str,
) -> int:
    local_rows = [row for row in dor.read_csv(local_path) if row.get("snapshot_date") == snapshot_date]
    if not local_rows:
        raise RuntimeError(f"No local rows for {snapshot_date}: {local_path}")

    shared_rows = dor.read_csv(shared_path)
    kept_rows = [row for row in shared_rows if row.get("snapshot_date") != snapshot_date]
    shared_path.parent.mkdir(parents=True, exist_ok=True)
    dor.write_csv(shared_path, columns, kept_rows + local_rows)
    return len(local_rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge one local option snapshot date into the Google Drive shared folder."
    )
    parser.add_argument(
        "--snapshot-date",
        help="Snapshot date to sync. Defaults to the latest local snapshot date.",
    )
    args = parser.parse_args()

    configure_runtime()
    sync_dir = sync_settings.legacy_google_drive_sync_dir()
    if sync_dir is None:
        raise RuntimeError("sync_config.json does not contain legacy_google_drive_sync_dir.")

    local_data = ROOT / "data"
    shared_data = sync_dir / "data"
    shared_reports = sync_dir / "reports"
    shared_data.mkdir(parents=True, exist_ok=True)
    shared_reports.mkdir(parents=True, exist_ok=True)

    status_path = local_data / dor.SNAPSHOT_STATUS_FILE
    status = dor.read_snapshot_status(status_path)
    latest_snapshot_date = str(status.get("snapshot_date") or status.get("trade_date") or "")
    if not latest_snapshot_date:
        raise RuntimeError(f"Could not determine latest snapshot date from {status_path}")
    sync_snapshot_date = str(args.snapshot_date or latest_snapshot_date)

    if args.snapshot_date:
        print(f"Legacy Google Drive sync selected snapshot date: {sync_snapshot_date}")
    else:
        print(f"Legacy Google Drive sync latest snapshot date: {sync_snapshot_date}")
    copied = {
        "underlying": merge_snapshot_csv(
            local_data / "option_screen_underlying_snapshot.csv",
            shared_data / "option_screen_underlying_snapshot.csv",
            osm.AGG_COLUMNS,
            sync_snapshot_date,
        ),
        "signals": merge_snapshot_csv(
            local_data / "daily_option_signals.csv",
            shared_data / "daily_option_signals.csv",
            dor.SIGNAL_COLUMNS,
            sync_snapshot_date,
        ),
        "contracts": merge_snapshot_csv(
            local_data / "option_screen_contract_snapshot.csv",
            shared_data / "option_screen_contract_snapshot.csv",
            osm.CONTRACT_COLUMNS,
            sync_snapshot_date,
        ),
        "volume_contracts": merge_snapshot_csv(
            local_data / dor.VOLUME_CONTRACT_SNAPSHOT_FILE,
            shared_data / dor.VOLUME_CONTRACT_SNAPSHOT_FILE,
            osm.CONTRACT_COLUMNS,
            sync_snapshot_date,
        ),
    }

    shutil.copy2(status_path, shared_data / dor.SNAPSHOT_STATUS_FILE)
    quote_path = local_data / "current_quote_snapshot.json"
    if quote_path.exists():
        shutil.copy2(quote_path, shared_data / "current_quote_snapshot.json")

    agg = dor.read_csv(shared_data / "option_screen_underlying_snapshot.csv")
    signals = dor.read_csv(shared_data / "daily_option_signals.csv")
    contracts = dor.read_csv(shared_data / "option_screen_contract_snapshot.csv")
    volume_contracts = dor.read_csv(shared_data / dor.VOLUME_CONTRACT_SNAPSHOT_FILE)
    quote_snapshot = dor.read_quote_snapshot(shared_data / "current_quote_snapshot.json")
    quote_map = quote_snapshot.get("quotes", {}) if isinstance(quote_snapshot.get("quotes", {}), dict) else {}
    latest_symbols = sorted(
        {
            row.get("underlying")
            for row in agg
            if row.get("snapshot_date") == latest_snapshot_date and row.get("underlying")
        }
    )
    report_groups = {rg.COMBINED_GROUP_NAME: latest_symbols}
    report_path = shared_reports / "options_anomaly_report.html"
    dashboard_renderer.render_html(
        report_path,
        agg,
        signals,
        contracts,
        latest_snapshot_date,
        dor.trailing_weekdays(latest_snapshot_date, 7),
        volume_contract_rows=volume_contracts,
        report_groups=report_groups,
        quote_map=quote_map,
        snapshot_status=status,
    )

    print(json.dumps(copied, ensure_ascii=False, indent=2))
    print(f"Shared report updated: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
