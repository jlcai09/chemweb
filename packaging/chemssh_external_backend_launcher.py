from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def main() -> None:
    root = _runtime_root()
    backend_dir = root / "backend"
    if not backend_dir.exists():
        print(f"chemssh: backend directory not found next to launcher: {backend_dir}", file=sys.stderr)
        raise SystemExit(1)

    sys.path.insert(0, str(root))
    cli = importlib.import_module("backend.app.cli")
    cli.main()


if __name__ == "__main__":
    main()
