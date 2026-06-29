"""Trigger detectors.

ops-PR1 ships O1 only (no-progress watchdog). O2 (repeated verdict pattern)
and O3 (stuck-in-phase) land in ops-PR2 and ops-PR3 respectively — see
~/memory/projects/devclaw/plan.md for the full taxonomy.
"""

from __future__ import annotations

from .no_progress import NoProgressDetector

__all__ = ["NoProgressDetector"]
