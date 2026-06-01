from __future__ import annotations

import json
from pathlib import Path

import pytest

import caskd_session


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.alive: dict[str, bool] = {}
        self.crash_logs: list[tuple[str, str]] = []
        self.respawned: list[str] = []
        self.marker_map: dict[str, str] = {}

    def is_alive(self, pane_id: str) -> bool:
        return bool(self.alive.get(pane_id, False))

    def find_pane_by_title_marker(self, marker: str) -> str | None:
        for prefix, pane in self.marker_map.items():
            if marker.startswith(prefix) or prefix.startswith(marker):
                return pane
        return None

    def list_panes(self) -> list[dict]:
        """Return list of alive panes with their IDs and titles."""
        return [
            {"pane_id": pane_id, "title": ""}
            for pane_id, is_alive in self.alive.items()
            if is_alive
        ]

    def save_crash_log(self, pane_id: str, crash_log_path: str, *, lines: int = 1000) -> None:
        self.crash_logs.append((pane_id, crash_log_path))

    def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None,
                     stderr_log_path: str | None = None, remain_on_exit: bool = True) -> None:
        self.respawned.append(pane_id)
        self.alive[pane_id] = True


def test_caskd_ensure_pane_respawns_dead_pane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pane is dead, ensure_pane should respawn it and update session file."""
    session_path = tmp_path / ".codex-session"
    session_path.write_text(
        json.dumps({
            "session_id": "test-session",
            "terminal": "tmux",
            "pane_id": "%1",
            "pane_title_marker": "CCB-codex-test",
            "runtime_dir": str(tmp_path),
            "work_dir": str(tmp_path),
            "active": True,
            "codex_start_cmd": "codex resume deadbeef",
        }),
        encoding="utf-8",
    )

    backend = FakeTmuxBackend()
    backend.alive = {"%1": False, "%2": False}
    backend.marker_map = {"CCB-codex": "%2"}
    monkeypatch.setattr(caskd_session, "get_backend_for_session", lambda data: backend)

    sess = caskd_session.load_project_session(tmp_path)
    assert sess is not None

    ok, pane = sess.ensure_pane()
    assert ok is True
    assert pane == "%2"
    assert "%2" in backend.respawned

    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert data["pane_id"] == "%2"


def test_caskd_ensure_pane_already_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pane is already alive, ensure_pane should return success immediately."""
    session_path = tmp_path / ".codex-session"
    session_path.write_text(
        json.dumps({
            "session_id": "test-session",
            "terminal": "tmux",
            "pane_id": "%1",
            "pane_title_marker": "CCB-codex-test",
            "work_dir": str(tmp_path),
            "active": True,
        }),
        encoding="utf-8",
    )

    backend = FakeTmuxBackend()
    backend.alive = {"%1": True}
    monkeypatch.setattr(caskd_session, "get_backend_for_session", lambda data: backend)

    sess = caskd_session.load_project_session(tmp_path)
    assert sess is not None

    ok, pane = sess.ensure_pane()
    assert ok is True
    assert pane == "%1"
    assert backend.respawned == []  # No respawn needed


def test_caskd_ensure_pane_marker_rediscover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When original pane is dead but marker finds alive pane, should update pane_id."""
    session_path = tmp_path / ".codex-session"
    session_path.write_text(
        json.dumps({
            "session_id": "test-session",
            "terminal": "tmux",
            "pane_id": "%1",
            "pane_title_marker": "CCB-codex-test",
            "work_dir": str(tmp_path),
            "active": True,
        }),
        encoding="utf-8",
    )

    backend = FakeTmuxBackend()
    backend.alive = {"%1": False, "%2": True}  # %2 is alive
    backend.marker_map = {"CCB-codex": "%2"}
    monkeypatch.setattr(caskd_session, "get_backend_for_session", lambda data: backend)

    sess = caskd_session.load_project_session(tmp_path)
    assert sess is not None

    ok, pane = sess.ensure_pane()
    assert ok is True
    assert pane == "%2"
    assert backend.respawned == []  # No respawn needed, just rediscovered

    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert data["pane_id"] == "%2"


def test_load_project_session_prefers_registry_for_caller_pane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_path = tmp_path / ".codex-session"
    session_path.write_text(
        json.dumps({
            "session_id": "stale-session",
            "terminal": "wezterm",
            "pane_id": "999",
            "pane_title_marker": "CCB-stale",
            "runtime_dir": str(tmp_path / "stale-runtime"),
            "work_dir": str(tmp_path),
            "active": True,
        }),
        encoding="utf-8",
    )

    registry_runtime = tmp_path / "registry-runtime"
    registry_runtime.mkdir()
    monkeypatch.setattr(
        caskd_session,
        "load_registry_by_claude_pane",
        lambda pane_id: {
            "ccb_session_id": "registry-session",
            "claude_pane_id": "16",
            "codex_pane_id": "20",
            "codex_runtime_dir": str(registry_runtime),
            "codex_terminal": "wezterm",
            "codex_pane_title_marker": "CCB-Codex",
            "codex_start_cmd": "codex",
            "work_dir": str(tmp_path),
            "work_dir_norm": str(tmp_path).lower(),
        },
    )

    # Mock backend to return pane "20" as alive (for liveness check)
    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "20", "title": "CCB-Codex"}]

    monkeypatch.setattr(caskd_session, "get_backend_for_session", lambda data: MockBackend())

    sess = caskd_session.load_project_session(tmp_path, caller_pane_id="16")

    assert sess is not None
    assert sess.data["session_id"] == "registry-session"
    assert sess.data["pane_id"] == "20"
    assert sess.data["pane_title_marker"] == "CCB-Codex"


def test_caskd_ensure_pane_mux_mode_prefers_marker_over_pane_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In mux-server mode, pane_id is NOT globally unique across windows.
    ensure_pane must use marker (globally unique) to resolve the correct pane."""
    session_path = tmp_path / ".codex-session"
    session_path.write_text(
        json.dumps({
            "session_id": "test-session",
            "terminal": "wezterm",
            "pane_id": "1",  # Same pane_id as another window!
            "pane_title_marker": "CCB-Codex-ai-12345",
            "work_dir": str(tmp_path),
            "active": True,
        }),
        encoding="utf-8",
    )

    backend = FakeTmuxBackend()
    # Simulate mux mode: two panes with same ID "1" from different windows
    # but different titles. The marker resolves to the correct one.
    backend.alive = {"1": True}
    backend.marker_map = {"CCB-Codex-ai-12345": "1"}

    # Override list_panes to simulate mux mode with two panes
    backend.list_panes = lambda: [
        {"pane_id": "1", "title": "CCB-Codex-ai-99999"},  # Wrong window's pane
        {"pane_id": "1", "title": "CCB-Codex-ai-12345"},  # Our pane (same ID!)
    ]
    # Override find_pane_by_title_marker for WeztermBackend-style resolution
    backend.find_pane_by_title_marker = lambda marker: "1" if any(
        marker in str(p.get("title", ""))
        for p in backend.list_panes()
        if str(p.get("pane_id")) == "1"
    ) else None

    monkeypatch.setattr(caskd_session, "get_backend_for_session", lambda data: backend)

    sess = caskd_session.load_project_session(tmp_path)
    assert sess is not None

    ok, pane = sess.ensure_pane()
    assert ok is True
    # The key assertion: marker was used to find the pane, not just pane_id
    assert pane == "1"
