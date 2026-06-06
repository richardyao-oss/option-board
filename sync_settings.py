from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "sync_config.json"
DEFAULT_DATA_DIR = Path("data")
DEFAULT_REPORT_PATH = Path("reports") / "options_anomaly_report.html"


def _clean_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sync_mode() -> str:
    return str(read_config().get("mode") or "git").strip().lower()


def configured_sync_dir() -> Path | None:
    payload = read_config()
    if str(payload.get("mode") or "").strip().lower() == "git":
        return None
    return _clean_path(payload.get("sync_dir"))


def legacy_google_drive_sync_dir() -> Path | None:
    payload = read_config()
    return _clean_path(payload.get("legacy_google_drive_sync_dir") or payload.get("sync_dir"))


def git_remote_url() -> str:
    return str(read_config().get("remote") or "").strip()


def resolve_data_dir(requested: Path) -> Path:
    sync_dir = configured_sync_dir()
    if sync_dir is None:
        return requested
    if requested == DEFAULT_DATA_DIR:
        return sync_dir / "data"
    return requested


def resolve_report_path(requested: Path) -> Path:
    sync_dir = configured_sync_dir()
    if sync_dir is None:
        return requested
    if requested == DEFAULT_REPORT_PATH:
        return sync_dir / "reports" / "options_anomaly_report.html"
    return requested


def sync_status_path() -> Path | None:
    sync_dir = configured_sync_dir()
    if sync_dir is None:
        return None
    return sync_dir / "update_status.json"


def sync_lock_path() -> Path | None:
    sync_dir = configured_sync_dir()
    if sync_dir is None:
        return None
    return sync_dir / "update.lock"
