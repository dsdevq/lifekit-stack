"""Incident classifiers — heuristics that classify an O2 incident before the
playbook layer picks between L2 (steer the goal) and L3 (open a fix-PR
against devclaw itself).

An incident classifier is a pure-ish function: given a ``goal_id`` and the
``goals_dir`` root, sniff the goal's on-disk state (STATUS.md frontmatter,
inbox.md tail, log.md tail) for known signatures. If the signature is a
known devclaw-side defect (eval truncation, phantom verdict, planner-loop,
model-spec drift, etc.), return a match with a *named* signature + evidence
excerpts. Otherwise return None and let the default L2 path run.

Classifiers can read the filesystem — they're a boundary between the
mechanism layer (detectors, playbooks) and disk-realized truth (the goal
folder). They MUST NOT call MCP, spawn subprocesses, or write anywhere.
"""

from __future__ import annotations

from .devclaw_defect import DevclawDefectMatch, classify_devclaw_defect

__all__ = ["DevclawDefectMatch", "classify_devclaw_defect"]
