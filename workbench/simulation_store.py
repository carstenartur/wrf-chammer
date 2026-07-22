#!/usr/bin/env python3
"""Public simulation store with ERA5 dependency coordination."""

from __future__ import annotations

from typing import Any

import workbench._simulation_store_dependency_core as _core
from workbench._simulation_store_dependency_core import *  # noqa: F401,F403
from workbench.era5_dependency_lock import ERA5_DEPENDENCY_LOCK


class SimulationStore(_core.SimulationStore):
    """Coordinate simulation creation with dependency-aware cache deletion."""

    def create_job(
        self, specification_key: str, *, retry_of: str | None = None
    ) -> dict[str, Any]:
        with ERA5_DEPENDENCY_LOCK:
            return super().create_job(specification_key, retry_of=retry_of)


__all__ = list(getattr(_core, "__all__", ()))
if "SimulationStore" not in __all__:
    __all__.append("SimulationStore")
