"""Trigger detectors.

ops-PR1 shipped O1 (no-progress watchdog). ops-PR3 adds O2 (no-steering
watchdog — running-but-drifting goals). Further triggers (O3 repeated
verdict pattern, etc.) stay deferred — see
~/memory/projects/devclaw/plan.md for the full taxonomy.
"""

from __future__ import annotations

from .no_progress import NoProgressDetector
from .no_steering import NoSteeringDetector

__all__ = ["NoProgressDetector", "NoSteeringDetector"]
