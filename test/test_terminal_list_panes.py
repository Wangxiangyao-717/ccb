from terminal import TerminalBackend, WeztermBackend


def test_terminal_backend_has_list_panes_method():
    assert hasattr(TerminalBackend, 'list_panes')
    assert callable(getattr(TerminalBackend, 'list_panes', None))


def test_wezterm_backend_has_public_list_panes():
    backend = WeztermBackend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)
