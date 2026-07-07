from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.core.config import SecurityConfig, Settings, TerminalConfig, WorkspaceConfig
from backend.app.core.errors import AppError
from backend.app.core.security import WorkspaceSecurity
from backend.app.main import create_app
from backend.app.providers.terminal.base import TerminalProvider
from backend.app.providers.terminal.local_pty import LocalPtyTerminalProvider
from backend.app.services.terminal_service import TerminalManager, TerminalSession, terminal_manager, utc_now


CLIENT_A = "client_test_a"
CLIENT_B = "client_test_b"
CLIENT_A_HEADERS = {"X-ChemSSH-Client-Id": CLIENT_A}
CLIENT_B_HEADERS = {"X-ChemSSH-Client-Id": CLIENT_B}


class FakeTerminalProvider(TerminalProvider):
    def __init__(self) -> None:
        self._shell = ""
        self.cwd = ""
        self.rows = 0
        self.cols = 0
        self.writes: list[str] = []
        self.alive = False

    @property
    def shell(self) -> str:
        return self._shell

    def start(self, cwd: str, rows: int, cols: int, shell: str | None = None) -> None:
        self.cwd = cwd
        self.rows = rows
        self.cols = cols
        self._shell = shell or "fake-shell"
        self.alive = True

    def write(self, data: str) -> None:
        self.writes.append(data)

    def read(self, size: int = 4096) -> str:
        return ""

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols

    def terminate(self) -> None:
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive


def test_terminal_config_defaults() -> None:
    settings = Settings()

    assert settings.terminal.enabled is True
    assert settings.terminal.max_sessions == 4
    assert settings.terminal.default_rows == 30
    assert settings.terminal.default_cols == 120
    assert settings.terminal.idle_timeout_seconds == 3600
    assert settings.terminal.allow_sync_cwd is True


def test_windows_powershell_args_configure_interactive_encoding() -> None:
    provider = LocalPtyTerminalProvider()
    provider._shell = "powershell.exe"

    args = provider._shell_args()

    assert "-NoExit" in args
    assert "-NoProfile" in args
    assert "[Console]::InputEncoding" in args[-1]
    assert "Set-PSReadLineOption" in args[-1]


def test_terminal_env_drops_inherited_python_activation_state(tmp_path: Path) -> None:
    from backend.app.providers.terminal.local_pty import _clean_inherited_terminal_env

    venv = tmp_path / ".venv"
    conda = tmp_path / "miniconda3" / "envs" / "base"
    normal_bin = tmp_path / "bin"
    for path in (venv / "bin", conda / "bin", normal_bin):
        path.mkdir(parents=True)

    env = _clean_inherited_terminal_env(
        {
            "PATH": os.pathsep.join([str(venv / "bin"), str(conda / "bin"), str(normal_bin)]),
            "VIRTUAL_ENV": str(venv),
            "VIRTUAL_ENV_PROMPT": "(.venv)",
            "CONDA_PREFIX": str(conda),
            "CONDA_DEFAULT_ENV": "base",
            "CONDA_SHLVL": "1",
            "PYTHONHOME": str(tmp_path / "python-home"),
            "SHELL": "/bin/bash",
        }
    )

    assert env["PATH"] == str(normal_bin)
    assert "VIRTUAL_ENV" not in env
    assert "VIRTUAL_ENV_PROMPT" not in env
    assert "CONDA_PREFIX" not in env
    assert "CONDA_DEFAULT_ENV" not in env
    assert "CONDA_SHLVL" not in env
    assert "PYTHONHOME" not in env
    assert env["SHELL"] == "/bin/bash"


