import json
from pathlib import Path
from pane_registry import clear_provider_registry_fields, upsert_registry, load_registry_by_session_id


def test_clear_provider_registry_fields_removes_only_provider_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)

    upsert_registry({
        "ccb_session_id": "test-session",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "codex-pane-1",
        "codex_runtime_dir": "/tmp/codex",
        "gemini_pane_id": "gemini-pane-1",
        "gemini_runtime_dir": "/tmp/gemini",
    })

    result = clear_provider_registry_fields("test-session", "codex")
    assert result is True

    data = load_registry_by_session_id("test-session")
    assert data is not None
    assert "codex_pane_id" not in data
    assert "codex_runtime_dir" not in data
    assert data["claude_pane_id"] == "claude-pane-1"
    assert data["gemini_pane_id"] == "gemini-pane-1"


def test_clear_provider_registry_fields_nonexistent_session(tmp_path, monkeypatch):
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)
    result = clear_provider_registry_fields("nonexistent", "codex")
    assert result is False
