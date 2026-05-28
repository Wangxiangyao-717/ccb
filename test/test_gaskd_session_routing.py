import json
from pathlib import Path
from gaskd_session import GeminiProjectSession, compute_session_key, load_project_session


def test_gemini_project_session_has_ccb_session_id_property(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"session_id": "ai-1716890000-12345"}
    session = GeminiProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"


def test_gemini_project_session_ccb_session_id_priority(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")

    # New format takes priority
    data = {"ccb_session_id": "new", "session_id": "old"}
    session = GeminiProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "new"

    # Falls back to session_id
    data2 = {"session_id": "old-format"}
    session2 = GeminiProjectSession(session_file=session_file, data=data2)
    assert session2.ccb_session_id == "old-format"


def test_compute_session_key_prioritizes_ccb_session_id_gemini(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"session_id": "ai-1716890000-12345", "pane_id": "%42", "pane_title_marker": "CCB-Gemini"}
    session = GeminiProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "ccb:ai-1716890000-12345"


def test_compute_session_key_falls_back_to_pane_id_gemini(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"pane_id": "%42", "pane_title_marker": "CCB-Gemini"}
    session = GeminiProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "gemini_pane:%42"


def test_load_project_session_with_ccb_session_id_gemini(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text(json.dumps({
        "session_id": "target-session", "active": True,
    }), encoding="utf-8")

    session = load_project_session(tmp_path, ccb_session_id="target-session")
    assert session is not None
    assert session.ccb_session_id == "target-session"


def test_load_project_session_ccb_session_id_no_fallback_gemini(tmp_path):
    session_file = tmp_path / ".gemini-session"
    session_file.write_text(json.dumps({
        "session_id": "session-A", "active": True,
    }), encoding="utf-8")

    session = load_project_session(tmp_path, ccb_session_id="session-B")
    assert session is None
