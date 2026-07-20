#!/usr/bin/env python3
"""Local lifecycle CLI for the WRF Workbench."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from workbench.readiness import collect_readiness

REPO_ROOT = Path(__file__).resolve().parents[1]


def _configured_path(environment_name: str, default: Path) -> Path:
    configured = os.environ.get(environment_name)
    path = Path(configured).expanduser() if configured else default
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


RUNTIME_DIR = _configured_path(
    "WRF_CHAMMER_RUNTIME_DIR", REPO_ROOT / "workbench-runs" / ".runtime"
)
STATE_FILE = RUNTIME_DIR / "server.json"
SERVER_LOG_FILE = RUNTIME_DIR / "server.log"
WORKER_LOG_FILE = RUNTIME_DIR / "worker.log"


def _read_state() -> dict[str, Any] | None:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_state() -> dict[str, Any]:
    state = _read_state() or {}
    server_pid = int(state.get("server_pid") or state.get("pid") or 0)
    worker_pid = int(state.get("worker_pid") or 0)
    server_running = _pid_alive(server_pid)
    worker_running = _pid_alive(worker_pid)
    return {
        **state,
        "pid": server_pid or None,
        "server_pid": server_pid or None,
        "worker_pid": worker_pid or None,
        "server_running": server_running,
        "worker_running": worker_running,
        "running": server_running and worker_running,
        "degraded": server_running != worker_running,
        "log_file": str(SERVER_LOG_FILE),
        "server_log_file": str(SERVER_LOG_FILE),
        "worker_log_file": str(WORKER_LOG_FILE),
    }


def _health_url(host: str, port: int) -> str:
    visible_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{visible_host}:{port}/api/health"


def _wait_for_health(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(_health_url(host, port), timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("ok"):
                    return True
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.2)
    return False


def _print(payload: Any, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2))


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    if pid <= 0:
        return
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _stop_pid(pid: int, timeout: float) -> None:
    if not _pid_alive(pid):
        return
    _signal_process_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.1, timeout)
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid):
        _signal_process_group(pid, signal.SIGKILL)


def _spawn(command: list[str], log_path: Path) -> subprocess.Popen[str]:
    handle = log_path.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            text=True,
        )
    finally:
        handle.close()


def _write_state(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_FILE)


def command_start(args: argparse.Namespace) -> int:
    current = _process_state()
    if current["running"]:
        _print(
            {
                "status": "already-running",
                "server_pid": current["server_pid"],
                "worker_pid": current["worker_pid"],
                "url": current.get("url"),
                "server_log_file": current["server_log_file"],
                "worker_log_file": current["worker_log_file"],
            },
            args.json,
        )
        return 0

    if current["server_running"] or current["worker_running"]:
        _stop_pid(int(current.get("worker_pid") or 0), min(args.timeout, 10.0))
        _stop_pid(int(current.get("server_pid") or 0), min(args.timeout, 5.0))
        STATE_FILE.unlink(missing_ok=True)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server_command = [
        sys.executable,
        "-m",
        "workbench.server.application",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    server = _spawn(server_command, SERVER_LOG_FILE)
    if not _wait_for_health(args.host, args.port, timeout=args.timeout):
        _stop_pid(server.pid, 2.0)
        _print(
            {
                "status": "failed",
                "message": "Workbench API/UI did not become healthy before the timeout.",
                "server_pid": server.pid,
                "server_log_file": str(SERVER_LOG_FILE),
            },
            args.json,
        )
        return 1

    worker_command = [
        sys.executable,
        "-m",
        "workbench.job_worker",
        "--repo-root",
        str(REPO_ROOT),
        "--poll-seconds",
        str(args.worker_poll_seconds),
    ]
    worker = _spawn(worker_command, WORKER_LOG_FILE)
    time.sleep(0.25)
    if worker.poll() is not None:
        _stop_pid(server.pid, 2.0)
        _print(
            {
                "status": "failed",
                "message": "Persistent job worker exited during startup.",
                "worker_exit_code": worker.returncode,
                "worker_log_file": str(WORKER_LOG_FILE),
            },
            args.json,
        )
        return 1

    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    state = {
        "pid": server.pid,
        "server_pid": server.pid,
        "worker_pid": worker.pid,
        "host": args.host,
        "port": args.port,
        "url": f"http://{url_host}:{args.port}/",
        "server_command": server_command,
        "worker_command": worker_command,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_state(state)

    if args.open:
        webbrowser.open(state["url"])
    _print(
        {
            "status": "running",
            **state,
            "server_running": True,
            "worker_running": True,
            "server_log_file": str(SERVER_LOG_FILE),
            "worker_log_file": str(WORKER_LOG_FILE),
        },
        args.json,
    )
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state = _process_state()
    server_pid = int(state.get("server_pid") or 0)
    worker_pid = int(state.get("worker_pid") or 0)
    if not state["server_running"] and not state["worker_running"]:
        STATE_FILE.unlink(missing_ok=True)
        _print({"status": "stopped", "message": "Workbench is not running."}, args.json)
        return 0

    # Stop the worker first so active jobs receive an orderly cancellation before
    # the HTTP server disappears.
    _stop_pid(worker_pid, args.timeout)
    _stop_pid(server_pid, min(args.timeout, 5.0))
    STATE_FILE.unlink(missing_ok=True)
    _print(
        {
            "status": "stopped",
            "server_pid": server_pid or None,
            "worker_pid": worker_pid or None,
        },
        args.json,
    )
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = _process_state()
    _print(state, args.json)
    return 0 if state["running"] else 1


def command_doctor(args: argparse.Namespace) -> int:
    payload = collect_readiness(REPO_ROOT, include_images=not args.skip_images)
    _print(payload, args.json)
    return 0 if payload["ok"] else 1


def _read_log(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def command_logs(args: argparse.Namespace) -> int:
    selected: dict[str, dict[str, Any]] = {}
    if args.component in {"server", "all"}:
        selected["server"] = {
            "log_file": str(SERVER_LOG_FILE),
            "lines": _read_log(SERVER_LOG_FILE, args.lines),
        }
    if args.component in {"worker", "all"}:
        selected["worker"] = {
            "log_file": str(WORKER_LOG_FILE),
            "lines": _read_log(WORKER_LOG_FILE, args.lines),
        }
    if not any(entry["lines"] for entry in selected.values()):
        _print("No requested Workbench log exists yet.", args.json)
        return 1
    if args.json:
        _print(selected, True)
    else:
        for component, entry in selected.items():
            print(f"== {component}: {entry['log_file']} ==")
            print("\n".join(entry["lines"]))
    return 0


def command_worker(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        "-m",
        "workbench.job_worker",
        "--repo-root",
        str(REPO_ROOT),
        "--poll-seconds",
        str(args.poll_seconds),
        "--cancel-grace-seconds",
        str(args.cancel_grace_seconds),
    ]
    if args.once:
        command.append("--once")
    if args.worker_id:
        command.extend(["--worker-id", args.worker_id])
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    return int(completed.returncode)


def command_update_images(_args: argparse.Namespace) -> int:
    if not shutil.which("docker"):
        print("Docker is required to build runtime images.", file=sys.stderr)
        return 1
    builds = [
        (["docker", "build", "-f", "Dockerfile", "-t", "wrf-reproducible:latest", "."], "WRF"),
        (["docker", "build", "-f", "Dockerfile.wps", "-t", "wps-reproducible:latest", "."], "WPS"),
    ]
    for command, label in builds:
        print(f"Building {label} runtime image...", flush=True)
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wrf-chammer", description="Manage the local WRF Workbench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start the local Workbench API, GUI, and worker")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8080)
    start.add_argument("--timeout", type=float, default=15.0)
    start.add_argument("--worker-poll-seconds", type=float, default=1.0)
    start.add_argument("--open", action="store_true", help="Open the GUI in the default browser")
    start.add_argument("--json", action="store_true")
    start.set_defaults(func=command_start)

    stop = subparsers.add_parser("stop", help="Stop the local Workbench worker and server")
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(func=command_stop)

    status = subparsers.add_parser("status", help="Show server and worker lifecycle status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    doctor = subparsers.add_parser("doctor", help="Check readiness for local real-data simulations")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--skip-images", action="store_true", help="Do not inspect local WRF/WPS images")
    doctor.set_defaults(func=command_doctor)

    logs = subparsers.add_parser("logs", help="Show the locally managed server and worker logs")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--component", choices=("server", "worker", "all"), default="all")
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=command_logs)

    worker = subparsers.add_parser("worker", help="Run a persistent worker in the foreground")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--worker-id")
    worker.add_argument("--poll-seconds", type=float, default=1.0)
    worker.add_argument("--cancel-grace-seconds", type=float, default=8.0)
    worker.set_defaults(func=command_worker)

    images = subparsers.add_parser("update-images", help="Build the local WRF and WPS runtime images")
    images.set_defaults(func=command_update_images)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