def test_terminal_shims_include_vim_terminal_probe_workaround(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = LocalPtyTerminalProvider()

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    shim_dir = provider._ensure_transfer_shims()

    for command in ("vi", "vim"):
        wrapper = shim_dir / command
        assert wrapper.exists()
        content = wrapper.read_text(encoding="utf-8")
        assert "--cmd 'set t_RV= t_u7= t_RF= t_RB= t_RK='" in content
        assert f"real_command=$(command -v {command} 2>/dev/null)" in content


def test_terminal_shims_skip_vim_wrappers_when_compatibility_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = LocalPtyTerminalProvider()
    provider.vim_compatibility = False

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    shim_dir = provider._ensure_transfer_shims()

    assert (shim_dir / "rz").exists()
    assert (shim_dir / "sz").exists()
    assert not (shim_dir / "vi").exists()
    assert not (shim_dir / "vim").exists()


def test_transfer_shim_posix_wrapper_prefers_packaged_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = LocalPtyTerminalProvider()

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    shim_dir = provider._ensure_transfer_shims()

    helper = shim_dir / "_chemssh_transfer_shim.py"
    helper_content = helper.read_text(encoding="utf-8")
    wrapper = (shim_dir / "sz").read_text(encoding="utf-8")

    assert helper_content.startswith("from __future__ import annotations\n")
    assert "--terminal-transfer-shim 'download'" in wrapper
    assert "_chemssh_transfer_shim.py" in wrapper
    assert "'download' \"$@\"" in wrapper
    assert str(Path(sys.executable)) in wrapper
    assert "CHEMSSH_TRANSFER_PYTHON" not in wrapper
    assert "command -v python3" not in wrapper


def test_terminal_session_sync_cwd_validates_workspace(tmp_path: Path) -> None:
    provider = FakeTerminalProvider()
    provider.start(str(tmp_path), 24, 80)
    created_at = utc_now()
    session = TerminalSession(
        session_id="term_test",
        client_id=CLIENT_A,
        provider=provider,
        cwd=str(tmp_path),
        created_at=created_at,
        last_active_at=created_at,
        security=WorkspaceSecurity(tmp_path),
        allow_sync_cwd=True,
    )
    subdir = tmp_path / "calc"
    subdir.mkdir()

    session.sync_cwd(str(subdir))

    assert session.cwd == str(subdir.resolve())
    assert provider.writes == [provider.build_cd_command(str(subdir.resolve()))]

    with pytest.raises(AppError) as exc:
        session.sync_cwd(str(tmp_path.parent))

    assert exc.value.code == "FORBIDDEN_PATH"


def test_terminal_session_extracts_cwd_marker_without_echo(tmp_path: Path) -> None:
    provider = FakeTerminalProvider()
    provider.start(str(tmp_path), 24, 80)
    created_at = utc_now()
    session = TerminalSession(
        session_id="term_test",
        client_id=CLIENT_A,
        provider=provider,
        cwd=str(tmp_path),
        created_at=created_at,
        last_active_at=created_at,
        security=WorkspaceSecurity(tmp_path),
        allow_sync_cwd=True,
    )
    subdir = tmp_path / "calc"
    subdir.mkdir()

    provider.writes.clear()
    provider.read = lambda size=4096: f"before\x1b]633;P;Cwd={subdir}\x07after"  # type: ignore[method-assign]

    assert session.read() == "beforeafter"
    assert session.cwd == str(subdir.resolve())
    assert session.consume_cwd_update() == str(subdir.resolve())
    assert session.consume_cwd_update() is None


def test_terminal_session_emits_command_done_after_user_command(tmp_path: Path) -> None:
    provider = FakeTerminalProvider()
    provider.start(str(tmp_path), 24, 80)
    created_at = utc_now()
    session = TerminalSession(
        session_id="term_test",
        client_id=CLIENT_A,
        provider=provider,
        cwd=str(tmp_path),
        created_at=created_at,
        last_active_at=created_at,
        security=WorkspaceSecurity(tmp_path),
        allow_sync_cwd=True,
    )

    provider.read = lambda size=4096: f"\x1b]633;P;Cwd={tmp_path}\x07"  # type: ignore[method-assign]
    assert session.read() == ""
    assert session.consume_command_done() == []

    session.write("touch result.out\r")
    assert session.read() == ""
    [event] = session.consume_command_done()

    assert event.seq == 1
    assert event.cwd == str(tmp_path.resolve())
    assert event.to_message() == {"type": "command_done", "seq": 1, "path": str(tmp_path.resolve())}


def test_terminal_session_does_not_accumulate_command_done_inside_repl(tmp_path: Path) -> None:
    provider = FakeTerminalProvider()
    provider.start(str(tmp_path), 24, 80)
    created_at = utc_now()
    session = TerminalSession(
        session_id="term_test",
        client_id=CLIENT_A,
        provider=provider,
        cwd=str(tmp_path),
        created_at=created_at,
        last_active_at=created_at,
        security=WorkspaceSecurity(tmp_path),
        allow_sync_cwd=True,
    )

    provider.read = lambda size=4096: f"\x1b]633;P;Cwd={tmp_path}\x07"  # type: ignore[method-assign]
    assert session.read() == ""
    assert session.consume_command_done() == []

    session.write("python\r")
    session.write("print(1)\r")
    session.write("exit()\r")

    assert session.read() == ""
    [event] = session.consume_command_done()
    assert event.seq == 1

    assert session.read() == ""
    assert session.consume_command_done() == []


def test_terminal_session_emits_command_done_for_pasted_shell_commands(tmp_path: Path) -> None:
    provider = FakeTerminalProvider()
    provider.start(str(tmp_path), 24, 80)
    created_at = utc_now()
    session = TerminalSession(
        session_id="term_test",
        client_id=CLIENT_A,
        provider=provider,
        cwd=str(tmp_path),
        created_at=created_at,
        last_active_at=created_at,
        security=WorkspaceSecurity(tmp_path),
        allow_sync_cwd=True,
    )

    cwd_marker = f"\x1b]633;P;Cwd={tmp_path}\x07"
    provider.read = lambda size=4096: f"{cwd_marker}{cwd_marker}"  # type: ignore[method-assign]
    session.write("touch a.out\r touch b.out\n")

    assert session.read() == ""
    events = session.consume_command_done()
    assert [event.seq for event in events] == [1, 2]


def test_terminal_manager_enforces_session_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.app.services.terminal_service.LocalPtyTerminalProvider", FakeTerminalProvider)
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path),
        terminal=TerminalConfig(max_sessions=1, default_rows=33, default_cols=101),
    )
    manager = TerminalManager()

    session = manager.create_session(settings, CLIENT_A, None, None, None, None)

    assert session.cwd == str(tmp_path.resolve())
    assert session.provider.rows == 33
    assert session.provider.cols == 101

    with pytest.raises(AppError) as exc:
        manager.create_session(settings, CLIENT_A, None, None, None, None)

    assert exc.value.code == "TERMINAL_LIMIT_REACHED"
    other_session = manager.create_session(settings, CLIENT_B, None, None, None, None)
    assert other_session.client_id == CLIENT_B

    manager.close_session(session.session_id, CLIENT_A)
    manager.close_session(other_session.session_id, CLIENT_B)


