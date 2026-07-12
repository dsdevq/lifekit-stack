"""Unit tests for the agentic cognition entrypoint + usage-limit classification.

Hermetic: monkeypatch ``asyncio.create_subprocess_exec`` — never spawn a real
``claude``.
"""

from __future__ import annotations

import pytest

from ops_agent.cognition import (
    DEFAULT_ANSWER_TOOLS,
    CognitionCall,
    CognitionError,
    _build_agentic_argv,
    _looks_like_usage_limit,
    call_claude,
    call_claude_agentic,
)


class _FakeProc:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def fake_subprocess(monkeypatch):
    state: dict = {"calls": []}

    def _build(proc: _FakeProc):
        async def _fake_exec(*argv, stdout=None, stderr=None, env=None, cwd=None):
            state["calls"].append({"argv": list(argv), "env": env, "cwd": cwd})
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
        return state

    return _build


# ---- _build_agentic_argv -----------------------------------------------


def test_agentic_argv_includes_add_dir_tools_and_turns() -> None:
    argv = _build_agentic_argv(
        "claude", "prompt", "sonnet", add_dir="/repo", allowed_tools=("Read", "Grep"), max_turns=8
    )
    assert argv[0] == "claude"
    assert "--print" in argv
    assert "--add-dir" in argv and "/repo" in argv
    assert "--allowedTools" in argv
    # tools joined into one arg.
    idx = argv.index("--allowedTools")
    assert argv[idx + 1] == "Read Grep"
    assert "--max-turns" in argv and "8" in argv
    # prompt is last.
    assert argv[-1] == "prompt"


def test_agentic_argv_omits_model_when_none() -> None:
    argv = _build_agentic_argv(
        "claude", "p", None, add_dir="/r", allowed_tools=(), max_turns=4
    )
    assert "--model" not in argv
    # empty allowlist → no --allowedTools flag.
    assert "--allowedTools" not in argv


def test_default_answer_tools_are_read_only() -> None:
    # No Write/Edit in the default allowlist — read-only floor.
    joined = " ".join(DEFAULT_ANSWER_TOOLS)
    assert "Write" not in joined
    assert "Edit" not in joined
    assert "Read" in joined


# ---- call_claude_agentic ------------------------------------------------


@pytest.mark.asyncio
async def test_agentic_call_passes_cwd_and_returns_stdout(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b'{"action": "noop"}', returncode=0)
    state = fake_subprocess(proc)

    result = await call_claude_agentic("p", cwd="/data/workspaces/g")

    assert isinstance(result, CognitionCall)
    assert result.stdout == '{"action": "noop"}'
    assert state["calls"][0]["cwd"] == "/data/workspaces/g"


@pytest.mark.asyncio
async def test_agentic_call_scrubs_api_key(monkeypatch, fake_subprocess) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
    proc = _FakeProc(stdout=b"ok", returncode=0)
    state = fake_subprocess(proc)
    await call_claude_agentic("p", cwd="/r")
    assert "ANTHROPIC_API_KEY" not in state["calls"][0]["env"]


@pytest.mark.asyncio
async def test_agentic_non_zero_exit_raises(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b"boom", stderr=b"stack", returncode=1)
    fake_subprocess(proc)
    with pytest.raises(CognitionError) as exc_info:
        await call_claude_agentic("p", cwd="/r")
    assert exc_info.value.reason == "non_zero_exit"


# ---- usage-limit classification ----------------------------------------


def test_looks_like_usage_limit_matches_quota_phrasing() -> None:
    assert _looks_like_usage_limit("Claude usage limit reached; resets at 3pm")
    assert _looks_like_usage_limit("", "429 too many requests")
    assert not _looks_like_usage_limit("plain old crash", "traceback")


@pytest.mark.asyncio
async def test_call_claude_tags_usage_limit_on_quota_message(fake_subprocess) -> None:
    # Quota message on stdout, empty stderr, non-zero exit — the exact trap.
    proc = _FakeProc(stdout=b"Claude usage limit reached. resets at 5pm", stderr=b"", returncode=1)
    fake_subprocess(proc)
    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p")
    assert exc_info.value.reason == "usage_limit"


@pytest.mark.asyncio
async def test_call_claude_plain_crash_stays_non_zero_exit(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b"segfault", stderr=b"core dumped", returncode=139)
    fake_subprocess(proc)
    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p")
    assert exc_info.value.reason == "non_zero_exit"
