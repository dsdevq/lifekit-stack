"""Trigger detectors.

ops-PR1 shipped O1 (no-progress watchdog). ops-PR3 added O2 (no-steering
watchdog — running-but-drifting goals). ops-PR4 adds O3 (verifying-stall
watchdog — goals stuck in transient phases past a threshold). Further
triggers stay deferred — see ~/memory/projects/devclaw/plan.md for the
full taxonomy.
"""

from __future__ import annotations

from .no_progress import NoProgressDetector
from .no_steering import NoSteeringDetector
from .verifying_stall import VerifyingStallDetector

__all__ = ["NoProgressDetector", "NoSteeringDetector", "VerifyingStallDetector"]
