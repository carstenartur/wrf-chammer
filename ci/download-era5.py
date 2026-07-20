#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Download ERA5 requests described in a JSON file.")
    parser.add_argument("--config", required=True, help="Path to the JSON download configuration.")
    parser.add_argument("--output-dir", required=True, help="Directory where GRIB files will be stored.")
    parser.add_argument(
        "--manifest",
        help="Optional output manifest path. Defaults to <output-dir>/era5-manifest.json.",
    )
    parser.add_argument(
        "--progress",
        help="Optional atomic JSON progress file for local job orchestration.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum attempts for each uncached request (default: 3).",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=2.0,
        help="Base delay for exponential retry backoff (default: 2 seconds).",
    )
    return parser.parse_args(argv)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(config_path: Path) -> tuple[dict[str, Any], str]:
    try:
        config_text = config_path.read_text(encoding="utf-8")
        config = json.loads(config_text)
    except OSError as exc:
        raise DownloadError("ERA5 download configuration could not be read.") from exc
    except json.JSONDecodeError as exc:
        raise DownloadError("ERA5 download configuration is not valid JSON.") from exc
    requests = config.get("requests") if isinstance(config, dict) else None
    if not isinstance(requests, dict) or not requests:
        raise DownloadError("Configuration must contain a non-empty 'requests' object.")
    return config, config_text


def build_client():
    try:
        import cdsapi
    except ModuleNotFoundError as exc:
        raise DownloadError(
            "cdsapi is required for uncached ERA5 downloads. "
            "Install it or pre-populate target files for offline runs."
        ) from exc

    return cdsapi.Client()


