#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import sync_settings
from runtime_env import PYTHON, clean_env_for_child, configure_runtime


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_PATH = ROOT / "reports" / "options_anomaly_report.html"
HOST = "127.0.0.1"
PORT = 8765
LOG_PATH = ROOT / "reports" / "report_server.log"
SERVER_VERSION = "2026-06-06-report-alias"

UPDATE_CONFIG = {
    "intraday": {
        "mode": "intraday",
        "name": "intraday update",
        "timeout": 2400,
    },
    "preopen": {
        "mode": "preopen",
        "name": "complete review",
        "timeout": 2400,
    },
}

update_lock = threading.Lock()
last_result: dict[str, object] = {
    "ok": True,
    "message": "No update has run in this console yet.",
    "updated_at": None,
    "sync_mode": sync_settings.sync_mode(),
    "report": str(REPORT_PATH),
}


def json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def run_update(kind: str) -> dict[str, object]:
    config = UPDATE_CONFIG.get(kind)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if config is None:
        return {"ok": False, "message": f"Unknown update type: {kind}", "updated_at": now}

    if not PYTHON.exists():
        return {
            "ok": False,
            "message": f"Virtual environment Python not found: {PYTHON}",
            "updated_at": now,
        }

    cmd = [
        str(PYTHON),
        str(ROOT / "git_sync_update.py"),
        "--mode",
        str(config["mode"]),
    ]
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=clean_env_for_child(),
        text=True,
        capture_output=True,
        timeout=int(config["timeout"]),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ok = proc.returncode == 0
    name = str(config["name"])
    return {
        "ok": ok,
        "message": f"{name} completed and pushed to Git." if ok else f"{name} failed. Check OpenD, Git status, and network.",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(time.time() - started, 1),
        "computer": socket.gethostname(),
        "sync_mode": sync_settings.sync_mode(),
        "data_dir": str(DATA_DIR),
        "report": str(REPORT_PATH),
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OptionReportServer/2.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_data(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/report", "/report.html"):
            if not REPORT_PATH.exists():
                payload = json_bytes({"ok": False, "message": f"Report does not exist: {REPORT_PATH}"})
                self.send_data(404, payload, "application/json; charset=utf-8")
                return
            self.send_data(200, REPORT_PATH.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/status":
            self.send_data(200, json_bytes(last_result), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/info":
            payload = {
                "ok": True,
                "app": "option_report_console",
                "version": SERVER_VERSION,
                "report": str(REPORT_PATH),
                "sync_mode": sync_settings.sync_mode(),
                "data_dir": str(DATA_DIR),
                "computer": socket.gethostname(),
            }
            self.send_data(200, json_bytes(payload), "application/json; charset=utf-8")
            return
        self.send_data(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        global last_result
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/update/preopen", "/api/update/intraday"):
            self.send_data(404, b"Not found", "text/plain; charset=utf-8")
            return

        kind = parsed.path.rsplit("/", 1)[-1]
        if not update_lock.acquire(blocking=False):
            payload = {"ok": False, "message": "An update is already running. Please wait for it to finish."}
            self.send_data(409, json_bytes(payload), "application/json; charset=utf-8")
            return

        try:
            last_result = {
                "ok": True,
                "message": "Updating. Please wait...",
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "sync_mode": sync_settings.sync_mode(),
                "report": str(REPORT_PATH),
            }
            result = run_update(kind)
            last_result = result
            self.send_data(200 if result.get("ok") else 500, json_bytes(result), "application/json; charset=utf-8")
        except subprocess.TimeoutExpired:
            last_result = {
                "ok": False,
                "message": "Update timed out. Check OpenD, network, and Git status.",
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.send_data(504, json_bytes(last_result), "application/json; charset=utf-8")
        finally:
            update_lock.release()


def main() -> int:
    configure_runtime()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
        LOG_PATH.write_text(
            f"Option report console: http://{HOST}:{PORT}/\n"
            f"Report: {REPORT_PATH}\n"
            f"Sync mode: {sync_settings.sync_mode()}\n",
            encoding="utf-8",
        )
        server.serve_forever()
    except Exception as exc:
        LOG_PATH.write_text(f"server failed: {type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
