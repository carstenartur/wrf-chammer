#!/usr/bin/env python3
"""Public simulation store with ERA5 dependency coordination."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

import workbench._simulation_store_dependency_core as _core
from workbench._simulation_store_dependency_core import *  # noqa: F401,F403
from workbench.era5_dependency_lock import ERA5_DEPENDENCY_LOCK

_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class SimulationStore(_core.SimulationStore):
    """Coordinate simulation creation with dependency-aware cache deletion."""

    def _require_input_dataset_available(self, specification: dict[str, Any]) -> None:
        identity = specification.get("identity")
        era5_input = identity.get("era5_input") if isinstance(identity, dict) else None
        plan_key = era5_input.get("plan_key") if isinstance(era5_input, dict) else None
        files = era5_input.get("files") if isinstance(era5_input, dict) else None
        data_service = getattr(self.specification_service, "data_service", None)
        plan_directory_getter = getattr(data_service, "plan_directory", None)
        # The production PipelineSpecificationService always exposes its
        # Era5DataService. Isolated persistence adapters used by unit tests may
        # intentionally provide only immutable specification objects; their
        # executor tests retain the independent input-byte verification.
        if not callable(plan_directory_getter):
            return
        if (
            not isinstance(plan_key, str)
            or not _PLAN_KEY_RE.fullmatch(plan_key)
            or not isinstance(files, list)
            or not files
        ):
            raise SimulationStoreError(
                "input_dataset_unavailable",
                "The immutable specification does not reference an available ERA5 input dataset.",
            )
        raw_directory = plan_directory_getter(plan_key)
        try:
            if raw_directory.is_symlink() or not raw_directory.is_dir():
                raise OSError("ERA5 plan directory is unavailable")
            plan_directory = raw_directory.resolve(strict=True)
        except OSError as exc:
            raise SimulationStoreError(
                "input_dataset_unavailable",
                "The verified ERA5 cache entry is no longer available.",
            ) from exc
        for sidecar in ("checksums.json", "provenance.json"):
            path = plan_directory / sidecar
            try:
                unavailable = path.is_symlink() or not path.is_file()
            except OSError:
                unavailable = True
            if unavailable:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The verified ERA5 cache metadata is incomplete.",
                )
        for entry in files:
            if not isinstance(entry, dict):
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file list is invalid.",
                )
            value = entry.get("path")
            if not isinstance(value, str) or "\\" in value or "\x00" in value:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file path is invalid.",
                )
            relative = PurePosixPath(value)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file path is invalid.",
                )
            expected_size = entry.get("size_bytes")
            try:
                target = (plan_directory / relative.as_posix()).resolve(strict=True)
                unavailable = (
                    plan_directory not in target.parents
                    or target.is_symlink()
                    or not target.is_file()
                )
                if (
                    not unavailable
                    and isinstance(expected_size, int)
                    and not isinstance(expected_size, bool)
                ):
                    unavailable = target.stat().st_size != expected_size
            except OSError:
                unavailable = True
            if unavailable:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "One or more verified ERA5 input files are no longer available.",
                )

    def create_job(
        self, specification_key: str, *, retry_of: str | None = None
    ) -> dict[str, Any]:
        with ERA5_DEPENDENCY_LOCK:
            try:
                specification = self.specification_service.get(specification_key)
            except Exception as exc:
                raise SimulationStoreError(
                    "specification_not_found",
                    "Immutable pipeline specification not found.",
                ) from exc
            self._require_input_dataset_available(specification)
            return super().create_job(specification_key, retry_of=retry_of)


__all__ = list(getattr(_core, "__all__", ()))
if "SimulationStore" not in __all__:
    __all__.append("SimulationStore")
