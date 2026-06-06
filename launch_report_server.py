from __future__ import annotations

import socket
import subprocess
import sys
import time
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

from runtime_env import PYTHON, PYTHONW, ROOT, clean_env_for_child, configure_runtime


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"
PID_PATH = ROOT / "reports" / "report_server.pid"
LOG_PATH = ROOT / "reports" / "report_server_process.log"
EXPECTED_SERVER_VERSION = "2026-06-06-git-sync"


def port_is_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def read_server_json(path: str) -> dict[str, object] | None:
    try:
        with urlopen(f"{URL.rstrip('/')}{path}", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return None


def current_server_is_fresh() -> bool:
    info = read_server_json("/api/info")
    return bool(
        info
        and info.get("app") == "option_report_console"
        and info.get("version") == EXPECTED_SERVER_VERSION
    )


def current_server_is_option_console() -> bool:
    info = read_server_json("/api/info")
    if info and info.get("app") == "option_report_console":
        return True
    status = read_server_json("/api/status")
    message = str((status or {}).get("message", ""))
    return any(token in message for token in ("update", "console", "report", "Git"))


def listening_pid() -> int | None:
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{PORT}") and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def stop_stale_server() -> bool:
    pid = listening_pid()
    if not pid:
        return False
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, text=True)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not port_is_open():
            return True
        time.sleep(0.25)
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
        ],
        capture_output=True,
        text=True,
    )
    deadline = time.time() + 8
    while time.time() < deadline:
        if not port_is_open():
            return True
        time.sleep(0.25)
    return not port_is_open()


def main() -> int:
    configure_runtime()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if port_is_open():
        if current_server_is_fresh():
            print(f"Report console is already running: {URL}")
            return 0
        if not current_server_is_option_console():
            print(f"Port {PORT} is occupied by another process. Please close it first.", file=sys.stderr)
            return 1
        print("Detected an old report console. Restarting it...")
        if not stop_stale_server():
            print("Failed to stop the old report console. Please close it manually and try again.", file=sys.stderr)
            return 1

    python = PYTHONW if PYTHONW.exists() else PYTHON
    if not python.exists():
        print(f"Missing venv Python: {PYTHON}", file=sys.stderr)
        print("Run setup_venv.cmd first.", file=sys.stderr)
        return 1

    cmd = [str(python), str(ROOT / "report_server.py")]
    flags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_BREAKAWAY_FROM_JOB"):
        flags |= getattr(subprocess, name, 0)

    with LOG_PATH.open("ab") as log:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=clean_env_for_child(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=flags,
                close_fds=True,
            )
        except OSError:
            flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=clean_env_for_child(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=flags,
                close_fds=True,
            )

    PID_PATH.write_text(str(proc.pid), encoding="ascii")
    deadline = time.time() + 8
    while time.time() < deadline:
        if port_is_open():
            print(f"Started report console: {URL}")
            print(f"PID: {proc.pid}")
            return 0
        exit_code = proc.poll()
        if exit_code is not None:
            print(f"Report console exited early with code {exit_code}", file=sys.stderr)
            if LOG_PATH.exists():
                print(LOG_PATH.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            return 1
        time.sleep(0.25)

    print(f"Report console process started but {URL} did not respond yet.", file=sys.stderr)
    print(f"PID: {proc.pid}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