def test_terminal_manager_passes_vim_compatibility_to_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("backend.app.services.terminal_service.LocalPtyTerminalProvider", FakeTerminalProvider)
    settings = Settings(workspace=WorkspaceConfig(root=tmp_path))
    manager = TerminalManager()

    session = manager.create_session(settings, CLIENT_A, None, None, None, None, vim_compatibility=False)

    assert getattr(session.provider, "vim_compatibility") is False

    manager.close_session(session.session_id, CLIENT_A)


def test_terminal_manager_releases_session_after_client_disconnect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("backend.app.services.terminal_service.LocalPtyTerminalProvider", FakeTerminalProvider)
    settings = Settings(workspace=WorkspaceConfig(root=tmp_path))
    manager = TerminalManager()
    session = manager.create_session(settings, CLIENT_A, None, None, None, None)

    attached = manager.attach_client(session.session_id, CLIENT_A)
    manager.detach_client(session.session_id, CLIENT_A)

    assert attached.clients == 0
    assert attached.is_alive() is False
    assert manager.list_sessions(CLIENT_A) == []


def test_terminal_manager_scopes_sessions_to_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("backend.app.services.terminal_service.LocalPtyTerminalProvider", FakeTerminalProvider)
    settings = Settings(workspace=WorkspaceConfig(root=tmp_path))
    manager = TerminalManager()

    session_a = manager.create_session(settings, CLIENT_A, None, None, None, None)
    session_b = manager.create_session(settings, CLIENT_B, None, None, None, None)

    assert [item.session_id for item in manager.list_sessions(CLIENT_A)] == [session_a.session_id]
    assert [item.session_id for item in manager.list_sessions(CLIENT_B)] == [session_b.session_id]

    with pytest.raises(AppError) as exc:
        manager.close_session(session_b.session_id, CLIENT_A)

    assert exc.value.code == "TERMINAL_SESSION_NOT_FOUND"
    assert session_b.is_alive() is True

    with pytest.raises(AppError) as exc:
        manager.attach_client(session_b.session_id, CLIENT_A)

    assert exc.value.code == "TERMINAL_SESSION_NOT_FOUND"

    manager.close_session(session_a.session_id, CLIENT_A)
    manager.close_session(session_b.session_id, CLIENT_B)


