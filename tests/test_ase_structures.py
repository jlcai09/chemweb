from __future__ import annotations

import json
import struct
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.constraints import FixAtoms
from ase.db import connect
from ase.io import write
from fastapi.testclient import TestClient

from backend.app.core.config import (
    AseViewerConfig,
    BrotliConfig,
    CompressionConfig,
    ServerConfig,
    Settings,
    ViewerConfig,
    WorkspaceConfig,
)
import backend.app.services.structure_service as structure_service_module
from backend.app.main import create_app
from backend.app.services.structure_service import STRUCTURE_BINARY_MEDIA_TYPE, StructureService


def _find_outcar_fixture(name: str) -> Path | None:
    path = Path(__file__).resolve().parent.parent / "idea" / "tmp" / name
    return path if path.is_file() else None


def make_client(
    root: Path,
    *,
    max_points_json: int = 200_000,
    max_file_size_mb: int = 50,
    debug: bool = False,
) -> TestClient:
    settings = Settings(
        server=ServerConfig(debug=debug),
        workspace=WorkspaceConfig(root=root),
        viewer=ViewerConfig(max_file_size_mb=max_file_size_mb, ase=AseViewerConfig(max_points_json=max_points_json)),
    )
    return TestClient(create_app(settings))


def _write_selective_poscar(path: Path) -> None:
    path.write_text(
        "generated\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "H O\n"
        "1 1\n"
        "Selective Dynamics\n"
        "Direct\n"
        "0 0 0 T T T\n"
        "0.5 0.5 0.5 F F F\n",
        encoding="utf-8",
    )


def make_compressed_client(root: Path, *, max_points_json: int = 1) -> TestClient:
    settings = Settings(
        workspace=WorkspaceConfig(root=root),
        viewer=ViewerConfig(ase=AseViewerConfig(max_points_json=max_points_json)),
        compression=CompressionConfig(brotli=BrotliConfig(enabled=True, level=1)),
    )
    return TestClient(create_app(settings))


def test_ase_preview_reads_xyz(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "water.xyz"
    sample.write_text("3\nwater\nO 0 0 0\nH 0.76 0.58 0\nH -0.76 0.58 0\n", encoding="utf-8")

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "water.xyz"
    assert payload["transport"] == "json"
    assert payload["is_trajectory"] is False
    assert payload["n_frames"] == 1
    assert payload["n_atoms"] == 3
    assert payload["initial_frame_index"] == 0
    assert payload["frame"]["symbols"] == ["O", "H", "H"]
    assert payload["frame"]["positions"][1] == [0.76, 0.58, 0.0]


def test_ase_preview_returns_last_trajectory_frame_and_fixed_indices(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "fixed.traj"
    frame0 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]])
    frame1 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 2]])
    for atoms in (frame0, frame1):
        atoms.set_constraint(FixAtoms(indices=[1]))
    frame0.calc = SinglePointCalculator(frame0, energy=-1.0, forces=np.array([[1, 0, 0], [0, 0, 50]]))
    frame1.calc = SinglePointCalculator(frame1, energy=-2.0, forces=np.array([[0, 2, 0], [0, 0, 99]]))
    write(sample, [frame0, frame1])

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_trajectory"] is True
    assert payload["transport"] == "binary-available"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["positions"][1] == [0.0, 0.0, 2.0]
    assert payload["frame"]["fixed_indices"] == [1]
    assert payload["frame"]["energy"] == -2.0
    assert payload["frame"]["fmax"] == 2.0


def test_ase_frame_reads_requested_index(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\nframe 0\nH 0 0 0\n"
        "1\nframe 1\nH 1 0 0\n",
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_index"] == 1
    assert payload["positions"] == [[1.0, 0.0, 0.0]]


def test_ase_preview_and_json_chunk_support_variable_topology_db(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "mixed.db"
    database = connect(sample)
    database.write(Atoms("H", positions=[[0, 0, 0]]))
    database.write(Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]]))
    database.write(Atoms("O", positions=[[1, 0, 0]]))

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    chunk = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 0, "count": 3})

    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["is_trajectory"] is True
    assert preview_payload["topology_stable"] is False
    assert preview_payload["transport"] == "binary-available"
    assert preview_payload["n_frames"] == 3

    assert chunk.status_code == 200
    chunk_payload = chunk.json()
    assert chunk_payload["start"] == 0
    assert chunk_payload["count"] == 3
    assert [frame["symbols"] for frame in chunk_payload["frames"]] == [["H"], ["H", "H"], ["O"]]


