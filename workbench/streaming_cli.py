#!/usr/bin/env python3
"""Lifecycle CLI adapter using the streaming application handler."""

from __future__ import annotations

import sys
import time
import webbrowser

from workbench import cli as base


def command_start(args) -> int:
    current = base._process_state()
    if current["running"]:
        base._print(
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
        base._stop_pid(int(current.get("worker_pid") or 0), min(args.timeout, 10.0))
        base._stop_pid(int(current.get("server_pid") or 0), min(args.timeout, 5.0))
        base.STATE_FILE.unlink(missing_ok=True)

    base.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server_command = [
        sys.executable,
        "-m",
        "workbench.server.streaming_application",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    server = base._spawn(server_command, base.SERVER_LOG_FILE)
    if not base._wait_for_health(args.host, args.port, timeout=args.timeout):
        base._stop_pid(server.pid, 2.0)
        base._print(
            {
                "status": "failed",
                "message": "Workbench API/UI did not become healthy before the timeout.",
                "server_pid": server.pid,
                "server_log_file": str(base.SERVER_LOG_FILE),
            },
            args.json,
        )
        return 1

    worker_command = [
        sys.executable,
        "-m",
        "workbench.job_worker",
        "--repo-root",
        str(base.REPO_ROOT),
        "--poll-seconds",
        str(args.worker_poll_seconds),
    ]
    worker = base._spawn(worker_command, base.WORKER_LOG_FILE)
    time.sleep(0.25)
    if worker.poll() is not None:
        base._stop_pid(server.pid, 2.0)
        base._print(
            {
                "status": "failed",
                "message": "Persistent job worker exited during startup.",
                "worker_exit_code": worker.returncode,
                "worker_log_file": str(base.WORKER_LOG_FILE),
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
    base._write_state(state)
    if args.open:
        webbrowser.open(state["url"])
    base._print(
        {
            "status": "running",
            **state,
            "server_running": True,
            "worker_running": True,
            "server_log_file": str(base.SERVER_LOG_FILE),
            "worker_log_file": str(base.WORKER_LOG_FILE),
        },
        args.json,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = base.build_parser().parse_args(argv)
    if args.command == "start":
        return command_start(args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
