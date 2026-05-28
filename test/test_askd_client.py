import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from askd_client import _read_ccb_session_id
from providers import CASK_CLIENT_SPEC, GASK_CLIENT_SPEC, OASK_CLIENT_SPEC


def test_read_ccb_session_id_prefers_ccb_env(monkeypatch):
    monkeypatch.setenv("CCB_SESSION_ID", "ccb-id")
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-id")
    assert _read_ccb_session_id(CASK_CLIENT_SPEC) == "ccb-id"


def test_read_ccb_session_id_falls_back_to_legacy(monkeypatch):
    monkeypatch.delenv("CCB_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-id")
    assert _read_ccb_session_id(CASK_CLIENT_SPEC) == "codex-id"


def test_read_ccb_session_id_gemini_uses_gemini_env(monkeypatch):
    monkeypatch.delenv("CCB_SESSION_ID", raising=False)
    monkeypatch.setenv("GEMINI_SESSION_ID", "gemini-id")
    assert _read_ccb_session_id(GASK_CLIENT_SPEC) == "gemini-id"


def test_read_ccb_session_id_opencode_uses_opencode_env(monkeypatch):
    monkeypatch.delenv("CCB_SESSION_ID", raising=False)
    monkeypatch.setenv("OPENCODE_SESSION_ID", "opencode-id")
    assert _read_ccb_session_id(OASK_CLIENT_SPEC) == "opencode-id"


def test_read_ccb_session_id_returns_none_when_not_set(monkeypatch):
    monkeypatch.delenv("CCB_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert _read_ccb_session_id(CASK_CLIENT_SPEC) is None


def test_read_ccb_session_id_strips_whitespace(monkeypatch):
    monkeypatch.setenv("CCB_SESSION_ID", "  ccb-id  ")
    assert _read_ccb_session_id(CASK_CLIENT_SPEC) == "ccb-id"


def test_read_ccb_session_id_empty_ccb_falls_back(monkeypatch):
    monkeypatch.setenv("CCB_SESSION_ID", "   ")
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-id")
    assert _read_ccb_session_id(CASK_CLIENT_SPEC) == "codex-id"
