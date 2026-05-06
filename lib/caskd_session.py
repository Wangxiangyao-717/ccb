from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ccb_config import apply_backend_env
from pane_registry import load_registry_by_claude_pane
from session_utils import find_project_session_file as _find_project_session_file, safe_write_session
from terminal import get_backend_for_session

apply_backend_env()


def find_project_session_file(work_dir: Path) -> Optional[Path]:
    return _find_project_session_file(work_dir, ".codex-session")


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


def _load_registry_backed_session(work_dir: Path, caller_pane_id: Optional[str]) -> Optional["CodexProjectSession"]:
    pane_id = str(caller_pane_id or "").strip()
    if not pane_id:
        return None

    record = load_registry_by_claude_pane(pane_id)
    if not isinstance(record, dict):
        return None

    expected_work_dir = _normalize_work_dir(work_dir)
    record_work_dir = _normalize_work_dir(record.get("work_dir_norm") or record.get("work_dir") or "")
    if expected_work_dir and record_work_dir and expected_work_dir != record_work_dir:
        return None

    runtime_dir = str(record.get("codex_runtime_dir") or "").strip()
    codex_pane_id = str(record.get("codex_pane_id") or "").strip()
    terminal = str(record.get("codex_terminal") or record.get("terminal") or "").strip()
    if not runtime_dir or not codex_pane_id or not terminal:
        return None

    session_file = _find_project_session_file(work_dir, ".codex-session") or (Path(work_dir).resolve() / ".codex-session")
    data = {
        "session_id": str(record.get("ccb_session_id") or "").strip(),
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
    return CodexProjectSession(session_file=session_file, data=data)


@dataclass
class CodexProjectSession:
    session_file: Path
    data: dict

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


def load_project_session(work_dir: Path, caller_pane_id: Optional[str] = None) -> Optional[CodexProjectSession]:
    caller_pane_id = (
        str(caller_pane_id or "").strip()
        or (os.environ.get("WEZTERM_PANE") or "").strip()
        or (os.environ.get("TMUX_PANE") or "").strip()
    )
    session = _load_registry_backed_session(work_dir, caller_pane_id)
    if session is not None:
        return session

    session_file = find_project_session_file(work_dir)
    if not session_file:
        return None
    data = _read_json(session_file)
    if not data:
        return None
    return CodexProjectSession(session_file=session_file, data=data)


def compute_session_key(session: CodexProjectSession) -> str:
    # Use a stable per-pane key for serialization.
    # codex_session_id can change when the user runs `codex resume` inside the same pane; using it as a key
    # can accidentally create a second worker and cause concurrent injection to the same pane.
    marker = session.pane_title_marker
    if marker:
        return f"codex_marker:{marker}"
    pane = session.pane_id
    if pane:
        return f"codex_pane:{pane}"
    sid = session.codex_session_id
    if sid:
        return f"codex:{sid}"
    return f"codex_file:{session.session_file}"
