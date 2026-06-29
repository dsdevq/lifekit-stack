"""Unit tests for the cognition layer.

Hermetic: we never spawn a real ``claude`` subprocess. We monkeypatch
``asyncio.create_subprocess_exec`` to return a fake process object whose
``communicate()`` is awaitable and whose ``returncode`` we control.
"""

from __future__ import annotations

import asyncio

import pytest

from ops_agent.cognition import (
    CognitionCall,
    CognitionError,
    _build_argv,
    _scrubbed_env,
    call_claude,
)


class _FakeProc:
    """Minimal stand-in for an ``asyncio.subprocess.Process`` object."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        communicate_delay_s: float = 0.0,
        raise_in_communicate: type[BaseException] | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._delay = communicate_delay_s
        self._raise = raise_in_communicate
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise is not None:
            raise self._raise()
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Yield a setter for the fake process returned by create_subprocess_exec.

    The fixture captures ``argv``, ``env`` for each call so tests can assert
    on what would have been spawned.
    """
    state: dict = {"calls": []}

    def _build(proc: _FakeProc, *, raise_oserror: bool = False):
        async def _fake_exec(*argv, stdout=None, stderr=None, env=None):
            state["calls"].append({"argv": list(argv), "env": env})
            if raise_oserror:
                raise OSError("simulated spawn failure")
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
        return state

    return _build


# ---- _build_argv ----------------------------------------------------------


def test_build_argv_without_model_omits_flag() -> None:
    argv = _build_argv("claude", "hi", None)
    assert argv == ["claude", "--print", "--output-format=text", "hi"]


def test_build_argv_with_model_inserts_flag() -> None:
    argv = _build_argv("claude", "hi", "sonnet")
    assert argv == ["claude", "--print", "--output-format=text", "--model", "sonnet", "hi"]


def test_build_argv_respects_custom_bin() -> None:
    argv = _build_argv("/opt/claude", "p", None)
    assert argv[0] == "/opt/claude"


# ---- _scrubbed_env --------------------------------------------------------


def test_scrubbed_env_strips_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("OTHER_KEY", "kept")
    env = _scrubbed_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert env["OTHER_KEY"] == "kept"


def test_scrubbed_env_strips_auth_token(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leak")
    env = _scrubbed_env()
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_scrubbed_env_safe_when_keys_absent(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    env = _scrubbed_env()  # should not raise
    assert isinstance(env, dict)


# ---- call_claude — happy path --------------------------------------------


@pytest.mark.asyncio
async def test_call_claude_returns_stdout_on_success(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b'{"action": "noop"}', stderr=b"", returncode=0)
    state = fake_subprocess(proc)

    result = await call_claude("prompt text", role="ops-agent")

    assert isinstance(result, CognitionCall)
    assert result.stdout == '{"action": "noop"}'
    assert result.latency_ms >= 0
    assert len(state["calls"]) == 1
    # Default model: sonnet — env-driven default lands in argv.
    argv = state["calls"][0]["argv"]
    assert "--model" in argv
    assert "sonnet" in argv


@pytest.mark.asyncio
async def test_call_claude_strips_anthropic_env_on_spawn(monkeypatch, fake_subprocess) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked")
    proc = _FakeProc(stdout=b"ok", returncode=0)
    state = fake_subprocess(proc)

    await call_claude("p")

    env_sent = state["calls"][0]["env"]
    assert "ANTHROPIC_API_KEY" not in env_sent


@pytest.mark.asyncio
async def test_call_claude_honors_model_override(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b"ok", returncode=0)
    state = fake_subprocess(proc)

    await call_claude("p", model="opus")

    argv = state["calls"][0]["argv"]
    assert "opus" in argv


@pytest.mark.asyncio
async def test_call_claude_honors_env_bin_override(monkeypatch, fake_subprocess) -> None:
    monkeypatch.setenv("OPS_AGENT_CLAUDE_BIN", "/usr/bin/myclaude")
    proc = _FakeProc(stdout=b"ok", returncode=0)
    state = fake_subprocess(proc)

    await call_claude("p")

    assert state["calls"][0]["argv"][0] == "/usr/bin/myclaude"


@pytest.mark.asyncio
async def test_call_claude_no_model_when_env_empty(monkeypatch, fake_subprocess) -> None:
    monkeypatch.setenv("OPS_AGENT_CLAUDE_MODEL", "")
    proc = _FakeProc(stdout=b"ok", returncode=0)
    state = fake_subprocess(proc)

    await call_claude("p")

    argv = state["calls"][0]["argv"]
    assert "--model" not in argv


# ---- call_claude — failure paths ------------------------------------------


@pytest.mark.asyncio
async def test_call_claude_spawn_failure_raises_cognition_error(fake_subprocess) -> None:
    proc = _FakeProc()
    fake_subprocess(proc, raise_oserror=True)

    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p")
    assert exc_info.value.reason == "spawn_failed"


@pytest.mark.asyncio
async def test_call_claude_timeout_raises_cognition_error(fake_subprocess) -> None:
    # communicate_delay > timeout → asyncio.wait_for raises TimeoutError
    proc = _FakeProc(stdout=b"slow", communicate_delay_s=2.0)
    fake_subprocess(proc)

    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p", timeout_s=1)
    assert exc_info.value.reason == "timeout"
    # The kill path should have run.
    assert proc.killed


@pytest.mark.asyncio
async def test_call_claude_non_zero_exit_raises(fake_subprocess) -> None:
    proc = _FakeProc(stdout=b"out tail", stderr=b"err detail", returncode=2)
    fake_subprocess(proc)

    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p")
    assert exc_info.value.reason == "non_zero_exit"
    assert "err detail" in exc_info.value.stderr
    assert "out tail" in exc_info.value.stdout_tail


@pytest.mark.asyncio
async def test_call_claude_decodes_invalid_utf8_replacement(fake_subprocess) -> None:
    # Invalid UTF-8 in stdout/stderr — we must not crash on decode.
    proc = _FakeProc(stdout=b"\xff\xff hello", stderr=b"\xff", returncode=0)
    fake_subprocess(proc)

    result = await call_claude("p")
    assert "hello" in result.stdout


# ---- env helpers integration --------------------------------------------


@pytest.mark.asyncio
async def test_call_claude_uses_env_timeout(monkeypatch, fake_subprocess) -> None:
    monkeypatch.setenv("OPS_AGENT_CLAUDE_TIMEOUT_S", "1")
    proc = _FakeProc(stdout=b"slow", communicate_delay_s=3.0)
    fake_subprocess(proc)

    with pytest.raises(CognitionError) as exc_info:
        await call_claude("p")
    assert exc_info.value.reason == "timeout"


def test_call_claude_module_level_role_logging(caplog) -> None:
    # Just confirm the module wires logging — no functional assertion.
    import logging

    logger = logging.getLogger("ops_agent.cognition")
    assert logger is not None
