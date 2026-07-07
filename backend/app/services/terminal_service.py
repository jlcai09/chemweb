from __future__ import annotations

import threading
import uuid
import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.core.security import WorkspaceSecurity
from backend.app.models.terminal import TerminalSessionResponse
from backend.app.providers.terminal.base import TerminalProvider
from backend.app.providers.terminal.local_pty import LocalPtyTerminalProvider


CWD_MARKER_PREFIX = "\x1b]633;P;Cwd="
TRANSFER_MARKER_PREFIX = "\x1b]777;chemssh-transfer;"
CONTROL_MARKER_PREFIXES = (CWD_MARKER_PREFIX, TRANSFER_MARKER_PREFIX)
ZMODEM_SIGNATURES = (
    "rz waiting to receive",
    "**B00000000000000",
    "**B0100",
    "**\x18B00000000000000",
    "**\x18B0100",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TerminalTransferRequest:
    transfer_id: str
    direction: str
    cwd: str
    argv: list[str]
    paths: list[str] = field(default_factory=list)
    ack_path: str | None = None
    error: str | None = None

    def to_message(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "transfer_request",
            "transfer_id": self.transfer_id,
            "direction": self.direction,
            "cwd": self.cwd,
            "argv": self.argv,
        }
        if self.paths:
            payload["paths"] = self.paths
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class TerminalCommandDone:
    seq: int
    cwd: str

    def to_message(self) -> dict[str, Any]:
        return {"type": "command_done", "seq": self.seq, "path": self.cwd}


@dataclass
class TerminalSession:
    session_id: str
    client_id: str
    provider: TerminalProvider
    cwd: str
    created_at: datetime
    last_active_at: datetime
    security: WorkspaceSecurity
    allow_sync_cwd: bool
    clients: int = 0
    _pending_cwd: str | None = field(default=None, init=False, repr=False)
    _pending_command_done: list[TerminalCommandDone] = field(default_factory=list, init=False, repr=False)
    _command_done_seq: int = field(default=0, init=False, repr=False)
    _entered_command_count: int = field(default=0, init=False, repr=False)
    _shell_prompt_ready: bool = field(default=True, init=False, repr=False)
    _control_marker_buffer: str = field(default="", init=False, repr=False)
    _zmodem_signature_buffer: str = field(default="", init=False, repr=False)
    _native_zmodem_intercepted: bool = field(default=False, init=False, repr=False)
    _pending_transfers: list[TerminalTransferRequest] = field(default_factory=list, init=False, repr=False)
    _active_transfers: dict[str, TerminalTransferRequest] = field(default_factory=dict, init=False, repr=False)

    @property
    def shell(self) -> str:
        return self.provider.shell

    def read(self, size: int = 4096) -> str:
        data = self.provider.read(size)
        if data:
            self.touch()
            return self._extract_control_markers(data)
        return self._extract_control_markers("")

    def write(self, data: str) -> None:
        self._note_user_input(data)
        self.provider.write(data)
        self.touch()

    def resize(self, rows: int, cols: int) -> None:
        self.provider.resize(rows, cols)
        self.touch()

    def sync_cwd(self, raw_path: str) -> None:
        if not self.allow_sync_cwd:
            raise AppError("TERMINAL_SYNC_DISABLED", "Terminal cwd sync is disabled", 403)

        path = self.security.resolve_path(raw_path)
        if not path.exists() or not path.is_dir():
            raise AppError("NOT_A_DIRECTORY", f"Terminal cwd is not a directory: {path}", 400)

        self.provider.write_control(self.provider.build_cd_command(str(path)))
        self.cwd = str(path)
        self.touch()

    def consume_cwd_update(self) -> str | None:
        cwd = self._pending_cwd
        self._pending_cwd = None
        return cwd

    def consume_command_done(self) -> list[TerminalCommandDone]:
        events = self._pending_command_done
        self._pending_command_done = []
        return events

    def consume_transfer_requests(self) -> list[TerminalTransferRequest]:
        transfers = self._pending_transfers
        self._pending_transfers = []
        for transfer in transfers:
            self._active_transfers[transfer.transfer_id] = transfer
        return transfers

    def format_transfer_result(self, transfer_id: str, success: bool, message: str | None = None) -> str:
        transfer = self._active_transfers.pop(transfer_id, None)
        if transfer and transfer.ack_path:
            self._write_transfer_ack(transfer.ack_path, success, message)
        status = "complete" if success else "failed"
        suffix = f": {message}" if message else ""
        self.touch()
        return f"\r\nchemssh transfer {status} ({transfer_id}){suffix}\r\n"

    def _extract_control_markers(self, data: str) -> str:
        if not data and not self._control_marker_buffer:
            return ""

        text = self._control_marker_buffer + data
        self._control_marker_buffer = ""
        parts: list[str] = []
        index = 0
        while index < len(text):
            marker = _find_next_control_marker(text, index)
            if marker is None:
                remaining, pending = _split_possible_marker_prefix(text[index:])
                parts.append(remaining)
                self._control_marker_buffer = pending
                break

            start, prefix = marker
            parts.append(text[index:start])
            value_start = start + len(prefix)
            bel_end = text.find("\x07", value_start)
            st_end = text.find("\x1b\\", value_start)
            end, terminator_length = _first_marker_end(bel_end, st_end)
            if end < 0:
                self._control_marker_buffer = text[start:]
                break

            value = text[value_start:end]
            if prefix == CWD_MARKER_PREFIX:
                self._accept_cwd_marker(value)
            elif prefix == TRANSFER_MARKER_PREFIX:
                self._accept_transfer_marker(value)
            index = end + terminator_length

        return self._filter_native_zmodem("".join(parts))

    def _accept_cwd_marker(self, raw_path: str) -> None:
        try:
            path = self.security.resolve_path(raw_path)
        except AppError:
            return
        if not path.exists() or not path.is_dir():
            return
        normalized = str(path)
        if self._entered_command_count > 0:
            self._entered_command_count -= 1
            self._command_done_seq += 1
            self._pending_command_done.append(TerminalCommandDone(seq=self._command_done_seq, cwd=normalized))
            self.touch()
        self._shell_prompt_ready = True
        if normalized != self.cwd:
            self.cwd = normalized
            self._pending_cwd = normalized
            self.touch()

    def _note_user_input(self, data: str) -> None:
        if not data or not self._shell_prompt_ready:
            return
        line_feeds_without_carriage_return = sum(
            1
            for index, char in enumerate(data)
            if char == "\n" and (index == 0 or data[index - 1] != "\r")
        )
        command_count = data.count("\r") + line_feeds_without_carriage_return
        if command_count > 0:
            self._entered_command_count += command_count
            self._shell_prompt_ready = False

    def _accept_transfer_marker(self, encoded_payload: str) -> None:
        try:
            raw_payload = base64.urlsafe_b64decode(_pad_base64(encoded_payload)).decode("utf-8")
            payload = json.loads(raw_payload)
        except Exception:
            return

        if not isinstance(payload, dict):
            return
        direction = payload.get("direction")
        argv = payload.get("argv")
        raw_cwd = payload.get("cwd")
        ack_path = payload.get("ack_path")
        if direction not in {"upload", "download"} or not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            return
        argv = [_strip_shell_line_ending(item) for item in argv]
        if not isinstance(raw_cwd, str):
            raw_cwd = self.cwd
        raw_cwd = _strip_shell_line_ending(raw_cwd)
        if not isinstance(ack_path, str):
            ack_path = None

        try:
            cwd_path = self.security.resolve_path(raw_cwd)
        except AppError:
            cwd_path = Path(self.cwd)

        transfer_id = f"transfer_{uuid.uuid4().hex[:12]}"
        paths: list[str] = []
        error: str | None = None
        if direction == "download":
            try:
                paths = self._resolve_transfer_download_paths(argv, cwd_path)
            except AppError as exc:
                error = exc.message
        self._pending_transfers.append(
            TerminalTransferRequest(
                transfer_id=transfer_id,
                direction=direction,
                cwd=str(cwd_path),
                argv=argv,
                paths=paths,
                ack_path=ack_path,
                error=error,
            )
        )
        self.touch()

    def _write_transfer_ack(self, raw_ack_path: str, success: bool, message: str | None) -> None:
        ack_path = self.provider.resolve_transfer_ack_path(raw_ack_path)
        if ack_path is None:
            return
        ack_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"success": success, "message": message or ""}
        ack_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _filter_native_zmodem(self, data: str) -> str:
        if not data:
            return ""

        probe = self._zmodem_signature_buffer + data
        self._zmodem_signature_buffer = probe[-64:]
        lowered_probe = probe.lower()
        if self._native_zmodem_intercepted or not any(signature.lower() in lowered_probe for signature in ZMODEM_SIGNATURES):
            return data

        self._native_zmodem_intercepted = True
        self.provider.write_control("\x03")
        if "rz waiting to receive" in lowered_probe:
            self._pending_transfers.append(
                TerminalTransferRequest(
                    transfer_id=f"transfer_{uuid.uuid4().hex[:12]}",
                    direction="upload",
                    cwd=self.cwd,
                    argv=["rz"],
                )
            )
            return "\r\nchemssh: native rz/ZMODEM was intercepted; opening browser upload instead.\r\n"

        return (
            "\r\nchemssh: native sz/ZMODEM was intercepted to avoid a stuck terminal. "
            "Use PATH-resolved sz so ChemSSH can receive the requested file list.\r\n"
        )

    def _resolve_transfer_download_paths(self, argv: list[str], cwd: Path) -> list[str]:
        candidates: list[str] = []
        skip_next = False
        for arg in argv[1:]:
            arg = _strip_shell_line_ending(arg)
            if not arg:
                continue
            if skip_next:
                skip_next = False
                continue
            if arg in {"-e", "--escape", "-i", "--input-command"}:
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            candidates.append(arg)

        if not candidates:
            raise AppError("TERMINAL_TRANSFER_NO_PATHS", "sz did not provide any downloadable paths", 400)

        paths: list[str] = []
        for candidate in candidates:
            raw_path = candidate if Path(candidate).is_absolute() else str(cwd / candidate)
            path = self.security.resolve_path(raw_path)
            if not path.exists():
                raise AppError("FILE_NOT_FOUND", f"File not found: {path}", 404)
            paths.append(str(path))
        return paths

    def close(self) -> None:
        self.provider.terminate()
        self.touch()

    def attach_client(self) -> None:
        self.clients += 1
        self.touch()

    def detach_client(self) -> int:
        self.clients = max(self.clients - 1, 0)
        self.touch()
        return self.clients

    def is_alive(self) -> bool:
        return self.provider.is_alive()

    def touch(self) -> None:
        self.last_active_at = utc_now()

    def to_response(self) -> TerminalSessionResponse:
        return TerminalSessionResponse(
            session_id=self.session_id,
            cwd=self.cwd,
            shell=self.shell,
            created_at=self.created_at,
            last_active_at=self.last_active_at,
            alive=self.is_alive(),
            clients=self.clients,
        )


