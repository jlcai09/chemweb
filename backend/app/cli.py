from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Delay import of heavy modules (uvicorn, fastapi, create_app) until actually needed
# This significantly speeds up --check-port and reuse-existing scenarios


@dataclass(frozen=True)
class ExistingChemSSHServer:
    url: str
    project_version: str | None
    workspace_root: str | None


@dataclass(frozen=True)
class PortProbeResult:
    occupied: bool
    chemssh: ExistingChemSSHServer | None = None
    detail: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chemssh")
    parser.add_argument("--config", help="Path to YAML config file")
    parser.add_argument("--workspace-root", help="Workspace root directory")
    parser.add_argument("--host", help="Bind host, defaults to config server.host")
    parser.add_argument("--port", type=int, help="Bind port, defaults to config server.port")
    parser.add_argument(
        "--reuse-existing",
        choices=["auto", "never", "any-chemssh"],
        default="auto",
        help=(
            "How to handle an occupied port before startup. "
            "'auto' reuses an existing ChemSSH server with the same workspace, "
            "'any-chemssh' reuses any ChemSSH server on that port, and 'never' fails."
        ),
    )
    parser.add_argument(
        "--check-port",
        action="store_true",
        help="Check the configured host and port, then exit without starting the server.",
    )
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload")
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable ChemSSH debug mode, including expensive structure streaming timing probes.",
    )
    return parser


def _run_terminal_transfer_shim(args: Sequence[str]) -> int:
    if len(args) < 2:
        print("chemssh: --terminal-transfer-shim requires direction and shim directory", file=sys.stderr)
        return 2
    direction, shim_dir, *transfer_args = args
    if direction not in {"upload", "download"}:
        print(f"chemssh: invalid transfer shim direction: {direction}", file=sys.stderr)
        return 2
    from backend.app.providers.terminal.local_pty import run_transfer_shim

    return run_transfer_shim(direction, shim_dir, transfer_args)


