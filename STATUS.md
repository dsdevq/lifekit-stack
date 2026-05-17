# STATUS

Where each architecture phase stands today. Phases are defined in `~/.life/system/autonomous-overnight-architecture.md` §7 ("The ramp"). This file is the at-a-glance index; the source of truth is the architecture doc.

Legend: **Live** = shipped and in production use. **Deferred** = designed but not yet built; will land when its prerequisites and real friction appear.

## Phase 0 — sync only — Live

The baseline: `~/.life/` on the VPS, SSHFS-mounted from the laptop, plain Markdown + YAML + JSONL. No autonomous behavior, just a portable knowledge layer Kit and Denys both read and append to.

## Phase 3.5 — `lifekit-curator` extraction — Live

The curator runs as a peer container that drains `~/.life/queue.jsonl` every ~15 min and is the only process allowed to mutate `~/.life/domains/`. Domain edits land as git commits on `~/.life/` so any bad write is one `git revert` away.

## Phase 5 — MVP intake + dispatch (research/draft/chore) — Live

Intake, Dispatch, and `task_update` skills plus the `~/.life/tasks/<id>/` substrate are wired end-to-end. The dispatch cron picks up `status: ready` specs overnight and spawns sub-agents for `kind: research|draft|chore` via `sessions_spawn`. Morning brief surfaces blocked or in-flight tasks.

## Phase 5.5 — lightweight `kind: code` via sub-agent + skill — Live

The `code-task` workspace skill lets a sub-agent clone the target repo into `/tmp/<task_id>/`, work on a fresh branch, run tests, push, and open a PR — all under a hard `budget.max_runtime_seconds` cap. Good for small features, bug fixes, refactors, doc updates, and dependency bumps finishable in <4h with at most one round of replan. This file was created by that runner.

## Phase 6 — swarm wiring (heavy `kind: code` runner) — Deferred

For work Phase 5.5 can't reach: multi-day tasks, multi-cycle Planner/Generator/Evaluator critique, durable resume across container restarts. Lands as a `BuildEngine` adapter that HTTP-POSTs to the existing `swarm-langgraph.service`; the dispatch skill grows a `code-heavy` branch. Blocked behind swarm's internal migration from `claude-agent-sdk` to Codex CLI (required by June 2026 per Anthropic policy).

## Phase 7+ — opportunistic extensions — Deferred

Heartbeat-driven self-scheduling of unblocked tasks, task chaining / DAGs, per-task model overrides beyond role defaults, a multi-agent OpenClaw split, and cross-device PC Kit dispatch. All deliberately unbuilt — each lands only when a concrete recurring friction makes its absence painful.
