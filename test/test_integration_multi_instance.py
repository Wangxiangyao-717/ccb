# Integration tests for multi-instance routing
import json
import os
from pathlib import Path

import pytest

from session_utils import find_project_session_file, AmbiguityError
from caskd_session import load_project_session, _load_registry_backed_session
from pane_registry import upsert_registry, load_registry_by_session_id


def test_multi_instance_precise_routing(tmp_path):
    """Two CCB instances in same directory should route precisely by session_id"""
    root = tmp_path / "project"
    root.mkdir()

    # Instance A
    session_a = root / ".codex-session"
    session_a.write_text(json.dumps({
        "session_id": "instance-A",
        "active": True,
        "pane_id": "pane-A",
        "work_dir": str(root),
    }), encoding="utf-8")

    # Instance B
    session_b = root / ".codex-session-1"
    session_b.write_text(json.dumps({
        "session_id": "instance-B",
        "active": True,
        "pane_id": "pane-B",
        "work_dir": str(root),
    }), encoding="utf-8")

    # Precise routing by session_id
    found_a = find_project_session_file(root, ".codex-session", session_id="instance-A")
    assert found_a == session_a

    found_b = find_project_session_file(root, ".codex-session", session_id="instance-B")
    assert found_b == session_b

    # No fallback when session_id doesn't match
    found_none = find_project_session_file(root, ".codex-session", session_id="instance-C")
    assert found_none is None


def test_multi_instance_ambiguity_returns_none(tmp_path):
    """Multiple active instances without identity should return None (ambiguity)"""
    root = tmp_path / "project"
    root.mkdir()

    # Two active instances
    (root / ".codex-session").write_text(json.dumps({
        "session_id": "instance-A",
        "active": True,
        "pane_id": "pane-A",
    }), encoding="utf-8")

    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "instance-B",
        "active": True,
        "pane_id": "pane-B",
    }), encoding="utf-8")

    # Without session_id, should return None (ambiguity)
    found = find_project_session_file(root, ".codex-session")
    assert found is None


def test_multi_instance_liveness_probe_resolves_ambiguity(tmp_path, monkeypatch):
    """Phase 2 liveness probe should resolve ambiguity by checking which panes are alive"""
    monkeypatch.delenv("WEZTERM_PANE", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    root = tmp_path / "project"
    root.mkdir()

    # Two active instances
    (root / ".codex-session").write_text(json.dumps({
        "session_id": "instance-A",
        "active": True,
        "pane_id": "pane-A",
        "pane_title_marker": "CCB-Codex-A",
    }), encoding="utf-8")

    (root / ".codex-session-1").write_text(json.dumps({
        "session_id": "instance-B",
        "active": True,
        "pane_id": "pane-B",
        "pane_title_marker": "CCB-Codex-B",
    }), encoding="utf-8")

    # Mock backend.list_panes() to show only instance-B is alive
    class MockBackend:
        def list_panes(self):
            return [{"pane_id": "pane-B", "title": "CCB-Codex-B"}]

    monkeypatch.setattr("caskd_session.get_backend_for_session", lambda data: MockBackend())

    # Without session_id, liveness probe should pick instance-B
    session = load_project_session(root)
    assert session is not None
    assert session.ccb_session_id == "instance-B"


def test_multi_instance_stale_registry_cleanup(tmp_path, monkeypatch):
    """Stale registry entries should be cleaned up during fallback"""
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "caskd_session.load_registry_by_claude_pane",
        lambda pane_id: __import__("pane_registry").load_registry_by_claude_pane(pane_id),
    )

    root = tmp_path / "project"
    root.mkdir()

    # Stale session file
    (root / ".codex-session").write_text(json.dumps({
        "session_id": "stale-session",
        "active": False,
        "pane_id": "dead-pane",
    }), encoding="utf-8")

    # Registry pointing to stale session
    upsert_registry({
        "ccb_session_id": "stale-session",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "dead-pane",
        "codex_runtime_dir": str(root),
        "codex_terminal": "tmux",
        "work_dir": str(root),
    })

    # Fallback should detect stale and clean codex fields
    result = _load_registry_backed_session(root, "claude-pane-1")
    assert result is None

    # Verify codex fields cleaned, but claude_pane_id preserved
    registry = load_registry_by_session_id("stale-session")
    assert registry is not None
    assert "codex_pane_id" not in registry
    assert "codex_runtime_dir" not in registry
    assert registry["claude_pane_id"] == "claude-pane-1"


def test_multi_instance_cmd_kill_registry_cleanup(tmp_path, monkeypatch):
    """cmd_kill should clear provider fields but preserve other providers"""
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)

    # Simulate a registry with all three providers active
    upsert_registry({
        "ccb_session_id": "multi-provider-session",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "codex-pane-1",
        "codex_runtime_dir": str(tmp_path),
        "gemini_pane_id": "gemini-pane-1",
        "gemini_runtime_dir": str(tmp_path),
        "opencode_pane_id": "opencode-pane-1",
        "opencode_runtime_dir": str(tmp_path),
    })

    from pane_registry import clear_provider_registry_fields

    # Kill codex - should only clear codex fields
    clear_provider_registry_fields("multi-provider-session", "codex")

    registry = load_registry_by_session_id("multi-provider-session")
    assert registry is not None
    assert "codex_pane_id" not in registry
    assert "codex_runtime_dir" not in registry
    # Other providers should be preserved
    assert registry["gemini_pane_id"] == "gemini-pane-1"
    assert registry["opencode_pane_id"] == "opencode-pane-1"
    assert registry["claude_pane_id"] == "claude-pane-1"


def test_multi_instance_registry_removal_when_all_providers_cleared(tmp_path, monkeypatch):
    """Registry should be removable when all provider fields are cleared"""
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)

    from pane_registry import clear_provider_registry_fields, remove_registry

    upsert_registry({
        "ccb_session_id": "cleanup-test",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "codex-pane-1",
    })

    # Clear codex (the only provider)
    clear_provider_registry_fields("cleanup-test", "codex")

    registry = load_registry_by_session_id("cleanup-test")
    assert registry is not None
    # No provider fields remain
    has_provider_fields = any(
        k.startswith("codex_") or k.startswith("gemini_") or k.startswith("opencode_")
        for k in registry.keys()
    )
    assert not has_provider_fields
    # claude_pane_id is preserved
    assert registry["claude_pane_id"] == "claude-pane-1"

    # Now remove entire registry
    remove_registry("cleanup-test")
    registry = load_registry_by_session_id("cleanup-test")
    assert registry is None