def _probe_host(host: str) -> str:
    if host in {"0.0.0.0", ""}:
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _host_for_url(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _server_url(host: str, port: int) -> str:
    client_host = _probe_host(host)
    return f"http://{_host_for_url(client_host)}:{port}"


def _same_workspace(left: str | None, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(left).expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return False


def _probe_existing_server(host: str, port: int, *, token: str | None = None, timeout: float = 1.0) -> PortProbeResult:
    client_host = _probe_host(host)

    # Use shorter timeout for localhost connections (should be instant)
    socket_timeout = 0.1 if client_host in ("127.0.0.1", "::1") else timeout

    try:
        with socket.create_connection((client_host, port), timeout=socket_timeout):
            pass
    except OSError:
        return PortProbeResult(occupied=False)

    base_url = _server_url(host, port)
    identity_url = f"{base_url}/api/system/identity"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(identity_url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return PortProbeResult(occupied=True, detail=f"HTTP {exc.code} from {identity_url}")
    except (OSError, URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return PortProbeResult(occupied=True, detail=str(exc))

    if not isinstance(payload, dict) or payload.get("app") != "chemssh":
        return PortProbeResult(occupied=True, detail=f"{identity_url} did not return a ChemSSH identity")

    return PortProbeResult(
        occupied=True,
        chemssh=ExistingChemSSHServer(
            url=base_url,
            project_version=payload.get("project_version") if isinstance(payload.get("project_version"), str) else None,
            workspace_root=payload.get("workspace_root") if isinstance(payload.get("workspace_root"), str) else None,
        ),
    )


def _handle_existing_server(args: argparse.Namespace, host: str, port: int, workspace_root: Path, token: str | None = None) -> bool:
    probe = _probe_existing_server(host, port, token=token) if token else _probe_existing_server(host, port)
    if not probe.occupied:
        return False

    if args.reuse_existing == "never":
        raise RuntimeError(f"Port {port} on {host} is already occupied.")

    if probe.chemssh is None:
        message = f"Port {port} on {host} is occupied, but it is not a reusable ChemSSH server."
        if probe.detail:
            message = f"{message} Detail: {probe.detail}"
        raise RuntimeError(message)

    existing = probe.chemssh
    if args.reuse_existing == "auto" and not _same_workspace(existing.workspace_root, workspace_root):
        raise RuntimeError(
            "Port {port} on {host} is already used by ChemSSH, but with a different workspace: "
            "{existing_workspace}. Current workspace: {current_workspace}. "
            "Use --reuse-existing any-chemssh only if you intentionally want to connect to it.".format(
                port=port,
                host=host,
                existing_workspace=existing.workspace_root or "<unknown>",
                current_workspace=workspace_root,
            )
        )

    print(f"ChemSSH is already running at {existing.url}; reusing the existing server.")
    if existing.workspace_root:
        print(f"Workspace: {existing.workspace_root}")
    return True


def _check_port_only(args: argparse.Namespace, host: str, port: int, workspace_root: Path, token: str | None = None) -> int:
    probe = _probe_existing_server(host, port, token=token) if token else _probe_existing_server(host, port)
    if not probe.occupied:
        print(f"Port {port} on {host} is available.")
        return 0

    if args.reuse_existing == "never":
        print(f"Port {port} on {host} is occupied and --reuse-existing is never.", file=sys.stderr)
        return 1

    if probe.chemssh is None:
        message = f"Port {port} on {host} is occupied, but it is not a reusable ChemSSH server."
        if probe.detail:
            message = f"{message} Detail: {probe.detail}"
        print(message, file=sys.stderr)
        return 1

    existing = probe.chemssh
    if args.reuse_existing == "auto" and not _same_workspace(existing.workspace_root, workspace_root):
        print(
            "Port {port} on {host} is used by ChemSSH with a different workspace: "
            "{existing_workspace}. Current workspace: {current_workspace}.".format(
                port=port,
                host=host,
                existing_workspace=existing.workspace_root or "<unknown>",
                current_workspace=workspace_root,
            ),
            file=sys.stderr,
        )
        return 1

    print(f"Port {port} on {host} is used by a reusable ChemSSH server.")
    print(f"URL: {existing.url}")
    if existing.workspace_root:
        print(f"Workspace: {existing.workspace_root}")
    return 0


def _get_minimal_config(args: argparse.Namespace) -> tuple[str, int, Path, str | None]:
    """Get minimal configuration needed for port detection.

    Only imports config module if defaults are needed, avoiding heavy FastAPI imports.
    """
    # Try to get everything from command line args first
    host = args.host
    port = args.port
    workspace_root = args.workspace_root

    token: str | None = None

    # Load config if values are missing or if a configured token may be needed
    # to identify an already-running protected ChemSSH server.
    if args.config or not (host and port and workspace_root):
        from backend.app.core.config import load_settings
        settings = load_settings(args.config, workspace_root=args.workspace_root)
        host = host or settings.server.host
        port = port or settings.server.port
        workspace_root = workspace_root or str(settings.workspace.root)
        if settings.security.enable_token:
            token = settings.security.token

    return host, port, Path(workspace_root).expanduser().resolve(), token


def main(argv: Sequence[str] | None = None) -> None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args[:1] == ["--terminal-transfer-shim"]:
        raise SystemExit(_run_terminal_transfer_shim(raw_args[1:]))

    args = build_parser().parse_args(raw_args)
    if args.debug:
        os.environ["CHEMSSH_DEBUG"] = "1"
        if args.reuse_existing == "auto":
            args.reuse_existing = "never"

    # Step 1: Get minimal config for detection (fast path, minimal imports)
    host, port, workspace_root, token = _get_minimal_config(args)

    # Step 2: --check-port mode - detect and exit immediately
    if args.check_port:
        raise SystemExit(_check_port_only(args, host, port, workspace_root, token))

    # Step 3: Check if we can reuse an existing server (fast path)
    try:
        if _handle_existing_server(args, host, port, workspace_root, token):
            # Found reusable server, exit without importing heavy modules
            return
    except RuntimeError as exc:
        print(f"chemssh: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # Step 4: Need to start a new server - now import heavy modules
    import uvicorn
    from backend.app.core.config import load_settings
    from backend.app.main import create_app

    # Load full settings for server startup
    settings = load_settings(args.config, workspace_root=args.workspace_root)
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    if args.reload:
        uvicorn.run(
            "backend.app.main:app",
            host=host,
            port=port,
            reload=True,
            log_level=args.log_level,
        )
        return

    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(settings, idle_shutdown_callback=request_shutdown)
    config = uvicorn.Config(app, host=host, port=port, log_level=args.log_level)
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
