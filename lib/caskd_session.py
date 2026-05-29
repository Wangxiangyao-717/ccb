from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ccb_config import apply_backend_env
from pane_registry import load_registry_by_claude_pane, clear_provider_registry_fields
from session_utils import find_project_session_file as _find_project_session_file, safe_write_session, list_session_candidates, AmbiguityError
from terminal import get_backend_for_session

apply_backend_env()


def find_project_session_file(work_dir: Path, caller_pane_id: Optional[str] = None, session_id: Optional[str] = None) -> Optional[Path]:
    return _find_project_session_file(work_dir, ".codex-session", caller_pane_id=caller_pane_id, session_id=session_id)


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_work_dir(value: str | Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).resolve()).lower()
    except Exception:
        return raw.replace("\\", "/").lower()


def _load_registry_backed_session(work_dir: Path, caller_pane_id: Optional[str], expected_ccb_session_id: Optional[str] = None) -> Optional["CodexProjectSession"]:
    pane_id = str(caller_pane_id or "").strip()
    if not pane_id:
        return None

    record = load_registry_by_claude_pane(pane_id)
    if not isinstance(record, dict):
        return None

    # Validate ccb_session_id if provided
    if expected_ccb_session_id:
        registry_ccb_session_id = str(record.get("ccb_session_id") or "").strip()
        if registry_ccb_session_id != expected_ccb_session_id:
            return None

    expected_work_dir = _normalize_work_dir(work_dir)
    record_work_dir = _normalize_work_dir(record.get("work_dir_norm") or record.get("work_dir") or "")
    if expected_work_dir and record_work_dir and expected_work_dir != record_work_dir:
        return None

    ccb_session_id = str(record.get("ccb_session_id") or "").strip()

    # Find session file (resolve early for stale validation)
    session_file = (
        _find_project_session_file(
            work_dir,
            ".codex-session",
            caller_pane_id=pane_id,
            session_id=ccb_session_id or None,
        )
        or (Path(work_dir).resolve() / ".codex-session")
    )

    # Validate session file is not stale
    if session_file.exists():
        file_data = _read_json(session_file)
        if file_data and (file_data.get("active") is False or file_data.get("ended_at")):
            # Session is stale, clean codex fields from registry
            if ccb_session_id:
                clear_provider_registry_fields(ccb_session_id, "codex")
            return None

    runtime_dir = str(record.get("codex_runtime_dir") or "").strip()
    codex_pane_id = str(record.get("codex_pane_id") or "").strip()
    terminal = str(record.get("codex_terminal") or record.get("terminal") or "").strip()
    if not runtime_dir or not codex_pane_id or not terminal:
        return None

    data = {
        "session_id": ccb_session_id,
        "runtime_dir": runtime_dir,
        "terminal": terminal,
        "tmux_session": record.get("codex_tmux_session"),
        "pane_id": codex_pane_id,
        "pane_title_marker": str(record.get("codex_pane_title_marker") or "").strip(),
        "work_dir": str(record.get("work_dir") or work_dir),
        "work_dir_norm": str(record.get("work_dir_norm") or expected_work_dir),
        "active": True,
    }
    codex_start_cmd = str(record.get("codex_start_cmd") or "").strip()
    if codex_start_cmd:
        data["codex_start_cmd"] = codex_start_cmd
        data["start_cmd"] = codex_start_cmd
    codex_session_path = str(record.get("codex_session_path") or "").strip()
    if codex_session_path:
        data["codex_session_path"] = codex_session_path
    codex_session_id = str(record.get("codex_session_id") or "").strip()
    if codex_session_id:
        data["codex_session_id"] = codex_session_id

    # Liveness check: verify the pane is actually alive before returning
    backend = get_backend_for_session(data)
    if backend and hasattr(backend, 'list_panes'):
        try:
            panes = backend.list_panes()
            # Only do liveness check if we got actual pane data.
            # Empty list could mean probe failure (tmux/wezterm command failed)
            # or unimplemented backend (iTerm2 returns [] always).
            # In both cases, trust the registry rather than cleaning it.
            if panes:
                alive_pane_ids = {str(p.get("pane_id")) for p in panes}
                alive_titles = {p.get("title", "") for p in panes}
                pane_id = str(data.get("pane_id") or "")
                marker = str(data.get("pane_title_marker") or "")
                pane_alive = (
                    (pane_id and pane_id in alive_pane_ids) or
                    (marker and marker in alive_titles)
                )
                if not pane_alive:
                    # Pane is dead and we have proof (non-empty pane list),
                    # clean codex fields from registry
                    if ccb_session_id:
                        clear_provider_registry_fields(ccb_session_id, "codex")
                    return None
        except Exception:
            # Probe failed, trust registry and return session
            pass

    return CodexProjectSession(session_file=session_file, data=data)


