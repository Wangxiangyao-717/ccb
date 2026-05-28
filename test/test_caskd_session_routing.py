import json
from pathlib import Path
from caskd_session import CodexProjectSession, compute_session_key, load_project_session
from session_utils import AmbiguityError


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