def test_ase_variable_topology_db_binary_chunk(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "mixed.db"
    database = connect(sample)
    database.write(Atoms("H", positions=[[0, 0, 0]]))
    database.write(Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]]))
    database.write(Atoms("O", positions=[[1, 0, 0]]))

    response = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 3})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(STRUCTURE_BINARY_MEDIA_TYPE)
    body = response.content
    assert body[:4] == b"CWB1"
    header_len = struct.unpack("<I", body[4:8])[0]
    header = json.loads(body[8 : 8 + header_len].decode("utf-8"))
    padding = (4 - (header_len % 4)) % 4
    data_start = 8 + header_len + padding

    assert header["frame_encoding"] == "variable-atoms-v1"
    assert header["n_atoms"] == 0
    assert header["topology_stable"] is False
    assert header["arrays"]["frame_atom_counts"]["shape"] == [3]
    assert header["arrays"]["positions"]["shape"] == [4, 3]
    assert header["arrays"]["numbers"]["shape"] == [4]
    assert header["arrays"]["cells"]["shape"] == [3, 3, 3]
    assert header["arrays"]["pbc"]["shape"] == [3, 3]

    frame_counts_spec = header["arrays"]["frame_atom_counts"]
    frame_atom_counts = np.frombuffer(
        body,
        dtype="<i4",
        count=frame_counts_spec["byte_length"] // np.dtype("<i4").itemsize,
        offset=data_start + frame_counts_spec["offset"],
    )
    assert frame_atom_counts.tolist() == [1, 2, 1]

    numbers_spec = header["arrays"]["numbers"]
    numbers = np.frombuffer(
        body,
        dtype="<i4",
        count=numbers_spec["byte_length"] // np.dtype("<i4").itemsize,
        offset=data_start + numbers_spec["offset"],
    )
    # H=1, H2 -> 1,1, O=8; concatenated as [1, 1, 1, 8]
    assert numbers.tolist() == [1, 1, 1, 8]

    positions_spec = header["arrays"]["positions"]
    positions = np.frombuffer(
        body,
        dtype="<f4",
        count=positions_spec["byte_length"] // np.dtype("<f4").itemsize,
        offset=data_start + positions_spec["offset"],
    ).reshape(positions_spec["shape"])
    # frame 0 (H) at [0,0,0]; frame 1 (H2) at [0,0,0],[0,0,1]; frame 2 (O) at [1,0,0]
    assert positions[0:1].tolist() == [[0.0, 0.0, 0.0]]
    assert positions[1:3].tolist() == [[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert positions[3:4].tolist() == [[1.0, 0.0, 0.0]]

    pbc_spec = header["arrays"]["pbc"]
    pbc = np.frombuffer(
        body,
        dtype=np.uint8,
        count=pbc_spec["byte_length"],
        offset=data_start + pbc_spec["offset"],
    ).reshape(pbc_spec["shape"])
    # db frames are non-periodic by default
    assert pbc.tolist() == [[0, 0, 0], [0, 0, 0], [0, 0, 0]]


def test_ase_preview_blocks_path_traversal(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/structures/ase/preview", params={"path": str(tmp_path / ".." / "outside.xyz")})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_PATH"


def test_ase_parse_failure_uses_app_error(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "bad.xyz"
    sample.write_text("not an xyz file\n", encoding="utf-8")

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "STRUCTURE_PARSE_FAILED"


def test_ase_preview_and_frames_can_force_large_file(tmp_path: Path) -> None:
    client = make_client(tmp_path, max_file_size_mb=0)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\nframe 0\nH 0 0 0\n"
        "1\nframe 1\nH 1 0 0\n",
        encoding="utf-8",
    )

    blocked = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    forced_preview = client.get("/api/structures/ase/preview", params={"path": str(sample), "force": True})
    forced_frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 1, "force": True})
    forced_chunk = client.get(
        "/api/structures/ase/frames.bin",
        params={"path": str(sample), "start": 0, "count": 2, "force": True},
    )

    assert blocked.status_code == 413
    assert blocked.json()["error"]["code"] == "STRUCTURE_FILE_TOO_LARGE"
    assert forced_preview.status_code == 200
    assert forced_preview.json()["size_limit_overridden"] is True
    assert forced_frame.status_code == 200
    assert forced_frame.json()["positions"] == [[1.0, 0.0, 0.0]]
    assert forced_chunk.status_code == 200
    assert forced_chunk.content[:4] == b"CWB1"


def test_ase_binary_envelope_contains_float32_positions_and_fixed_mask(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "fixed.traj"
    frame0 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]])
    frame1 = Atoms("H2", positions=[[1, 0, 0], [1, 0, 1]])
    for atoms in (frame0, frame1):
        atoms.set_constraint(FixAtoms(indices=[1]))
    write(sample, [frame0, frame1])

    response = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(STRUCTURE_BINARY_MEDIA_TYPE)
    body = response.content
    assert body[:4] == b"CWB1"
    header_len = struct.unpack("<I", body[4:8])[0]
    header = json.loads(body[8 : 8 + header_len].decode("utf-8"))
    padding = (4 - (header_len % 4)) % 4
    data_start = 8 + header_len + padding

    assert header["arrays"]["positions"]["shape"] == [2, 2, 3]
    assert header["arrays"]["fixed_mask"]["shape"] == [2, 2]

    positions_spec = header["arrays"]["positions"]
    positions = np.frombuffer(
        body,
        dtype="<f4",
        count=positions_spec["byte_length"] // np.dtype("<f4").itemsize,
        offset=data_start + positions_spec["offset"],
    ).reshape(positions_spec["shape"])
    assert positions[1, 0].tolist() == [1.0, 0.0, 0.0]

    fixed_spec = header["arrays"]["fixed_mask"]
    fixed_mask = np.frombuffer(
        body,
        dtype=np.uint8,
        count=fixed_spec["byte_length"],
        offset=data_start + fixed_spec["offset"],
    ).reshape(fixed_spec["shape"])
    assert fixed_mask.tolist() == [[0, 1], [0, 1]]


def test_ase_xyz_preview_caches_summary_for_binary_chunks(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "".join(f"1\nframe {index}\nH {index} 0 0\n" for index in range(5)),
        encoding="utf-8",
    )
    original_scan = StructureService._scan_structure
    scan_count = 0

    def counted_scan(self, path, fmt, cancellation=None, force_full_scan=False):
        nonlocal scan_count
        scan_count += 1
        return original_scan(self, path, fmt, cancellation, force_full_scan)

    monkeypatch.setattr(StructureService, "_scan_structure", counted_scan)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    chunk0 = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})
    chunk1 = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 2, "count": 2})

    assert preview.status_code == 200
    assert chunk0.status_code == 200
    assert chunk1.status_code == 200
    assert scan_count == 1


def test_ase_plain_xyz_fast_range_reads_requested_chunk(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "".join(f"1\nframe {index}\nH {index} 0 0\n" for index in range(8)),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 5, "count": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [frame["frame_index"] for frame in payload["frames"]] == [5, 6]
    assert [frame["positions"][0][0] for frame in payload["frames"]] == [5.0, 6.0]


def test_ase_large_xyz_fast_preview_then_frame_chunk_runs_forward_scan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "large_movie.xyz"
    comment = "c" * 6200
    frame_count = 1800
    sample.write_text(
        "".join(f"1\n{comment} {index}\nH {index} 0 0\n" for index in range(frame_count)),
        encoding="utf-8",
    )

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["scan_completed"] is False
    assert preview_payload["frame"]["positions"][0][0] == float(frame_count - 1)

    response = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 5, "count": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [frame["frame_index"] for frame in payload["frames"]] == [5, 6]
    assert [frame["positions"][0][0] for frame in payload["frames"]] == [5.0, 6.0]


def test_ase_large_plain_xyz_fast_binary_chunk_reads_unsampled_frames(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "large_movie.xyz"
    comment = "c" * 6204
    frame_count = 1800
    sample.write_text(
        "".join(f"1\n{comment}\nH {index:04d} 0 0\n" for index in range(frame_count)),
        encoding="utf-8",
    )

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    assert preview.status_code == 200
    assert preview.json()["scan_completed"] is False

    response = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 16, "count": 16})

    assert response.status_code == 200
    header_length = struct.unpack_from("<I", response.content, 4)[0]
    header = json.loads(response.content[8 : 8 + header_length].decode("utf-8"))
    data_start = 8 + header_length + ((4 - (header_length % 4)) % 4)
    assert header["start"] == 16
    assert header["count"] == 16
    positions_spec = header["arrays"]["positions"]
    positions = np.frombuffer(
        response.content,
        dtype="<f4",
        count=positions_spec["byte_length"] // np.dtype("<f4").itemsize,
        offset=data_start + positions_spec["offset"],
    ).reshape(positions_spec["shape"])
    assert positions[0, 0, 0] == 16.0
    assert positions[-1, 0, 0] == 31.0


