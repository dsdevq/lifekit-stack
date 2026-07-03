"""Trigger detectors.

ops-PR1 shipped O1 (no-progress watchdog). ops-PR3 added O2 (no-steering
watchdog — running-but-drifting goals). ops-PR4 adds O3 (verifying-stall
watchdog — goals stuck in transient phases past a threshold). O4 adds the
trend-signal-repeat consumer: fires when devclaw's own trend detector
retrospectively self-escalates a signal (≥N consecutive daily fires on
the same signal_id for one goal). Further triggers stay deferred — see
~/memory/projects/devclaw/plan.md for the full taxonomy.
"""

from __future__ import annotations

from .no_progress import NoProgressDetector
from .no_steering import NoSteeringDetector
from .trend_signal_repeat import TrendSignalRepeatDetector
from .verifying_stall import VerifyingStallDetector

__all__ = [
    "NoProgressDetector",
    "NoSteeringDetector",
    "TrendSignalRepeatDetector",
    "VerifyingStallDetector",
]
