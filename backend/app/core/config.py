from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8888
    debug: bool = False
    idle_shutdown_seconds: int = Field(default=0, ge=0)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )


class WorkspaceConfig(BaseModel):
    root: Path = Field(default_factory=Path.cwd)
    allow_delete: bool = True
    max_upload_size_mb: int = 500
    max_read_size_mb: int = 5


class SchedulerConfig(BaseModel):
    type: str = "slurm"
    refresh_interval: int = 10


class AseViewerConfig(BaseModel):
    enabled: bool = True
    prefer_binary: bool = True
    json_round_digits: int = Field(default=6, ge=0, le=8)
    max_atoms: int = 200_000
    max_frames: int = 2_000
    max_points_json: int = 200_000
    binary_chunk_frames: int = Field(default=32, ge=1, le=512)
    cache_ttl_minutes: int = 30


class ViewerConfig(BaseModel):
    default_style: str = "stick"
    max_file_size_mb: int = 50
    ase: AseViewerConfig = Field(default_factory=AseViewerConfig)


class SecurityConfig(BaseModel):
    enable_token: bool = False
    token: str = "change-me"

    @model_validator(mode="after")
    def validate_token_config(self) -> "SecurityConfig":
        if self.enable_token and not self.token:
            raise ValueError("security.token cannot be empty when security.enable_token is True")
        return self


class LogConfig(BaseModel):
    default_tail_lines: int = 20
    max_tail_lines: int = 100
    max_tail_bytes_mb: int = 5


class TerminalConfig(BaseModel):
    enabled: bool = True
    shell: Optional[str] = None
    max_sessions: int = 4
    default_rows: int = 30
    default_cols: int = 120
    idle_timeout_seconds: int = Field(default=3600, ge=0)
    allow_sync_cwd: bool = True


class BrotliConfig(BaseModel):
    enabled: bool = True
    level: int = Field(default=1, ge=1, le=11)


class CompressionConfig(BaseModel):
    brotli: BrotliConfig = Field(default_factory=BrotliConfig)


class PluginsConfig(BaseModel):
    enabled: bool = True
    directories: list[Path] = Field(default_factory=list)


class ClientCacheConfig(BaseModel):
    enabled: bool = True
    directory: Optional[Path] = None
    cleanup_offline_days: int = Field(default=14, ge=1)
    max_file_size_kb: int = Field(default=512, ge=16)


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    viewer: ViewerConfig = Field(default_factory=ViewerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logs: LogConfig = Field(default_factory=LogConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    client_cache: ClientCacheConfig = Field(default_factory=ClientCacheConfig)


_settings: Optional[Settings] = None


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "debug"}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without deps
        raise RuntimeError("PyYAML is required when using --config") from exc

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must contain a YAML mapping: {path}")
    return data


def _normalize_settings(settings: Settings) -> Settings:
    settings.workspace.root = settings.workspace.root.expanduser().resolve()
    settings.scheduler.type = settings.scheduler.type.lower().strip()
    settings.viewer.default_style = settings.viewer.default_style.lower().strip()
    if settings.terminal.shell is not None:
        settings.terminal.shell = settings.terminal.shell.strip() or None
    if settings.client_cache.directory is not None:
        settings.client_cache.directory = settings.client_cache.directory.expanduser().resolve()
    return settings


def load_settings(
    config_path: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> Settings:
    data: dict[str, Any] = {}
    if config_path:
        config_file = Path(config_path).expanduser().resolve()
        data = _load_yaml(config_file)

    env_root = os.getenv("CHEMSSH_WORKSPACE")
    if workspace_root or env_root:
        data.setdefault("workspace", {})
        data["workspace"]["root"] = str(workspace_root or env_root)

    env_debug = os.getenv("CHEMSSH_DEBUG")
    if env_debug is not None:
        data.setdefault("server", {})
        data["server"]["debug"] = _env_truthy(env_debug)

    settings = _normalize_settings(Settings(**data))
    set_settings(settings)
    return settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = _normalize_settings(settings)


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
