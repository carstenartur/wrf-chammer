#!/usr/bin/env python3
"""Versioned, path-safe run manifests for persistent WRF simulations."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

FORMAT_NAME = "wrf-chammer-run-manifest"
FORMAT_VERSION = 1
_SPEC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]" )
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "secret",
    "token",
)
_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class SimulationRunManifestError(RuntimeError):
    """A classified, user-safe run-manifest error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None


def _elapsed_seconds(start: Any, end: Any) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    elapsed = (finished - started).total_seconds()
    return elapsed if elapsed >= 0 else None


class SimulationRunManifestService:
    """Build deterministic manifests from immutable specifications and persisted state."""

    def __init__(self, repo_root: Path, simulation_store: Any):
        self.repo_root = repo_root.resolve()
        self.simulation_store = simulation_store
        self.specification_service = getattr(simulation_store, "specification_service", None)

    def manifest(self, job_id: str) -> dict[str, Any]:
        job = self.simulation_store.get_job(job_id)
        specification_key = job.get("specification_key")
        if not isinstance(specification_key, str) or not _SPEC_KEY_RE.fullmatch(
            specification_key
        ):
            raise SimulationRunManifestError(
                "run_manifest_integrity_error",
                "The simulation does not reference a valid immutable specification.",
            )

        specification = self._load_specification(specification_key)
        resolved_namelists = self._resolved_namelists(specification_key)
        manifest: dict[str, Any] = {
            "format": {"name": FORMAT_NAME, "version": FORMAT_VERSION},
            "simulation": self._simulation_snapshot(job),
            "completeness": {
                "terminal": job.get("status") in _TERMINAL_STATUSES,
                "successful": job.get("status") == "SUCCEEDED",
                "scope": "persistent-state-snapshot",
            },
            "immutable_specification": self._safe_copy(specification),
            "resolved_namelists": resolved_namelists,
            "input_datasets": self._safe_copy(job.get("input_datasets", [])),
            "runtime_snapshots": self._safe_copy(job.get("runtime_snapshots", [])),
            "steps": self._safe_copy(job.get("steps", [])),
            "artifacts": self._safe_copy(job.get("artifacts", [])),
            "resource_measurements": self._safe_copy(
                job.get("resource_measurements", [])
            ),
            "resource_report": self._resource_report(job),
        }
        manifest["integrity"] = {
            "algorithm": "sha256",
            "canonical_payload_sha256": hashlib.sha256(
                _canonical_json(manifest)
            ).hexdigest(),
        }
        return manifest

    def _load_specification(self, specification_key: str) -> dict[str, Any]:
        getter = getattr(self.specification_service, "get", None)
        if not callable(getter):
            raise SimulationRunManifestError(
                "run_manifest_unavailable",
                "The immutable specification service is unavailable.",
            )
        try:
            specification = getter(specification_key)
        except Exception as exc:
            raise SimulationRunManifestError(
                "run_manifest_integrity_error",
                "The immutable simulation specification could not be verified.",
            ) from exc
        if (
            not isinstance(specification, dict)
            or specification.get("specification_key") != specification_key
            or specification.get("immutable") is not True
        ):
            raise SimulationRunManifestError(
                "run_manifest_integrity_error",
                "The immutable simulation specification failed manifest validation.",
            )
        return specification

    def _resolved_namelists(self, specification_key: str) -> dict[str, Any]:
        root_value = getattr(self.specification_service, "root", None)
        if not isinstance(root_value, Path):
            raise SimulationRunManifestError(
                "run_manifest_unavailable",
                "The immutable specification artifact root is unavailable.",
            )
        try:
            root = root_value.resolve(strict=True)
            raw_directory = root / specification_key
            if raw_directory.is_symlink() or not raw_directory.is_dir():
                raise OSError("specification directory unavailable")
            directory = raw_directory.resolve(strict=True)
            if directory.parent != root:
                raise OSError("specification directory escaped its root")
        except OSError as exc:
            raise SimulationRunManifestError(
                "run_manifest_integrity_error",
                "The immutable specification artifact directory is unavailable.",
            ) from exc

        result: dict[str, Any] = {}
        for filename, key in (
            ("namelist.wps", "namelist_wps"),
            ("namelist.input", "namelist_input"),
        ):
            path = directory / filename
            try:
                if path.is_symlink() or not path.is_file():
                    raise OSError(f"{filename} unavailable")
                resolved = path.resolve(strict=True)
                if resolved.parent != directory:
                    raise OSError(f"{filename} escaped specification directory")
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise SimulationRunManifestError(
                    "run_manifest_integrity_error",
                    f"The resolved {filename} artifact could not be verified.",
                ) from exc
            encoded = content.encode("utf-8")
            result[key] = {
                "filename": filename,
                "content": content,
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        return result

    def _simulation_snapshot(self, job: dict[str, Any]) -> dict[str, Any]:
        return self._safe_copy(
            {
                "id": job.get("id"),
                "specification_key": job.get("specification_key"),
                "retry_of": job.get("retry_of"),
                "status": job.get("status"),
                "created_at": job.get("created_at"),
                "queued_at": job.get("queued_at"),
                "started_at": job.get("started_at"),
                "finished_at": job.get("finished_at"),
                "current_step_id": job.get("current_step_id"),
                "error": job.get("error"),
            }
        )

    def _resource_report(self, job: dict[str, Any]) -> dict[str, Any]:
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        measurements = (
            job.get("resource_measurements")
            if isinstance(job.get("resource_measurements"), list)
            else []
        )
        artifacts = (
            job.get("artifacts") if isinstance(job.get("artifacts"), list) else []
        )
        input_datasets = (
            job.get("input_datasets")
            if isinstance(job.get("input_datasets"), list)
            else []
        )

        ordered_step_ids = [
            step.get("id")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("id"), str)
        ]
        buckets = {
            step_id: self._empty_resource_bucket(step_id) for step_id in ordered_step_ids
        }
        buckets["job"] = self._empty_resource_bucket(None)
        total = self._empty_resource_bucket(None)

        for measurement in measurements:
            if not isinstance(measurement, dict):
                continue
            step_id = measurement.get("step_id")
            bucket_key = step_id if step_id in buckets else "job"
            self._add_measurement(buckets[bucket_key], measurement)
            self._add_measurement(total, measurement)

        artifact_size = 0
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            size = artifact.get("size_bytes")
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                artifact_size += size
                step_id = artifact.get("step_id")
                bucket_key = step_id if step_id in buckets else "job"
                buckets[bucket_key]["artifact_size_bytes"] += size
        total["artifact_size_bytes"] = artifact_size

        input_size = 0
        input_file_count = 0
        for dataset in input_datasets:
            files = dataset.get("files") if isinstance(dataset, dict) else None
            if not isinstance(files, list):
                continue
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                size = entry.get("size_bytes")
                if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                    input_size += size
                input_file_count += 1

        report_steps = [buckets[step_id] for step_id in ordered_step_ids]
        if buckets["job"]["measurement_count"] or buckets["job"][
            "artifact_size_bytes"
        ]:
            report_steps.append(buckets["job"])
        return self._safe_copy(
            {
                "elapsed_wall_seconds": _elapsed_seconds(
                    job.get("started_at"), job.get("finished_at")
                ),
                "input_file_count": input_file_count,
                "input_size_bytes": input_size,
                "artifact_count": len(artifacts),
                "artifact_size_bytes": artifact_size,
                "measurement_count": total["measurement_count"],
                "cpu_seconds_sum": total["cpu_seconds_sum"],
                "wall_seconds_sum": total["wall_seconds_sum"],
                "max_rss_bytes": total["max_rss_bytes"],
                "max_reported_disk_bytes": total["max_reported_disk_bytes"],
                "by_step": report_steps,
            }
        )

    @staticmethod
    def _empty_resource_bucket(step_id: str | None) -> dict[str, Any]:
        return {
            "step_id": step_id,
            "measurement_count": 0,
            "cpu_seconds_sum": 0.0,
            "wall_seconds_sum": 0.0,
            "max_rss_bytes": None,
            "max_reported_disk_bytes": None,
            "artifact_size_bytes": 0,
        }

    @staticmethod
    def _add_measurement(bucket: dict[str, Any], measurement: dict[str, Any]) -> None:
        bucket["measurement_count"] += 1
        for field, target in (
            ("cpu_seconds", "cpu_seconds_sum"),
            ("wall_seconds", "wall_seconds_sum"),
        ):
            number = _nonnegative_number(measurement.get(field))
            if number is not None:
                bucket[target] += number
        for field, target in (
            ("max_rss_bytes", "max_rss_bytes"),
            ("disk_bytes", "max_reported_disk_bytes"),
        ):
            number = _nonnegative_number(measurement.get(field))
            if number is not None:
                current = bucket[target]
                bucket[target] = number if current is None else max(current, number)

    def _safe_copy(self, value: Any, *, field_name: str = "") -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                lowered = key.lower()
                if any(part in lowered for part in _SECRET_KEY_PARTS):
                    result[key] = "[redacted]"
                else:
                    result[key] = self._safe_copy(item, field_name=key)
            return result
        if isinstance(value, (list, tuple)):
            return [self._safe_copy(item, field_name=field_name) for item in value]
        if isinstance(value, Path):
            return "[redacted:absolute-path]"
        if isinstance(value, str):
            if "\x00" in value:
                raise SimulationRunManifestError(
                    "run_manifest_integrity_error",
                    "The persisted simulation contains an invalid string value.",
                )
            if "\n" not in value and self._looks_like_absolute_path(value):
                return "[redacted:absolute-path]"
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise SimulationRunManifestError(
            "run_manifest_integrity_error",
            f"The persisted simulation contains an unsupported {field_name or 'value'} type.",
        )

    @staticmethod
    def _looks_like_absolute_path(value: str) -> bool:
        text = value.strip()
        if not text or "://" in text:
            return False
        return text.startswith(("/", "\\")) or bool(_WINDOWS_ABSOLUTE_RE.match(text))


__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "SimulationRunManifestError",
    "SimulationRunManifestService",
]