@dataclass
class CodexProjectSession:
    session_file: Path
    data: dict

    @property
    def ccb_session_id(self) -> str:
        """CCB launcher session ID. Prefers ccb_session_id field, falls back to session_id for backward compat.
        This is NOT the Codex native session ID (codex_session_id)."""
        value = self.data.get("ccb_session_id")
        if value:
            return str(value).strip()
        value = self.data.get("session_id")
        return str(value or "").strip()

    @property
    def terminal(self) -> str:
        return (self.data.get("terminal") or "tmux").strip() or "tmux"

    @property
    def pane_id(self) -> str:
        v = self.data.get("pane_id")
        if not v and self.terminal == "tmux":
            v = self.data.get("tmux_session")
        return str(v or "").strip()

    @property
    def pane_title_marker(self) -> str:
        return str(self.data.get("pane_title_marker") or "").strip()

    @property
    def codex_session_path(self) -> str:
        return str(self.data.get("codex_session_path") or "").strip()

    @property
    def codex_session_id(self) -> str:
        return str(self.data.get("codex_session_id") or "").strip()

    @property
    def work_dir(self) -> str:
        return str(self.data.get("work_dir") or self.session_file.parent)

    @property
    def runtime_dir(self) -> Path:
        return Path(self.data.get("runtime_dir") or self.session_file.parent)

    @property
    def start_cmd(self) -> str:
        # Prefer explicit codex_start_cmd when present.
        return str(self.data.get("codex_start_cmd") or self.data.get("start_cmd") or "").strip()

    def backend(self):
        return get_backend_for_session(self.data)

    def ensure_pane(self) -> Tuple[bool, str]:
        backend = self.backend()
        if not backend:
            return False, "Terminal backend not available"

        pane_id = self.pane_id
        if pane_id and backend.is_alive(pane_id):
            return True, pane_id

        marker = self.pane_title_marker
        resolver = getattr(backend, "find_pane_by_title_marker", None)
        resolved: Optional[str] = None
        if marker and callable(resolver):
            resolved = resolver(marker)
            if resolved and backend.is_alive(str(resolved)):
                self.data["pane_id"] = str(resolved)
                self.data["updated_at"] = _now_str()
                self._write_back()
                return True, str(resolved)

        # tmux self-heal: if pane exists but is dead (remain-on-exit), respawn in-place.
        if self.terminal == "tmux":
            start_cmd = self.start_cmd
            respawn = getattr(backend, "respawn_pane", None)
            if start_cmd and callable(respawn):
                last_err: str | None = None
                for target in [resolved, pane_id]:
                    if not target or not str(target).startswith("%"):
                        continue
                    try:
                        saver = getattr(backend, "save_crash_log", None)
                        if callable(saver):
                            try:
                                runtime = self.runtime_dir
                                runtime.mkdir(parents=True, exist_ok=True)
                                crash_log = runtime / f"pane-crash-{int(time.time())}.log"
                                saver(str(target), str(crash_log), lines=1000)
                            except Exception:
                                pass
                        respawn(str(target), cmd=start_cmd, cwd=self.work_dir, remain_on_exit=True)
                        if backend.is_alive(str(target)):
                            self.data["pane_id"] = str(target)
                            self.data["updated_at"] = _now_str()
                            self._write_back()
                            return True, str(target)
                        last_err = "respawn did not revive pane"
                    except Exception as exc:
                        last_err = f"{exc}"
                if last_err:
                    return False, f"Pane not alive and respawn failed: {last_err}"

        return False, f"Pane not alive: {pane_id}"

    def update_codex_log_binding(self, *, log_path: Optional[str], session_id: Optional[str]) -> None:
        updated = False
        if log_path and self.data.get("codex_session_path") != log_path:
            self.data["codex_session_path"] = log_path
            updated = True
        if session_id and self.data.get("codex_session_id") != session_id:
            self.data["codex_session_id"] = session_id
            self.data["codex_start_cmd"] = f"codex resume {session_id}"
            updated = True
        if updated:
            self.data["updated_at"] = _now_str()
            if self.data.get("active") is False:
                self.data["active"] = True
            self._write_back()

    def _write_back(self) -> None:
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        ok, err = safe_write_session(self.session_file, payload)
        if not ok:
            # Best-effort: never raise (daemon should continue).
            _ = err


