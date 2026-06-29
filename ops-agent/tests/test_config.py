"""Config loader — env wiring + defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from ops_agent.config import load_config


def test_defaults_resolve_under_home(monkeypatch) -> None:
    for k in (
        "OPS_AGENT_GOALS_DIR",
        "OPS_AGENT_INCIDENTS_DIR",
        "OPS_AGENT_POLL_INTERVAL_S",
        "OPS_AGENT_DEDUP_WINDOW_S",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.poll_interval_s == 60.0
    assert cfg.dedup_window_s == 24 * 3600.0
    # Defaults expand to absolute paths via os.path.expanduser.
    assert cfg.goals_dir.is_absolute()
    assert cfg.incidents_dir.is_absolute()


def test_env_overrides(monkeypatch, tmp_path: Path) -> None:
    g = tmp_path / "g"
    i = tmp_path / "i"
    g.mkdir()
    i.mkdir()
    monkeypatch.setenv("OPS_AGENT_GOALS_DIR", str(g))
    monkeypatch.setenv("OPS_AGENT_INCIDENTS_DIR", str(i))
    monkeypatch.setenv("OPS_AGENT_POLL_INTERVAL_S", "5")
    monkeypatch.setenv("OPS_AGENT_DEDUP_WINDOW_S", "120")
    cfg = load_config()
    assert cfg.goals_dir == g.resolve()
    assert cfg.incidents_dir == i.resolve()
    assert cfg.poll_interval_s == 5.0
    assert cfg.dedup_window_s == 120.0


def test_bad_float_raises(monkeypatch) -> None:
    monkeypatch.setenv("OPS_AGENT_POLL_INTERVAL_S", "not-a-number")
    with pytest.raises(ValueError):
        load_config()
