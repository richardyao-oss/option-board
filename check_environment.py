from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from runtime_env import PYTHON, ROOT, clean_env_for_child, configure_runtime, running_in_project_venv


HOST = "127.0.0.1"
OPEND_PORT = 11111
REPORT_PORT = 8765


class CheckReport:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def warn(self, message: str) -> None:
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failed = True
        print(f"[FAIL] {message}")


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=1.0):
            return True
    except OSError:
        return False


def raw_path_keys() -> list[str]:
    if os.name != "nt":
        return [key for key in os.environ if key.lower() == "path"]

    kernel = ctypes.windll.kernel32
    kernel.GetEnvironmentStringsW.restype = ctypes.POINTER(ctypes.c_wchar)
    pointer = kernel.GetEnvironmentStringsW()
    if not pointer:
        return []

    values: list[str] = []
    chars: list[str] = []
    index = 0
    try:
        while True:
            ch = pointer[index]
            if ch == "\0":
                if not chars:
                    break
                values.append("".join(chars))
                chars = []
            else:
                chars.append(ch)
            index += 1
    finally:
        kernel.FreeEnvironmentStringsW(pointer)

    return [value.split("=", 1)[0] for value in values if value.lower().startswith("path=")]


def check_python(report: CheckReport) -> None:
    if not PYTHON.exists():
        report.fail(f"Missing project Python: {PYTHON}")
        return
    report.ok(f"Project Python exists: {PYTHON}")

    if running_in_project_venv():
        report.ok("This check is running inside .venv-futu")
    else:
        report.warn(f"This check is running with {sys.executable}; prefer {PYTHON}")

    configure_runtime()
    try:
        test_path = ROOT / ".futu-appdata" / "write-test.txt"
        test_path.write_text("ok", encoding="ascii")
        test_path.unlink()
        report.ok("Project .futu-appdata is writable")
    except OSError as exc:
        report.fail(f"Project .futu-appdata is not writable: {exc}")

    try:
        import pandas
        import futu

        futu_file = str(Path(futu.__file__).resolve())
        report.ok(f"pandas import OK: {getattr(pandas, '__version__', 'unknown')}")
        report.ok(f"futu-api import OK: {getattr(futu, '__version__', 'unknown')}")
        if ".venv-futu" in futu_file:
            report.ok(f"futu-api is loaded from .venv-futu: {futu_file}")
        else:
            report.fail(f"futu-api is not loaded from .venv-futu: {futu_file}")
    except Exception as exc:
        report.fail(f"Python dependency import failed: {type(exc).__name__}: {exc}")


def check_legacy_dirs(report: CheckReport) -> None:
    legacy_dirs = [
        ROOT / ".python-packages",
        ROOT / ".python-packages-fixed",
        ROOT / ".python-packages-local",
    ]
    active = [path.name for path in legacy_dirs if path.exists()]
    if active:
        report.warn(f"Legacy target package dirs still exist: {', '.join(active)}")
    else:
        report.ok("No active legacy target package dirs found")


def check_path_env(report: CheckReport) -> None:
    keys = raw_path_keys()
    if len(keys) <= 1:
        report.ok(f"Process environment has one Path key: {keys[0] if keys else 'none'}")
    else:
        report.warn(f"Process environment has duplicate Path keys: {', '.join(keys)}")

    clean_keys = [key for key in clean_env_for_child() if key.lower() == "path"]
    if clean_keys == ["Path"]:
        report.ok("Child process environment will be normalized to Path only")
    else:
        report.fail(f"Child process Path normalization unexpected: {clean_keys}")


def check_opend_process(report: CheckReport) -> None:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Futu_OpenD.exe", "/FO", "CSV", "/NH"],
        text=True,
        capture_output=True,
        env=clean_env_for_child(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        report.warn(f"Could not inspect OpenD process list: {detail or 'unknown error'}")
        return
    text = (result.stdout or "").strip()
    if "Futu_OpenD.exe" in text:
        report.ok("Futu_OpenD.exe process is running")
    else:
        report.warn("Futu_OpenD.exe process was not found by tasklist")


def check_opend_connection(report: CheckReport, skip_api: bool) -> None:
    if port_is_open(OPEND_PORT):
        report.ok(f"OpenD port is listening: {HOST}:{OPEND_PORT}")
    else:
        report.fail(f"OpenD port is not listening: {HOST}:{OPEND_PORT}")
        return

    if skip_api:
        report.warn("Skipped Futu API connection check")
        return

    try:
        from futu import RET_OK, OpenQuoteContext

        try:
            quote_ctx = OpenQuoteContext(host=HOST, port=OPEND_PORT, ai_type=1)
        except TypeError:
            quote_ctx = OpenQuoteContext(host=HOST, port=OPEND_PORT)
        try:
            ret, data = quote_ctx.get_global_state()
        finally:
            quote_ctx.close()

        if ret == RET_OK:
            if hasattr(data, "to_dict"):
                data_dict: dict[str, Any] = data.to_dict()  # type: ignore[assignment]
            elif isinstance(data, dict):
                data_dict = data
            else:
                data_dict = {"raw": str(data)}
            report.ok("Futu API quote context connected")
            print(json.dumps(data_dict, ensure_ascii=False, indent=2, default=str))
        else:
            report.fail(f"Futu API returned error: {data}")
    except Exception as exc:
        report.fail(f"Futu API connection failed: {type(exc).__name__}: {exc}")


def check_report_server(report: CheckReport) -> None:
    html_path = ROOT / "reports" / "options_anomaly_report.html"
    if html_path.exists():
        report.ok(f"Report HTML exists: {html_path}")
    else:
        report.warn(f"Report HTML does not exist yet: {html_path}")

    if not port_is_open(REPORT_PORT):
        report.warn(f"Report console is not listening yet: {HOST}:{REPORT_PORT}")
        return

    report.ok(f"Report console port is listening: {HOST}:{REPORT_PORT}")
    try:
        with urllib.request.urlopen(f"http://{HOST}:{REPORT_PORT}/api/status", timeout=2) as response:
            payload = response.read().decode("utf-8", errors="replace")
        report.ok(f"Report console status endpoint responded: {payload[:300]}")
    except urllib.error.URLError as exc:
        report.warn(f"Report console status endpoint did not respond cleanly: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-api", action="store_true", help="skip Futu API quote-context connection")
    args = parser.parse_args()

    report = CheckReport()
    print(f"Project: {ROOT}")
    check_python(report)
    check_legacy_dirs(report)
    check_path_env(report)
    check_opend_process(report)
    check_opend_connection(report, args.skip_api)
    check_report_server(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