def test_ase_large_plain_xyz_fast_stream_reuses_preview_summary(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "large_movie.xyz"
    comment = "c" * 6204
    frame_count = 1800
    sample.write_text(
        "".join(f"1\n{comment}\nH {index:04d} 0 0\n" for index in range(frame_count)),
        encoding="utf-8",
    )

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    assert preview.status_code == 200
    assert preview.json()["scan_completed"] is False

    stream = client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 16})

    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: chunk" in stream.text
    assert "event: meta" not in stream.text
    assert "stable_stride_direct_arrays" not in stream.text
    assert "complete_index_direct_arrays" not in stream.text

    debug_client = make_client(tmp_path, debug=True)
    debug_stream = debug_client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 16})

    assert debug_stream.status_code == 200
    assert "event: meta" in debug_stream.text
    assert "stable_stride_direct_arrays" in debug_stream.text
    assert "complete_index_direct_arrays" not in debug_stream.text
    assert "fast_xyz_direct_arrays:xyz" in debug_stream.text


def test_ase_frame_stream_default_chunk_size_is_32(tmp_path: Path) -> None:
    client = make_client(tmp_path, debug=True)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "".join(f"1\nframe {index}\nH {index} 0 0\n" for index in range(40)),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/frames.stream", params={"path": str(sample)})

    assert response.status_code == 200
    ready_lines = response.text.splitlines()
    ready_event_index = ready_lines.index("event: ready")
    ready_data = json.loads(ready_lines[ready_event_index + 1].removeprefix("data: "))
    assert ready_data["chunk_size"] == 32


def test_ase_large_plain_xyz_fast_range_falls_back_when_stride_changes(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "large_movie.xyz"
    comment = "c" * 6200
    frame_count = 1800
    sample.write_text(
        "".join(f"1\n{comment} {index}\nH {index} 0 0\n" for index in range(frame_count)),
        encoding="utf-8",
    )

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    assert preview.status_code == 200
    assert preview.json()["scan_completed"] is False

    response = client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 32})

    assert response.status_code == 200
    assert "event: chunk" in response.text
    assert "event: error" not in response.text


def test_ase_extxyz_fast_range_preserves_cell_and_offsets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.extxyz"
    sample.write_text(
        "".join(
            (
                "2\n"
                f'Lattice="{index + 1} 0 0 0 {index + 2} 0 0 0 {index + 3}" '
                "Properties=species:S:1:pos:R:3 pbc=\"T T F\"\n"
                f"H {index} 0 0\n"
                f"O {index} 0 1\n"
            )
            for index in range(4)
        ),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 2, "count": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [frame["frame_index"] for frame in payload["frames"]] == [2, 3]
    assert payload["frames"][0]["symbols"] == ["H", "O"]
    assert payload["frames"][0]["positions"][0] == [2.0, 0.0, 0.0]
    assert payload["frames"][0]["cell"] == [[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 5.0]]
    assert payload["frames"][0]["pbc"] == [True, True, False]


def test_ase_xyz_extension_with_extxyz_header_preserves_cell(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\n"
        "Lattice=\"2 0 0 0 3 0 0 0 4\" Properties=species:S:1:pos:R:3 pbc=\"T F T\"\n"
        "H 0 0 0\n",
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "extxyz"
    assert payload["frame"]["cell"] == [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]]
    assert payload["frame"]["pbc"] == [True, False, True]


def test_ase_extxyz_preview_caches_summary_for_binary_chunks(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.extxyz"
    sample.write_text(
        "".join(
            (
                "1\n"
                "Lattice=\"1 0 0 0 1 0 0 0 1\" Properties=species:S:1:pos:R:3\n"
                f"H {index} 0 0\n"
            )
            for index in range(5)
        ),
        encoding="utf-8",
    )
    original_scan = StructureService._scan_structure
    scan_count = 0

    def counted_scan(self, path, fmt, cancellation=None, force_full_scan=False):
        nonlocal scan_count
        scan_count += 1
        return original_scan(self, path, fmt, cancellation, force_full_scan)

    monkeypatch.setattr(StructureService, "_scan_structure", counted_scan)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    chunk0 = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})
    chunk1 = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 2, "count": 2})

    assert preview.status_code == 200
    assert preview.json()["format"] == "extxyz"
    assert chunk0.status_code == 200
    assert chunk1.status_code == 200
    assert scan_count == 1


