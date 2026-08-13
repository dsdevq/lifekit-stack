---
name: morning-brief
description: Daily cross-project morning brief delivered to Telegram - what shipped, what's in flight, what needs Denys, backlog head - across lifekit-hq/devclaw, lifekit-hq/lifekit-dashboard, dsdevq/finance-sentry plus live devclaw goal state, ending with numbered recommendations Denys can select by replying (e.g. "1 and 3"); selected items get dispatched to devclaw via the devclaw MCP. Fires from the morning-brief cron job; also triggers when Denys asks for a morning brief / status across projects, or replies to a brief with a selection.
metadata:
  {
    "openclaw": { "requires": { "bins": ["gh"] } },
  }
---

# morning-brief — the daily "where are we", answerable by reply

Denys starts most days with the same questions: *what shipped, what's in flight,
what's waiting on me, what should we do next?* This skill answers them in one
deterministic pass over canonical sources and turns the answer into work: the
brief ends with numbered recommendations, Denys replies with the numbers he
wants, and those get dispatched to devclaw (companion mode: **scope stays
human, devclaw executes**).

Two modes, decided by context:

- **BRIEF mode** — the cron message (or Denys asking for a brief): produce and
  deliver the brief. Steps 1–5.
- **DISPATCH mode** — Denys replying to a brief with a selection ("1 and 3",
  "do 2", "first one"): read the persisted brief, dispatch exactly the selected
  items. Step 6.

## Canonical sources — never re-derive these from git archaeology

| Question | Source |
|---|---|
| What shipped by devclaw? | `gh pr list --repo <r> --state merged --search "\"Delivered by devclaw\" in:body merged:>=<date-7d>"` — every devclaw-delivered PR body carries the literal signature `Delivered by devclaw`; union in the same query with `label:devclaw` in place of the `in:body` clause (dedupe by number). A merged PR matching neither was merged by a human, NOT shipped by devclaw. |
| Total merged (context) | `gh pr list --repo <r> --state merged --search "merged:>=<date-7d>"` — used only for the "plus <N> merged by others" line |
| What's in flight? | `gh pr list --repo <r> --state open` |
| What's in backlog? | `gh issue list --repo <r> --state open`, ordered P0 → P1 → P2 |
| Trajectory | `PLAN.md` on main (product repos); `docs/proposals/` status lines (devclaw) |
| What needs Denys live? | devclaw MCP `list_goals` — needs-you bucket only |

The repo set (extend when a new repo becomes active):
`lifekit-hq/devclaw` · `lifekit-hq/lifekit-dashboard` · `dsdevq/finance-sentry`
(finance-sentry lives under **dsdevq**, not lifekit-hq — the wrong org here was
the recurring "finance-sentry unreachable" diagnostic).

Backlog convention (2026-08-12): **issues = not-yet-started work; PLAN.md
milestones = shipped trajectory.** An issue graduates to a PLAN.md milestone
line when its PR merges.

## BRIEF mode

1. **Repo sweep.** For each repo run the `gh` calls above (`--json` output;
   `gh` is installed and authenticated in the gateway). A failed call = name
   the gap in the brief ("finance-sentry unreachable"); never fill it with
   guesses.

2. **Live devclaw headline.** Call `list_goals` (devclaw MCP). Extract only
   goals with `blocked_on` set, `progress.stalled` true, or a stop-state
   direction verdict — one line each, with the verb that clears it
   (`answer_unknowns` / `steer_goal` / `resume_goal`). Name the verb; never
   run it.

3. **Compose the brief** (format below). Attention-first: needs-you at the
   top, shipped last. A calm day reads calm — never manufacture urgency.

4. **Recommend 2–4 next actions.** Every recommendation MUST be:
   - **traceable** — grounded in a named open issue, open PR, PLAN.md
     destination line, or blocked goal. Fewer honest recommendations beat
     four padded ones; never invent work to fill the menu.
   - **PR-shaped** — one coherent PR with 1–3 concrete acceptance criteria
     (these become the dispatch instructions verbatim).
   - **ordered** — P0/unblock actions first, then P1 (active thrust), then P2.
   - **marked** — `[dispatchable]` if devclaw can execute it, `[you]` if it's
     a Denys-action (review/merge a PR, steer a goal, answer unknowns).
   Don't re-pitch an item Denys declined on recent consecutive briefs as #1 —
   demote it (check the previous brief files).

5. **Persist, then deliver.** Write the full brief to
   `briefs/latest.md` in this agent's workspace AND a dated copy
   `briefs/YYYY-MM-DD.md` (the dispatch turn runs in a different session —
   the file is the handoff; the date comes from `date -u`). Then output the
   brief as your final message — the cron job announces it to Telegram.

### Brief format

```
Morning brief — <YYYY-MM-DD>

⚠ Needs you (<n>)
- [devclaw live] <goal> — BLOCKED: <why> → <verb>
- [<repo>] PR #<n> "<title>" — open <n>d, awaiting review

In flight (<n>)
- [<repo>] PR #<n> "<title>" — open <n>d

Shipped by devclaw (<n>)
- [<repo>] #<n> <title>
plus <N> merged by others   (total merged minus devclaw-shipped; omit when 0)

Backlog head
- [<repo>] P1: #<n> <title>   (top items only, never the full list)

Recommendations — reply with numbers to dispatch (e.g. "1 and 3")
1. [dispatchable] <action> — <source: issue #N / PLAN.md line> · 1 PR
   done when: <acceptance criteria, 1–3 bullets>
2. [you] review PR #<n> …
```

Keep it Telegram-sized: trim titles, relative times ("2d ago"), no tables.
If nothing needs Denys, say so first and plainly — that is the most valuable
possible answer.

## DISPATCH mode (step 6)

Denys replied with a selection. Then:

1. Read `briefs/latest.md` from this agent's workspace. If it's missing or
   older than 48 h, say so and stop — never dispatch from memory of a brief
   you can't read.
2. Map the reply to recommendation numbers. Ambiguous ("yes", "ok") → ask
   which numbers; never assume "all".
3. For each selected `[dispatchable]` item, dispatch via the devclaw MCP:
   `dispatch_task` for a scoped single-PR change (the default), or
   `create_goal(mode=one_shot)` only when the brief explicitly shaped it as a
   multi-task program. The task instructions carry the recommendation's
   acceptance criteria verbatim and reference the issue — include
   `Closes #<n>` so the PR closes it.
4. For selected `[you]` items, restate the exact command/verb — do not run it.
5. Reply with one receipt line per dispatch: task id + repo + the issue it
   closes. **Never dispatch anything unselected.** No selection = the brief
   was the deliverable.

## Hard rules

- **Read-only except dispatch.** This skill never steers, resumes, or cancels
  existing goals, never merges PRs, never closes issues by hand.
- **Fail loud.** Missing MCP tools → say "devclaw MCP not reachable — live
  state and dispatch unavailable" and deliver the repo-only brief. Never
  fabricate status; an error named is a good brief, an invented status is a
  broken one.
- **Scope stays human.** Recommendations are a menu, not a queue. Unselected
  items just remain in backlog.
