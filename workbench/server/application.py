#!/usr/bin/env python3
"""Primary local WRF Workbench application server."""

from __future__ import annotations

import argparse
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workbench.domain_planner import (  # noqa: E402
    DomainPlanningError,
    available_profiles,
    plan_domain,
)
from workbench.readiness import collect_readiness  # noqa: E402
from workbench.server.server import ApiError, WorkbenchApiHandler, WorkbenchApiServer  # noqa: E402


class WorkbenchApplicationHandler(WorkbenchApiHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in {"/api/readiness", "/api/domain/profiles"}:
            super().do_GET()
            return
        try:
            self._require_local_client()
            if path == "/api/readiness":
                self._send_json(HTTPStatus.OK, collect_readiness(self.server.repo_root))
            else:
                self._send_json(HTTPStatus.OK, {"ok": True, "profiles": available_profiles()})
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/domain/plan":
            super().do_POST()
            return
        try:
            self._require_local_client()
            request = self._read_json()
            plan = plan_domain(request)
            self._send_json(HTTPStatus.OK, plan)
        except DomainPlanningError as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": exc.errors},
            )
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "domain_planning_error", str(exc))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local WRF Workbench application")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (normally detected automatically)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    server = WorkbenchApiServer((args.host, args.port), WorkbenchApplicationHandler, args.repo_root)
    print(f"WRF Workbench available at http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
