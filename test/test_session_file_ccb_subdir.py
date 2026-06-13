"""Tests for session file .ccb/ subdirectory support."""
from __future__ import annotations

import json
from pathlib import Path

from session_utils import (
    _iter_session_file_candidates,
    allocate_session_file,
    find_project_session_file,
    list_session_candidates,
)


def _write_session(path: Path, *, active: bool = True, session_id: str = "test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"session_id": session_id, "active": active, "work_dir": str(path.parent)}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TestIterSessionFileCandidates:
    def test_finds_ccb_subdir_first(self, tmp_path: Path) -> None:
        """Files in .ccb/ should be yielded before root-level files."""
        _write_session(tmp_path / ".ccb" / ".codex-session", session_id="ccb-one")
        _write_session(tmp_path / ".codex-session", session_id="root-one")

        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert len(result) == 2
        # .ccb/ file comes first
        assert result[0] == tmp_path / ".ccb" / ".codex-session"
        assert result[1] == tmp_path / ".codex-session"

    def test_finds_only_ccb_when_no_root(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".ccb" / ".codex-session")

        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert len(result) == 1
        assert result[0] == tmp_path / ".ccb" / ".codex-session"

    def test_finds_only_root_when_no_ccb(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".codex-session")

        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert len(result) == 1
        assert result[0] == tmp_path / ".codex-session"

    def test_numbered_files_ccb_before_root(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".ccb" / ".codex-session-1")
        _write_session(tmp_path / ".codex-session-1")

        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert len(result) == 2
        assert result[0] == tmp_path / ".ccb" / ".codex-session-1"
        assert result[1] == tmp_path / ".codex-session-1"

    def test_numbered_files_sorted_within_location(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".ccb" / ".codex-session-2")
        _write_session(tmp_path / ".ccb" / ".codex-session-1")

        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert len(result) == 2
        assert result[0].name == ".codex-session-1"
        assert result[1].name == ".codex-session-2"

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = list(_iter_session_file_candidates(tmp_path, ".codex-session"))
        assert result == []


class TestAllocateSessionFile:
    def test_allocates_in_ccb_subdir(self, tmp_path: Path) -> None:
        """New files should always be created in .ccb/ subdirectory."""
        result = allocate_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session"

    def test_allocates_in_ccb_when_root_exists(self, tmp_path: Path) -> None:
        """Even when root has a session file, new allocation goes to .ccb/."""
        _write_session(tmp_path / ".codex-session", session_id="old-root")
        result = allocate_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session"

    def test_reuses_matching_session_id_in_ccb(self, tmp_path: Path) -> None:
        """If session_id matches a file in .ccb/, reuse it."""
        _write_session(tmp_path / ".ccb" / ".codex-session", session_id="my-id")
        result = allocate_session_file(tmp_path, ".codex-session", session_id="my-id")
        assert result == tmp_path / ".ccb" / ".codex-session"

    def test_reuses_matching_session_id_in_root(self, tmp_path: Path) -> None:
        """If session_id matches a file at root (legacy), reuse it for backward compat."""
        _write_session(tmp_path / ".codex-session", session_id="my-id")
        result = allocate_session_file(tmp_path, ".codex-session", session_id="my-id")
        assert result == tmp_path / ".codex-session"

    def test_numbered_allocation_in_ccb(self, tmp_path: Path) -> None:
        """When .ccb/.codex-session exists, next allocation is .ccb/.codex-session-1."""
        _write_session(tmp_path / ".ccb" / ".codex-session")
        result = allocate_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session-1"

    def test_numbered_skips_root_files(self, tmp_path: Path) -> None:
        """Numbered allocation considers both .ccb/ and root for index gaps."""
        _write_session(tmp_path / ".ccb" / ".codex-session")
        _write_session(tmp_path / ".codex-session-1")
        result = allocate_session_file(tmp_path, ".codex-session")
        # .ccb/ has base, root has -1, so next in .ccb/ is -2
        # (we skip index 1 because it exists at root)
        assert result == tmp_path / ".ccb" / ".codex-session-2"


class TestFindProjectSessionFile:
    def test_finds_active_in_ccb(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".ccb" / ".codex-session", active=True)
        result = find_project_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session"

    def test_finds_active_in_root_fallback(self, tmp_path: Path) -> None:
        """If no .ccb/ file, fall back to root-level active file."""
        _write_session(tmp_path / ".codex-session", active=True)
        result = find_project_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".codex-session"

    def test_prefers_ccb_over_root(self, tmp_path: Path) -> None:
        """When both exist and active, .ccb/ wins."""
        _write_session(tmp_path / ".ccb" / ".codex-session", active=True, session_id="ccb")
        _write_session(tmp_path / ".codex-session", active=True, session_id="root")
        result = find_project_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session"

    def test_skips_inactive_ccb_finds_active_root(self, tmp_path: Path) -> None:
        """If .ccb/ file is inactive, fall through to active root file."""
        _write_session(tmp_path / ".ccb" / ".codex-session", active=False)
        _write_session(tmp_path / ".codex-session", active=True)
        result = find_project_session_file(tmp_path, ".codex-session")
        assert result == tmp_path / ".codex-session"

    def test_parent_walk_finds_ccb(self, tmp_path: Path) -> None:
        """Parent directory walk should find .ccb/ session files."""
        _write_session(tmp_path / ".ccb" / ".codex-session", active=True)
        child = tmp_path / "subdir"
        child.mkdir()
        result = find_project_session_file(child, ".codex-session")
        assert result == tmp_path / ".ccb" / ".codex-session"


class TestListSessionCandidates:
    def test_includes_both_locations(self, tmp_path: Path) -> None:
        _write_session(tmp_path / ".ccb" / ".codex-session", active=True)
        _write_session(tmp_path / ".codex-session", active=True)
        result = list_session_candidates(tmp_path, ".codex-session")
        assert len(result) == 2
