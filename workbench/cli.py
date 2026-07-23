#!/usr/bin/env python3
"""Local lifecycle CLI for the WRF Workbench."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from workbench.readiness import collect_readiness
from workbench.runtime_image_service import (
    RuntimeImageError,
    default_manifest_path,
    load_activation,
    pull_release,
    runtime_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "workbench-runs" / ".runtime"
STATE_FILE = RUNTIME_DIR / "server.json"
LOG_FILE = RUNTIME_DIR / "server.log"


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


def _server_state() -> dict[str, Any]:
    state = _read_state() or {}
    pid = int(state.get("pid") or 0)
    return {
        **state,
        "pid": pid or None,
        "running": _pid_alive(pid),
        "log_file": str(LOG_FILE),
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


def _runtime_failure(exc: RuntimeImageError, json_output: bool) -> int:
    _print(
        {
            "status": "failed",
            "error": {"code": exc.code, "message": exc.message},
        },
        json_output,
    )
    return 1


def command_start(args: argparse.Namespace) -> int:
    current = _server_state()
    if current["running"]:
        _print(
            {
                "status": "already-running",
                "pid": current["pid"],
                "url": current.get("url"),
                "log_file": current["log_file"],
            },
            args.json,
        )
        return 0

    try:
        active_runtime = load_activation(REPO_ROOT, required=False)
        child_environment = {**os.environ, **runtime_environment(REPO_ROOT)}
    except RuntimeImageError as exc:
        return _runtime_failure(exc, args.json)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "workbench.server.application",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=child_environment,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log_handle.close()
    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    state = {
        "pid": process.pid,
        "host": args.host,
        "port": args.port,
        "url": f"http://{url_host}:{args.port}/",
        "command": command,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_release": active_runtime.get("release") if active_runtime else None,
        "runtime_manifest_sha256": (
            active_runtime.get("manifest_sha256") if active_runtime else None
        ),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    if not _wait_for_health(args.host, args.port, timeout=args.timeout):
        _print(
            {
                "status": "failed",
                "message": "Workbench did not become healthy before the timeout.",
                "pid": process.pid,
                "log_file": str(LOG_FILE),
            },
            args.json,
        )
        return 1

    if args.open:
        webbrowser.open(state["url"])
    _print({"status": "running", **state, "log_file": str(LOG_FILE)}, args.json)
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state = _server_state()
    if not state["running"] or not state["pid"]:
        STATE_FILE.unlink(missing_ok=True)
        _print({"status": "stopped", "message": "Workbench is not running."}, args.json)
        return 0
    pid = int(state["pid"])
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + args.timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    STATE_FILE.unlink(missing_ok=True)
    _print({"status": "stopped", "pid": pid}, args.json)
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = _server_state()
    _print(state, args.json)
    return 0 if state["running"] else 1


def command_doctor(args: argparse.Namespace) -> int:
    payload = collect_readiness(REPO_ROOT, include_images=not args.skip_images)
    _print(payload, args.json)
    return 0 if payload["ok"] else 1


def command_logs(args: argparse.Namespace) -> int:
    if not LOG_FILE.is_file():
        _print("No Workbench server log exists yet.", args.json)
        return 1
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[-args.lines :]
    if args.json:
        _print({"log_file": str(LOG_FILE), "lines": selected}, True)
    else:
        print("\n".join(selected))
    return 0


def command_images_pull(args: argparse.Namespace) -> int:
    manifest = args.manifest or default_manifest_path(REPO_ROOT)
    try:
        activation = pull_release(REPO_ROOT, manifest, engine_name=args.engine)
    except RuntimeImageError as exc:
        return _runtime_failure(exc, args.json)
    _print({"status": "ready", "activation": activation}, args.json)
    return 0


def command_images_status(args: argparse.Namespace) -> int:
    try:
        activation = load_activation(REPO_ROOT, required=False)
    except RuntimeImageError as exc:
        return _runtime_failure(exc, args.json)
    payload = {
        "status": "ready" if activation else "not-configured",
        "activation": activation,
        "default_manifest": str(default_manifest_path(REPO_ROOT)),
    }
    _print(payload, args.json)
    return 0 if activation else 1


def command_update_images(args: argparse.Namespace) -> int:
    """Backward-compatible alias for pulling a digest-pinned release."""
    return command_images_pull(args)


def _add_image_pull_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--engine")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wrf-chammer", description="Manage the local WRF Workbench"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start the local Workbench API and GUI")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8080)
    start.add_argument("--timeout", type=float, default=15.0)
    start.add_argument("--open", action="store_true", help="Open the GUI in the default browser")
    start.add_argument("--json", action="store_true")
    start.set_defaults(func=command_start)

    stop = subparsers.add_parser("stop", help="Stop the locally managed Workbench server")
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(func=command_stop)

    status = subparsers.add_parser("status", help="Show whether the Workbench server is running")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    doctor = subparsers.add_parser("doctor", help="Check readiness for local real-data simulations")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--skip-images", action="store_true", help="Do not inspect runtime images")
    doctor.set_defaults(func=command_doctor)

    logs = subparsers.add_parser("logs", help="Show the locally managed server log")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=command_logs)

    images = subparsers.add_parser("images", help="Manage digest-pinned runtime images")
    image_commands = images.add_subparsers(dest="image_command", required=True)
    image_pull = image_commands.add_parser("pull", help="Pull and activate one runtime release")
    _add_image_pull_arguments(image_pull)
    image_pull.set_defaults(func=command_images_pull)
    image_status = image_commands.add_parser("status", help="Show the active runtime release")
    image_status.add_argument("--json", action="store_true")
    image_status.set_defaults(func=command_images_status)

    update = subparsers.add_parser(
        "update-images",
        help="Deprecated alias for 'images pull'; no local compiler build is performed",
    )
    _add_image_pull_arguments(update)
    update.set_defaults(func=command_update_images)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