def resolve_target(output_dir: Path, target_name: Any) -> Path:
    if not isinstance(target_name, str) or not target_name.strip():
        raise DownloadError("ERA5 request target must be a non-empty relative path.")

    relative = Path(target_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise DownloadError(f"ERA5 request target must stay inside the output directory: {target_name!r}")

    target = (output_dir / relative).resolve()
    if target != output_dir and output_dir not in target.parents:
        raise DownloadError(f"ERA5 request target escapes the output directory: {target_name!r}")
    return target


class ProgressReporter:
    def __init__(self, path: Path | None, total_requests: int):
        self.path = path
        self.payload: dict[str, Any] = {
            "version": 1,
            "status": "queued",
            "total_requests": total_requests,
            "completed_requests": 0,
            "current_request": None,
            "current_attempt": None,
            "outputs": [],
            "updated_at": utc_now(),
        }

    def update(self, **changes: Any) -> None:
        self.payload.update(changes)
        self.payload["updated_at"] = utc_now()
        if self.path is not None:
            atomic_json(self.path, self.payload)

    def output(self, *, name: str, target: str, cached: bool, size_bytes: int, sha256: str) -> None:
        outputs = self.payload.setdefault("outputs", [])
        if isinstance(outputs, list):
            outputs.append({
                "name": name,
                "target": target,
                "cached": cached,
                "size_bytes": size_bytes,
                "sha256": sha256,
            })
        self.update(completed_requests=int(self.payload.get("completed_requests", 0)) + 1)


def _install_signal_handlers() -> None:
    def handle_signal(signum: int, _frame: Any) -> None:
        raise DownloadCancelled(f"Received signal {signum}")

    for signal_name in ("SIGTERM", "SIGINT"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, handle_signal)


def _validate_request(name: str, request_def: Any) -> tuple[str, dict[str, Any], Any, str]:
    if not isinstance(request_def, dict):
        raise DownloadError(f"Request '{name}' must be an object.")
    dataset = request_def.get("dataset")
    request_body = request_def.get("request")
    target_name = request_def.get("target")
    prefix = request_def.get("ungrib_prefix", "FILE")
    if not dataset or not isinstance(request_body, dict) or not target_name:
        raise DownloadError(f"Request '{name}' must define 'dataset', 'request', and 'target'.")
    if not isinstance(prefix, str) or not prefix.strip():
        raise DownloadError(f"Request '{name}' must define a non-empty ungrib_prefix.")
    return str(dataset), request_body, target_name, prefix


def run(args: argparse.Namespace, reporter: ProgressReporter) -> int:
    if args.max_attempts < 1 or args.max_attempts > 10:
        raise DownloadError("--max-attempts must be between 1 and 10.")
    if args.retry_base_seconds < 0 or args.retry_base_seconds > 300:
        raise DownloadError("--retry-base-seconds must be between 0 and 300.")

    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config, config_text = load_config(config_path)
    manifest_path = Path(args.manifest).resolve() if args.manifest else output_dir / "era5-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    reporter.update(status="running")
    client = None
    outputs: list[dict[str, Any]] = []
    claimed_targets: set[Path] = set()

    for name, request_def in config["requests"].items():
        dataset, request_body, target_name, prefix = _validate_request(name, request_def)
        target_path = resolve_target(output_dir, target_name)
        if target_path in claimed_targets:
            raise DownloadError(f"Multiple ERA5 requests target the same file: {target_name!r}")
        claimed_targets.add(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(target_path.name + ".part")
        cached = target_path.is_file() and target_path.stat().st_size > 0
        reporter.update(current_request=name, current_attempt=0, status="verifying" if cached else "downloading")

        if not cached:
            if client is None:
                client = build_client()
            last_error: Exception | None = None
            for attempt in range(1, args.max_attempts + 1):
                reporter.update(current_request=name, current_attempt=attempt, status="downloading")
                if temp_path.exists() or temp_path.is_symlink():
                    temp_path.unlink()
                try:
                    client.retrieve(dataset, request_body, str(temp_path))
                    if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                        raise DownloadError(f"CDS download for request '{name}' produced an empty file.")
                    temp_path.replace(target_path)
                    last_error = None
                    break
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    last_error = exc
                    if temp_path.exists() or temp_path.is_symlink():
                        temp_path.unlink()
                    if attempt < args.max_attempts:
                        delay = args.retry_base_seconds * (2 ** (attempt - 1))
                        reporter.update(status="waiting_retry", current_attempt=attempt)
                        print(
                            f"ERA5 request '{name}' attempt {attempt} failed ({type(exc).__name__}); "
                            f"retrying after {delay:.1f} seconds.",
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(delay)
            if last_error is not None:
                raise DownloadError(
                    f"ERA5 request '{name}' failed after {args.max_attempts} attempts "
                    f"({type(last_error).__name__})."
                ) from last_error

        size_bytes = target_path.stat().st_size
        file_sha256 = sha256_file(target_path)
        output = {
            "name": name,
            "dataset": dataset,
            "target": str(target_path),
            "ungrib_prefix": prefix,
            "cached": cached,
            "size_bytes": size_bytes,
            "sha256": file_sha256,
            "request_sha256": sha256_text(json.dumps(request_body, sort_keys=True)),
        }
        outputs.append(output)
        reporter.output(
            name=name,
            target=str(target_name),
            cached=cached,
            size_bytes=size_bytes,
            sha256=file_sha256,
        )

    manifest = {
        "config": str(config_path),
        "config_sha256": sha256_text(config_text),
        "outputs": outputs,
    }
    atomic_json(manifest_path, manifest)
    reporter.update(
        status="succeeded",
        current_request=None,
        current_attempt=None,
        manifest_written=True,
    )
    print(f"Wrote ERA5 manifest to {manifest_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    progress_path = Path(args.progress).resolve() if args.progress else None
    try:
        config_path = Path(args.config).resolve()
        config, _ = load_config(config_path)
        total_requests = len(config["requests"])
    except DownloadError as exc:
        reporter = ProgressReporter(progress_path, 0)
        reporter.update(status="failed", error={"code": "invalid_configuration", "message": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1

    reporter = ProgressReporter(progress_path, total_requests)
    _install_signal_handlers()
    try:
        return run(args, reporter)
    except DownloadCancelled:
        reporter.update(status="cancelled", current_attempt=None)
        print("ERA5 download cancelled.", file=sys.stderr)
        return 130
    except DownloadError as exc:
        reporter.update(
            status="failed",
            error={"code": "download_failed", "message": str(exc)},
            current_attempt=None,
        )
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        reporter.update(
            status="failed",
            error={
                "code": "unexpected_download_error",
                "message": f"Unexpected ERA5 downloader error ({type(exc).__name__}).",
            },
            current_attempt=None,
        )
        print(f"Unexpected ERA5 downloader error ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