class TerminalManager:
    def __init__(self) -> None:
        self.sessions: dict[str, TerminalSession] = {}
        self._lock = threading.RLock()

    def create_session(
        self,
        settings: Settings,
        client_id: str,
        cwd: str | None,
        shell: str | None,
        rows: int | None,
        cols: int | None,
        vim_compatibility: bool = True,
    ) -> TerminalSession:
        if not settings.terminal.enabled:
            raise AppError("TERMINAL_DISABLED", "Terminal is disabled by configuration", 403)

        with self._lock:
            self._prune_closed_sessions()
            if self._alive_count(client_id) >= settings.terminal.max_sessions:
                raise AppError("TERMINAL_LIMIT_REACHED", "Terminal session limit reached", 429)

            security = WorkspaceSecurity(settings.workspace.root)
            safe_cwd = security.resolve_path(cwd)
            if not safe_cwd.exists() or not safe_cwd.is_dir():
                raise AppError("NOT_A_DIRECTORY", f"Terminal cwd is not a directory: {safe_cwd}", 400)

            provider = LocalPtyTerminalProvider()
            provider.vim_compatibility = vim_compatibility
            session_id = f"term_{uuid.uuid4().hex[:12]}"
            started_at = utc_now()
            try:
                provider.start(
                    str(safe_cwd),
                    rows or settings.terminal.default_rows,
                    cols or settings.terminal.default_cols,
                    shell or settings.terminal.shell,
                )
            except ImportError as exc:
                raise AppError("TERMINAL_DEPENDENCY_MISSING", "ptyprocess is required for terminal support", 500) from exc
            except FileNotFoundError as exc:
                raise AppError("TERMINAL_SHELL_NOT_FOUND", f"Terminal shell not found: {shell}", 400) from exc
            except Exception as exc:
                provider.terminate()
                raise AppError("TERMINAL_START_FAILED", f"Terminal failed to start: {exc}", 500) from exc

            session = TerminalSession(
                session_id=session_id,
                client_id=client_id,
                provider=provider,
                cwd=str(safe_cwd),
                created_at=started_at,
                last_active_at=started_at,
                security=security,
                allow_sync_cwd=settings.terminal.allow_sync_cwd,
            )
            self.sessions[session_id] = session
            return session

    def get_session(self, session_id: str, client_id: str) -> TerminalSession:
        with self._lock:
            session = self._get_owned_session(session_id, client_id)
        return session

    def close_session(self, session_id: str, client_id: str) -> None:
        with self._lock:
            session = self._get_owned_session(session_id, client_id)
            self.sessions.pop(session_id, None)
        session.close()

    def attach_client(self, session_id: str, client_id: str) -> TerminalSession:
        with self._lock:
            session = self._get_owned_session(session_id, client_id)
            session.attach_client()
            return session

    def detach_client(self, session_id: str, client_id: str) -> None:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session.client_id != client_id:
                return
            remaining_clients = session.detach_client()
            if remaining_clients > 0:
                return
            self.sessions.pop(session_id, None)
        session.close()

    def list_sessions(self, client_id: str) -> list[TerminalSessionResponse]:
        with self._lock:
            sessions = sorted(
                (session for session in self.sessions.values() if session.client_id == client_id),
                key=lambda item: item.created_at,
            )
        return [session.to_response() for session in sessions]

    def _get_owned_session(self, session_id: str, client_id: str) -> TerminalSession:
        session = self.sessions.get(session_id)
        if session is None or session.client_id != client_id:
            raise AppError("TERMINAL_SESSION_NOT_FOUND", f"Terminal session not found: {session_id}", 404)
        return session

    def _alive_count(self, client_id: str) -> int:
        return sum(1 for session in self.sessions.values() if session.client_id == client_id and session.is_alive())

    def _prune_closed_sessions(self) -> None:
        closed = [session_id for session_id, session in self.sessions.items() if not session.is_alive()]
        for session_id in closed:
            self.sessions.pop(session_id, None)


terminal_manager = TerminalManager()


def _first_marker_end(bel_end: int, st_end: int) -> tuple[int, int]:
    candidates: list[tuple[int, int]] = []
    if bel_end >= 0:
        candidates.append((bel_end, 1))
    if st_end >= 0:
        candidates.append((st_end, 2))
    return min(candidates, default=(-1, 0), key=lambda item: item[0])


def _find_next_control_marker(text: str, start_index: int) -> tuple[int, str] | None:
    matches = [(index, prefix) for prefix in CONTROL_MARKER_PREFIXES if (index := text.find(prefix, start_index)) >= 0]
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])


def _split_possible_marker_prefix(text: str) -> tuple[str, str]:
    max_length = min(max(len(prefix) for prefix in CONTROL_MARKER_PREFIXES) - 1, len(text))
    for length in range(max_length, 0, -1):
        suffix = text[-length:]
        if any(prefix.startswith(suffix) for prefix in CONTROL_MARKER_PREFIXES):
            return text[:-length], suffix
    return text, ""


def _pad_base64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")


def _strip_shell_line_ending(value: str) -> str:
    return value.rstrip("\r\n")
