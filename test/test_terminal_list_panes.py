from terminal import TerminalBackend


def test_terminal_backend_has_list_panes_method():
    assert hasattr(TerminalBackend, 'list_panes')
    assert callable(getattr(TerminalBackend, 'list_panes', None))