def test_terminal_sessions_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.app.services.terminal_service.LocalPtyTerminalProvider", FakeTerminalProvider)
    terminal_manager.sessions.clear()
    settings = Settings(workspace=WorkspaceConfig(root=tmp_path))
    client = TestClient(create_app(settings))

    missing_client = client.get("/api/terminal/sessions")
    assert missing_client.status_code == 400
    assert missing_client.json()["error"]["code"] == "CLIENT_ID_REQUIRED"

    created = client.post(
        "/api/terminal/sessions",
        headers=CLIENT_A_HEADERS,
        json={"cwd": str(tmp_path), "rows": 20, "cols": 80, "vim_compatibility": False},
    )

    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert getattr(terminal_manager.sessions[session_id].provider, "vim_compatibility") is False

    listed = client.get("/api/terminal/sessions", headers=CLIENT_A_HEADERS)
    assert listed.status_code == 200
    assert any(item["session_id"] == session_id for item in listed.json()["items"])

    other_listed = client.get("/api/terminal/sessions", headers=CLIENT_B_HEADERS)
    assert other_listed.status_code == 200
    assert other_listed.json()["items"] == []

    wrong_client_close = client.delete(f"/api/terminal/sessions/{session_id}", headers=CLIENT_B_HEADERS)
    assert wrong_client_close.status_code == 404

    closed = client.delete(f"/api/terminal/sessions/{session_id}", headers=CLIENT_A_HEADERS)
    assert closed.status_code == 200
    assert closed.json()["success"] is True


def test_terminal_websocket_requires_token_when_enabled(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path),
        security=SecurityConfig(enable_token=True, token="test-secret"),
    )
    client = TestClient(create_app(settings))

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/terminal/ws/missing?client_id={CLIENT_A}"):
            pass

    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# Bidirectional cwd sync: bash --rcfile init script
# ---------------------------------------------------------------------------


def test_terminal_cwd_init_script_contains_expected_snippets() -> None:
    """The embedded bash init script must emulate login profile loading, install
    the cwd marker, and stay idempotent with env-level PROMPT_COMMAND injection."""
    from backend.app.providers.terminal.local_pty import _CHEMSSH_CWD_INIT_SH

    assert _CHEMSSH_CWD_INIT_SH
    # Marker fragment emitted by PROMPT_COMMAND (matches backend terminal_service.py parser)
    assert "033]633;P;Cwd=" in _CHEMSSH_CWD_INIT_SH
    assert "PROMPT_COMMAND" in _CHEMSSH_CWD_INIT_SH
    # Preserve login-shell behavior while allowing the marker to run after profiles
    assert "source /etc/profile" in _CHEMSSH_CWD_INIT_SH
    assert "${HOME}/.bash_profile" in _CHEMSSH_CWD_INIT_SH
    assert "${HOME}/.bash_login" in _CHEMSSH_CWD_INIT_SH
    assert "${HOME}/.profile" in _CHEMSSH_CWD_INIT_SH
    # Idempotency: pattern guard for double-chaining the marker
    assert '"]633;P;Cwd="*)' in _CHEMSSH_CWD_INIT_SH


