#!/usr/bin/env python3
"""Public result service with immutable-specification provenance validation."""

from __future__ import annotations

from typing import Any

import workbench._simulation_result_service_core as _core
from workbench._simulation_result_service_core import *  # noqa: F401,F403


class SimulationResultService(_core.SimulationResultService):
    """Checksum-indexed result access tied to the frozen pipeline identity."""

    def viewer_html(self, job_id: str) -> bytes:
        html = super().viewer_html(job_id).decode("utf-8")
        marker = "</body>"
        script = '<script src="/web/result-viewer-tools.js"></script>'
        if marker not in html or script in html:
            raise _core.SimulationResultError(
                "viewer_unavailable",
                "The integrated WRF result viewer cannot load its map tools.",
            )
        return html.replace(marker, f"{script}\n{marker}", 1).encode("utf-8")

    def _result_context(
        self, job_id: str
    ) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, Any]]:
        job, run_root, index, products = super()._result_context(job_id)
        specification_service = getattr(self.store, "specification_service", None)
        getter = getattr(specification_service, "get", None)
        if not callable(getter):
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The immutable pipeline specification service is unavailable.",
            )
        try:
            specification = getter(job["specification_key"])
        except Exception as exc:
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The immutable pipeline specification cannot be verified.",
            ) from exc
        identity = specification.get("identity") if isinstance(specification, dict) else None
        if not isinstance(identity, dict):
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The immutable pipeline specification identity is invalid.",
            )
        source = identity.get("source")
        era5_input = identity.get("era5_input")
        runtime = identity.get("runtime")
        expected_revision = (
            source.get("repository_revision") if isinstance(source, dict) else None
        )
        expected_plan = era5_input.get("plan_key") if isinstance(era5_input, dict) else None
        if not isinstance(expected_revision, str) or index.get("source_revision") != expected_revision:
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The result index repository revision does not match the immutable specification.",
            )
        if not isinstance(expected_plan, str) or index.get("era5_plan_key") != expected_plan:
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The result index ERA5 plan does not match the immutable specification.",
            )
        if not isinstance(runtime, dict) or index.get("runtime") != runtime:
            raise _core.SimulationResultError(
                "result_integrity_error",
                "The result index runtime identities do not match the immutable specification.",
            )
        return job, run_root, index, products


__all__ = list(getattr(_core, "__all__", ()))
if "SimulationResultService" not in __all__:
    __all__.append("SimulationResultService")
