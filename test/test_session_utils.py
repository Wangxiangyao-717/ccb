from __future__ import annotations

import json
from pathlib import Path

from session_utils import find_project_session_file, safe_write_session


def test_find_project_session_file_walks_upwards(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    leaf = root / "a" / "b" / "c"
    leaf.mkdir(parents=True)

    session = root / ".codex-session"
    session.write_text("{}", encoding="utf-8")

    found = find_project_session_file(leaf, ".codex-session")
    assert found == session


def test_find_project_session_file_falls_back_to_numbered_variant(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    leaf = root / "a" / "b"
    leaf.mkdir(parents=True)

    session = root / ".codex-session-1"
    session.write_text("{}", encoding="utf-8")

    found = find_project_session_file(leaf, ".codex-session")
    assert found == session


def test_find_project_session_file_prefers_registry_matched_numbered_variant(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    leaf = root / "a" / "b"
    leaf.mkdir(parents=True)

    (root / ".codex-session").write_text(json.dumps({"session_id": "old-session"}), encoding="utf-8")
    target = root / ".codex-session-1"
    target.write_text(json.dumps({"session_id": "wanted-session"}), encoding="utf-8")

    monkeypatch.setattr(
        "session_utils.load_registry_by_claude_pane",
        lambda pane_id: {"ccb_session_id": "wanted-session", "claude_pane_id": pane_id},
    )

    found = find_project_session_file(leaf, ".codex-session", caller_pane_id="16")
    assert found == target


def test_safe_write_session_atomic_write(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    ok, err = safe_write_session(target, '{"hello":"world"}\n')
    assert ok is True
    assert err is None
    assert target.read_text(encoding="utf-8") == '{"hello":"world"}\n'
    assert not target.with_suffix(".tmp").exists()

    ok2, err2 = safe_write_session(target, '{"hello":"again"}\n')
    assert ok2 is True
    assert err2 is None
    assert target.read_text(encoding="utf-8") == '{"hello":"again"}\n'
    assert not target.with_suffix(".tmp").exists()

