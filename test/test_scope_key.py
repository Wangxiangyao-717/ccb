from __future__ import annotations

import json
from pathlib import Path

import pytest

from scope_key import (
    compute_scope_key,
    detect_terminal_control_endpoint,
    resolve_state_file,
    scope_key_digest,
    scoped_state_file,
    _normalize_endpoint,
)


def _clear_terminal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all terminal-related env vars."""
    for k in [
        "WEZTERM_UNIX_SOCKET", "WEZTERM_PANE", "CODEX_WEZTERM_PANE",
        "TMUX", "TMUX_PANE",
    ]:
        monkeypatch.delenv(k, raising=False)


# --- detect_terminal_control_endpoint ---

def test_detect_wezterm_unix_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_UNIX_SOCKET", "/tmp/gui-sock-12345")
    result = detect_terminal_control_endpoint()
    assert result is not None
    assert result[0] == "wezterm"
    assert "gui-sock-12345" in result[1]


def test_detect_wezterm_pane_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "5")
    result = detect_terminal_control_endpoint()
    assert result == ("wezterm", "pane-5")


def test_detect_wezterm_unix_socket_over_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_UNIX_SOCKET", "/tmp/gui-sock-99")
    monkeypatch.setenv("WEZTERM_PANE", "5")
    result = detect_terminal_control_endpoint()
    assert result is not None
    assert result[0] == "wezterm"
    assert "gui-sock-99" in result[1]  # socket wins over pane


def test_detect_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
    result = detect_terminal_control_endpoint()
    assert result is not None
    assert result[0] == "tmux"


def test_detect_tmux_pane_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("TMUX_PANE", "%3")
    result = detect_terminal_control_endpoint()
    assert result == ("tmux", "pane-%3")


def test_detect_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    assert detect_terminal_control_endpoint() is None


# --- compute_scope_key ---

def test_scope_key_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "5")
    sk1 = compute_scope_key("/tmp/test")
    sk2 = compute_scope_key("/tmp/test")
    assert sk1 == sk2


def test_scope_key_different_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "5")
    sk_a = compute_scope_key("/tmp/test", endpoint=("wezterm", "sock-a"))
    sk_b = compute_scope_key("/tmp/test", endpoint=("wezterm", "sock-b"))
    assert sk_a != sk_b


def test_scope_key_different_work_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    ep = ("wezterm", "sock-a")
    sk_a = compute_scope_key("/tmp/dir1", endpoint=ep)
    sk_b = compute_scope_key("/tmp/dir2", endpoint=ep)
    assert sk_a != sk_b


def test_scope_key_none_when_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    assert compute_scope_key("/tmp/test") is None


def test_scope_key_normalizes_work_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    ep = ("wezterm", "sock-a")
    sk1 = compute_scope_key("/tmp/Test", endpoint=ep)
    sk2 = compute_scope_key("/tmp/test", endpoint=ep)
    # On Windows, both resolve to same lowercase path
    assert sk1["work_dir"] == sk2["work_dir"]


# --- scope_key_digest ---

def test_digest_deterministic() -> None:
    sk = {"backend_kind": "wezterm", "terminal_control_endpoint": "/tmp/x", "work_dir": "/a/b"}
    assert scope_key_digest(sk) == scope_key_digest(sk)


def test_digest_different_for_different_keys() -> None:
    sk1 = {"backend_kind": "wezterm", "terminal_control_endpoint": "/tmp/a", "work_dir": "/x"}
    sk2 = {"backend_kind": "wezterm", "terminal_control_endpoint": "/tmp/b", "work_dir": "/x"}
    assert scope_key_digest(sk1) != scope_key_digest(sk2)


def test_digest_length() -> None:
    sk = {"backend_kind": "wezterm", "terminal_control_endpoint": "/tmp/x", "work_dir": "/a"}
    assert len(scope_key_digest(sk)) == 16


# --- scoped_state_file ---

def test_scoped_state_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCB_RUN_DIR", str(tmp_path))
    path = scoped_state_file("caskd", "abc123")
    assert path.name == "caskd-abc123.json"
    assert path.parent == tmp_path


# --- resolve_state_file ---

def test_resolve_env_override(tmp_path: Path) -> None:
    override = str(tmp_path / "custom.json")
    result = resolve_state_file("caskd", env_override=override)
    assert result == Path(override)


def test_resolve_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "5")
    result = resolve_state_file("caskd", work_dir="/tmp/test")
    assert result is not None
    assert result.name.startswith("caskd-")
    assert result.name.endswith(".json")


def test_resolve_none_when_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_terminal_env(monkeypatch)
    result = resolve_state_file("caskd", work_dir="/tmp/test")
    assert result is None


def test_resolve_env_overrides_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_terminal_env(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "5")
    override = str(tmp_path / "custom.json")
    result = resolve_state_file("caskd", env_override=override, work_dir="/tmp/test")
    assert result == Path(override)


# --- compare-and-delete (from askd_server) ---

def test_compare_and_delete_matching_token(tmp_path: Path) -> None:
    from askd_server import _compare_and_delete_state
    state_file = tmp_path / "test.json"
    state_file.write_text(json.dumps({"token": "abc", "port": 1234}), encoding="utf-8")
    _compare_and_delete_state(state_file, "abc")
    assert not state_file.exists()


def test_compare_and_delete_wrong_token(tmp_path: Path) -> None:
    from askd_server import _compare_and_delete_state
    state_file = tmp_path / "test.json"
    state_file.write_text(json.dumps({"token": "xyz", "port": 1234}), encoding="utf-8")
    _compare_and_delete_state(state_file, "abc")
    assert state_file.exists()


def test_compare_and_delete_missing_file(tmp_path: Path) -> None:
    from askd_server import _compare_and_delete_state
    state_file = tmp_path / "nonexistent.json"
    _compare_and_delete_state(state_file, "abc")  # Should not raise
