#!/usr/bin/env python3
"""Validated public facade for persistent simulation-job state.

The internal SQLite adapter lives in ``_simulation_store_sqlite``. This module
validates immutable specification boundaries and cross-platform paths before
delegating to that adapter.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from workbench._simulation_store_sqlite import SCHEMA_VERSION, SimulationStoreError
from workbench._simulation_store_sqlite import SimulationStore as _SQLiteSimulationStore

_SPEC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXPECTED_STEP_IDS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)
_REQUIRED_RUNTIMES = ("wps", "wrf", "postprocessing")


class SimulationStore(_SQLiteSimulationStore):
    """SQLite store with a strictly validated immutable specification boundary."""

    def _load_specification(self, specification_key: str) -> dict[str, Any]:
        specification = super()._load_specification(specification_key)
        self._validate_specification(specification_key, specification)
        return specification

    @staticmethod
    def _validate_specification(
        expected_key: str, specification: Any
    ) -> None:
        if not isinstance(specification, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification is not a JSON object.",
            )
        if not _SPEC_KEY_RE.fullmatch(expected_key) or specification.get(
            "specification_key"
        ) != expected_key:
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification key does not match its content.",
            )
        if specification.get("immutable") is not True or specification.get(
            "execution_started"
        ) is not False:
            raise SimulationStoreError(
                "specification_integrity_error",
                "Simulation jobs require an immutable specification that has not started execution.",
            )

        identity = specification.get("identity")
        if not isinstance(identity, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no valid identity object.",
            )

        steps = identity.get("steps")
        if not isinstance(steps, list):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no valid step contracts.",
            )
        step_ids: list[str] = []
        for contract in steps:
            if not isinstance(contract, dict):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    "Every immutable pipeline step contract must be an object.",
                )
            step_id = contract.get("id")
            if not isinstance(step_id, str) or step_id not in _EXPECTED_STEP_IDS:
                raise SimulationStoreError(
                    "specification_integrity_error",
                    "The immutable pipeline specification contains an unknown step.",
                )
            step_ids.append(step_id)
        if tuple(step_ids) != _EXPECTED_STEP_IDS:
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification must contain all pipeline steps once and in order.",
            )

        era5_input = identity.get("era5_input")
        if not isinstance(era5_input, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no ERA5 input dataset.",
            )
        plan_key = era5_input.get("plan_key")
        if not isinstance(plan_key, str) or not _PLAN_KEY_RE.fullmatch(plan_key):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification contains an invalid ERA5 plan key.",
            )
        provenance = era5_input.get("provenance")
        files = era5_input.get("files")
        if (
            not isinstance(provenance, dict)
            or provenance.get("artificial_weather_data") is not False
            or not isinstance(files, list)
            or not files
        ):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification must reference verified real ERA5 files.",
            )

        runtime = identity.get("runtime")
        if not isinstance(runtime, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no runtime snapshots.",
            )
        for runtime_name in _REQUIRED_RUNTIMES:
            value = runtime.get(runtime_name)
            if not isinstance(value, dict):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The immutable pipeline specification has no {runtime_name} runtime snapshot.",
                )
            reference = value.get("reference")
            identity_value = value.get("identity")
            if not isinstance(reference, str) or not reference.strip():
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The {runtime_name} runtime reference is invalid.",
                )
            if not isinstance(identity_value, str) or not _RUNTIME_ID_RE.fullmatch(
                identity_value
            ):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The {runtime_name} runtime identity must be a pinned SHA-256 digest.",
                )

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SimulationStoreError(
                "invalid_artifact", "Artifact path must be non-empty."
            )
        text = value.strip()
        if "\x00" in text or "\\" in text:
            raise SimulationStoreError(
                "invalid_artifact",
                "Artifact path must use an unambiguous relative POSIX path.",
            )
        raw_parts = text.split("/")
        posix_path = PurePosixPath(text)
        windows_path = PureWindowsPath(text)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise SimulationStoreError(
                "invalid_artifact",
                "Artifact path must stay inside the simulation directory.",
            )
        return "/".join(raw_parts)


__all__ = ["SCHEMA_VERSION", "SimulationStore", "SimulationStoreError"]
