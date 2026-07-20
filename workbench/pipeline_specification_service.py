#!/usr/bin/env python3
"""Persistence and server-side creation of immutable real pipeline specifications."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.era5_service import Era5DataService, Era5DataServiceError
from workbench.pipeline_specification import (
    PIPELINE_PROFILES,
    PipelineSpecificationError,
    build_run_specification_identity,
    canonical_json,
    sha256_value,
)

_SPEC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class PipelineSpecificationServiceError(RuntimeError):
    """A safe, user-facing specification service error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_json(path: Path, code: str, message: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineSpecificationServiceError(code, message) from exc
    if not isinstance(value, dict):
        raise PipelineSpecificationServiceError(code, message)
    return value


class PipelineSpecificationService:
    """Freeze real-run inputs and configuration before worker execution."""

    def __init__(
        self,
        repo_root: Path,
        data_service: Era5DataService,
        *,
        specification_root: Path | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.data_service = data_service
        self.root = (
            specification_root
            or self.repo_root / "workbench-runs" / "specifications"
        ).resolve()

    def profiles(self) -> dict[str, Any]:
        return {
            profile_id: {
                "id": profile_id,
                "label": profile["label"],
                "max_grid_points": profile["max_grid_points"],
                "e_vert": profile["e_vert"],
                "history_interval_minutes": profile["history_interval_minutes"],
                "postprocessing_profile": profile["postprocessing_profile"],
            }
            for profile_id, profile in PIPELINE_PROFILES.items()
        }

    def readiness(self) -> dict[str, Any]:
        runtime, errors = self._runtime_identities(raise_on_missing=False)
        source_revision, source_error = self._source_revision(raise_on_missing=False)
        if source_error:
            errors.append(source_error)
        return {
            "ok": True,
            "ready": not errors,
            "errors": errors,
            "runtime": runtime,
            "source_revision": source_revision,
            "profiles": self.profiles(),
        }

    def create(
        self,
        request: dict[str, Any],
        latest_preview: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise PipelineSpecificationServiceError(
                "invalid_specification_request",
                "Pipeline specification request must be a JSON object.",
            )
        plan_key = request.get("plan_key")
        profile_id = request.get("profile", "small-real-data-demo")
        if not isinstance(plan_key, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_key):
            raise PipelineSpecificationServiceError(
                "invalid_plan_key", "A valid prepared ERA5 plan key is required."
            )
        if not isinstance(profile_id, str) or profile_id not in PIPELINE_PROFILES:
            raise PipelineSpecificationServiceError(
                "invalid_pipeline_profile", "Choose a supported real pipeline profile."
            )
        job = self.data_service.require_preview(latest_preview)
        try:
            plan = self.data_service.load_prepared_plan(plan_key, persist_refresh=True)
        except Era5DataServiceError as exc:
            raise PipelineSpecificationServiceError(
                "era5_plan_unavailable", exc.message
            ) from exc
        plan_directory = self.data_service.plan_directory(plan_key)
        checksums = _load_json(
            plan_directory / "checksums.json",
            "era5_checksums_unavailable",
            "Verified ERA5 checksums are required before freezing a real run.",
        )
        provenance = _load_json(
            plan_directory / "provenance.json",
            "era5_provenance_unavailable",
            "Verified ERA5 provenance is required before freezing a real run.",
        )
        runtime, _errors = self._runtime_identities(raise_on_missing=True)
        source_revision, _source_error = self._source_revision(raise_on_missing=True)
        try:
            identity, namelists = build_run_specification_identity(
                job=job,
                era5_plan=plan,
                checksums=checksums,
                provenance=provenance,
                runtime=runtime,
                source_revision=source_revision,
                profile_id=profile_id,
            )
        except PipelineSpecificationError as exc:
            raise PipelineSpecificationServiceError(
                "pipeline_specification_invalid", "; ".join(exc.errors)
            ) from exc
        spec_key = sha256_value(identity)
        directory = self._specification_directory(spec_key)
        if directory.exists():
            existing = self.get(spec_key)
            if existing.get("identity") != identity:
                raise PipelineSpecificationServiceError(
                    "specification_identity_collision",
                    "An immutable specification identity collision was detected.",
                )
            return existing

        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return self.get(spec_key)
        specification = {
            "specification_key": spec_key,
            "created_at": _utc_now(),
            "immutable": True,
            "execution_started": False,
            "identity": identity,
            "artifacts": {
                "specification": f"specifications/{spec_key}/run-specification.json",
                "namelist_wps": f"specifications/{spec_key}/namelist.wps",
                "namelist_input": f"specifications/{spec_key}/namelist.input",
            },
        }
        try:
            _atomic_text(
                directory / "run-specification.json",
                json.dumps(specification, indent=2, sort_keys=True) + "\n",
            )
            _atomic_text(directory / "namelist.wps", namelists["namelist.wps"])
            _atomic_text(directory / "namelist.input", namelists["namelist.input"])
            _atomic_text(
                directory / "identity.sha256",
                spec_key + "  run-specification.identity\n",
            )
        except Exception:
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return specification

    def get(self, spec_key: str) -> dict[str, Any]:
        directory = self._specification_directory(spec_key)
        specification = _load_json(
            directory / "run-specification.json",
            "specification_not_found",
            "Immutable pipeline specification not found.",
        )
        identity = specification.get("identity")
        if not isinstance(identity, dict) or sha256_value(identity) != spec_key:
            raise PipelineSpecificationServiceError(
                "specification_integrity_error",
                "The immutable pipeline specification failed its identity check.",
            )
        for name in ("namelist.wps", "namelist.input", "identity.sha256"):
            path = directory / name
            if not path.is_file() or path.is_symlink():
                raise PipelineSpecificationServiceError(
                    "specification_integrity_error",
                    "The immutable pipeline specification is incomplete.",
                )
        return specification

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.is_symlink() or not _SPEC_KEY_RE.fullmatch(directory.name):
                continue
            try:
                specification = self.get(directory.name)
            except PipelineSpecificationServiceError:
                continue
            identity = specification["identity"]
            entries.append({
                "specification_key": specification["specification_key"],
                "created_at": specification["created_at"],
                "immutable": True,
                "job_id": identity.get("job", {}).get("id"),
                "profile": identity.get("profile", {}).get("id"),
                "plan_key": identity.get("era5_input", {}).get("plan_key"),
                "execution_started": bool(specification.get("execution_started")),
            })
        entries.sort(key=lambda entry: str(entry.get("created_at", "")), reverse=True)
        return entries

    def _runtime_identities(
        self, *, raise_on_missing: bool
    ) -> tuple[dict[str, Any], list[str]]:
        runtime: dict[str, Any] = {}
        errors: list[str] = []
        defaults = {
            "wps": "wps-reproducible:latest",
            "wrf": "wrf-reproducible:latest",
            "postprocessing": "wrf-chammer-postprocess:latest",
        }
        for name, default_reference in defaults.items():
            prefix = f"WRF_CHAMMER_{name.upper()}_RUNTIME"
            reference = os.environ.get(f"{prefix}_REFERENCE", default_reference)
            identity = os.environ.get(f"{prefix}_IDENTITY")
            if not identity:
                errors.append(
                    f"Set {prefix}_IDENTITY to a pinned sha256 runtime identity."
                )
            runtime[name] = {"reference": reference, "identity": identity}
        if errors and raise_on_missing:
            raise PipelineSpecificationServiceError(
                "runtime_identity_unavailable", " ".join(errors)
            )
        return runtime, errors

    def _source_revision(
        self, *, raise_on_missing: bool
    ) -> tuple[str | None, str | None]:
        configured = os.environ.get("WRF_CHAMMER_SOURCE_REVISION")
        if configured:
            revision = configured.strip().lower()
        else:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=True,
                )
                revision = result.stdout.strip().lower()
            except (OSError, subprocess.SubprocessError):
                revision = ""
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            message = "A pinned repository source revision is unavailable."
            if raise_on_missing:
                raise PipelineSpecificationServiceError(
                    "source_revision_unavailable", message
                )
            return None, message
        return revision, None

    def _specification_directory(self, spec_key: str) -> Path:
        if not isinstance(spec_key, str) or not _SPEC_KEY_RE.fullmatch(spec_key):
            raise PipelineSpecificationServiceError(
                "specification_not_found", "Immutable pipeline specification not found."
            )
        root = self.root.resolve()
        directory = (root / spec_key).resolve()
        if directory.parent != root:
            raise PipelineSpecificationServiceError(
                "specification_not_found", "Immutable pipeline specification not found."
            )
        return directory