def test_ase_extxyz_preview_uses_fast_parser_for_common_properties(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.extxyz"
    sample.write_text(
        "2\n"
        "Lattice=\"2 0 0 0 3 0 0 0 4\" Properties=species:S:1:pos:R:3:move_mask:L:1:forces:R:3 energy=-1.5 pbc=\"T F T\"\n"
        "H 0 0 0 F 0 0 0\n"
        "O 0.5 0.25 0.75 T 3 0 4\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("common extxyz properties should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "extxyz"
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["positions"] == [[0.0, 0.0, 0.0], [0.5, 0.25, 0.75]]
    assert payload["frame"]["cell"] == [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]]
    assert payload["frame"]["pbc"] == [True, False, True]
    assert payload["frame"]["fixed_indices"] == [0]
    assert payload["frame"]["energy"] == -1.5
    assert payload["frame"]["fmax"] == 5.0


def test_ase_extxyz_fast_parser_reads_ref_energy_and_forces(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "train.extxyz"
    sample.write_text(
        "2\n"
        "Lattice=\"2 0 0 0 3 0 0 0 4\" Properties=species:S:1:pos:R:3:REF_forces:R:3 REF_energy=-7.25 pbc=\"T T T\"\n"
        "H 0 0 0 0 0 0\n"
        "O 0.5 0.25 0.75 0 3 4\n"
        "2\n"
        "Lattice=\"2 0 0 0 3 0 0 0 4\" Properties=species:S:1:pos:R:3:REF_forces:R:3 REF_energy=-8.5 pbc=\"T T T\"\n"
        "H 0 0 1 0 0 1\n"
        "O 0.5 0.25 1.75 6 8 0\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("REF_energy/REF_forces extxyz should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "extxyz"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["energy"] == -8.5
    assert payload["frame"]["fmax"] == 10.0


def test_ase_plain_xyz_fast_parser_reads_comment_energy(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\n"
        "FORCE:    ...  ENERGY:  -1.25\n"
        "H 0 0 0\n"
        "1\n"
        "FORCE:    ...  ENERGY:  -2.5\n"
        "H 1 0 0\n",
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame"]["positions"] == [[1.0, 0.0, 0.0]]
    assert payload["frame"]["energy"] == -2.5


def test_ase_plain_xyz_fixed_topology_fast_range_keeps_metadata(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "".join(
            (
                "2\n"
                f"frame {index} energy={-1.0 - index} fmax={2.0 + index}\n"
                f"H {index}.0 0.0 0.0\n"
                f"O {index}.5 1.0 0.0\n"
            )
            for index in range(4)
        ),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 1, "count": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [frame["symbols"] for frame in payload["frames"]] == [["H", "O"], ["H", "O"]]
    assert [frame["numbers"] for frame in payload["frames"]] == [[1, 8], [1, 8]]
    assert payload["frames"][0]["positions"] == [[1.0, 0.0, 0.0], [1.5, 1.0, 0.0]]
    assert payload["frames"][1]["positions"] == [[2.0, 0.0, 0.0], [2.5, 1.0, 0.0]]
    assert payload["frames"][0]["energy"] == -2.0
    assert payload["frames"][1]["fmax"] == 4.0


def test_ase_plain_xyz_variable_topology_range_uses_fallback(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\nframe 0\nH 0 0 0\n"
        "2\nframe 1\nH 1 0 0\nO 1 1 0\n",
        encoding="utf-8",
    )

    fixed_range_calls = 0
    original_fixed_range = StructureService._read_plain_xyz_fixed_frame_range

    def counted_fixed_range(self, path, index, start, end):
        nonlocal fixed_range_calls
        fixed_range_calls += 1
        return original_fixed_range(self, path, index, start, end)

    monkeypatch.setattr(StructureService, "_read_plain_xyz_fixed_frame_range", counted_fixed_range)

    response = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 0, "count": 2})

    assert response.status_code == 200
    assert fixed_range_calls == 0
    payload = response.json()
    assert [frame["symbols"] for frame in payload["frames"]] == [["H"], ["H", "O"]]


def test_ase_traj_preview_uses_native_index_and_last_frame(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.traj"
    frame0 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]])
    frame1 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 2]])
    write(sample, [frame0, frame1])

    original_scan = StructureService._scan_structure
    scan_count = 0

    def counted_scan(self, path, fmt, cancellation=None, force_full_scan=False):
        nonlocal scan_count
        scan_count += 1
        return original_scan(self, path, fmt, cancellation, force_full_scan)

    monkeypatch.setattr(StructureService, "_scan_structure", counted_scan)

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "traj"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["positions"][1] == [0.0, 0.0, 2.0]
    assert scan_count == 1


def test_ase_traj_frame_and_chunk_use_native_index(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.traj"
    frame0 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 1]])
    frame1 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 2]])
    frame2 = Atoms("H2", positions=[[0, 0, 0], [0, 0, 3]])
    write(sample, [frame0, frame1, frame2])

    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 2})
    chunk = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 1, "count": 2})

    assert frame.status_code == 200
    assert frame.json()["positions"][1] == [0.0, 0.0, 3.0]
    assert chunk.status_code == 200
    assert chunk.content[:4] == b"CWB1"


def test_ase_db_preview_uses_native_index_and_last_row(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.db"
    database = connect(sample)
    database.write(Atoms("H", positions=[[0, 0, 0]]))
    database.write(Atoms("H", positions=[[2, 0, 0]]))

    original_scan = StructureService._scan_structure
    scan_count = 0

    def counted_scan(self, path, fmt, cancellation=None, force_full_scan=False):
        nonlocal scan_count
        scan_count += 1
        return original_scan(self, path, fmt, cancellation, force_full_scan)

    monkeypatch.setattr(StructureService, "_scan_structure", counted_scan)

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "db"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["positions"][0] == [2.0, 0.0, 0.0]
    assert scan_count == 1


def test_ase_db_frame_and_chunk_use_native_index(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.db"
    database = connect(sample)
    database.write(Atoms("H", positions=[[0, 0, 0]]))
    database.write(Atoms("H", positions=[[1, 0, 0]]))
    database.write(Atoms("H", positions=[[2, 0, 0]]))

    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 2})
    chunk = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 1, "count": 2})

    assert frame.status_code == 200
    assert frame.json()["positions"][0] == [2.0, 0.0, 0.0]
    assert chunk.status_code == 200
    assert chunk.content[:4] == b"CWB1"


