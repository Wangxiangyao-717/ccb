"""
scope_key.py - Per-control-instance daemon scope computation.

Each WezTerm GUI instance / tmux server gets its own daemon process,
keyed by scope_key = (backend_kind, terminal_control_endpoint, work_dir).

This prevents daemons started in different terminal windows from
interfering with each other's wezterm cli / tmux commands.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional


def detect_terminal_control_endpoint() -> Optional[tuple[str, str]]:
    """Detect terminal backend and control endpoint.

    Returns (backend_kind, endpoint) or None if no supported terminal detected.

    Priority:
      WezTerm: WEZTERM_UNIX_SOCKET > WEZTERM_PANE / CODEX_WEZTERM_PANE > None
      tmux:    TMUX > TMUX_PANE > None
    """
    # WezTerm
    sock = (os.environ.get("WEZTERM_UNIX_SOCKET") or "").strip()
    if sock:
        return ("wezterm", _normalize_endpoint(sock))
    pane = (
        (os.environ.get("WEZTERM_PANE") or "").strip()
        or (os.environ.get("CODEX_WEZTERM_PANE") or "").strip()
    )
    if pane:
        return ("wezterm", f"pane-{pane}")

    # tmux
    tmux = (os.environ.get("TMUX") or "").strip()
    if tmux:
        return ("tmux", _normalize_endpoint(tmux))
    tmux_pane = (os.environ.get("TMUX_PANE") or "").strip()
    if tmux_pane:
        return ("tmux", f"pane-{tmux_pane}")

    return None


def _normalize_endpoint(value: str) -> str:
    """Normalize a socket path for consistent hashing."""
    if not value:
        return ""
    if os.name == "nt":
        return str(Path(value).resolve()).lower()
    return value


def compute_scope_key(
    work_dir: str,
    endpoint: Optional[tuple[str, str]] = None,
) -> Optional[dict]:
    """Compute scope_key dict for daemon isolation.

    Returns None if terminal control endpoint cannot be detected
    (daemon mode should be disabled in this case).
    """
    if endpoint is None:
        endpoint = detect_terminal_control_endpoint()
    if endpoint is None:
        return None
    backend_kind, control_endpoint = endpoint
    return {
        "backend_kind": backend_kind,
        "terminal_control_endpoint": control_endpoint,
        "work_dir": str(Path(work_dir).resolve()).lower(),
    }


def scope_key_digest(scope_key: dict) -> str:
    """SHA256 hash of scope_key, first 16 hex chars."""
    raw = json.dumps(scope_key, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def scoped_state_file(provider_name: str, scope_digest: str) -> Path:
    """Generate scoped state file path: run_dir()/{provider}-{scope}.json"""
    from askd_runtime import run_dir
    return run_dir() / f"{provider_name}-{scope_digest}.json"


def resolve_state_file(
    provider_name: str,
    *,
    env_override: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> Optional[Path]:
    """Resolve state file path with priority:

    1. env_override (CCB_*_STATE_FILE) - highest priority
    2. scoped state file (if work_dir and terminal detected)
    3. None (caller decides fallback to global)
    """
    if env_override:
        return Path(env_override).expanduser()
    if work_dir:
        scope_key = compute_scope_key(work_dir)
        if scope_key:
            return scoped_state_file(provider_name, scope_key_digest(scope_key))
    return None
