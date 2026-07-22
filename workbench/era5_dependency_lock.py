#!/usr/bin/env python3
"""Process-local lock coordinating ERA5 cache dependencies.

The Workbench API is a threaded local server. Simulation creation and ERA5 cache
deletion must not pass each other between dependency snapshot validation and
state mutation. The lock deliberately covers only this small local critical
section; SQLite remains the authoritative simulation state.
"""

from __future__ import annotations

import threading

ERA5_DEPENDENCY_LOCK = threading.RLock()

__all__ = ["ERA5_DEPENDENCY_LOCK"]
