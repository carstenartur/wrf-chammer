#!/usr/bin/env python3
"""Public simulation worker with atomic preflight/step event ordering."""

from __future__ import annotations

import sys
from typing import Any

import workbench._simulation_worker_preflight_core as _core
from workbench._simulation_worker_preflight_core import *  # noqa: F401,F403
from workbench.simulation_resources import evaluate_resource_admission


class SimulationWorker(_core.SimulationWorker):
    """Admission-aware worker that claims and records preflight atomically."""

    def run(self, *, once: bool = False) -> int:
        self.store.recover_interrupted_jobs()
        while not self._stopping.is_set():
            candidate = self.store.next_queued_job()
            if candidate is None:
                if once:
                    return 0
                self._stopping.wait(self.poll_seconds)
                continue

            specification = self.specification_service.get(
                candidate["specification_key"]
            )
            assessment = evaluate_resource_admission(
                specification, self.resource_provider()
            )
            if not assessment["admitted"]:
                self.store.add_resource_measurement(
                    candidate["id"],
                    step_id=None,
                    metadata={
                        "phase": "preflight",
                        "worker_id": self.worker_id,
                        "assessment": assessment,
                    },
                )
                self.store.reject_queued_job(
                    candidate["id"],
                    code="INSUFFICIENT_RESOURCES",
                    message=(
                        "The simulation cannot start because the host does not "
                        "meet the frozen minimum resource estimate."
                    ),
                    details={"assessment": assessment},
                )
                if once:
                    return 0
                continue

            job = self.store.claim_job(
                candidate["id"],
                self.worker_id,
                max_active_jobs=self.max_active_jobs,
                preflight=assessment,
            )
            if job is None:
                if once:
                    return 0
                self._stopping.wait(self.poll_seconds)
                continue

            self.store.add_resource_measurement(
                job["id"],
                step_id=job.get("current_step_id"),
                metadata={
                    "phase": "preflight",
                    "worker_id": self.worker_id,
                    "assessment": assessment,
                },
            )
            self.execute_job(job["id"])
            if once:
                return 0
        return 0


_core.SimulationWorker = SimulationWorker


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


__all__ = list(getattr(_core, "__all__", ()))
if "SimulationWorker" not in __all__:
    __all__.append("SimulationWorker")


if __name__ == "__main__":
    raise SystemExit(main())
