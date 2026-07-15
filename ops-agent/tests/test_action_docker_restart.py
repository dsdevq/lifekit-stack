"""Unit tests for the docker_restart action.

Hermetic: we monkeypatch ``asyncio.create_subprocess_exec`` with a
controllable fake so no real ``docker`` binary is invoked. Each fake
returns a stub process whose ``communicate()`` / ``returncode`` / ``kill``
the test can drive deterministically.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from ops_agent.actions import ActionOutcome, outcome_to_dict, perform_docker_restart

ALLOWED = "compose-devclaw-mcp-1"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Each test starts from a clean env so allowlist / timeout reads are deterministic."""
    monkeypatch.delenv("OPS_AGENT_DOCKER_RESTART_ALLOWLIST", raising=False)
    monkeypatch.delenv("OPS_AGENT_DOCKER_TIMEOUT_S", raising=False)


@dataclass
class _FakeProc:
    """Fake ``asyncio.subprocess.Process`` driven by the test."""

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0
    hang: bool = False  # if True, communicate() never resolves
    raise_on_communicate: Exception | None = None
    killed: bool = False
    waited: bool = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.raise_on_communicate is not None:
            raise self.raise_on_communicate
        if self.hang:
            # Hang forever — caller is expected to wait_for() this with a timeout.
            await asyncio.sleep(60)
        return (self.stdout, self.stderr)

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


@dataclass
class _Spawn:
    """Captures create_subprocess_exec calls + the proc(s) it should return."""

    procs: list[_FakeProc] = field(default_factory=list)
    raise_on_spawn: Exception | None = None
    calls: list[tuple] = field(default_factory=list)

    def make_patch(self):
        async def _create(*args, **kwargs):
            self.calls.append(args)
            if self.raise_on_spawn is not None:
                raise self.raise_on_spawn
            return self.procs.pop(0)

        return _create


# ---- happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_perform_docker_restart_ok(monkeypatch) -> None:
    spawn = _Spawn(procs=[_FakeProc(stdout=b"compose-devclaw-mcp-1\n", returncode=0)])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)

    assert outcome.action == "docker_restart"
    assert outcome.status == "ok"
    assert outcome.detail["service_name"] == ALLOWED
    assert outcome.detail["returncode"] == 0
    assert "compose-devclaw-mcp-1" in outcome.detail["stdout"]
    assert outcome.error_reason is None
    # Subprocess invoked with exactly the safe argv shape — no shell.
    assert spawn.calls[0] == ("docker", "restart", ALLOWED)


@pytest.mark.asyncio
async def test_perform_docker_restart_accepts_mcp_kwarg_for_dispatch_uniformity(
    monkeypatch,
) -> None:
    """``mcp`` is documented as accepted-but-unused. Verify it doesn't error."""
    spawn = _Spawn(procs=[_FakeProc(returncode=0)])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED, mcp=object())
    assert outcome.status == "ok"


# ---- input validation --------------------------------------------------


@pytest.mark.asyncio
async def test_empty_service_name_is_rejected(monkeypatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart("")
    assert outcome.status == "failed"
    assert outcome.error_reason == "invalid_service_name"
    assert spawn.calls == []  # never spawned anything


@pytest.mark.asyncio
async def test_whitespace_service_name_is_rejected(monkeypatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart("   ")
    assert outcome.status == "failed"
    assert outcome.error_reason == "invalid_service_name"
    assert spawn.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "service; rm -rf /",
        "service && ls",
        "service|cat",
        "../etc/passwd",
        "service name with space",
        "service$VAR",
        "service`cmd`",
        "service\nnewline",
    ],
)
async def test_invalid_pattern_is_rejected_before_subprocess(monkeypatch, bad: str) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(bad)
    assert outcome.status == "failed"
    assert outcome.error_reason == "invalid_service_name"
    assert spawn.calls == []


