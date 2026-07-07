from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.dependencies import get_client_id_dependency, get_settings_dependency, validate_client_id
from backend.app.models.terminal import (
    CreateTerminalSessionRequest,
    TerminalCloseResponse,
    TerminalSessionListResponse,
    TerminalSessionResponse,
)
from backend.app.services.terminal_service import TerminalSession, terminal_manager


router = APIRouter(prefix="/terminal", tags=["terminal"])


@router.post("/sessions", response_model=TerminalSessionResponse)
def create_terminal_session(
    payload: CreateTerminalSessionRequest,
    settings: Settings = Depends(get_settings_dependency),
    client_id: str = Depends(get_client_id_dependency),
) -> TerminalSessionResponse:
    session = terminal_manager.create_session(
        settings,
        client_id=client_id,
        cwd=payload.cwd,
        shell=payload.shell,
        rows=payload.rows,
        cols=payload.cols,
        vim_compatibility=payload.vim_compatibility,
    )
    return session.to_response()


@router.get("/sessions", response_model=TerminalSessionListResponse)
def list_terminal_sessions(client_id: str = Depends(get_client_id_dependency)) -> TerminalSessionListResponse:
    return TerminalSessionListResponse(items=terminal_manager.list_sessions(client_id))


@router.delete("/sessions/{session_id}", response_model=TerminalCloseResponse)
def close_terminal_session(
    session_id: str,
    client_id: str = Depends(get_client_id_dependency),
) -> TerminalCloseResponse:
    terminal_manager.close_session(session_id, client_id)
    return TerminalCloseResponse()


@router.websocket("/ws/{session_id}")
async def terminal_ws(
    websocket: WebSocket,
    session_id: str,
    settings: Settings = Depends(get_settings_dependency),
) -> None:
    await websocket.accept()

    try:
        client_id = validate_client_id(websocket.query_params.get("client_id"))
        if not settings.terminal.enabled:
            raise AppError("TERMINAL_DISABLED", "Terminal is disabled by configuration", 403)
        session = terminal_manager.attach_client(session_id, client_id)
    except AppError as exc:
        await websocket.send_json(_error_message(exc))
        await websocket.close(code=1008)
        return

    send_lock = asyncio.Lock()
    reader_task = asyncio.create_task(_terminal_reader(websocket, session, send_lock))

    try:
        while True:
            message = await websocket.receive_json()
            await _handle_terminal_message(websocket, send_lock, session, message)
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await reader_task
        terminal_manager.detach_client(session_id, client_id)


async def _terminal_reader(websocket: WebSocket, session: TerminalSession, send_lock: asyncio.Lock) -> None:
    try:
        while session.is_alive():
            data = await asyncio.to_thread(session.read, 4096)
            cwd = session.consume_cwd_update()
            if cwd:
                await _send_json(websocket, send_lock, {"type": "cwd", "path": cwd})
            for command_done in session.consume_command_done():
                await _send_json(websocket, send_lock, command_done.to_message())
            for transfer in session.consume_transfer_requests():
                await _send_json(websocket, send_lock, transfer.to_message())
            if data:
                await _send_json(websocket, send_lock, {"type": "output", "data": data})
        await _send_json(websocket, send_lock, {"type": "exit", "code": session.provider.exit_code()})
    except asyncio.CancelledError:
        raise
    except (RuntimeError, WebSocketDisconnect):
        return


async def _handle_terminal_message(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    session: TerminalSession,
    message: Any,
) -> None:
    if not isinstance(message, dict):
        await _send_json(websocket, send_lock, {"type": "error", "code": "INVALID_MESSAGE", "message": "Message must be an object"})
        return

    try:
        message_type = message.get("type")
        if message_type == "input":
            data = message.get("data", "")
            if not isinstance(data, str):
                raise AppError("INVALID_INPUT", "Terminal input must be a string", 400)
            session.write(data)
        elif message_type == "resize":
            session.resize(int(message.get("rows", 30)), int(message.get("cols", 120)))
        elif message_type == "sync_cwd":
            path = message.get("path", "")
            if not isinstance(path, str):
                raise AppError("INVALID_PATH", "Terminal cwd path must be a string", 400)
            session.sync_cwd(path)
        elif message_type == "transfer_result":
            transfer_id = message.get("transfer_id", "")
            if not isinstance(transfer_id, str) or not transfer_id:
                raise AppError("INVALID_TRANSFER", "Terminal transfer id is required", 400)
            success = bool(message.get("success", False))
            result_message = message.get("message")
            if result_message is not None and not isinstance(result_message, str):
                raise AppError("INVALID_TRANSFER", "Terminal transfer message must be a string", 400)
            await _send_json(
                websocket,
                send_lock,
                {"type": "output", "data": session.format_transfer_result(transfer_id, success, result_message)},
            )
        else:
            raise AppError("INVALID_MESSAGE_TYPE", f"Unsupported terminal message type: {message_type}", 400)
    except AppError as exc:
        await _send_json(websocket, send_lock, _error_message(exc))
    except Exception as exc:
        await _send_json(
            websocket,
            send_lock,
            {"type": "error", "code": "TERMINAL_MESSAGE_FAILED", "message": str(exc)},
        )


async def _send_json(websocket: WebSocket, send_lock: asyncio.Lock, payload: dict[str, Any]) -> None:
    async with send_lock:
        await websocket.send_json(payload)


def _error_message(exc: AppError) -> dict[str, Any]:
    return {"type": "error", "code": exc.code, "message": exc.message}
