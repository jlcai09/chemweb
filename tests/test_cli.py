from argparse import Namespace
from pathlib import Path

import pytest

from backend.app import cli


def _args(reuse_existing: str) -> Namespace:
    return Namespace(reuse_existing=reuse_existing)


def test_cli_transfer_shim_hidden_mode_bypasses_regular_parser(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_transfer_shim(args):
        calls.append(list(args))
        return 7

    monkeypatch.setattr(cli, "_run_terminal_transfer_shim", fake_transfer_shim)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--terminal-transfer-shim", "download", "/tmp/shim", "result.out"])

    assert exc.value.code == 7
    assert calls == [["download", "/tmp/shim", "result.out"]]


def test_cli_reuses_existing_chemssh_with_same_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    existing = cli.ExistingChemSSHServer(
        url="http://127.0.0.1:8888",
        project_version="0.2.0",
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, chemssh=existing),
    )

    reused = cli._handle_existing_server(_args("auto"), "127.0.0.1", 8888, tmp_path)

    assert reused is True
    assert "reusing the existing server" in capsys.readouterr().out


def test_cli_rejects_existing_chemssh_with_different_workspace(tmp_path: Path, monkeypatch) -> None:
    existing = cli.ExistingChemSSHServer(
        url="http://127.0.0.1:8888",
        project_version="0.2.0",
        workspace_root=str(tmp_path / "other"),
    )
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, chemssh=existing),
    )

    with pytest.raises(RuntimeError, match="different workspace"):
        cli._handle_existing_server(_args("auto"), "127.0.0.1", 8888, tmp_path)


def test_cli_can_reuse_any_existing_chemssh(tmp_path: Path, monkeypatch) -> None:
    existing = cli.ExistingChemSSHServer(
        url="http://127.0.0.1:8888",
        project_version="0.2.0",
        workspace_root=str(tmp_path / "other"),
    )
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, chemssh=existing),
    )

    reused = cli._handle_existing_server(_args("any-chemssh"), "127.0.0.1", 8888, tmp_path)

    assert reused is True


def test_cli_rejects_non_chemssh_port_occupant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, detail="HTTP 404"),
    )

    with pytest.raises(RuntimeError, match="not a reusable ChemSSH server"):
        cli._handle_existing_server(_args("auto"), "127.0.0.1", 8888, tmp_path)


def test_cli_does_not_reuse_when_port_is_free(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=False),
    )

    reused = cli._handle_existing_server(_args("auto"), "127.0.0.1", 8888, tmp_path)

    assert reused is False


def test_cli_check_port_reports_available_port(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=False),
    )

    exit_code = cli._check_port_only(_args("auto"), "127.0.0.1", 8888, tmp_path)

    assert exit_code == 0
    assert "available" in capsys.readouterr().out


def test_cli_check_port_reports_reusable_chemssh(tmp_path: Path, monkeypatch, capsys) -> None:
    existing = cli.ExistingChemSSHServer(
        url="http://127.0.0.1:8888",
        project_version="0.2.0",
        workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, chemssh=existing),
    )

    exit_code = cli._check_port_only(_args("auto"), "127.0.0.1", 8888, tmp_path)

    assert exit_code == 0
    assert "reusable ChemSSH" in capsys.readouterr().out


def test_cli_check_port_rejects_unusable_port(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "_probe_existing_server",
        lambda host, port: cli.PortProbeResult(occupied=True, detail="HTTP 404"),
    )

    exit_code = cli._check_port_only(_args("auto"), "127.0.0.1", 8888, tmp_path)

    assert exit_code == 1
    assert "not a reusable ChemSSH server" in capsys.readouterr().err