@pytest.mark.asyncio
async def test_oversized_service_name_is_rejected(monkeypatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    huge = "a" * 250
    outcome = await perform_docker_restart(huge)
    assert outcome.status == "failed"
    assert outcome.error_reason == "invalid_service_name"
    assert spawn.calls == []


# ---- self-preservation -------------------------------------------------


@pytest.mark.asyncio
async def test_self_restart_is_refused_even_if_allowlisted(monkeypatch) -> None:
    """Belt-and-braces: hard-refuse our own container name even if the
    operator mis-includes it in the allowlist env var."""
    monkeypatch.setenv("OPS_AGENT_DOCKER_RESTART_ALLOWLIST", "compose-ops-agent-1")
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart("compose-ops-agent-1")
    assert outcome.status == "failed"
    assert outcome.error_reason == "cannot_restart_self"
    assert spawn.calls == []


# ---- allowlist ---------------------------------------------------------


@pytest.mark.asyncio
async def test_non_allowlisted_service_is_rejected(monkeypatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart("compose-notify-relay-1")
    assert outcome.status == "failed"
    assert outcome.error_reason == "service_not_allowlisted"
    assert "notify-relay" in outcome.error_message
    assert spawn.calls == []


@pytest.mark.asyncio
async def test_allowlist_env_override_admits_new_service(monkeypatch) -> None:
    monkeypatch.setenv(
        "OPS_AGENT_DOCKER_RESTART_ALLOWLIST",
        "compose-devclaw-mcp-1,compose-notify-relay-1",
    )
    spawn = _Spawn(procs=[_FakeProc(returncode=0)])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart("compose-notify-relay-1")
    assert outcome.status == "ok"


# ---- subprocess failure paths ------------------------------------------


@pytest.mark.asyncio
async def test_nonzero_exit_returns_failed(monkeypatch) -> None:
    spawn = _Spawn(
        procs=[_FakeProc(returncode=1, stderr=b"Error: No such container: compose-devclaw-mcp-1")]
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)
    assert outcome.status == "failed"
    assert outcome.error_reason == "nonzero_exit"
    assert "No such container" in outcome.error_message
    assert outcome.detail["returncode"] == 1


@pytest.mark.asyncio
async def test_timeout_returns_failed_and_kills_proc(monkeypatch) -> None:
    monkeypatch.setenv("OPS_AGENT_DOCKER_TIMEOUT_S", "0.05")
    fake = _FakeProc(hang=True)
    spawn = _Spawn(procs=[fake])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)
    assert outcome.status == "failed"
    assert outcome.error_reason == "timeout"
    assert fake.killed is True


@pytest.mark.asyncio
async def test_subprocess_spawn_failure_returns_failed(monkeypatch) -> None:
    spawn = _Spawn(raise_on_spawn=FileNotFoundError("no such file: docker"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)
    assert outcome.status == "failed"
    assert outcome.error_reason == "docker_cli_missing"


@pytest.mark.asyncio
async def test_unexpected_spawn_exception_is_caught(monkeypatch) -> None:
    spawn = _Spawn(raise_on_spawn=PermissionError("docker.sock not writable"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)
    assert outcome.status == "failed"
    assert outcome.error_reason == "subprocess_spawn_failed"
    assert "PermissionError" in outcome.error_message


@pytest.mark.asyncio
async def test_communicate_exception_is_caught(monkeypatch) -> None:
    fake = _FakeProc(raise_on_communicate=RuntimeError("pipe broke"))
    spawn = _Spawn(procs=[fake])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn.make_patch())

    outcome = await perform_docker_restart(ALLOWED)
    assert outcome.status == "failed"
    assert outcome.error_reason == "subprocess_error"
    assert "RuntimeError" in outcome.error_message


# ---- outcome serialization ---------------------------------------------


def test_outcome_to_dict_round_trip() -> None:
    outcome = ActionOutcome(
        action="docker_restart",
        status="ok",
        detail={"service_name": ALLOWED, "returncode": 0, "stdout": "x", "stderr": ""},
    )
    d = outcome_to_dict(outcome)
    assert d["action"] == "docker_restart"
    assert d["status"] == "ok"
    assert d["detail"]["service_name"] == ALLOWED
