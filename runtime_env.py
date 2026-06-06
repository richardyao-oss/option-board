from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_APPDATA = ROOT / ".futu-appdata"
PYTHON = ROOT / ".venv-futu" / "Scripts" / "python.exe"
PYTHONW = ROOT / ".venv-futu" / "Scripts" / "pythonw.exe"


def configure_runtime() -> None:
    """Keep project runs isolated from global AppData and old target packages."""
    PROJECT_APPDATA.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(PROJECT_APPDATA)
    os.environ["appdata"] = str(PROJECT_APPDATA)
    os.environ.setdefault("FUTU_OPEND_HOST", "127.0.0.1")
    os.environ.setdefault("FUTU_OPEND_PORT", "11111")
    remove_legacy_package_paths()


def remove_legacy_package_paths() -> None:
    legacy_dirs = {
        str((ROOT / ".python-packages").resolve()).lower(),
        str((ROOT / ".python-packages-fixed").resolve()).lower(),
        str((ROOT / ".python-packages-local").resolve()).lower(),
    }
    sys.path[:] = [
        entry
        for entry in sys.path
        if not entry or str(Path(entry).resolve()).lower() not in legacy_dirs
    ]


def clean_env_for_child() -> dict[str, str]:
    env: dict[str, str] = {}
    path_value = ""
    for key, value in os.environ.items():
        if key.lower() == "path":
            path_value = value
            continue
        env[key] = value
    if path_value:
        env["Path"] = path_value
    env["APPDATA"] = str(PROJECT_APPDATA)
    env["appdata"] = str(PROJECT_APPDATA)
    env.setdefault("FUTU_OPEND_HOST", "127.0.0.1")
    env.setdefault("FUTU_OPEND_PORT", "11111")
    return env


def running_in_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == PYTHON.resolve()
    except OSError:
        return False
