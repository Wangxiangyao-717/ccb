"""
session_utils.py - Session file permission check utility
"""
from __future__ import annotations
import json
import os
import stat
from pathlib import Path
from typing import Tuple, Optional, Iterable

from pane_registry import load_registry_by_claude_pane


class AmbiguityError(Exception):
    """Raised when multiple active session candidates exist and cannot be resolved"""
    pass


def check_session_writable(session_file: Path) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Check if session file is writable

    Returns:
        (writable, error_reason, fix_suggestion)
    """
    session_file = Path(session_file)
    parent = session_file.parent

    # 1. Check if parent directory exists and is accessible
    if not parent.exists():
        return False, f"Directory not found: {parent}", f"mkdir -p {parent}"

    if not os.access(parent, os.X_OK):
        return False, f"Directory not accessible (missing x permission): {parent}", f"chmod +x {parent}"

    # 2. Check if parent directory is writable
    if not os.access(parent, os.W_OK):
        return False, f"Directory not writable: {parent}", f"chmod u+w {parent}"

    # 3. If file doesn't exist, directory writable is enough
    if not session_file.exists():
        return True, None, None

    # 4. Check if it's a regular file
    if session_file.is_symlink():
        target = session_file.resolve()
        return False, f"Is symlink pointing to {target}", f"rm -f {session_file}"

    if session_file.is_dir():
        return False, "Is directory, not file", f"rmdir {session_file} or rm -rf {session_file}"

    if not session_file.is_file():
        return False, "Not a regular file", f"rm -f {session_file}"

    # 5. Check file ownership (POSIX only)
    if os.name != "nt" and hasattr(os, "getuid"):
        try:
            file_stat = session_file.stat()
            file_uid = getattr(file_stat, "st_uid", None)
            current_uid = os.getuid()

            if isinstance(file_uid, int) and file_uid != current_uid:
                import pwd

                try:
                    owner_name = pwd.getpwuid(file_uid).pw_name
                except KeyError:
                    owner_name = str(file_uid)
                current_name = pwd.getpwuid(current_uid).pw_name
                return (
                    False,
                    f"File owned by {owner_name} (current user: {current_name})",
                    f"sudo chown {current_name}:{current_name} {session_file}",
                )
        except Exception:
            pass

    # 6. Check if file is writable
    if not os.access(session_file, os.W_OK):
        mode = stat.filemode(session_file.stat().st_mode)
        return False, f"File not writable (mode: {mode})", f"chmod u+w {session_file}"

    return True, None, None


def safe_write_session(session_file: Path, content: str) -> Tuple[bool, Optional[str]]:
    """
    Safely write session file, return friendly error on failure

    Returns:
        (success, error_message)
    """
    session_file = Path(session_file)

    # Pre-check
    writable, reason, fix = check_session_writable(session_file)
    if not writable:
        return False, f"❌ Cannot write {session_file.name}: {reason}\n💡 Fix: {fix}"

    # Attempt atomic write
    tmp_file = session_file.with_suffix(".tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, session_file)
        return True, None
    except PermissionError as e:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return False, f"❌ Cannot write {session_file.name}: {e}\n💡 Try: rm -f {session_file} then retry"
    except Exception as e:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return False, f"❌ Write failed: {e}"


def print_session_error(msg: str, to_stderr: bool = True) -> None:
    """Output session-related error"""
    import sys
    output = sys.stderr if to_stderr else sys.stdout
    print(msg, file=output)


def _iter_session_file_candidates(directory: Path, session_filename: str) -> Iterable[Path]:
    base = directory / session_filename
    numbered: list[tuple[int, Path]] = []

    if base.exists():
        yield base

    prefix = f"{session_filename}-"
    try:
        for path in directory.glob(f"{session_filename}-*"):
            if not path.is_file():
                continue
            suffix = path.name[len(prefix):]
            if not suffix.isdigit():
                continue
            numbered.append((int(suffix), path))
    except Exception:
        return

    for _idx, path in sorted(numbered, key=lambda item: item[0]):
        yield path


def _read_session_identity(path: Path) -> set[str]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except Exception:
        return set()

    if not isinstance(data, dict):
        return set()

    values = {
        str(data.get("session_id") or "").strip(),
        str(data.get("ccb_session_id") or "").strip(),
        str(data.get("claude_session_id") or "").strip(),
    }
    return {value for value in values if value}


def allocate_session_file(work_dir: Path, session_filename: str, *, session_id: str | None = None) -> Path:
    directory = Path(work_dir).resolve()
    session_id = str(session_id or "").strip()

    for candidate in _iter_session_file_candidates(directory, session_filename):
        if session_id and session_id in _read_session_identity(candidate):
            return candidate

    base = directory / session_filename
    if not base.exists():
        return base

    index = 1
    while True:
        candidate = directory / f"{session_filename}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def find_project_session_file(work_dir: Path, session_filename: str, *, caller_pane_id: str | None = None, session_id: str | None = None) -> Optional[Path]:
    """Locate the session file that belongs to the current caller.

    Routing rules (in priority order):
    1. If *session_id* is provided: precise match only, return None if no match.
    2. If *caller_pane_id* is provided: look up the registry to obtain the
       expected session_id, then do a precise match.  A work_dir consistency
       check prevents cross-project misrouting.
    3. If no identity can be determined: return the single active candidate
       when unambiguous, otherwise return None so the upper layer can run
       its phase-2 liveness probe.

    Active candidates are filtered by ``active != False`` and ``ended_at``
    being absent.  The parent-directory walk is preserved from the original
    implementation.
    """
    expected_session_id = str(session_id or "").strip()

    if not expected_session_id:
        pane_id = str(caller_pane_id or "").strip()
        if pane_id:
            try:
                record = load_registry_by_claude_pane(pane_id)
            except Exception:
                record = None
            if isinstance(record, dict):
                # Validate work_dir consistency (prevent cross-project misrouting)
                registry_work_dir = str(
                    record.get("work_dir_norm") or record.get("work_dir") or ""
                )
                if registry_work_dir:
                    try:
                        resolved_work_dir = str(Path(work_dir).resolve())
                        # Simple normalized comparison: resolve both paths and
                        # compare case-insensitively on Windows.
                        norm_registry = registry_work_dir.replace("\\", "/").rstrip("/")
                        norm_request = resolved_work_dir.replace("\\", "/").rstrip("/")
                        if os.name == "nt":
                            norm_registry = norm_registry.casefold()
                            norm_request = norm_request.casefold()
                        if norm_registry != norm_request:
                            record = None
                    except Exception:
                        pass
                if record:
                    expected_session_id = str(
                        record.get("ccb_session_id") or ""
                    ).strip()

    current = Path(work_dir).resolve()
    while True:
        candidates = list(_iter_session_file_candidates(current, session_filename))

        # Filter active candidates (active != False && no ended_at)
        active_candidates: list[tuple[Path, dict]] = []
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("active") is False or data.get("ended_at"):
                    continue
                active_candidates.append((candidate, data))
            except Exception:
                continue

        if expected_session_id:
            for candidate, data in active_candidates:
                file_session_id = str(data.get("session_id") or "").strip()
                if file_session_id == expected_session_id:
                    return candidate
            # No match at this level - continue parent walk if possible
            if current == current.parent:
                return None  # Exhausted all levels, precise match failed
            current = current.parent
            continue

        if len(active_candidates) == 0:
            if current == current.parent:
                return None
            current = current.parent
            continue
        elif len(active_candidates) == 1:
            return active_candidates[0][0]
        else:
            return None  # Ambiguity - upper layer does phase 2


def list_session_candidates(work_dir: Path, session_filename: str) -> list[Path]:
    """Return all active candidate session files from cwd up to parent directories.

    Lightweight filter: ``active != False`` and no ``ended_at``.
    Sorted by proximity (nearest first), then by number within same directory.
    """
    all_candidates: list[tuple[int, Path]] = []
    current = Path(work_dir).resolve()
    distance = 0

    while True:
        candidates = list(_iter_session_file_candidates(current, session_filename))
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("active") is False or data.get("ended_at"):
                    continue
                all_candidates.append((distance, candidate))
            except Exception:
                continue

        if current == current.parent:
            break
        current = current.parent
        distance += 1

    all_candidates.sort(key=lambda x: x[0])
    return [path for _, path in all_candidates]
