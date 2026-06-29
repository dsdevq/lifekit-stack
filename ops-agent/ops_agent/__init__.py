"""ops-agent — resident watcher for devclaw incidents (ops-PR1 baseline).

Boundary contract (see ~/memory/projects/devclaw/plan.md, section
'Operations agent — close the "stuck → owner-fixes" loop'):

  * READ-ONLY against devclaw's substrates (goal STATUS.md, state_store.db).
  * NO writes to devclaw state, NO mutation of user repos.
  * ops-PR1 is L0-only — detect, persist incident, append to log. No Claude
    call, no MCP call, no auto-poke. Those land in ops-PR2+.
"""

from __future__ import annotations

__version__ = "0.1.0"
