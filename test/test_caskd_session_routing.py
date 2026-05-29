import json
from pathlib import Path
from caskd_session import CodexProjectSession, compute_session_key, load_project_session, _load_registry_backed_session
from session_utils import AmbiguityError
from pane_registry import upsert_registry, load_registry_by_session_id


def test_codex_project_session_has_ccb_session_id_property(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"session_id": "ai-1716890000-12345", "runtime_dir": str(tmp_path), "terminal": "tmux"}
    session = CodexProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"


def test_codex_project_session_ccb_session_id_priority(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    # New format takes priority
    data = {"ccb_session_id": "new", "session_id": "old"}
    session = CodexProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "new"

    # Falls back to session_id
    data2 = {"session_id": "old-format"}
    session2 = CodexProjectSession(session_file=session_file, data=data2)
    assert session2.ccb_session_id == "old-format"


def test_compute_session_key_prioritizes_ccb_session_id(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"session_id": "ai-1716890000-12345", "pane_id": "%42", "pane_title_marker": "CCB-Codex"}
    session = CodexProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "ccb:ai-1716890000-12345"


def test_compute_session_key_falls_back_to_pane_id(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"pane_id": "%42", "pane_title_marker": "CCB-Codex"}
    session = CodexProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "codex_pane:%42"


# --- Task 12: load_project_session accepts ccb_session_id ---


def test_load_project_session_with_ccb_session_id(tmp_path, monkeypatch):
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    session_file = tmp_path / ".codex-session"
    session_file.write_text(json.dumps({
        "session_id": "target-session", "active": True,
        "runtime_dir": str(tmp_path), "terminal": "tmux",
    }), encoding="utf-8")

    session = load_project_session(tmp_path, ccb_session_id="target-session")
    assert session is not None
    assert session.ccb_session_id == "target-session"


def test_load_project_session_ccb_session_id_no_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    session_file = tmp_path / ".codex-session"
    session_file.write_text(json.dumps({
        "session_id": "session-A", "active": True,
    }), encoding="utf-8")

    session = load_project_session(tmp_path, ccb_session_id="session-B")
    assert session is None


# --- Task 12b: Phase 2 liveness probe for ambiguity resolution ---


def test_load_project_session_resolves_ambiguity_via_liveness_probe(tmp_path, monkeypatch):
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    root = tmp_path / "repo"
    root.mkdir()

    (root / ".codex-session").write_text(json.dumps({
        "session_id": "instance-A", "active": True,
        "pane_id": "pane-A", "pane_title_marker": "CCB-Codex-A",
    }), encoding="utf-8")
    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "instance-B", "active": True,
        "pane_id": "pane-B", "pane_title_marker": "CCB-Codex-B",
    }), encoding="utf-8")

    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "pane-B", "title": "CCB-Codex-B"}]

    monkeypatch.setattr("caskd_session.get_backend_for_session", lambda data: MockBackend())

    session = load_project_session(root)
    assert session is not None
    assert session.ccb_session_id == "instance-B"


def test_load_project_session_raises_ambiguity_error_on_multiple_alive(tmp_path, monkeypatch):
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    root = tmp_path / "repo"
    root.mkdir()

    (root / ".codex-session").write_text(json.dumps({
        "session_id": "instance-A", "active": True, "pane_id": "pane-A",
    }), encoding="utf-8")
    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "instance-B", "active": True, "pane_id": "pane-B",
    }), encoding="utf-8")

    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "pane-A", "title": ""}, {"pane_id": "pane-B", "title": ""}]

    monkeypatch.setattr("caskd_session.get_backend_for_session", lambda data: MockBackend())

    import pytest
    with pytest.raises(AmbiguityError):
        load_project_session(root)


# --- Task 12c: Registry fallback stale validation and cleanup ---


def test_registry_fallback_validates_and_cleans_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)
    monkeypatch.setattr("caskd_session.load_registry_by_claude_pane",
                        lambda pane_id: __import__("pane_registry").load_registry_by_claude_pane(pane_id))

    root = tmp_path / "repo"
    root.mkdir()

    session_file = root / ".codex-session"
    session_file.write_text(json.dumps({
        "session_id": "stale-session", "active": False, "pane_id": "dead-pane",
    }), encoding="utf-8")

    upsert_registry({
        "ccb_session_id": "stale-session",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "dead-pane",
        "codex_runtime_dir": str(root),
        "codex_terminal": "tmux",
        "work_dir": str(root),
    })

    result = _load_registry_backed_session(root, "claude-pane-1")
    assert result is None

    registry = load_registry_by_session_id("stale-session")
    assert registry is not None
    assert "codex_pane_id" not in registry
    assert "codex_runtime_dir" not in registry
    assert registry["claude_pane_id"] == "claude-pane-1"


# --- Bug fix: stale sessions with dead panes + new alive session ---


def test_load_project_session_skips_stale_sessions_finds_alive_one(tmp_path, monkeypatch):
    """When old sessions have dead panes and a new session has an alive pane,
    should find the alive one even without explicit ccb_session_id."""
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    root = tmp_path / "repo"
    root.mkdir()

    # Old stale session: active=True but pane is dead
    (root / ".codex-session").write_text(json.dumps({
        "session_id": "old-session", "active": True,
        "pane_id": "dead-pane-1", "pane_title_marker": "CCB-Codex",
    }), encoding="utf-8")

    # Another old stale session
    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "old-session-2", "active": True,
        "pane_id": "dead-pane-2", "pane_title_marker": "CCB-Codex",
    }), encoding="utf-8")

    # New session with alive pane
    (root / ".codex-session-2").write_text(json.dumps({
        "session_id": "new-session", "active": True,
        "pane_id": "alive-pane", "pane_title_marker": "CCB-Codex-new-id",
    }), encoding="utf-8")

    # Backend shows only the new pane is alive
    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "alive-pane", "title": "CCB-Codex-new-id"}]

    monkeypatch.setattr("caskd_session.get_backend_for_session", lambda data: MockBackend())

    session = load_project_session(root)
    assert session is not None
    assert session.ccb_session_id == "new-session"


def test_load_project_session_with_caller_pane_still_triggers_phase2(tmp_path, monkeypatch):
    """Phase 2 should trigger even when caller_pane_id exists but no ccb_session_id."""
    # caller_pane_id is set but doesn't match any registry entry
    monkeypatch.setenv("WEZTERM_PANE", "pane-999")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr("caskd_session.load_registry_by_claude_pane", lambda pid: None)

    root = tmp_path / "repo"
    root.mkdir()

    (root / ".codex-session").write_text(json.dumps({
        "session_id": "stale", "active": True, "pane_id": "dead",
    }), encoding="utf-8")
    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "alive-session", "active": True, "pane_id": "alive",
    }), encoding="utf-8")

    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "alive", "title": ""}]

    monkeypatch.setattr("caskd_session.get_backend_for_session", lambda data: MockBackend())

    session = load_project_session(root, caller_pane_id="pane-999")
    assert session is not None
    assert session.ccb_session_id == "alive-session"
