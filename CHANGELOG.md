# Changelog

## Unreleased

### Added
- `config/modules.yaml` — routing manifest describing each wired capability module (id, description, MCP URL, example intents). Copied to `/srv/life/system/modules.yaml` on every deploy so OpenClaw can read it for routing decisions.
- `skills/lifekit-router/SKILL.md` — system skill that tells OpenClaw to read `modules.yaml` at session start and route intents to the right module.
- `compose/devclaw-mcp/` — new `devclaw-mcp` service exposing the devclaw orchestrator as a streamable-http MCP server on :8000. Register via `compose/devclaw-mcp/mcp-config.json` in `openclaw.json` to give openclaw-gateway full conversational access to devclaw tasks.

### Changed
- `compose/curator/Dockerfile` — curator container now installs `lifekit` from source (`pip install lifekit @ git+...`) and runs `lifekit curator daemon` instead of vendoring `curator.py`. Logic lives in the framework.
- `compose/docker-compose.yml` — curator `LIFEKIT_LIFE_DIR` env var replaced with `LIFEKIT_ROOT` to align with lifekit's canonical path abstraction.

### Removed
- `compose/curator/curator.py` — moved to `dsdevq/lifekit` (`src/lifekit/curator/`).
- `compose/curator/test_curator.py` — moved to `dsdevq/lifekit` (`tests/test_curator.py`).