def test_ase_db_and_traj_fixed_indices_do_not_break_fmax(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    db_sample = tmp_path / "tuple-fixed.db"
    db_atoms = Atoms("H4", positions=[[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]])
    db_atoms.set_constraint(FixAtoms(indices=[1, 3]))
    db_atoms.calc = SinglePointCalculator(
        db_atoms,
        energy=-1.0,
        forces=np.array([[3.0, 0, 0], [99.0, 0, 0], [4.0, 0, 0], [88.0, 0, 0]]),
    )
    connect(db_sample).write(db_atoms)

    traj_sample = tmp_path / "tuple-fixed.traj"
    traj_atoms = Atoms("H4", positions=[[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]])
    traj_atoms.set_constraint(FixAtoms(indices=[1, 3]))
    traj_atoms.calc = SinglePointCalculator(
        traj_atoms,
        energy=-2.0,
        forces=np.array([[5.0, 0, 0], [77.0, 0, 0], [12.0, 0, 0], [66.0, 0, 0]]),
    )
    write(traj_sample, [traj_atoms])

    db_preview = client.get("/api/structures/ase/preview", params={"path": str(db_sample)})
    traj_preview = client.get("/api/structures/ase/preview", params={"path": str(traj_sample)})

    assert db_preview.status_code == 200
    assert db_preview.json()["frame"]["fixed_indices"] == [1, 3]
    assert db_preview.json()["frame"]["fmax"] == 4.0

    assert traj_preview.status_code == 200
    assert traj_preview.json()["frame"]["fixed_indices"] == [1, 3]
    assert traj_preview.json()["frame"]["fmax"] == 12.0


def test_ase_xdatcar_preview_and_frame_use_fast_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "XDATCAR"
    sample.write_text(
        "generated\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "H O\n"
        "1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n",
        encoding="utf-8",
    )
    _write_selective_poscar(tmp_path / "POSCAR")

    original_read = structure_service_module.read

    def fail_ase_read(*args, **kwargs):
        if kwargs.get("format") == "vasp":
            return original_read(*args, **kwargs)
        raise AssertionError("standard XDATCAR should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["format"] == "vasp-xdatcar"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["positions"] == [[0.5, 0.0, 0.0], [1.0, 0.75, 3.0]]
    assert payload["frame"]["fixed_indices"] == [1]

    assert frame.status_code == 200
    assert frame.json()["positions"] == [[0.0, 0.0, 0.0], [1.0, 1.5, 2.0]]
    assert frame.json()["fixed_indices"] == [1]


def test_ase_xdatcar_binary_chunk_uses_fast_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "XDATCAR"
    sample.write_text(
        "generated\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "H O\n"
        "1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n",
        encoding="utf-8",
    )
    _write_selective_poscar(tmp_path / "POSCAR")

    original_read = structure_service_module.read

    def fail_ase_read(*args, **kwargs):
        if kwargs.get("format") == "vasp":
            return original_read(*args, **kwargs)
        raise AssertionError("standard XDATCAR should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    response = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(STRUCTURE_BINARY_MEDIA_TYPE)
    body = response.content
    assert body[:4] == b"CWB1"
    header_len = struct.unpack("<I", body[4:8])[0]
    header = json.loads(body[8 : 8 + header_len].decode("utf-8"))
    padding = (4 - (header_len % 4)) % 4
    data_start = 8 + header_len + padding

    assert header["format"] == "vasp-xdatcar"
    assert header["symbols"] == ["H", "O"]
    assert header["arrays"]["positions"]["shape"] == [2, 2, 3]
    assert header["arrays"]["fixed_mask"]["shape"] == [2, 2]

    positions_spec = header["arrays"]["positions"]
    positions = np.frombuffer(
        body,
        dtype="<f4",
        count=positions_spec["byte_length"] // np.dtype("<f4").itemsize,
        offset=data_start + positions_spec["offset"],
    ).reshape(positions_spec["shape"])
    assert positions.tolist() == [
        [[0.0, 0.0, 0.0], [1.0, 1.5, 2.0]],
        [[0.5, 0.0, 0.0], [1.0, 0.75, 3.0]],
    ]

    fixed_spec = header["arrays"]["fixed_mask"]
    fixed_mask = np.frombuffer(
        body,
        dtype=np.uint8,
        count=fixed_spec["byte_length"],
        offset=data_start + fixed_spec["offset"],
    ).reshape(fixed_spec["shape"])
    assert fixed_mask.tolist() == [[0, 1], [0, 1]]

    debug_client = make_client(tmp_path, debug=True)
    debug_stream = debug_client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 2})
    assert debug_stream.status_code == 200
    assert '"read_path": "fast_xdatcar_direct_arrays"' in debug_stream.text


def test_ase_xdatcar_header_per_frame_variant_uses_fast_variable_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "XDATCAR2"
    sample.write_text(
        "Ag Cu O\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "Ag Cu O\n"
        "1 1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "0.25 0.25 0.25\n"
        "Ag C\n"
        "1.0\n"
        "4 0 0\n"
        "0 5 0\n"
        "0 0 6\n"
        "Ag C\n"
        "1 2\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n"
        "0.1 0.2 0.3\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("header-per-frame XDATCAR variant should use the fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame0 = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})
    chunk = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["format"] == "vasp-xdatcar"
    assert payload["n_frames"] == 2
    assert payload["topology_stable"] is False
    assert payload["frame"]["symbols"] == ["Ag", "C", "C"]
    assert payload["frame"]["positions"] == [[1.0, 0.0, 0.0], [2.0, 1.25, 4.5], [0.4, 1.0, 1.8]]

    assert frame0.status_code == 200
    assert frame0.json()["symbols"] == ["Ag", "Cu", "O"]
    assert frame0.json()["positions"] == [[0.0, 0.0, 0.0], [1.0, 1.5, 2.0], [0.5, 0.75, 1.0]]

    assert chunk.status_code == 200
    header_len = struct.unpack("<I", chunk.content[4:8])[0]
    header = json.loads(chunk.content[8 : 8 + header_len].decode("utf-8"))
    assert header["frame_encoding"] == "variable-atoms-v1"
    assert header["arrays"]["frame_atom_counts"]["shape"] == [2]

    debug_client = make_client(tmp_path, debug=True)
    debug_stream = debug_client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 2})
    assert debug_stream.status_code == 200
    assert '"read_path": "fast_xdatcar_variable"' in debug_stream.text


