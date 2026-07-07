from io import BytesIO
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile

from fastapi.testclient import TestClient

from backend.app import __version__
from backend.app.core.config import BrotliConfig, CompressionConfig, PluginsConfig, SchedulerConfig, SecurityConfig, Settings, WorkspaceConfig
from backend.app.main import create_app
from backend.app.providers.scheduler import pbs as pbs_provider
from backend.app.providers.scheduler import slurm as slurm_provider


def make_client(root: Path, scheduler: str = "slurm") -> TestClient:
    settings = Settings(workspace=WorkspaceConfig(root=root), scheduler=SchedulerConfig(type=scheduler))
    return TestClient(create_app(settings))


def make_client_with_compression(root: Path, enabled: bool = True, level: int = 1) -> TestClient:
    settings = Settings(
        workspace=WorkspaceConfig(root=root),
        compression=CompressionConfig(brotli=BrotliConfig(enabled=enabled, level=level)),
    )
    return TestClient(create_app(settings))


def make_client_with_read_limit(root: Path, max_read_size_mb: int) -> TestClient:
    settings = Settings(workspace=WorkspaceConfig(root=root, max_read_size_mb=max_read_size_mb))
    return TestClient(create_app(settings))


def make_client_with_token(root: Path, token: str = "test-secret") -> TestClient:
    settings = Settings(
        workspace=WorkspaceConfig(root=root),
        security=SecurityConfig(enable_token=True, token=token),
    )
    return TestClient(create_app(settings))


