from __future__ import annotations

import importlib.util
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.core.security import WorkspaceSecurity


PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
PLUGIN_MANIFEST = "chemssh-plugin.json"
PLUGIN_STATE = ".chemssh-plugin-state.json"
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


@dataclass
class PluginContext:
    plugin_id: str
    plugin_dir: Path
    settings: Settings
    workspace_security: WorkspaceSecurity
    logger: logging.Logger
    manifest: dict[str, Any]
    dependencies: dict[str, Any]


@dataclass
class PluginRuntime:
    plugin_id: str
    module: ModuleType | None
    instance: Any
    router_loaded: bool = False
    active: bool = True


class PluginService:
    def __init__(self, settings: Settings, app: FastAPI) -> None:
        self.settings = settings
        self.app = app
        self.project_root = Path(__file__).resolve().parents[3]
        self.plugin_dirs = self._plugin_dirs()
        self.security = WorkspaceSecurity(settings.workspace.root)
        self.logger = logging.getLogger("chemssh.plugins")
        self._manifests: dict[str, dict[str, Any]] = {}
        self._plugin_roots: dict[str, Path] = {}
        self._runtimes: dict[str, PluginRuntime] = {}

    def list_plugins(self) -> list[dict[str, Any]]:
        self.scan()
        plugins: list[dict[str, Any]] = []
        for plugin_id, manifest in sorted(self._manifests.items()):
            plugins.append(self._public_manifest(plugin_id, manifest))
        return plugins

    def activate(self, plugin_id: str) -> dict[str, Any]:
        self.scan()
        manifest = self._manifest(plugin_id)
        dependency_config = self._effective_python_deps(plugin_id, manifest)
        runtime = self._runtimes.get(plugin_id)
        if runtime:
            runtime.active = True
            self._include_plugin_router(plugin_id, runtime)
            self._call_hook(runtime.instance, "on_activate")
            return self._runtime_payload(plugin_id, manifest)

        instance: Any = None
        module: ModuleType | None = None
        backend_entry = manifest.get("entry", {}).get("backend")
        if backend_entry:
            self._ensure_dependency_paths(plugin_id, dependency_config)
            module = self._load_backend_module(plugin_id, manifest, backend_entry)
            factory_name = backend_entry.get("factory", "create_plugin")
            factory = getattr(module, factory_name, None)
            if not callable(factory):
                raise AppError("PLUGIN_FACTORY_NOT_FOUND", f"Plugin factory not found: {factory_name}", 500)
            context = PluginContext(
                plugin_id=plugin_id,
                plugin_dir=self._plugin_roots[plugin_id],
                settings=self.settings,
                workspace_security=self.security,
                logger=logging.getLogger(f"chemssh.plugins.{plugin_id}"),
                manifest=manifest,
                dependencies=dependency_config,
            )
            instance = factory(context)

        runtime = PluginRuntime(plugin_id=plugin_id, module=module, instance=instance)
        self._runtimes[plugin_id] = runtime
        self._include_plugin_router(plugin_id, runtime)
        self._call_hook(instance, "on_activate")
        return self._runtime_payload(plugin_id, manifest)

    def deactivate(self, plugin_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(plugin_id)
        if runtime:
            runtime.active = False
            self._remove_plugin_routes(plugin_id)
            runtime.router_loaded = False
            self._call_hook(runtime.instance, "on_deactivate")
        manifest = self._manifest(plugin_id)
        return self._runtime_payload(plugin_id, manifest, active=False)

    def asset_path(self, plugin_id: str, asset_path: str | None = None) -> Path:
        self.scan()
        manifest = self._manifest(plugin_id)
        plugin_dir = self._plugin_roots[plugin_id]
        frontend_entry = manifest.get("entry", {}).get("frontend", {})
        frontend_deps = manifest.get("dependencies", {}).get("frontend", {})
        index_rel = (
            frontend_entry.get("path")
            or frontend_deps.get("bundle")
            or frontend_deps.get("dist")
        )
        if not index_rel:
            raise AppError("PLUGIN_ASSET_NOT_FOUND", f"Plugin has no frontend assets: {plugin_id}", 404)

        index_path = self._resolve_plugin_path(plugin_dir, str(index_rel))
        asset_root = index_path if index_path.is_dir() else index_path.parent
        target = asset_root / (asset_path or "index.html")
        resolved = target.resolve(strict=False)
        if not self._is_inside(resolved, asset_root):
            raise AppError("FORBIDDEN_PLUGIN_ASSET", "Plugin asset path is outside the frontend directory", 403)
        if not resolved.exists() or not resolved.is_file():
            raise AppError("PLUGIN_ASSET_NOT_FOUND", f"Plugin asset not found: {asset_path or 'index.html'}", 404)
        return resolved

    def dependency_status(self, plugin_id: str) -> dict[str, Any]:
        self.scan()
        manifest = self._manifest(plugin_id)
        plugin_dir = self._plugin_roots[plugin_id]
        deps = self._effective_python_deps(plugin_id, manifest)
        requirements_path = self._requirements_path(plugin_dir, deps)
        requirements = self._read_requirements(requirements_path)
        mode = deps.get("mode", "none")
        python = self._python_for_dependency_mode(plugin_id, deps)
        installed = self._installed_packages(python, requirements) if python else {}
        missing = [name for name in requirements if name not in installed]
        return {
            "plugin_id": plugin_id,
            "python": {
                "mode": mode,
                "manifest_mode": manifest.get("dependencies", {}).get("python", {}).get("mode", "none"),
                "python": str(python) if python else None,
                "requirements": str(requirements_path) if requirements_path else None,
                "packages": [{"name": name, "version": installed.get(name)} for name in requirements],
                "missing": missing,
                "satisfied": bool(requirements) and not missing if mode != "none" else True,
            },
        }

    def install_dependencies(self, plugin_id: str, mode: str | None = None, venv: str | None = None) -> dict[str, Any]:
        self.scan()
        manifest = self._manifest(plugin_id)
        plugin_dir = self._plugin_roots[plugin_id]
        base_deps = self._effective_python_deps(plugin_id, manifest)
        install_mode = (mode or base_deps.get("mode") or "host").strip().lower()
        if install_mode not in {"host", "venv"}:
            raise AppError("PLUGIN_INSTALL_MODE_UNSUPPORTED", "Install mode must be host or venv", 400)

        deps = dict(base_deps)
        deps["mode"] = install_mode
        if venv:
            deps["venv"] = venv
        requirements_path = self._requirements_path(plugin_dir, deps)
        if not requirements_path or not requirements_path.is_file():
            raise AppError("PLUGIN_REQUIREMENTS_NOT_FOUND", "Plugin requirements file was not found", 404)

        if install_mode == "host":
            python = Path(sys.executable)
        else:
            venv_dir = self._venv_dir(plugin_id, deps)
            if venv_dir is None:
                raise AppError("PLUGIN_VENV_INVALID", "Plugin venv path is invalid", 400)
            if not self._venv_python(venv_dir).exists():
                self._run_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=plugin_dir)
            python = self._venv_python(venv_dir)

        result = self._run_command([str(python), "-m", "pip", "install", "-r", str(requirements_path)], cwd=plugin_dir)
        self._write_dependency_override(plugin_id, {"mode": install_mode, **({"venv": deps.get("venv")} if install_mode == "venv" else {})})
        status = self.dependency_status(plugin_id)
        status["install"] = result
        return status

    def configure_external_python(self, plugin_id: str, python: str) -> dict[str, Any]:
        self.scan()
        self._manifest(plugin_id)
        path = Path(python).expanduser().resolve(strict=False)
        if not path.exists() or not path.is_file():
            raise AppError("PLUGIN_EXTERNAL_PYTHON_NOT_FOUND", f"Python executable not found: {python}", 404)
        self._run_command([str(path), "-c", "import sys; print(sys.executable)"], cwd=self._plugin_roots[plugin_id])
        self._write_dependency_override(plugin_id, {"mode": "external", "python": str(path)})
        return self.dependency_status(plugin_id)

    def scan(self) -> None:
        if not self.settings.plugins.enabled:
            self._manifests = {}
            self._plugin_roots = {}
            return
        manifests: dict[str, dict[str, Any]] = {}
        roots: dict[str, Path] = {}
        for directory in self.plugin_dirs:
            if not directory.exists() or not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                manifest_path = child / PLUGIN_MANIFEST
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.logger.warning("Could not read plugin manifest %s: %s", manifest_path, exc)
                    continue
                plugin_id = str(manifest.get("id", "")).strip()
                if not PLUGIN_ID_RE.match(plugin_id):
                    self.logger.warning("Ignoring plugin with invalid id %r at %s", plugin_id, manifest_path)
                    continue
                if plugin_id in manifests:
                    self.logger.warning("Ignoring duplicate plugin id %s at %s", plugin_id, manifest_path)
                    continue
                if manifest.get("enabled") is False:
                    continue
                manifests[plugin_id] = manifest
                roots[plugin_id] = child.resolve()
        self._manifests = manifests
        self._plugin_roots = roots

    def _plugin_dirs(self) -> list[Path]:
        directories = [self.project_root / "plugins"]
        for path in self.settings.plugins.directories:
            directories.append(path if path.is_absolute() else self.project_root / path)
        unique: list[Path] = []
        seen: set[Path] = set()
        for directory in directories:
            resolved = directory.expanduser().resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(resolved)
        return unique

    def _manifest(self, plugin_id: str) -> dict[str, Any]:
        manifest = self._manifests.get(plugin_id)
        if not manifest:
            raise AppError("PLUGIN_NOT_FOUND", f"Plugin not found: {plugin_id}", 404)
        return manifest

    def _public_manifest(self, plugin_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": plugin_id,
            "name": manifest.get("name", plugin_id),
            "version": manifest.get("version"),
            "description": manifest.get("description"),
            "panels": manifest.get("panels", []),
            "file_manager": manifest.get("file_manager", {}),
            "dependencies": manifest.get("dependencies", {}),
            "active": self._runtimes.get(plugin_id).active if plugin_id in self._runtimes else False,
        }

    def _runtime_payload(self, plugin_id: str, manifest: dict[str, Any], *, active: bool = True) -> dict[str, Any]:
        return {
            "id": plugin_id,
            "active": active,
            "asset_url": f"/api/plugins/{plugin_id}/assets",
            "api_base": f"/api/plugins/{plugin_id}/api",
            "panels": manifest.get("panels", []),
            "file_manager": manifest.get("file_manager", {}),
        }

    def _load_backend_module(self, plugin_id: str, manifest: dict[str, Any], backend_entry: dict[str, Any]) -> ModuleType:
        plugin_dir = self._plugin_roots[plugin_id]
        module_name = str(backend_entry.get("module", "")).strip()
        if not module_name:
            raise AppError("PLUGIN_BACKEND_INVALID", f"Plugin backend module is missing: {plugin_id}", 500)
        module_file = self._resolve_plugin_path(plugin_dir, f"{module_name.replace('.', '/')}.py")
        if not module_file.is_file():
            raise AppError("PLUGIN_BACKEND_NOT_FOUND", f"Plugin backend module not found: {module_name}", 500)

        unique_name = f"_chemssh_plugin_{plugin_id.replace('-', '_')}_{module_name.replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(unique_name, module_file)
        if spec is None or spec.loader is None:
            raise AppError("PLUGIN_BACKEND_LOAD_FAILED", f"Could not load plugin backend: {module_name}", 500)
        module = importlib.util.module_from_spec(spec)
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(plugin_dir))
            sys.modules[unique_name] = module
            spec.loader.exec_module(module)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("PLUGIN_BACKEND_LOAD_FAILED", f"Plugin backend failed to load: {exc}", 500) from exc
        finally:
            sys.path[:] = previous_path
        return module

    def _ensure_dependency_paths(self, plugin_id: str, python_deps: dict[str, Any]) -> None:
        if python_deps.get("mode") != "venv":
            return
        venv_dir = self._venv_dir(plugin_id, python_deps)
        if venv_dir is None:
            return
        for site_packages in self._venv_site_packages(venv_dir):
            if site_packages.is_dir() and str(site_packages) not in sys.path:
                sys.path.insert(0, str(site_packages))

    def _effective_python_deps(self, plugin_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        python_deps = dict(manifest.get("dependencies", {}).get("python", {}) or {})
        override = self._dependency_override(plugin_id)
        if override:
            python_deps.update(override)
        python_deps.setdefault("mode", "none")
        return python_deps

    def _dependency_override(self, plugin_id: str) -> dict[str, Any]:
        state_path = self._state_path(plugin_id)
        if not state_path.is_file():
            return {}
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        override = data.get("python_dependencies", {})
        return override if isinstance(override, dict) else {}

    def _write_dependency_override(self, plugin_id: str, override: dict[str, Any]) -> None:
        state_path = self._state_path(plugin_id)
        state: dict[str, Any] = {}
        if state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except Exception:
                state = {}
        state["python_dependencies"] = override
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _state_path(self, plugin_id: str) -> Path:
        return self._plugin_roots[plugin_id] / PLUGIN_STATE

    def _requirements_path(self, plugin_dir: Path, deps: dict[str, Any]) -> Path | None:
        requirements = deps.get("requirements")
        if not requirements:
            return None
        return self._resolve_plugin_path(plugin_dir, str(requirements))

    def _read_requirements(self, requirements_path: Path | None) -> list[str]:
        if not requirements_path or not requirements_path.is_file():
            return []
        names: list[str] = []
        for line in requirements_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            match = REQUIREMENT_NAME_RE.match(stripped)
            if match:
                names.append(match.group(1).replace("_", "-").lower())
        return names

    def _python_for_dependency_mode(self, plugin_id: str, deps: dict[str, Any]) -> Path | None:
        mode = deps.get("mode", "none")
        if mode == "host":
            return Path(sys.executable)
        if mode == "venv":
            venv_dir = self._venv_dir(plugin_id, deps)
            return self._venv_python(venv_dir) if venv_dir else None
        if mode == "external" and deps.get("python"):
            return Path(str(deps["python"])).expanduser().resolve(strict=False)
        return None

    def _venv_dir(self, plugin_id: str, deps: dict[str, Any]) -> Path | None:
        plugin_dir = self._plugin_roots[plugin_id]
        venv_name = str(deps.get("venv") or ".venv")
        return self._resolve_plugin_path(plugin_dir, venv_name)

    @staticmethod
    def _venv_python(venv_dir: Path) -> Path:
        windows_python = venv_dir / "Scripts" / "python.exe"
        if windows_python.exists():
            return windows_python
        return venv_dir / "bin" / "python"

    def _installed_packages(self, python: Path, requirements: list[str]) -> dict[str, str]:
        if not requirements or not python.exists():
            return {}
        script = (
            "import importlib.metadata as m, json, sys\n"
            "names=json.loads(sys.argv[1])\n"
            "out={}\n"
            "for name in names:\n"
            "    try:\n"
            "        out[name]=m.version(name)\n"
            "    except m.PackageNotFoundError:\n"
            "        pass\n"
            "print(json.dumps(out))\n"
        )
        try:
            result = self._run_command([str(python), "-c", script, json.dumps(requirements)], cwd=self.project_root)
            data = json.loads(result["stdout"] or "{}")
        except Exception:
            return {}
        return {str(key).replace("_", "-").lower(): str(value) for key, value in data.items()}

    @staticmethod
    def _run_command(command: list[str], *, cwd: Path, timeout: int = 600) -> dict[str, Any]:
        try:
            result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise AppError("PLUGIN_COMMAND_TIMEOUT", f"Plugin command timed out: {' '.join(command)}", 504) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Plugin command failed").strip()
            raise AppError("PLUGIN_COMMAND_FAILED", message[-2000:], 500)
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }

    @staticmethod
    def _venv_site_packages(venv_dir: Path) -> list[Path]:
        candidates = [venv_dir / "Lib" / "site-packages"]
        lib_dir = venv_dir / "lib"
        if lib_dir.is_dir():
            candidates.extend(sorted(lib_dir.glob("python*/site-packages")))
        return candidates

    def _remove_plugin_routes(self, plugin_id: str) -> None:
        """Remove all routes registered under /api/plugins/{plugin_id}/api."""
        prefix = f"/api/plugins/{plugin_id}/api"
        self.app.router.routes[:] = [
            route for route in self.app.router.routes
            if not getattr(route, "path", "").startswith(prefix)
        ]

    def _include_plugin_router(self, plugin_id: str, runtime: PluginRuntime) -> None:
        if runtime.router_loaded:
            return
        router = getattr(runtime.instance, "router", None) if runtime.instance is not None else None
        if isinstance(router, APIRouter):
            route_start = len(self.app.router.routes)
            self.app.include_router(router, prefix=f"/api/plugins/{plugin_id}/api", tags=[f"plugin:{plugin_id}"])
            new_routes = self.app.router.routes[route_start:]
            if new_routes:
                del self.app.router.routes[route_start:]
                insert_at = self._spa_fallback_route_index()
                for offset, route in enumerate(new_routes):
                    self.app.router.routes.insert(insert_at + offset, route)
            runtime.router_loaded = True

    def _spa_fallback_route_index(self) -> int:
        for index, route in enumerate(self.app.router.routes):
            if getattr(route, "path", None) == "/{full_path:path}":
                return index
        return len(self.app.router.routes)

    def _resolve_plugin_path(self, plugin_dir: Path, relative_path: str) -> Path:
        candidate = (plugin_dir / relative_path).resolve(strict=False)
        if not self._is_inside(candidate, plugin_dir):
            raise AppError("FORBIDDEN_PLUGIN_PATH", "Plugin path escapes the plugin directory", 403)
        return candidate

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            return False

    @staticmethod
    def _call_hook(instance: Any, name: str) -> None:
        hook = getattr(instance, name, None)
        if callable(hook):
            hook()
