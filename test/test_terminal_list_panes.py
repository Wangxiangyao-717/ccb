from terminal import TerminalBackend, WeztermBackend, TmuxBackend, Iterm2Backend


def test_terminal_backend_has_list_panes_method():
    assert hasattr(TerminalBackend, 'list_panes')
    assert callable(getattr(TerminalBackend, 'list_panes', None))


def test_wezterm_backend_has_public_list_panes():
    backend = WeztermBackend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)


def test_tmux_backend_has_list_panes():
    backend = TmuxBackend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)


def test_iterm2_backend_has_list_panes():
    backend = Iterm2Backend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)
