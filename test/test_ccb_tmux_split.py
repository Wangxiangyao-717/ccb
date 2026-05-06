from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_run_up_sorts_providers_in_tmux(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")

    launcher = ccb.AILauncher(providers=["opencode", "gemini", "codex"])
    launcher.terminal_type = "tmux"

    called: list[str] = []

    def _start_provider(p: str) -> bool:
        called.append(p)
        return True

    monkeypatch.setattr(launcher, "_start_provider", _start_provider)
    monkeypatch.setattr(launcher, "_warmup_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_claude", lambda: 0)
    monkeypatch.setattr(launcher, "cleanup", lambda: None)

    rc = launcher.run_up()
    assert rc == 0
    assert called == ["codex", "gemini", "opencode"]


def test_start_codex_tmux_writes_bridge_pid(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMUX_PANE", "%0")

    # Ensure runtime dir lands under tmp_path.
    monkeypatch.setattr(ccb.tempfile, "gettempdir", lambda: str(tmp_path))

    # Avoid creating real FIFOs in unit tests.
    monkeypatch.setattr(ccb.os, "mkfifo", lambda p, _mode=0o600: Path(p).write_text("", encoding="utf-8"))

    # Fake tmux backend methods (no real tmux dependency).
    class _FakeTmuxBackend:
        def __init__(self, *args, **kwargs):
            self._created = 0

        def create_pane(
            self,
            cmd: str,
            cwd: str,
            direction: str = "right",
            percent: int = 50,
            parent_pane: str | None = None,
        ) -> str:
            self._created += 1
            return f"%{10 + self._created}"

        def set_pane_title(self, pane_id: str, title: str) -> None:
            return None

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            return None

        def respawn_pane(
            self,
            pane_id: str,
            *,
            cmd: str,
            cwd: str | None = None,
            stderr_log_path: str | None = None,
            remain_on_exit: bool = True,
        ) -> None:
            return None

    monkeypatch.setattr(ccb, "TmuxBackend", _FakeTmuxBackend)

    # Fake `tmux display-message ... #{pane_pid}`.
    def _fake_run(argv, *args, **kwargs):
        if argv[:3] == ["tmux", "display-message", "-p"] and "#{pane_pid}" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="12345\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ccb.subprocess, "run", _fake_run)

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 999

    monkeypatch.setattr(ccb.subprocess, "Popen", lambda *a, **k: _FakePopen(*a, **k))

    launcher = ccb.AILauncher(providers=["codex"])
    launcher.terminal_type = "tmux"

    assert launcher._start_codex_tmux() is True

    runtime = Path(launcher.runtime_dir) / "codex"
    assert (runtime / "bridge.pid").exists()
    assert (runtime / "bridge.pid").read_text(encoding="utf-8").strip() == "999"


def test_write_codex_session_uses_next_available_numbered_file(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    (tmp_path / ".codex-session").write_text(json.dumps({"session_id": "old-0"}), encoding="utf-8")
    (tmp_path / ".codex-session-1").write_text(json.dumps({"session_id": "old-1"}), encoding="utf-8")

    runtime = tmp_path / "runtime"
    runtime.mkdir()

    launcher = ccb.AILauncher(providers=["codex"])
    launcher.terminal_type = "wezterm"

    ok = launcher._write_codex_session(
        runtime,
        None,
        runtime / "input.fifo",
        runtime / "output.fifo",
        pane_id="20",
        pane_title_marker="CCB-Codex",
        codex_start_cmd="codex",
    )

    assert ok is True
    created = tmp_path / ".codex-session-2"
    assert created.exists()
    assert json.loads(created.read_text(encoding="utf-8"))["session_id"] == launcher.session_id
    assert json.loads((tmp_path / ".codex-session").read_text(encoding="utf-8"))["session_id"] == "old-0"


def test_cleanup_only_marks_current_session_file_inactive(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    launcher = ccb.AILauncher(providers=["codex"])
    launcher.terminal_type = "wezterm"
    launcher.wezterm_panes = {}

    other_file = tmp_path / ".codex-session"
    current_file = tmp_path / ".codex-session-1"
    other_file.write_text(json.dumps({"session_id": "other", "active": True}), encoding="utf-8")
    current_file.write_text(json.dumps({"session_id": launcher.session_id, "active": True}), encoding="utf-8")
    launcher.session_files = {"codex": current_file}

    registry_dir = home / ".ccb" / "run"
    registry_dir.mkdir(parents=True)
    registry_file = registry_dir / f"ccb-session-{launcher.session_id}.json"
    registry_file.write_text(json.dumps({"ccb_session_id": launcher.session_id}), encoding="utf-8")

    launcher.cleanup()

    assert json.loads(other_file.read_text(encoding="utf-8"))["active"] is True
    current_data = json.loads(current_file.read_text(encoding="utf-8"))
    assert current_data["active"] is False
    assert "ended_at" in current_data
    assert not registry_file.exists()


def test_get_latest_codex_session_id_writes_future_numbered_session_file(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    existing = tmp_path / ".codex-session"
    existing.write_text(json.dumps({"session_id": "other-session"}), encoding="utf-8")

    session_root = tmp_path / "codex-sessions"
    session_root.mkdir()
    log_path = session_root / "latest.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "cwd": str(tmp_path),
                    "id": "resume-target",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SESSION_ROOT", str(session_root))

    launcher = ccb.AILauncher(providers=["codex"])

    session_id, has_history = launcher._get_latest_codex_session_id()

    assert has_history is True
    assert session_id == "resume-target"
    assert json.loads(existing.read_text(encoding="utf-8"))["session_id"] == "other-session"
    future_file = tmp_path / ".codex-session-1"
    assert future_file.exists()
    future_data = json.loads(future_file.read_text(encoding="utf-8"))
    assert future_data["codex_session_id"] == "resume-target"
    assert future_data["codex_session_path"] == str(log_path)
