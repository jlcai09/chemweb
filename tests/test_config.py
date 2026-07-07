from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings, load_settings


def test_log_defaults() -> None:
    settings = Settings()

    assert settings.logs.default_tail_lines == 20
    assert settings.logs.max_tail_lines == 100
    assert settings.logs.max_tail_bytes_mb == 5


def test_server_idle_shutdown_defaults() -> None:
    settings = Settings()

    assert settings.server.idle_shutdown_seconds == 0


def test_client_cache_defaults() -> None:
    settings = Settings()

    assert settings.client_cache.enabled is True
    assert settings.client_cache.directory is None
    assert settings.client_cache.cleanup_offline_days == 14
    assert settings.client_cache.max_file_size_kb == 512


def test_server_idle_shutdown_yaml(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
server:
  idle_shutdown_seconds: 15
""",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.server.idle_shutdown_seconds == 15


def test_server_idle_shutdown_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(server={"idle_shutdown_seconds": -1})


def test_brotli_defaults() -> None:
    settings = Settings()

    assert settings.compression.brotli.enabled is True
    assert settings.compression.brotli.level == 1


def test_ase_viewer_defaults() -> None:
    settings = Settings()

    assert settings.viewer.ase.enabled is True
    assert settings.viewer.ase.prefer_binary is True
    assert settings.viewer.ase.json_round_digits == 6
    assert settings.viewer.ase.max_atoms == 200_000
    assert settings.viewer.ase.max_frames == 2_000
    assert settings.viewer.ase.max_points_json == 200_000
    assert settings.viewer.ase.binary_chunk_frames == 32


def test_brotli_yaml_accepts_on_off(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
compression:
  brotli:
    enabled: off
    level: 4
""",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.compression.brotli.enabled is False
    assert settings.compression.brotli.level == 4


def test_brotli_level_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(compression={"brotli": {"level": 12}})


def test_ase_viewer_ranges_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(viewer={"ase": {"json_round_digits": 9}})

    with pytest.raises(ValidationError):
        Settings(viewer={"ase": {"binary_chunk_frames": 0}})