def test_local_pty_emits_cwd_init_script_into_transfer_shim_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_chemssh_cwd_init_path writes the init script next to the transfer shims and
    reuses it on subsequent calls. terminate() must clean up the script."""
    provider = LocalPtyTerminalProvider()

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    init_path_1 = provider._chemssh_cwd_init_path()
    init_path_2 = provider._chemssh_cwd_init_path()

    assert init_path_1 == init_path_2
    assert init_path_1.exists()
    assert init_path_1.parent == provider._ensure_transfer_shims()
    assert "033]633;P;Cwd=" in init_path_1.read_text(encoding="utf-8")

    provider.terminate()
    assert not init_path_1.exists()


def test_local_pty_env_injects_prompt_command_marker_for_bash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: even before --rcfile kicks in, _terminal_env() injects the
    bidirectional cwd marker into PROMPT_COMMAND for bash-compatible shells."""
    monkeypatch.delenv("PROMPT_COMMAND", raising=False)
    monkeypatch.setattr("backend.app.providers.terminal.local_pty._is_windows", lambda: False)

    provider = LocalPtyTerminalProvider()
    provider._shell = "/bin/bash"
    env = provider._terminal_env()

    assert "PROMPT_COMMAND" in env
    assert "033]633;P;Cwd=" in env["PROMPT_COMMAND"]
    # printf format args from the marker
    assert "%s" in env["PROMPT_COMMAND"]
    assert "$PWD" in env["PROMPT_COMMAND"]


def test_local_pty_start_uses_rcfile_for_bash_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bash is launched with --rcfile <our-init-script> -i; the init script
    emulates login profile loading and then recovers PROMPT_COMMAND."""
    captured: dict[str, object] = {}

    class FakePtyProcessUnicode:
        @staticmethod
        def spawn(argv, **kwargs):
            captured["argv"] = tuple(argv)
            captured["kwargs"] = kwargs
            return None

    monkeypatch.setitem(sys.modules, "ptyprocess", SimpleNamespace(PtyProcessUnicode=FakePtyProcessUnicode))
    monkeypatch.setattr("backend.app.providers.terminal.local_pty._is_windows", lambda: False)

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    provider = LocalPtyTerminalProvider()
    provider.vim_compatibility = False
    provider.start(cwd=str(tmp_path), rows=30, cols=120, shell="/bin/bash")

    argv = captured["argv"]
    assert argv[0] == "/bin/bash"
    assert "-l" not in argv
    assert "--rcfile" in argv
    assert "-i" in argv

    rcfile_path = Path(argv[argv.index("--rcfile") + 1])
    assert rcfile_path.exists()
    init_content = rcfile_path.read_text(encoding="utf-8")
    assert "PROMPT_COMMAND" in init_content
    assert "033]633;P;Cwd=" in init_content
    assert "source /etc/profile" in init_content


def test_local_pty_start_skips_rcfile_for_non_bash_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-bash shells (e.g. zsh, fish) keep the original -l launch; --rcfile is bash-specific."""
    captured: dict[str, object] = {}

    class FakePtyProcessUnicode:
        @staticmethod
        def spawn(argv, **kwargs):
            captured["argv"] = tuple(argv)
            return None

    monkeypatch.setitem(sys.modules, "ptyprocess", SimpleNamespace(PtyProcessUnicode=FakePtyProcessUnicode))
    monkeypatch.setattr("backend.app.providers.terminal.local_pty._is_windows", lambda: False)

    def fake_mkdtemp(prefix: str) -> str:
        path = tmp_path / prefix.rstrip("-")
        path.mkdir()
        return str(path)

    monkeypatch.setattr("backend.app.providers.terminal.local_pty.tempfile.mkdtemp", fake_mkdtemp)

    provider = LocalPtyTerminalProvider()
    provider.vim_compatibility = False
    provider.start(cwd=str(tmp_path), rows=30, cols=120, shell="/bin/zsh")

    argv = captured["argv"]
    assert argv[0] == "/bin/zsh"
    assert "-l" in argv
    assert "--rcfile" not in argv