def test_ase_xdatcar_header_per_frame_variant_reuses_marker_offsets(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "XDATCAR2"
    sample.write_text(
        "Ag Cu O\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "Ag Cu O\n"
        "1 1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "0.25 0.25 0.25\n"
        "Ag C\n"
        "1.0\n"
        "4 0 0\n"
        "0 5 0\n"
        "0 0 6\n"
        "Ag C\n"
        "1 2\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n"
        "0.1 0.2 0.3\n",
        encoding="utf-8",
    )

    def fail_text_scan(*args, **kwargs):
        raise AssertionError("header-per-frame XDATCAR should reuse marker offsets")

    monkeypatch.setattr(StructureService, "_xdatcar_variable_header_offsets_by_text_scan", fail_text_scan)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["topology_stable"] is False
    assert payload["frame"]["symbols"] == ["Ag", "C", "C"]


def test_ase_xdatcar_header_per_frame_preview_uses_tail_fast_path(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "XDATCAR2"
    sample.write_text(
        "Ag Cu O\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "Ag Cu O\n"
        "1 1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "0.25 0.25 0.25\n"
        "Ag C\n"
        "1.0\n"
        "4 0 0\n"
        "0 5 0\n"
        "0 0 6\n"
        "Ag C\n"
        "1 2\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n"
        "0.1 0.2 0.3\n",
        encoding="utf-8",
    )

    def fail_full_marker_scan(*args, **kwargs):
        raise AssertionError("preview should not build a full XDATCAR marker offset table")

    def fail_marker_count(*args, **kwargs):
        raise AssertionError("preview should read n_frames from the last XDATCAR marker")

    monkeypatch.setattr(StructureService, "_xdatcar_preview_fast_threshold_bytes", lambda _self: 0)
    monkeypatch.setattr(StructureService, "_find_binary_marker_line_offsets", fail_full_marker_scan)
    monkeypatch.setattr(StructureService, "_count_binary_marker_occurrences", fail_marker_count)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["n_frames"] == 2
    assert payload["scan_completed"] is False
    assert payload["topology_stable"] is False
    assert payload["frame"]["symbols"] == ["Ag", "C", "C"]
    assert payload["frame"]["positions"] == [[1.0, 0.0, 0.0], [2.0, 1.25, 4.5], [0.4, 1.0, 1.8]]


def test_ase_xdatcar_header_per_frame_stream_rescans_after_fast_preview(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, debug=True)
    sample = tmp_path / "XDATCAR2"
    sample.write_text(
        "Ag Cu O\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "Ag Cu O\n"
        "1 1 1\n"
        "Direct configuration=     1\n"
        "0 0 0\n"
        "0.5 0.5 0.5\n"
        "0.25 0.25 0.25\n"
        "Ag C\n"
        "1.0\n"
        "4 0 0\n"
        "0 5 0\n"
        "0 0 6\n"
        "Ag C\n"
        "1 2\n"
        "Direct configuration=     2\n"
        "0.25 0 0\n"
        "0.5 0.25 0.75\n"
        "0.1 0.2 0.3\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("header-per-frame XDATCAR stream should use the fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    assert preview.status_code == 200
    assert preview.json()["scan_completed"] is False

    stream = client.get("/api/structures/ase/frames.stream", params={"path": str(sample), "chunk": 2})

    assert stream.status_code == 200
    assert "event: error" not in stream.text
    assert '"read_path": "fast_xdatcar_variable"' in stream.text


def test_ase_outcar_md_preview_returns_warning_and_metrics(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "OUTCAR"
    sample.write_text(
        "\n".join(
            [
                " POTCAR:   PAW_PBE H 15Jun2001",
                " POTCAR:   PAW_PBE O 08Apr2002",
                " POTCAR:   PAW_PBE H 15Jun2001",
                " POTCAR:   PAW_PBE O 08Apr2002",
                " IBRION =      0    ionic relaxation",
                " NIONS =      2 ions",
                " ions per type =               1   1",
                " ----------------------------------------- Iteration    1(   1)  ---------------------------------------",
                "      direct lattice vectors                 reciprocal lattice vectors",
                "     2.000000 0.000000 0.000000",
                "     0.000000 2.000000 0.000000",
                "     0.000000 0.000000 2.000000",
                " POSITION                                       TOTAL-FORCE (eV/Angst)",
                " -----------------------------------------------------------------------------------",
                "     0.000000 0.000000 0.000000    1.000000 0.000000 0.000000",
                "     1.000000 0.000000 0.000000    0.000000 2.000000 0.000000",
                " FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)",
                " ---------------------------------------------------",
                " free  energy   TOTEN  =       -1.250000 eV",
                "",
                " energy  without entropy=       -1.250000  energy(sigma->0) =       -1.250000",
                " ----------------------------------------- Iteration    2(   1)  ---------------------------------------",
                "      direct lattice vectors                 reciprocal lattice vectors",
                "     2.000000 0.000000 0.000000",
                "     0.000000 2.000000 0.000000",
                "     0.000000 0.000000 2.000000",
                " POSITION                                       TOTAL-FORCE (eV/Angst)",
                " -----------------------------------------------------------------------------------",
                "     0.100000 0.000000 0.000000    0.000000 0.000000 3.000000",
                "     1.100000 0.000000 0.000000    0.000000 4.000000 0.000000",
                " FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)",
                " ---------------------------------------------------",
                " free  energy   TOTEN  =       -2.500000 eV",
                "",
                " energy  without entropy=       -2.500000  energy(sigma->0) =       -2.500000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})
    chunk = client.get("/api/structures/ase/frames", params={"path": str(sample), "start": 0, "count": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "vasp-out"
    assert payload["warnings"] == ["vasp_outcar_constraints_missing", "vasp_outcar_md_may_lack_structure"]
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["positions"] == [[0.1, 0.0, 0.0], [1.1, 0.0, 0.0]]
    assert payload["frame"]["energy"] == -2.5
    assert payload["frame"]["fmax"] == 4.0

    assert frame.status_code == 200
    assert frame.json()["energy"] == -1.25
    assert frame.json()["fmax"] == 2.0

    assert chunk.status_code == 200
    chunk_frames = chunk.json()["frames"]
    assert [item["energy"] for item in chunk_frames] == [-1.25, -2.5]
    assert [item["fmax"] for item in chunk_frames] == [2.0, 4.0]


def test_ase_outcar_without_poscar_warns_and_uses_all_atom_fmax(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "OUTCAR"
    sample.write_text(
        "\n".join(
            [
                " POTCAR:   PAW_PBE H 15Jun2001",
                " POTCAR:   PAW_PBE O 08Apr2002",
                " POTCAR:   PAW_PBE H 15Jun2001",
                " POTCAR:   PAW_PBE O 08Apr2002",
                " NIONS =      2 ions",
                " ions per type =               1   1",
                " ----------------------------------------- Iteration    1(   1)  ---------------------------------------",
                "      direct lattice vectors                 reciprocal lattice vectors",
                "     2.000000 0.000000 0.000000",
                "     0.000000 2.000000 0.000000",
                "     0.000000 0.000000 2.000000",
                " POSITION                                       TOTAL-FORCE (eV/Angst)",
                " -----------------------------------------------------------------------------------",
                "     0.000000 0.000000 0.000000    1.000000 0.000000 0.000000",
                "     1.000000 0.000000 0.000000    0.000000 3.000000 4.000000",
                " FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)",
                " ---------------------------------------------------",
                " free  energy   TOTEN  =       -1.250000 eV",
                "",
                " energy  without entropy=       -1.250000  energy(sigma->0) =       -1.250000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    response = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == ["vasp_outcar_constraints_missing"]
    assert payload["frame"]["fixed_indices"] == []
    assert payload["frame"]["fmax"] == 5.0


def test_ase_outcar_fast_parser_aligns_with_ase() -> None:
    import numpy as np
    from ase.io import read as ase_read

    fixtures = [("OUTCAR1", 500), ("OUTCAR2", 6)]
    root = Path(__file__).resolve().parent.parent
    root_svc = StructureService(Settings(workspace=WorkspaceConfig(root=str(root))))
    for name, expected_n_frames in fixtures:
        sample = _find_outcar_fixture(name)
        if sample is None:
            import pytest
            pytest.skip(f"{name} not found (regression fixture check)")

        summary = root_svc._get_structure_summary(sample, "vasp-out")
        assert summary.n_frames == expected_n_frames, f"{name} n_frames: {summary.n_frames} != {expected_n_frames}"

        for idx in [0, 1, expected_n_frames // 2, expected_n_frames - 2, expected_n_frames - 1]:
            if idx < 0 or idx >= summary.n_frames:
                continue
            ase_atoms = ase_read(sample, index=idx, format="vasp-out")
            fast = root_svc.read_frame(str(sample), idx, fmt="vasp-out", force=True)
            symbol_ok = ase_atoms.get_chemical_symbols() == (fast.symbols or [])
            pos_ok = np.allclose(ase_atoms.get_positions(), fast.positions, atol=5e-6, rtol=0)
            cell_ok = np.allclose(np.asarray(ase_atoms.cell), fast.cell, atol=5e-6, rtol=0)
            energy_ok = np.isclose(ase_atoms.get_potential_energy(), fast.energy, atol=5e-8, rtol=0)
            afmax = float(np.max(np.linalg.norm(ase_atoms.calc.results["forces"], axis=1)))
            fmax_ok = np.isclose(afmax, fast.fmax, atol=5e-6, rtol=0)
            assert symbol_ok and pos_ok and cell_ok and energy_ok and fmax_ok, (
                f"{name} index={idx}: symbols={symbol_ok}, pos={pos_ok}, cell={cell_ok}, "
                f"energy={energy_ok}, fmax={fmax_ok}"
            )


def test_ase_outcar_fast_parser_reads_poscar_constraints() -> None:
    import numpy as np
    import pytest
    from ase.io import read as ase_read

    sample = _find_outcar_fixture("vaspfixtest/OUTCAR")
    if sample is None:
        pytest.skip("vaspfixtest OUTCAR not found (regression fixture check)")

    root = Path(__file__).resolve().parent.parent
    root_svc = StructureService(Settings(workspace=WorkspaceConfig(root=str(root))))
    ase_atoms = ase_read(sample, index=-1, format="vasp-out")
    preview = root_svc.preview(str(sample), fmt="vasp-out", force=True)
    frame = preview.frame

    fixed_indices = sorted({int(index) for constraint in ase_atoms.constraints for index in getattr(constraint, "index", [])})
    force_array = ase_atoms.get_forces()
    movable_mask = np.ones(len(ase_atoms), dtype=bool)
    movable_mask[fixed_indices] = False
    ase_fmax = float(np.max(np.linalg.norm(force_array[movable_mask], axis=1)))

    assert preview.warnings == []
    assert frame.fixed_indices == fixed_indices
    assert np.isclose(frame.fmax, ase_fmax, atol=5e-6, rtol=0)


def test_ase_poscar_preview_and_frame_use_fast_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "POSCAR"
    sample.write_text(
        "generated\n"
        "1.0\n"
        "2 0 0\n"
        "0 3 0\n"
        "0 0 4\n"
        "H O\n"
        "1 1\n"
        "Selective dynamics\n"
        "Direct\n"
        "0 0 0 F F F\n"
        "0.5 0.25 0.75 T T T\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("standard VASP 5 POSCAR should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["format"] == "vasp"
    assert payload["n_frames"] == 1
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["cell"] == [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]]
    assert payload["frame"]["positions"] == [[0.0, 0.0, 0.0], [1.0, 0.75, 3.0]]
    assert payload["frame"]["fixed_indices"] == [0]

    assert frame.status_code == 200
    assert frame.json()["positions"] == [[0.0, 0.0, 0.0], [1.0, 0.75, 3.0]]


def test_ase_xsd_preview_and_frame_use_fast_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "cell.xsd"
    sample.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<XSD><AtomisticTreeRoot><SymmetrySystem><MappingSet><MappingFamily><IdentityMapping>\n"
        "<Atom3d ID=\"4\" Components=\"H\" XYZ=\"0,0,0\" />\n"
        "<Atom3d ID=\"5\" Components=\"O\" XYZ=\"0.5,0.25,0.75\" RestrictedProperties=\"FractionalXYZ\" />\n"
        "<SpaceGroup AVector=\"2,0,0\" BVector=\"0,3,0\" CVector=\"0,0,4\" />\n"
        "</IdentityMapping></MappingFamily></MappingSet></SymmetrySystem></AtomisticTreeRoot></XSD>\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("periodic xsd should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["format"] == "xsd"
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["cell"] == [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]]
    assert payload["frame"]["positions"] == [[0.0, 0.0, 0.0], [1.0, 0.75, 3.0]]
    assert payload["frame"]["fixed_indices"] == [1]

    assert frame.status_code == 200
    assert frame.json()["positions"] == [[0.0, 0.0, 0.0], [1.0, 0.75, 3.0]]
    assert frame.json()["fixed_indices"] == [1]


def test_ase_xsd_fast_parser_reads_complete_restriction_fixed_indices(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "fixed-complete-restriction.xsd"
    sample.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<XSD><AtomisticTreeRoot><SymmetrySystem><MappingSet><MappingFamily><IdentityMapping>\n"
        "<Atom3d ID=\"4\" Components=\"H\" XYZ=\"0,0,0\" />\n"
        "<Atom3d ID=\"5\" Components=\"O\" XYZ=\"0.5,0.25,0.75\" />\n"
        "<CompleteRestriction ID=\"7\" RestrictsObjects=\"ci(1):5\" RestrictsProperties=\"FractionalXYZ\" />\n"
        "<SpaceGroup AVector=\"2,0,0\" BVector=\"0,3,0\" CVector=\"0,0,4\" />\n"
        "</IdentityMapping></MappingFamily></MappingSet></SymmetrySystem></AtomisticTreeRoot></XSD>\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("periodic xsd should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})

    assert preview.status_code == 200
    assert preview.json()["frame"]["fixed_indices"] == [1]


def test_ase_dmol_arc_preview_frame_and_binary_use_fast_parser(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.arc"
    sample.write_text(
        "!BIOSYM archive 3\n"
        "PBC=ON\n"
        "\n"
        "!DATE     Jan 01 00:00:00 2026\n"
        "PBC   2.00000   3.00000   4.00000  90.00000  90.00000  90.00000\n"
        "H1        0.00000000     0.00000000     0.00000000 XXXX 1      xx      H   0.000\n"
        "O2        1.00000000     1.50000000     2.00000000 XXXX 1      xx      O   0.000\n"
        "end\n"
        "end\n"
        "\n"
        "!DATE     Jan 01 00:00:01 2026\n"
        "PBC   2.00000   3.00000   4.00000  90.00000  90.00000  90.00000\n"
        "H1        0.50000000     0.00000000     0.00000000 XXXX 1      xx      H   0.000\n"
        "O2        1.00000000     0.75000000     3.00000000 XXXX 1      xx      O   0.000\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )

    def fail_ase_read(*args, **kwargs):
        raise AssertionError("standard DMol ARC should use the direct fast parser")

    monkeypatch.setattr(structure_service_module, "iread", fail_ase_read)
    monkeypatch.setattr(structure_service_module, "read", fail_ase_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample)})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 0})
    chunk = client.get("/api/structures/ase/frames.bin", params={"path": str(sample), "start": 0, "count": 2})

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["format"] == "dmol-arc"
    assert payload["n_frames"] == 2
    assert payload["initial_frame_index"] == 1
    assert payload["frame"]["symbols"] == ["H", "O"]
    assert payload["frame"]["positions"] == [[0.5, 0.0, 0.0], [1.0, 0.75, 3.0]]
    assert payload["frame"]["cell"] == [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]]
    assert payload["frame"]["pbc"] == [True, True, True]

    assert frame.status_code == 200
    assert frame.json()["positions"] == [[0.0, 0.0, 0.0], [1.0, 1.5, 2.0]]

    assert chunk.status_code == 200
    assert chunk.content[:4] == b"CWB1"
    header_len = struct.unpack("<I", chunk.content[4:8])[0]
    header = json.loads(chunk.content[8 : 8 + header_len].decode("utf-8"))
    assert header["format"] == "dmol-arc"
    assert header["arrays"]["positions"]["shape"] == [2, 2, 3]


def test_ase_generic_preview_reuses_loaded_frames_for_frame_reads(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    sample = tmp_path / "movie.pdb"
    sample.write_text("mock structure content\n", encoding="utf-8")
    atoms_frames = [
        Atoms("H", positions=[[0, 0, 0]]),
        Atoms("H", positions=[[1, 0, 0]]),
        Atoms("H", positions=[[2, 0, 0]]),
    ]
    iread_count = 0
    read_count = 0

    def fake_iread(path, index=":", format=None, do_not_split_by_at_sign=True):
        nonlocal iread_count
        assert Path(path) == sample
        assert index == ":"
        iread_count += 1
        yield from atoms_frames

    def fail_read(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        raise AssertionError("cached generic frames should avoid read()")

    monkeypatch.setattr(structure_service_module, "iread", fake_iread)
    monkeypatch.setattr(structure_service_module, "read", fail_read)

    preview = client.get("/api/structures/ase/preview", params={"path": str(sample), "format": "pdb"})
    frame = client.get("/api/structures/ase/frame", params={"path": str(sample), "index": 2, "format": "pdb"})

    assert preview.status_code == 200
    assert preview.json()["n_frames"] == 3
    assert frame.status_code == 200
    assert frame.json()["positions"] == [[2.0, 0.0, 0.0]]
    assert iread_count == 1
    assert read_count == 0


def test_brotli_compresses_ase_json_and_binary_but_not_zip(tmp_path: Path) -> None:
    client = make_compressed_client(tmp_path, max_points_json=200_000)
    sample = tmp_path / "movie.xyz"
    sample.write_text(
        "1\nframe 0\nH 0 0 0\n"
        "1\nframe 1\nH 1 0 0\n",
        encoding="utf-8",
    )

    json_response = client.get(
        "/api/structures/ase/preview",
        params={"path": str(sample)},
        headers={"Accept-Encoding": "br"},
    )
    assert json_response.status_code == 200
    assert json_response.headers["content-encoding"] == "br"
    assert json_response.json()["transport"] == "binary-available"

    binary_response = client.get(
        "/api/structures/ase/frames.bin",
        params={"path": str(sample), "start": 0, "count": 2},
        headers={"Accept-Encoding": "br"},
    )
    assert binary_response.status_code == 200
    assert binary_response.headers["content-encoding"] == "br"
    assert binary_response.content[:4] == b"CWB1"

    zip_response = client.post(
        "/api/files/download-archive",
        json={"paths": [str(sample)]},
        headers={"Accept-Encoding": "br"},
    )
    assert zip_response.status_code == 200
    assert "content-encoding" not in zip_response.headers
    with ZipFile(BytesIO(zip_response.content)) as archive:
        assert "movie.xyz" in archive.namelist()
