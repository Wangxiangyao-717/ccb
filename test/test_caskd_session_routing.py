from pathlib import Path
from caskd_session import CodexProjectSession


def test_codex_project_session_has_ccb_session_id_property(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    data = {"session_id": "ai-1716890000-12345", "runtime_dir": str(tmp_path), "terminal": "tmux"}
    session = CodexProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"


def test_codex_project_session_ccb_session_id_priority(tmp_path):
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")

    # New format takes priority
    data = {"ccb_session_id": "new", "session_id": "old"}
    session = CodexProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "new"

    # Falls back to session_id
    data2 = {"session_id": "old-format"}
    session2 = CodexProjectSession(session_file=session_file, data=data2)
    assert session2.ccb_session_id == "old-format"
