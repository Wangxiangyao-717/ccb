from ccb_protocol import CaskdRequest


def test_caskd_request_accepts_ccb_session_id():
    """CaskdRequest should accept ccb_session_id as optional field"""
    req = CaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message",
        ccb_session_id="ai-1716890000-12345",
        caller_pane_id="pane-1",
    )
    assert req.ccb_session_id == "ai-1716890000-12345"
    assert req.caller_pane_id == "pane-1"


def test_caskd_request_ccb_session_id_optional():
    """ccb_session_id should be optional (backward compatibility)"""
    req = CaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message",
    )
    assert req.ccb_session_id is None


from gaskd_protocol import GaskdRequest
from oaskd_protocol import OaskdRequest


def test_gaskd_request_accepts_ccb_session_id():
    req = GaskdRequest(
        client_id="test", work_dir="/tmp", timeout_s=300.0,
        quiet=False, message="test", ccb_session_id="ai-123"
    )
    assert req.ccb_session_id == "ai-123"
    assert not hasattr(req, 'caller_pane_id')


def test_oaskd_request_accepts_ccb_session_id():
    req = OaskdRequest(
        client_id="test", work_dir="/tmp", timeout_s=300.0,
        quiet=False, message="test", ccb_session_id="ai-123"
    )
    assert req.ccb_session_id == "ai-123"
    assert not hasattr(req, 'caller_pane_id')