def _resolve_ambiguity(candidates: list[Path]) -> Optional[Path]:
    """Phase 2 liveness probe: resolve ambiguity by checking which panes are actually alive.

    Returns the single alive candidate, None if no candidates survive, or raises
    AmbiguityError if multiple candidates are still alive.
    """
    if not candidates:
        return None

    first_data = _read_json(candidates[0])
    if not first_data:
        return None

    backend = get_backend_for_session(first_data)
    if not backend or not hasattr(backend, 'list_panes'):
        # Fail-closed: cannot probe, cannot determine which is alive
        raise AmbiguityError(f"Cannot probe panes (backend unavailable), {len(candidates)} active candidates exist")

    try:
        panes = backend.list_panes()
    except Exception:
        # Fail-closed: probe failed, cannot determine which is alive
        raise AmbiguityError(f"Pane liveness probe failed, {len(candidates)} active candidates exist")

    alive_pane_ids = {str(p.get("pane_id")) for p in panes}
    alive_titles = {p.get("title", "") for p in panes}

    surviving = []
    for candidate in candidates:
        data = _read_json(candidate)
        if not data:
            continue
        pane_id = str(data.get("pane_id") or "")
        marker = str(data.get("pane_title_marker") or "")
        if pane_id and pane_id in alive_pane_ids:
            surviving.append(candidate)
        elif marker and marker in alive_titles:
            # Use exact match for markers to avoid prefix collisions
            # (e.g., "CCB-Codex" should not match "CCB-Codex-ai-123")
            surviving.append(candidate)

    if len(surviving) == 1:
        return surviving[0]
    elif len(surviving) == 0:
        return None
    else:
        raise AmbiguityError(f"Multiple alive candidates: {[p.name for p in surviving]}")


def load_project_session(work_dir: Path, caller_pane_id: Optional[str] = None, ccb_session_id: Optional[str] = None) -> Optional[CodexProjectSession]:
    caller_pane_id = (
        str(caller_pane_id or "").strip()
        or (os.environ.get("WEZTERM_PANE") or "").strip()
        or (os.environ.get("TMUX_PANE") or "").strip()
    )
    # Registry-backed session first
    if caller_pane_id:
        session = _load_registry_backed_session(work_dir, caller_pane_id, ccb_session_id)
        if session is not None:
            return session

    # Phase 1: find_project_session_file (file-level filtering only)
    session_file = find_project_session_file(work_dir, caller_pane_id=caller_pane_id, session_id=ccb_session_id)

    # If we have an explicit session_id, trust Phase 1 (precise match)
    if session_file and ccb_session_id:
        data = _read_json(session_file)
        if data:
            return CodexProjectSession(session_file=session_file, data=data)
        return None

    # Without explicit session_id, Phase 1 may return a stale session
    # (active=True but pane dead). Check how many active candidates exist.
    if not ccb_session_id:
        candidates = list_session_candidates(work_dir, ".codex-session")
        if len(candidates) > 1:
            # Multiple active candidates - use Phase 2 liveness probe to find the truly alive one.
            # Let AmbiguityError propagate to caller (fail-closed: don't silently pick one)
            resolved = _resolve_ambiguity(candidates)
            if resolved:
                data = _read_json(resolved)
                if data:
                    return CodexProjectSession(session_file=resolved, data=data)
            return None

    # Single candidate or precise match - use Phase 1 result
    # (let ensure_pane() handle pane death via respawn)
    if session_file:
        data = _read_json(session_file)
        if data:
            return CodexProjectSession(session_file=session_file, data=data)

    return None


def compute_session_key(session: CodexProjectSession) -> str:
    """Compute unique worker key for session.

    Priority: ccb_session_id > pane_id > marker > file
    """
    ccb_sid = session.ccb_session_id
    if ccb_sid:
        return f"ccb:{ccb_sid}"

    pane = session.pane_id
    if pane:
        return f"codex_pane:{pane}"

    marker = session.pane_title_marker
    if marker:
        return f"codex_marker:{marker}"

    return f"codex_file:{session.session_file}"