def test_openapi_uses_project_version(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == __version__


def test_token_auth_protects_api_requests(tmp_path: Path) -> None:
    client = make_client_with_token(tmp_path)

    missing = client.get("/api/system/info")
    bad = client.get("/api/system/info", headers={"Authorization": "Bearer wrong"})
    good = client.get("/api/system/info", headers={"Authorization": "Bearer test-secret"})
    cookie_good = client.get("/api/files/list")
    query_good = TestClient(client.app).get("/api/system/identity", params={"token": "test-secret"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTH_REQUIRED"
    assert bad.status_code == 401
    assert good.status_code == 200
    assert "chemssh_token=" in good.headers.get("set-cookie", "")
    assert cookie_good.status_code == 200
    assert query_good.status_code == 200


def test_token_auth_401_includes_cors_headers_for_allowed_origin(tmp_path: Path) -> None:
    client = make_client_with_token(tmp_path)

    # Simulate a cross-origin request from a whitelisted origin
    response = client.get(
        "/api/system/info",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_system_info_and_file_roundtrip(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "sample.xyz"
    sample.write_text("1\nwater\nH 0 0 0\n", encoding="utf-8")

    system = client.get("/api/system/info")
    assert system.status_code == 200
    assert system.json()["project_version"] == __version__
    assert system.json()["workspace_root"] == str(tmp_path.resolve())

    identity = client.get("/api/system/identity")
    assert identity.status_code == 200
    assert identity.json()["app"] == "chemssh"
    assert identity.json()["project_version"] == __version__
    assert identity.json()["workspace_root"] == str(tmp_path.resolve())

    listing = client.get("/api/files/list")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["preview_type"] == "structure"

    read = client.get("/api/files/read", params={"path": str(sample)})
    assert read.status_code == 200
    assert read.json()["format"] == "xyz"

    write = client.post(
        "/api/files/write",
        json={"path": str(tmp_path / "input.inp"), "content": "alpha=1\n"},
    )
    assert write.status_code == 200
    assert (tmp_path / "input.inp").read_text(encoding="utf-8") == "alpha=1\n"


def test_file_read_can_force_large_preview(tmp_path: Path) -> None:
    client = make_client_with_read_limit(tmp_path, max_read_size_mb=0)
    sample = tmp_path / "large.txt"
    sample.write_bytes(b"large file\n")

    blocked = client.get("/api/files/read", params={"path": str(sample)})
    forced = client.get("/api/files/read", params={"path": str(sample), "force": True})

    assert blocked.status_code == 413
    assert blocked.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert forced.status_code == 200
    assert forced.json()["content"] == "large file\n"


def test_brotli_compresses_json_when_client_accepts_br(tmp_path: Path) -> None:
    client = make_client_with_compression(tmp_path)
    response = client.get("/api/system/info", headers={"Accept-Encoding": "br"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.json()["workspace_root"] == str(tmp_path.resolve())


def test_brotli_can_be_disabled(tmp_path: Path) -> None:
    client = make_client_with_compression(tmp_path, enabled=False)
    response = client.get("/api/system/info", headers={"Accept-Encoding": "br"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


def test_brotli_respects_zero_quality(tmp_path: Path) -> None:
    client = make_client_with_compression(tmp_path)
    response = client.get("/api/system/info", headers={"Accept-Encoding": "br;q=0, *;q=1"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


def test_brotli_stream_compresses_event_streams(tmp_path: Path) -> None:
    client = make_client_with_compression(tmp_path)
    sample = tmp_path / "sample.xyz"
    sample.write_text("1\nframe 0\nH 0 0 0\n1\nframe 1\nH 0 0 1\n", encoding="utf-8")

    response = client.get(
        "/api/structures/ase/frames.stream",
        params={"path": str(sample), "chunk": 1},
        headers={"Accept-Encoding": "br"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["content-encoding"] == "br"
    assert "content-length" not in response.headers
    assert "accept-encoding" in response.headers["vary"].lower()
    assert "event: chunk" in response.text


def test_plugins_can_be_listed_activated_and_served(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "sample.log"
    sample.write_text("not a quantum chemistry output\n", encoding="utf-8")

    listing = client.get("/api/plugins")
    assert listing.status_code == 200
    assert any(plugin["id"] == "cclib" for plugin in listing.json()["plugins"])

    dependencies = client.get("/api/plugins/cclib/dependencies")
    assert dependencies.status_code == 200
    assert dependencies.json()["python"]["mode"] == "host"
    assert dependencies.json()["python"]["requirements"].endswith("plugins\\cclib\\backend\\requirements.txt") or dependencies.json()["python"]["requirements"].endswith("plugins/cclib/backend/requirements.txt")

    bad_external = client.post("/api/plugins/cclib/dependencies/external", json={"python": str(tmp_path / "missing-python")})
    assert bad_external.status_code == 404
    assert bad_external.json()["error"]["code"] == "PLUGIN_EXTERNAL_PYTHON_NOT_FOUND"

    activated = client.post("/api/plugins/cclib/activate")
    assert activated.status_code == 200
    assert activated.json()["api_base"] == "/api/plugins/cclib/api"

    asset = client.get("/api/plugins/cclib/assets")
    assert asset.status_code == 200
    assert b"cclib" in asset.content

    probe = client.post("/api/plugins/cclib/api/probe", json={"path": str(sample)})
    assert probe.status_code == 200
    assert probe.json()["handler"] == "cclib-output"


def test_plugin_get_routes_take_precedence_over_spa_fallback(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "dummy-get"
    backend_dir = plugin_dir / "backend"
    backend_dir.mkdir(parents=True)
    (plugin_dir / "chemssh-plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "dummy-get",
                "name": "Dummy GET",
                "version": "0.1.0",
                "entry": {
                    "backend": {
                        "type": "python",
                        "module": "backend.plugin",
                        "factory": "create_plugin",
                    }
                },
                "panels": [{"id": "main", "title": "Dummy GET"}],
            }
        ),
        encoding="utf-8",
    )
    (backend_dir / "plugin.py").write_text(
        "\n".join(
            [
                "from fastapi import APIRouter",
                "",
                "class DummyPlugin:",
                "    def __init__(self, context):",
                "        self.router = APIRouter()",
                "        self.router.get('/hello')(self.hello)",
                "",
                "    def hello(self):",
                "        return {'ok': True}",
                "",
                "def create_plugin(context):",
                "    return DummyPlugin(context)",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path),
        plugins=PluginsConfig(directories=[plugins_dir]),
    )
    client = TestClient(create_app(settings))

    activated = client.post("/api/plugins/dummy-get/activate")
    assert activated.status_code == 200

    response = client.get("/api/plugins/dummy-get/api/hello")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"ok": True}

    deactivated = client.post("/api/plugins/dummy-get/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    removed = client.get("/api/plugins/dummy-get/api/hello")
    assert not (
        removed.status_code == 200
        and removed.headers.get("content-type", "").startswith("application/json")
        and removed.json() == {"ok": True}
    )

    reactivated = client.post("/api/plugins/dummy-get/activate")
    assert reactivated.status_code == 200

    restored = client.get("/api/plugins/dummy-get/api/hello")
    assert restored.status_code == 200
    assert restored.headers["content-type"].startswith("application/json")
    assert restored.json() == {"ok": True}

    route_paths = [getattr(route, "path", None) for route in client.app.router.routes]
    if "/{full_path:path}" in route_paths:
        assert route_paths.index("/api/plugins/dummy-get/api/hello") < route_paths.index("/{full_path:path}")


def test_files_api_blocks_path_traversal(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/files/list", params={"path": str(tmp_path / "..")})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_PATH"


def test_tail_defaults_and_cap(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "payload.bin"
    sample.write_text("".join(f"line {i}\n" for i in range(1, 151)), encoding="utf-8")

    default_tail = client.get("/api/files/tail", params={"path": str(sample)})
    assert default_tail.status_code == 200
    assert default_tail.json()["lines"] == 20
    assert default_tail.json()["content"].splitlines() == [f"line {i}" for i in range(131, 151)]

    capped_tail = client.get("/api/files/tail", params={"path": str(sample), "lines": 500})
    assert capped_tail.status_code == 200
    assert capped_tail.json()["lines"] == 100
    assert capped_tail.json()["content"].splitlines() == [f"line {i}" for i in range(51, 151)]


def test_download_archive_contains_selected_paths(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "sample.txt"
    folder = tmp_path / "folder"
    nested = folder / "nested.txt"
    folder.mkdir()
    sample.write_text("sample\n", encoding="utf-8")
    nested.write_text("nested\n", encoding="utf-8")

    response = client.post(
        "/api/files/download-archive",
        json={"paths": [str(sample), str(folder)]},
    )

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "sample.txt" in names
        assert "folder/" in names
        assert "folder/nested.txt" in names


def test_download_selection_get_archives_multiple_paths(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "sample.txt"
    folder = tmp_path / "folder"
    nested = folder / "nested.txt"
    folder.mkdir()
    sample.write_text("sample\n", encoding="utf-8")
    nested.write_text("nested\n", encoding="utf-8")

    response = client.get(
        "/api/files/download-selection",
        params=[("path", str(sample)), ("path", str(folder))],
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "sample.txt" in names
        assert "folder/" in names
        assert "folder/nested.txt" in names


def test_delete_directory_uses_native_rm_recursive(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    directory = tmp_path / "case"
    nested = directory / "nested"
    nested.mkdir(parents=True)
    (nested / "input.inp").write_text("input\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_which(command: str) -> str | None:
        return "/usr/bin/rm" if command == "rm" else None

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        target = Path(command[-1])
        for child in sorted(target.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
        target.rmdir()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.shutil.which", fake_which)
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.subprocess.run", fake_run)
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs._is_windows", lambda: False)

    response = client.delete("/api/files/delete", params={"path": str(directory)})

    assert response.status_code == 200
    assert captured["command"] == ["/usr/bin/rm", "-rf", "--", str(directory.resolve())]
    assert captured["kwargs"] == {"capture_output": True, "text": True, "check": False}
    assert not directory.exists()


def test_upload_accepts_relative_path_and_overwrites_nested_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    existing = tmp_path / "A" / "B.txt"
    keep = tmp_path / "A" / "C.txt"
    existing.parent.mkdir()
    existing.write_text("old\n", encoding="utf-8")
    keep.write_text("keep\n", encoding="utf-8")

    response = client.post(
        "/api/files/upload",
        data={"path": str(tmp_path), "relative_path": "A/B.txt"},
        files={"file": ("B.txt", b"new\n", "text/plain")},
    )

    assert response.status_code == 200
    assert existing.read_text(encoding="utf-8") == "new\n"
    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_upload_relative_path_blocks_traversal(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/files/upload",
        data={"path": str(tmp_path), "relative_path": "../escape.txt"},
        files={"file": ("escape.txt", b"bad", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_NAME"


def test_move_paths_uses_native_mv_and_refreshes_target(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    source_file = tmp_path / "sample.txt"
    source_dir = tmp_path / "case"
    nested = source_dir / "input.inp"
    target = tmp_path / "target"
    source_file.write_text("sample\n", encoding="utf-8")
    source_dir.mkdir()
    nested.write_text("input\n", encoding="utf-8")
    target.mkdir()
    captured: dict[str, object] = {}

    def fake_which(command: str) -> str | None:
        return "/usr/bin/mv" if command == "mv" else None

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        separator_index = command.index("--")
        sources = [Path(path) for path in command[separator_index + 1 : -1]]
        destination = Path(command[-1])
        for source in sources:
            source.rename(destination / source.name)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.shutil.which", fake_which)
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.subprocess.run", fake_run)

    response = client.post(
        "/api/files/move",
        json={"paths": [str(source_file), str(source_dir)], "target_directory": str(target)},
    )

    assert response.status_code == 200
    assert response.json()["path"] == str(target.resolve())
    assert captured["command"] == ["/usr/bin/mv", "--", str(source_file.resolve()), str(source_dir.resolve()), str(target.resolve())]
    assert captured["kwargs"] == {"capture_output": True, "text": True, "check": False}
    assert not source_file.exists()
    assert (target / "sample.txt").read_text(encoding="utf-8") == "sample\n"
    assert (target / "case" / "input.inp").read_text(encoding="utf-8") == "input\n"


def test_move_paths_reports_native_mv_failure_in_chinese(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    source = tmp_path / "mixed"
    nested = source / "nested"
    target = tmp_path / "target"
    nested.mkdir(parents=True)
    target.mkdir()
    (source / "file.txt").write_text("file\n", encoding="utf-8")
    (nested / "child.txt").write_text("child\n", encoding="utf-8")

    def fake_which(command: str) -> str | None:
        return "/usr/bin/mv" if command == "mv" else None

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="mock mv failed")

    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.shutil.which", fake_which)
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.subprocess.run", fake_run)

    response = client.post(
        "/api/files/move",
        json={"paths": [str(source)], "target_directory": str(target)},
    )

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "MOVE_FAILED"
    assert "移动失败" in error["message"]
    assert str(source.resolve()) in error["message"]
    assert str(target.resolve()) in error["message"]
    assert "mock mv failed" in error["message"]


def test_move_paths_blocks_self_descendant_and_existing_targets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    source_dir = tmp_path / "case"
    nested_target = source_dir / "nested"
    sibling = tmp_path / "sibling"
    source_file = tmp_path / "sample.txt"
    source_dir.mkdir()
    nested_target.mkdir()
    sibling.mkdir()
    source_file.write_text("sample\n", encoding="utf-8")
    (sibling / "sample.txt").write_text("existing\n", encoding="utf-8")

    into_self = client.post(
        "/api/files/move",
        json={"paths": [str(source_dir)], "target_directory": str(nested_target)},
    )
    collision = client.post(
        "/api/files/move",
        json={"paths": [str(source_file)], "target_directory": str(sibling)},
    )

    assert into_self.status_code == 400
    assert into_self.json()["error"]["code"] == "MOVE_INTO_SELF"
    assert collision.status_code == 409
    assert collision.json()["error"]["code"] == "PATH_EXISTS"


def test_move_paths_can_overwrite_merge_and_suffix_conflicts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.shutil.which", lambda command: None)
    client = make_client(tmp_path)
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    source_file = source / "sample.txt"
    target_file = target / "sample.txt"
    source_file.write_text("new sample\n", encoding="utf-8")
    target_file.write_text("old sample\n", encoding="utf-8")

    source_dir = source / "case"
    target_dir = target / "case"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "input.inp").write_text("new input\n", encoding="utf-8")
    (target_dir / "input.inp").write_text("old input\n", encoding="utf-8")
    (target_dir / "keep.out").write_text("keep\n", encoding="utf-8")

    suffix_source = source / "beta.txt"
    suffix_source.write_text("beta\n", encoding="utf-8")
    (target / "beta.txt").write_text("existing beta\n", encoding="utf-8")

    response = client.post(
        "/api/files/move",
        json={
            "target_directory": str(target),
            "items": [
                {"path": str(source_file), "overwrite": True},
                {"path": str(source_dir), "overwrite": True},
                {"path": str(suffix_source), "target_name": "beta.txt.new"},
            ],
        },
    )

    assert response.status_code == 200
    assert not source_file.exists()
    assert not source_dir.exists()
    assert not suffix_source.exists()
    assert target_file.read_text(encoding="utf-8") == "new sample\n"
    assert (target_dir / "input.inp").read_text(encoding="utf-8") == "new input\n"
    assert (target_dir / "keep.out").read_text(encoding="utf-8") == "keep\n"
    assert (target / "beta.txt").read_text(encoding="utf-8") == "existing beta\n"
    assert (target / "beta.txt.new").read_text(encoding="utf-8") == "beta\n"


def test_move_items_without_conflict_uses_bulk_native_mv(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    source = tmp_path / "source"
    target = tmp_path / "target"
    mixed = source / "mixed"
    nested = mixed / "nested"
    nested.mkdir(parents=True)
    target.mkdir()
    (mixed / "file.txt").write_text("file\n", encoding="utf-8")
    (nested / "child.txt").write_text("child\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_which(command: str) -> str | None:
        return "/usr/bin/mv" if command == "mv" else None

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        source_path = Path(command[-2])
        target_dir = Path(command[-1])
        source_path.rename(target_dir / source_path.name)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.shutil.which", fake_which)
    monkeypatch.setattr("backend.app.providers.filesystem.local_fs.subprocess.run", fake_run)

    response = client.post(
        "/api/files/move",
        json={
            "target_directory": str(target),
            "items": [{"path": str(mixed), "overwrite": True}],
        },
    )

    assert response.status_code == 200
    assert captured["command"] == ["/usr/bin/mv", "--", str(mixed.resolve()), str(target.resolve())]
    assert (target / "mixed" / "file.txt").read_text(encoding="utf-8") == "file\n"
    assert (target / "mixed" / "nested" / "child.txt").read_text(encoding="utf-8") == "child\n"


def test_copy_paths_can_overwrite_merge_and_suffix_conflicts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    source_file = source / "sample.txt"
    target_file = target / "sample.txt"
    source_file.write_text("new sample\n", encoding="utf-8")
    target_file.write_text("old sample\n", encoding="utf-8")

    source_dir = source / "case"
    target_dir = target / "case"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "input.inp").write_text("new input\n", encoding="utf-8")
    (target_dir / "input.inp").write_text("old input\n", encoding="utf-8")
    (target_dir / "keep.out").write_text("keep\n", encoding="utf-8")

    suffix_source = source / "beta.txt"
    suffix_source.write_text("beta\n", encoding="utf-8")
    (target / "beta.txt").write_text("existing beta\n", encoding="utf-8")

    response = client.post(
        "/api/files/copy",
        json={
            "target_directory": str(target),
            "items": [
                {"path": str(source_file), "overwrite": True},
                {"path": str(source_dir), "overwrite": True},
                {"path": str(suffix_source), "target_name": "beta.txt.new"},
            ],
        },
    )

    assert response.status_code == 200
    assert source_file.read_text(encoding="utf-8") == "new sample\n"
    assert (source_dir / "input.inp").read_text(encoding="utf-8") == "new input\n"
    assert suffix_source.read_text(encoding="utf-8") == "beta\n"
    assert target_file.read_text(encoding="utf-8") == "new sample\n"
    assert (target_dir / "input.inp").read_text(encoding="utf-8") == "new input\n"
    assert (target_dir / "keep.out").read_text(encoding="utf-8") == "keep\n"
    assert (target / "beta.txt").read_text(encoding="utf-8") == "existing beta\n"
    assert (target / "beta.txt.new").read_text(encoding="utf-8") == "beta\n"


def test_copy_paths_blocks_self_descendant_and_existing_targets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    source_dir = tmp_path / "case"
    nested_target = source_dir / "nested"
    sibling = tmp_path / "sibling"
    source_file = tmp_path / "sample.txt"
    source_dir.mkdir()
    nested_target.mkdir()
    sibling.mkdir()
    source_file.write_text("sample\n", encoding="utf-8")
    (sibling / "sample.txt").write_text("existing\n", encoding="utf-8")

    into_self = client.post(
        "/api/files/copy",
        json={"paths": [str(source_dir)], "target_directory": str(nested_target)},
    )
    collision = client.post(
        "/api/files/copy",
        json={"paths": [str(source_file)], "target_directory": str(sibling)},
    )

    assert into_self.status_code == 400
    assert into_self.json()["error"]["code"] == "COPY_INTO_SELF"
    assert collision.status_code == 409
    assert collision.json()["error"]["code"] == "PATH_EXISTS"


def test_submit_job_uses_requested_queue_command(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    script = tmp_path / "vasp.script"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_which(command: str) -> str:
        return f"/usr/bin/{command}"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(command, 0, stdout="12345.server\n", stderr="")

    monkeypatch.setattr(slurm_provider.shutil, "which", fake_which)
    monkeypatch.setattr(slurm_provider.subprocess, "run", fake_run)

    response = client.post(
        "/api/jobs/submit",
        json={"workdir": str(tmp_path), "script": "vasp.script", "command": "qsub"},
    )

    assert response.status_code == 200
    assert captured["command"] == ["qsub", "vasp.script"]
    assert captured["cwd"] == str(tmp_path.resolve())
    assert response.json()["scheduler"] == "qsub"
    assert response.json()["job_id"] == "12345.server"


def test_slurm_queue_can_list_all_or_current_user(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    captured: list[list[str]] = []

    def fake_which(command: str) -> str:
        return f"/usr/bin/{command}"

    def fake_getuser() -> str:
        return "alice"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        payload = {
            "jobs": [
                {
                    "job_id": 101,
                    "name": "job",
                    "user_name": "bob",
                    "job_state": "RUNNING",
                    "partition": "debug",
                    "time": {"elapsed": 12},
                    "working_directory": str(tmp_path),
                }
            ]
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(slurm_provider.shutil, "which", fake_which)
    monkeypatch.setattr(slurm_provider.getpass, "getuser", fake_getuser)
    monkeypatch.setattr(slurm_provider.subprocess, "run", fake_run)

    all_jobs = client.get("/api/queue/list")
    current_user_jobs = client.get("/api/queue/list", params={"current_user_only": True})

    assert all_jobs.status_code == 200
    assert current_user_jobs.status_code == 200
    assert captured == [["squeue", "--json"], ["squeue", "-u", "alice", "--json"]]


def test_pbs_queue_actions_use_pbs_commands(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, scheduler="pbs")
    captured: list[list[str]] = []

    def fake_which(command: str) -> str:
        return f"/usr/bin/{command}"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(pbs_provider.shutil, "which", fake_which)
    monkeypatch.setattr(pbs_provider.subprocess, "run", fake_run)

    hold = client.post("/api/queue/action", json={"job_id": "123.server", "action": "hold"})
    release = client.post("/api/queue/action", json={"job_id": "123.server", "action": "release"})
    cancel = client.post("/api/queue/action", json={"job_id": "123.server", "action": "cancel"})

    assert hold.status_code == 200
    assert release.status_code == 200
    assert cancel.status_code == 200
    assert captured == [["qhold", "123.server"], ["qrls", "123.server"], ["qdel", "123.server"]]
