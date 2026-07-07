from __future__ import annotations

import json
import logging
import math
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from io import StringIO
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

import numpy as np
from ase import Atoms
from ase.constraints import dict2constraint
from ase.data import atomic_numbers, chemical_symbols
from ase.db import connect
from ase.geometry.cell import cellpar_to_cell
from ase.io import iread, read
from ase.io.jsonio import decode
from ase.io.trajectory import Trajectory
from ase.io.extxyz import key_val_str_to_dict, parse_properties

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.core.security import WorkspaceSecurity
from backend.app.models.structure import AseFrame, AseFrameChunkResponse, AsePreviewResponse


STRUCTURE_BINARY_MEDIA_TYPE = "application/vnd.chemssh.structure+bin"
STRUCTURE_BINARY_MAGIC = b"CWB1"


def _tail_read_from_end(path: Path, max_bytes: int = 1_048_576) -> tuple[bytes, int, int]:
    """
    从文件尾部反向读取数据。
    返回 (data: bytes, file_size: int, tail_offset: int)
    data 是尾部合并后的数据，file_size 是文件总大小，tail_offset 是 data 开始的字节位置。
    使用 B5 tail 策略：从后往前读取块，避免 O(n²) 前插。
    """
    block_size = 8192
    chunks: list[bytes] = []
    total = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        remaining = handle.tell()
        while remaining > 0:
            read_size = min(block_size, remaining)
            remaining -= read_size
            handle.seek(remaining)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
    data = b"".join(reversed(chunks))
    file_size = remaining + len(data)
    return data, file_size, max(0, file_size - len(data))


def _find_last_marker_offset(tail_data: bytes, file_size: int, tail_offset: int, marker: bytes) -> int | None:
    """在尾部数据中反向搜索最后一个 marker，返回其在文件中的全局字节偏移。"""
    pos = tail_data.rfind(marker)
    if pos == -1:
        return None
    return tail_offset + pos


_FLOAT_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][-+]?\d+)?"
_PLAIN_XYZ_ENERGY_RE = re.compile(rf"\b(?:energy|e)\b\s*[:=]\s*({_FLOAT_PATTERN})", re.IGNORECASE)
_PLAIN_XYZ_FMAX_RE = re.compile(
    rf"\b(?:fmax|max_force|maxforce|force_max)\b\s*[:=]\s*({_FLOAT_PATTERN})",
    re.IGNORECASE,
)


class StructureCancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise AppError("REQUEST_CANCELLED", "Request was cancelled", 499)


@dataclass
class StructureSummary:
    n_frames: int
    n_atoms: int
    last_atoms: Atoms | None
    topology_stable: bool
    detected_format: str | None
    last_frame_data: "FrameData | None" = None
    fast_xyz_index: "FastXyzIndex | None" = None
    fast_native_index: "FastNativeIndex | None" = None
    fast_xdatcar_index: "FastXdatcarIndex | None" = None
    fast_xdatcar_variable_index: "FastXdatcarVariableIndex | None" = None
    fast_arc_index: "FastArcIndex | None" = None
    fast_outcar_index: "FastOutcarIndex | None" = None
    loaded_frames: tuple[Atoms, ...] | None = None
    file_incomplete: bool = False
    scan_completed: bool = True
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameData:
    positions: np.ndarray
    numbers: np.ndarray
    symbols: tuple[str, ...]
    cell: np.ndarray
    pbc: tuple[bool, bool, bool]
    tags: np.ndarray | None = None
    fixed_indices: tuple[int, ...] = ()
    energy: float | None = None
    fmax: float | None = None


@dataclass(frozen=True)
class StructureChunkArrays:
    count: int
    positions: np.ndarray
    cells: np.ndarray
    tags: np.ndarray
    fixed_mask: np.ndarray
    energy: np.ndarray
    fmax: np.ndarray
    symbols: tuple[str, ...]
    numbers: np.ndarray
    pbc: tuple[bool, bool, bool]


@dataclass(frozen=True)
class FastXyzIndex:
    frame_offsets: tuple[int, ...]
    frame_lengths: tuple[int, ...]
    atom_counts: tuple[int, ...]
    detected_format: str
    complete: bool = True
    n_frames: int | None = None
    last_frame_offset: int | None = None


@dataclass(frozen=True)
class FastNativeIndex:
    n_frames: int
    detected_format: str


@dataclass(frozen=True)
class FastXdatcarIndex:
    frame_offsets: tuple[int, ...]
    n_atoms: int
    symbols: tuple[str, ...]
    numbers: tuple[int, ...]
    cell: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    fixed_indices: tuple[int, ...] = ()
    detected_format: str = "vasp-xdatcar"


@dataclass(frozen=True)
class FastXdatcarVariableIndex:
    header_offsets: tuple[int, ...]
    detected_format: str = "vasp-xdatcar"


@dataclass(frozen=True)
class FastOutcarIndex:
    frame_offsets: tuple[int, ...]
    n_atoms: int
    symbols: tuple[str, ...]
    numbers: tuple[int, ...]
    cell: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    cells: tuple[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]], ...] = ()
    fixed_indices: tuple[int, ...] = ()
    energies: tuple[float | None, ...] = ()
    fmaxes: tuple[float | None, ...] = ()
    detected_format: str = "vasp-out"


@dataclass(frozen=True)
class FastArcIndex:
    frame_offsets: tuple[int, ...]
    n_atoms: int
    pbc: tuple[bool, bool, bool]
    detected_format: str = "dmol-arc"


@dataclass
class StructureCacheEntry:
    summary: StructureSummary
    created_at: float
    last_accessed_at: float


MAX_CACHE_SIZE = 1000
CACHE_TTL_SECONDS = 3600

_summary_cache: OrderedDict[tuple[str, str | None, int, int], StructureCacheEntry] = OrderedDict()
_summary_cache_lock = threading.Lock()


def _get_cached_summary(key: tuple) -> StructureSummary | None:
    with _summary_cache_lock:
        entry = _summary_cache.get(key)
        if entry is None:
            return None

        now = time.monotonic()
        if now - entry.created_at > CACHE_TTL_SECONDS:
            del _summary_cache[key]
            return None

        entry.last_accessed_at = now
        _summary_cache.move_to_end(key)
        return entry.summary


def _set_cached_summary(key: tuple, summary: StructureSummary) -> None:
    with _summary_cache_lock:
        now = time.monotonic()
        if key in _summary_cache:
            _summary_cache[key].summary = summary
            _summary_cache[key].last_accessed_at = now
            _summary_cache.move_to_end(key)
        else:
            _summary_cache[key] = StructureCacheEntry(
                summary=summary,
                created_at=now,
                last_accessed_at=now,
            )
            if len(_summary_cache) > MAX_CACHE_SIZE:
                _summary_cache.popitem(last=False)


class FastParserUnsupported(Exception):
    pass


def _profile_add(profile: dict[str, Any] | None, key: str, value: float) -> None:
    if profile is not None:
        profile[key] = float(profile.get(key, 0.0) or 0.0) + float(value)


def _profile_start(profile: dict[str, Any] | None) -> float:
    return time.perf_counter() if profile is not None else 0.0


def _profile_add_ms(profile: dict[str, Any] | None, key: str, start: float) -> None:
    if profile is not None:
        _profile_add(profile, key, (time.perf_counter() - start) * 1000.0)


def _profile_set(profile: dict[str, Any] | None, key: str, value: Any) -> None:
    if profile is not None:
        profile[key] = value

class StructureService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.security = WorkspaceSecurity(settings.workspace.root)

    def preview(
        self,
        raw_path: str,
        fmt: str | None = None,
        *,
        force: bool = False,
        cancellation: StructureCancellationToken | None = None,
    ) -> AsePreviewResponse:
        path = self._resolve_structure_file(raw_path, force=force)
        cancellation = cancellation or StructureCancellationToken()
        cancellation.check()
        summary = self._get_structure_summary(path, fmt, cancellation)
        cancellation.check()
        frame_index = summary.n_frames - 1
        frame = self._response_frame_from_summary(summary, frame_index)
        points = summary.n_atoms * summary.n_frames
        use_binary = (
            self.settings.viewer.ase.prefer_binary
            and summary.n_frames > 1
        )

        return AsePreviewResponse(
            path=str(path),
            name=path.name,
            format=summary.detected_format,
            transport="binary-available" if use_binary else "json",
            is_trajectory=summary.n_frames > 1,
            n_frames=summary.n_frames,
            n_atoms=summary.n_atoms,
            initial_frame_index=frame_index,
            topology_stable=summary.topology_stable,
            size_limit_overridden=force,
            frame=frame,
            file_incomplete=summary.file_incomplete,
            scan_completed=summary.scan_completed,
            warnings=list(summary.warnings),
        )

    def read_frame(self, raw_path: str, index: int, fmt: str | None = None, *, force: bool = False) -> AseFrame:
        if index < 0:
            raise AppError("INVALID_FRAME_INDEX", "Frame index must be greater than or equal to 0", 400)
        path = self._resolve_structure_file(raw_path, force=force)
        summary = self._get_reusable_summary_if_supported(path, fmt, allow_incomplete=False)
        frame = self._read_frame(path, index, fmt, summary=summary)
        return self._frame_response(frame, index)

    def read_frame_chunk_json(
        self,
        raw_path: str,
        start: int,
        count: int,
        fmt: str | None = None,
        *,
        force: bool = False,
    ) -> AseFrameChunkResponse:
        if start < 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk start must be greater than or equal to 0", 400)
        if count <= 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk count must be greater than 0", 400)

        path = self._resolve_structure_file(raw_path, force=force)
        safe_count = min(count, self.settings.viewer.ase.binary_chunk_frames)
        summary = self._get_reusable_summary_if_supported(path, fmt, allow_incomplete=False)
        frame_data = self._read_frame_range(path, start, safe_count, fmt, summary=summary)
        if not frame_data:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame start is outside range: {start}", 404)

        frames = [
            self._frame_response(frame, start + offset)
            for offset, frame in enumerate(frame_data)
        ]
        return AseFrameChunkResponse(start=start, count=len(frames), frames=frames)

    def read_frame_chunk_binary(self, raw_path: str, start: int, count: int, fmt: str | None = None, *, force: bool = False) -> bytes:
        if start < 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk start must be greater than or equal to 0", 400)
        if count <= 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk count must be greater than 0", 400)

        path = self._resolve_structure_file(raw_path, force=force)
        summary = self._get_structure_summary(path, fmt, allow_incomplete=False)
        if start >= summary.n_frames:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame start is outside range: {start}", 404)

        max_count = self.settings.viewer.ase.binary_chunk_frames
        safe_count = min(count, max_count, summary.n_frames - start)
        direct_payload = self._try_build_direct_fixed_binary_payload(
            path=path,
            summary=summary,
            start=start,
            count=safe_count,
            n_frames_total=summary.n_frames,
            profile=None,
        )
        if direct_payload is not None:
            return direct_payload

        frames = self._read_frame_range(path, start, safe_count, fmt, summary=summary)
        if len(frames) != safe_count:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", "Requested frame chunk is incomplete", 404)
        return self._build_binary_payload_from_frames(
            path=path,
            frames=frames,
            start=start,
            n_frames_total=summary.n_frames,
            detected_format=summary.detected_format,
        )

    def stream_frame_chunk_binary(
        self,
        resolved_path: Path,
        start: int,
        count: int,
        fmt: str | None,
        summary: "StructureSummary",
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        """Read a binary chunk using an already-resolved path and cached summary.

        This avoids the redundant path-resolution and summary lookup that
        ``read_frame_chunk_binary`` performs on every call, which matters for
        SSE streaming where hundreds of chunks are read in rapid succession.
        """
        if start < 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk start must be greater than or equal to 0", 400)
        if count <= 0:
            raise AppError("INVALID_FRAME_RANGE", "Chunk count must be greater than 0", 400)
        if start >= summary.n_frames:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame start is outside range: {start}", 404)

        max_count = self.settings.viewer.ase.binary_chunk_frames
        safe_count = min(count, max_count, summary.n_frames - start)
        _profile_set(profile, "safe_count", safe_count)
        direct_payload = self._try_build_direct_fixed_binary_payload(
            path=resolved_path,
            summary=summary,
            start=start,
            count=safe_count,
            n_frames_total=summary.n_frames,
            profile=profile,
        )
        if direct_payload is not None:
            return direct_payload

        t_read_0 = _profile_start(profile)
        frames = self._read_frame_range(resolved_path, start, safe_count, fmt, summary=summary, profile=profile)
        _profile_add_ms(profile, "read_frame_range_ms", t_read_0)
        if len(frames) != safe_count and summary.scan_completed:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", "Requested frame chunk is incomplete", 404)
        t_build_0 = _profile_start(profile)
        payload = self._build_binary_payload_from_frames(
            path=resolved_path,
            frames=frames,
            start=start,
            n_frames_total=summary.n_frames,
            detected_format=summary.detected_format,
            profile=profile,
        )
        _profile_add_ms(profile, "build_payload_total_ms", t_build_0)
        return payload

    def _resolve_structure_file(self, raw_path: str, *, force: bool = False) -> Path:
        if not self.settings.viewer.ase.enabled:
            raise AppError("ASE_VIEWER_DISABLED", "ASE structure preview is disabled", 400)

        path = self.security.resolve_path(raw_path)
        if not path.exists():
            raise AppError("FILE_NOT_FOUND", f"File not found: {path}", 404)
        if not path.is_file():
            raise AppError("NOT_A_FILE", f"Path is not a file: {path}", 400)

        max_bytes = self.settings.viewer.max_file_size_mb * 1024 * 1024
        size = path.stat().st_size
        if not force and size > max_bytes:
            raise AppError(
                "STRUCTURE_FILE_TOO_LARGE",
                f"File is {size} bytes; structure preview limit is {max_bytes} bytes",
                413,
            )
        return path

    def _get_structure_summary(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
        force_full_scan: bool = False,
        allow_incomplete: bool = True,
    ) -> StructureSummary:
        key = self._cache_key(path, fmt)
        cached = None if force_full_scan else _get_cached_summary(key)
        if cached is not None and (allow_incomplete or cached.scan_completed):
            return cached

        summary = self._scan_structure(
            path,
            fmt,
            cancellation,
            force_full_scan=force_full_scan or not allow_incomplete,
        )
        _set_cached_summary(key, summary)
        return summary

    def _get_reusable_summary_if_supported(
        self,
        path: Path,
        fmt: str | None,
        *,
        allow_incomplete: bool = True,
    ) -> StructureSummary | None:
        if (
            _is_xyz_like_format(path, fmt)
            or _is_native_indexed_format(path, fmt)
            or _is_vasp_xdatcar_format(path, fmt)
            or _is_vasp_poscar_format(path, fmt)
            or _is_xsd_format(path, fmt)
            or _is_dmol_arc_format(path, fmt)
        ):
            return self._get_structure_summary(path, fmt, allow_incomplete=allow_incomplete)
        return self._get_cached_structure_summary(path, fmt)

    def stream_requires_complete_summary(self, path: Path, fmt: str | None) -> bool:
        return _is_vasp_xdatcar_format(path, fmt)

    def _get_cached_structure_summary(self, path: Path, fmt: str | None) -> StructureSummary | None:
        key = self._cache_key(path, fmt)
        return _get_cached_summary(key)

    def _cache_key(self, path: Path, fmt: str | None) -> tuple[str, str | None, int, int]:
        stat = path.stat()
        return (str(path), _normalized_format(fmt), stat.st_size, stat.st_mtime_ns)

    def _scan_structure(self, path: Path, fmt: str | None, cancellation: StructureCancellationToken | None = None, force_full_scan: bool = False) -> StructureSummary:
        if _is_xyz_like_format(path, fmt):
            return self._scan_xyz_like(path, fmt, cancellation, force_full_scan=force_full_scan)
        if _is_native_indexed_format(path, fmt):
            return self._scan_native_indexed(path, fmt, cancellation)
        if _is_vasp_xdatcar_format(path, fmt):
            try:
                return self._scan_xdatcar(path, cancellation, force_full_scan=force_full_scan)
            except FastParserUnsupported:
                return self._scan_structure_with_ase(path, fmt, cancellation)
        if _is_vasp_outcar_format(path, fmt):
            try:
                return self._scan_outcar(path, cancellation)
            except FastParserUnsupported:
                return self._scan_structure_with_ase(path, fmt, cancellation)
        if _is_vasp_poscar_format(path, fmt):
            try:
                return self._scan_vasp_poscar(path)
            except FastParserUnsupported:
                return self._scan_structure_with_ase(path, fmt, cancellation)
        if _is_xsd_format(path, fmt):
            try:
                return self._scan_xsd(path)
            except FastParserUnsupported:
                return self._scan_structure_with_ase(path, fmt, cancellation)
        if _is_dmol_arc_format(path, fmt):
            try:
                return self._scan_dmol_arc(path, cancellation)
            except FastParserUnsupported:
                return self._scan_structure_with_ase(path, fmt, cancellation)
        return self._scan_structure_with_ase(path, fmt, cancellation)

    def _scan_native_indexed(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        detected_format = _detected_native_indexed_format(path, fmt)
        try:
            n_frames = self._native_frame_count(path, detected_format)
            cancellation.check()
            if n_frames == 0:
                raise AppError("STRUCTURE_PARSE_FAILED", f"ASE did not find any frames in {path.name}", 400)
            if n_frames > self.settings.viewer.ase.max_frames:
                raise AppError(
                    "STRUCTURE_TOO_MANY_FRAMES",
                    f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                    413,
                )

            first_frame = self._read_native_frame(path, detected_format, 0)
            self._validate_structure_frame(first_frame, path)
            last_frame = self._read_native_frame(path, detected_format, -1)
            self._validate_structure_frame(last_frame, path)
            first_numbers = _frame_numbers(first_frame)
            last_numbers = _frame_numbers(last_frame)
            topology_stable = _frame_atom_count(first_frame) == _frame_atom_count(last_frame) and first_numbers == last_numbers
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        return StructureSummary(
            n_frames=n_frames,
            n_atoms=_frame_atom_count(first_frame),
            last_atoms=last_frame if isinstance(last_frame, Atoms) else None,
            last_frame_data=last_frame if isinstance(last_frame, FrameData) else None,
            topology_stable=topology_stable,
            detected_format=detected_format,
            fast_native_index=FastNativeIndex(n_frames=n_frames, detected_format=detected_format),
        )

    def _native_frame_count(self, path: Path, detected_format: str) -> int:
        if detected_format == "traj":
            with Trajectory(str(path), "r") as trajectory:
                return len(trajectory)
        if detected_format == "db":
            with connect(path, serial=True) as database:
                return database.count()
        raise ValueError(f"Unsupported native indexed format: {detected_format}")

    def _find_xdatcar_last_frame_offset(self, path: Path) -> int | None:
        """从文件尾部反向搜索最后一个 'Direct configuration=' 的偏移。"""
        for max_bytes in (1_048_576, 4_194_304, 16_777_216, 67_108_864):
            try:
                tail_data, file_size, tail_offset = _tail_read_from_end(path, max_bytes=max_bytes)
            except OSError:
                return None
            marker_offset = _find_last_marker_offset(tail_data, file_size, tail_offset, b"Direct configuration=")
            if marker_offset is not None:
                return marker_offset
            if tail_offset == 0:
                return None
        return None

    def _find_arc_last_frame_offset(self, path: Path) -> int | None:
        """从文件尾部反向搜索最后一个 '!DATE' 的偏移。"""
        try:
            tail_data, file_size, tail_offset = _tail_read_from_end(path)
            return _find_last_marker_offset(tail_data, file_size, tail_offset, b"!DATE")
        except OSError:
            return None

    def _find_xyz_last_frame_offset(self, path: Path) -> int | None:
        """从文件尾部反向搜索 XYZ 格式最后一帧的原子数行的偏移。"""
        try:
            tail_data, file_size, tail_offset = _tail_read_from_end(path)
            tail_text = tail_data.decode("utf-8", errors="replace")
            lines = tail_text.split("\n")

            # 从后往前搜索格式为 "<digits>" 的行
            for i in range(len(lines) - 1, -1, -1):
                stripped = lines[i].strip()
                try:
                    n = int(stripped)
                except ValueError:
                    continue
                if n <= 0 or n > 100000:
                    continue
                # 验证：下一行非空（comment），再后面有 >= n 行坐标
                if i + 1 >= len(lines):
                    continue
                if not lines[i + 1].strip():
                    continue
                coord_end = min(i + 2 + n, len(lines))
                coord_lines = sum(1 for j in range(i + 2, coord_end) if lines[j].strip())
                if coord_lines < n:
                    continue
                # 用字节级方法定位第 i 行的字节偏移
                byte_cursor = 0
                for line_idx, line_bytes in enumerate(tail_data.split(b"\n")):
                    if line_idx == i:
                        return tail_offset + byte_cursor
                    byte_cursor += len(line_bytes) + 1
                return None
            return None
        except OSError:
            return None

    def _scan_xdatcar(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
        force_full_scan: bool = False,
    ) -> StructureSummary:
        """扫描 XDATCAR。预览大文件时只定位最后一帧，完整读取时建立随机访问索引。"""
        file_size = path.stat().st_size

        if not force_full_scan and file_size >= self._xdatcar_preview_fast_threshold_bytes():
            try:
                return self._scan_xdatcar_preview_fast(path, cancellation)
            except FastParserUnsupported:
                pass

        return self._scan_xdatcar_forward(path, cancellation)

    def _xdatcar_preview_fast_threshold_bytes(self) -> int:
        return 0

    def _scan_xdatcar_preview_fast(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()

        header = self._read_xdatcar_standard_header(path)
        last_marker_offset = self._find_xdatcar_last_frame_offset(path)
        if last_marker_offset is None:
            raise FastParserUnsupported("Could not find last XDATCAR frame marker")
        marker_line = self._xdatcar_marker_line_at_offset(path, last_marker_offset)
        n_frames = _xdatcar_frame_number_from_marker(marker_line)
        if n_frames is None:
            n_frames, _first_marker_offset = self._count_binary_marker_occurrences(
                path,
                b"Direct configuration=",
                cancellation,
            )
        if n_frames <= 0:
            raise FastParserUnsupported("XDATCAR has no Direct configuration frames")
        if n_frames > self.settings.viewer.ase.max_frames:
            raise AppError(
                "STRUCTURE_TOO_MANY_FRAMES",
                f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                413,
            )

        last_header_offset: int | None = None
        try:
            with path.open("rb") as handle:
                last_header_offset = self._xdatcar_header_offset_before_marker(handle, last_marker_offset)
        except OSError:
            last_header_offset = None

        if n_frames >= 2 and last_header_offset is not None and last_header_offset > 0:
            last_index = FastXdatcarVariableIndex(header_offsets=(last_header_offset,))
            last_frame_data = self._read_xdatcar_variable_frame(path, last_index, 0)
            self._validate_frame_data(last_frame_data, path)
            return StructureSummary(
                n_frames=n_frames,
                n_atoms=_frame_atom_count(last_frame_data),
                last_atoms=None,
                last_frame_data=last_frame_data,
                topology_stable=False,
                detected_format="vasp-xdatcar",
                scan_completed=False,
            )

        fixed_indices, constraint_warning = _read_outcar_fixed_indices_from_neighbor(path, header["n_atoms"])
        index = FastXdatcarIndex(
            frame_offsets=(self._xdatcar_coordinate_offset_after_marker(path, last_marker_offset),),
            n_atoms=header["n_atoms"],
            symbols=header["symbols"],
            numbers=header["numbers"],
            cell=header["cell"],
            fixed_indices=fixed_indices,
        )
        last_frame_data = self._read_xdatcar_frame(path, index, 0)
        self._validate_frame_data(last_frame_data, path)
        return StructureSummary(
            n_frames=n_frames,
            n_atoms=header["n_atoms"],
            last_atoms=None,
            last_frame_data=last_frame_data,
            topology_stable=True,
            detected_format=index.detected_format,
            scan_completed=False,
            warnings=(constraint_warning,) if constraint_warning else (),
        )

    def _read_xdatcar_standard_header(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.readline()
                scale_values = _parse_vasp_scaling_factor(handle.readline())
                raw_cell = np.array([handle.readline().split()[:3] for _ in range(3)], dtype=np.float64)
                if scale_values[0] < 0.0:
                    scale_values = np.cbrt(-1.0 * scale_values / np.linalg.det(raw_cell))
                cell = raw_cell * scale_values

                symbol_tokens = handle.readline().split()
                count_tokens = handle.readline().split()
                if not symbol_tokens or not count_tokens:
                    raise FastParserUnsupported("XDATCAR header is incomplete")
                try:
                    counts = [int(value) for value in count_tokens]
                except ValueError as exc:
                    raise FastParserUnsupported("XDATCAR atom counts are not standard VASP 5 format") from exc
                if len(symbol_tokens) != len(counts):
                    raise FastParserUnsupported("XDATCAR symbols and counts do not match")

                symbols: list[str] = []
                numbers: list[int] = []
                for symbol_token, count in zip(symbol_tokens, counts):
                    symbol = _normalize_symbol(symbol_token)
                    try:
                        number = _atomic_number_from_normalized_symbol(symbol)
                    except ValueError as exc:
                        raise FastParserUnsupported("XDATCAR contains unsupported atom symbols") from exc
                    symbols.extend([symbol] * count)
                    numbers.extend([number] * count)
                n_atoms = sum(counts)
                if n_atoms <= 0:
                    raise FastParserUnsupported("XDATCAR has no atoms")
                if n_atoms > self.settings.viewer.ase.max_atoms:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_ATOMS",
                        f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                        413,
                    )
                return {
                    "n_atoms": n_atoms,
                    "symbols": tuple(symbols),
                    "numbers": tuple(numbers),
                    "cell": tuple(tuple(float(value) for value in row) for row in cell),
                }
        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _require_xdatcar_header_offset(self, path: Path, marker_offset: int) -> int:
        try:
            with path.open("rb") as handle:
                header_offset = self._xdatcar_header_offset_before_marker(handle, marker_offset)
        except OSError as exc:
            raise FastParserUnsupported("Could not read XDATCAR frame header") from exc
        if header_offset is None:
            raise FastParserUnsupported("Could not locate XDATCAR frame header")
        return header_offset

    def _xdatcar_marker_line_at_offset(self, path: Path, marker_offset: int) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(marker_offset)
                marker_line = handle.readline()
                if not marker_line.startswith(b"Direct configuration="):
                    raise FastParserUnsupported("XDATCAR frame marker is invalid")
                return marker_line.decode("utf-8", errors="replace")
        except FastParserUnsupported:
            raise
        except OSError as exc:
            raise FastParserUnsupported("Could not read XDATCAR frame marker") from exc

    def _xdatcar_coordinate_offset_after_marker(self, path: Path, marker_offset: int) -> int:
        try:
            with path.open("rb") as handle:
                handle.seek(marker_offset)
                marker_line = handle.readline()
                if not marker_line.startswith(b"Direct configuration="):
                    raise FastParserUnsupported("XDATCAR frame marker is invalid")
                return handle.tell()
        except FastParserUnsupported:
            raise
        except OSError as exc:
            raise FastParserUnsupported("Could not read XDATCAR frame marker") from exc

    def _scan_xdatcar_forward(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        frame_offsets: list[int] = []

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.readline()
                scale_values = _parse_vasp_scaling_factor(handle.readline())
                raw_cell = np.array([handle.readline().split()[:3] for _ in range(3)], dtype=np.float64)
                if scale_values[0] < 0.0:
                    scale_values = np.cbrt(-1.0 * scale_values / np.linalg.det(raw_cell))
                cell = raw_cell * scale_values

                symbol_tokens = handle.readline().split()
                count_tokens = handle.readline().split()
                if not symbol_tokens or not count_tokens:
                    raise FastParserUnsupported("XDATCAR header is incomplete")
                try:
                    counts = [int(value) for value in count_tokens]
                except ValueError as exc:
                    raise FastParserUnsupported("XDATCAR atom counts are not standard VASP 5 format") from exc
                if len(symbol_tokens) != len(counts):
                    raise FastParserUnsupported("XDATCAR symbols and counts do not match")

                symbols: list[str] = []
                numbers: list[int] = []
                for symbol_token, count in zip(symbol_tokens, counts):
                    symbol = _normalize_symbol(symbol_token)
                    try:
                        number = _atomic_number_from_normalized_symbol(symbol)
                    except ValueError as exc:
                        raise FastParserUnsupported("XDATCAR contains unsupported atom symbols") from exc
                    symbols.extend([symbol] * count)
                    numbers.extend([number] * count)
                n_atoms = sum(counts)
                if n_atoms <= 0:
                    raise FastParserUnsupported("XDATCAR has no atoms")
                if n_atoms > self.settings.viewer.ase.max_atoms:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_ATOMS",
                        f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                        413,
                    )

                marker_lines = self._find_binary_marker_line_offsets(
                    path,
                    b"Direct configuration=",
                    cancellation,
                )
                frame_offsets = [next_line_start for _line_start, next_line_start in marker_lines]
                marker_offsets = [line_start for line_start, _next_line_start in marker_lines]
                if len(frame_offsets) > self.settings.viewer.ase.max_frames:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_FRAMES",
                        f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                        413,
                    )
        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        if not frame_offsets:
            raise FastParserUnsupported("XDATCAR has no Direct configuration frames")

        variable_header_offsets = self._xdatcar_variable_header_offsets(path, marker_offsets)
        if variable_header_offsets is not None:
            variable_index = FastXdatcarVariableIndex(header_offsets=variable_header_offsets)
            last_frame_data = self._read_xdatcar_variable_frame(path, variable_index, len(variable_header_offsets) - 1)
            self._validate_frame_data(last_frame_data, path)
            first_frame_data = self._read_xdatcar_variable_frame(path, variable_index, 0)
            self._validate_frame_data(first_frame_data, path)
            return StructureSummary(
                n_frames=len(variable_header_offsets),
                n_atoms=_frame_atom_count(first_frame_data),
                last_atoms=None,
                last_frame_data=last_frame_data,
                topology_stable=False,
                detected_format=variable_index.detected_format,
                fast_xdatcar_variable_index=variable_index,
            )

        fixed_indices, constraint_warning = _read_outcar_fixed_indices_from_neighbor(path, n_atoms)

        index = FastXdatcarIndex(
            frame_offsets=tuple(frame_offsets),
            n_atoms=n_atoms,
            symbols=tuple(symbols),
            numbers=tuple(numbers),
            cell=tuple(tuple(float(value) for value in row) for row in cell),
            fixed_indices=fixed_indices,
        )
        last_frame_data = self._read_xdatcar_frame(path, index, len(frame_offsets) - 1)
        self._validate_frame_data(last_frame_data, path)

        return StructureSummary(
            n_frames=len(frame_offsets),
            n_atoms=n_atoms,
            last_atoms=None,
            last_frame_data=last_frame_data,
            topology_stable=True,
            detected_format=index.detected_format,
            fast_xdatcar_index=index,
            warnings=(constraint_warning,) if constraint_warning else (),
        )

    def _xdatcar_variable_header_offsets(
        self,
        path: Path,
        marker_offsets: list[int] | tuple[int, ...] | None = None,
    ) -> tuple[int, ...] | None:
        if marker_offsets is not None:
            return self._xdatcar_variable_header_offsets_from_marker_offsets(path, marker_offsets)
        return self._xdatcar_variable_header_offsets_by_text_scan(path)

    def _xdatcar_variable_header_offsets_from_marker_offsets(
        self,
        path: Path,
        marker_offsets: list[int] | tuple[int, ...],
    ) -> tuple[int, ...] | None:
        header_offsets: list[int] = []
        try:
            with path.open("rb") as handle:
                for marker_offset in marker_offsets:
                    header_offset = self._xdatcar_header_offset_before_marker(handle, marker_offset)
                    if header_offset is None:
                        return None
                    header_offsets.append(header_offset)
        except OSError:
            return None
        return tuple(header_offsets) if len(header_offsets) >= 2 else None

    def _xdatcar_header_offset_before_marker(self, handle: Any, marker_offset: int) -> int | None:
        max_window = 1_048_576
        window = 4096

        while True:
            start = max(0, marker_offset - window)
            handle.seek(start)
            data = handle.read(marker_offset - start)
            line_starts = [0]
            line_starts.extend(index + 1 for index, byte in enumerate(data) if byte == 10 and index + 1 < len(data))

            candidate_index = len(line_starts) - 7
            if candidate_index >= 0 and (start == 0 or line_starts[candidate_index] != 0):
                previous: list[tuple[int, str]] = []
                candidate_starts = line_starts[candidate_index:]
                for offset_index, line_start in enumerate(candidate_starts):
                    line_end = candidate_starts[offset_index + 1] if offset_index + 1 < len(candidate_starts) else len(data)
                    line = data[line_start:line_end].decode("utf-8")
                    previous.append((start + line_start, line))
                return self._xdatcar_header_offset_from_previous(previous)

            if start == 0 or window >= max_window:
                return None
            window = min(max_window, window * 4)

    def _xdatcar_variable_header_offsets_by_text_scan(self, path: Path) -> tuple[int, ...] | None:
        header_offsets: list[int] = []
        previous: list[tuple[int, str]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                while True:
                    offset = handle.tell()
                    line = handle.readline()
                    if line == "":
                        break
                    if line.startswith("Direct configuration="):
                        header_offset = self._xdatcar_header_offset_from_previous(previous)
                        if header_offset is None:
                            return None
                        header_offsets.append(header_offset)
                    previous.append((offset, line))
                    if len(previous) > 7:
                        previous.pop(0)
        except OSError:
            return None
        return tuple(header_offsets) if len(header_offsets) >= 2 else None

    def _xdatcar_header_offset_from_previous(self, previous: list[tuple[int, str]]) -> int | None:
        if len(previous) < 7:
            return None
        try:
            scale_values = _parse_vasp_scaling_factor(previous[-6][1])
            raw_cell = np.array([previous[-5][1].split()[:3], previous[-4][1].split()[:3], previous[-3][1].split()[:3]], dtype=np.float64)
            if raw_cell.shape != (3, 3):
                return None
            _ = raw_cell * scale_values
            symbol_tokens = previous[-2][1].split()
            count_tokens = previous[-1][1].split()
            counts = [int(value) for value in count_tokens]
            if not symbol_tokens or len(symbol_tokens) != len(counts) or sum(counts) <= 0:
                return None
        except Exception:
            return None
        return previous[-7][0]

    def _scan_vasp_poscar(self, path: Path) -> StructureSummary:
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.readline()
                scale_values = _parse_vasp_scaling_factor(handle.readline())
                raw_cell = np.array([handle.readline().split()[:3] for _ in range(3)], dtype=np.float64)
                if scale_values[0] < 0.0:
                    scale_values = np.cbrt(-1.0 * scale_values / np.linalg.det(raw_cell))
                cell = raw_cell * scale_values

                symbol_tokens = handle.readline().split()
                try:
                    int(symbol_tokens[0])
                except (IndexError, ValueError):
                    pass
                else:
                    raise FastParserUnsupported("VASP 4 POSCAR symbol inference is delegated to ASE")

                count_tokens = handle.readline().split()
                if not symbol_tokens or not count_tokens:
                    raise FastParserUnsupported("POSCAR header is incomplete")
                try:
                    counts = [int(value) for value in count_tokens]
                except ValueError as exc:
                    raise FastParserUnsupported("POSCAR atom counts are not standard VASP 5 format") from exc
                if len(symbol_tokens) != len(counts):
                    raise FastParserUnsupported("POSCAR symbols and counts do not match")

                symbols: list[str] = []
                numbers: list[int] = []
                for symbol_token, count in zip(symbol_tokens, counts):
                    symbol = _normalize_symbol(symbol_token)
                    try:
                        number = _atomic_number_from_normalized_symbol(symbol)
                    except ValueError as exc:
                        raise FastParserUnsupported("POSCAR contains unsupported atom symbols") from exc
                    symbols.extend([symbol] * count)
                    numbers.extend([number] * count)
                n_atoms = sum(counts)
                if n_atoms <= 0:
                    raise FastParserUnsupported("POSCAR has no atoms")
                if n_atoms > self.settings.viewer.ase.max_atoms:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_ATOMS",
                        f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                        413,
                    )

                coordinate_type = handle.readline()
                selective_dynamics = coordinate_type[:1].lower() == "s"
                if selective_dynamics:
                    coordinate_type = handle.readline()
                cartesian = coordinate_type[:1].lower() in {"c", "k"}
                if not coordinate_type:
                    raise FastParserUnsupported("POSCAR coordinate type is missing")

                positions = np.empty((n_atoms, 3), dtype="<f4")
                fixed_indices: list[int] = []
                for atom_index in range(n_atoms):
                    fields = handle.readline().split()
                    if len(fields) < 3:
                        raise ValueError("Unexpected end of POSCAR coordinates")
                    coords = np.array([float(value) for value in fields[:3]], dtype=np.float64)
                    if cartesian:
                        position = coords * scale_values
                    else:
                        position = coords @ cell
                    positions[atom_index] = position
                    if selective_dynamics and len(fields) >= 6:
                        flags = [value.upper().startswith("F") for value in fields[3:6]]
                        if all(flags):
                            fixed_indices.append(atom_index)
        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        frame_data = FrameData(
            positions=positions,
            numbers=np.asarray(numbers, dtype=np.int32),
            symbols=tuple(symbols),
            cell=np.asarray(cell, dtype="<f4"),
            pbc=(True, True, True),
            fixed_indices=tuple(fixed_indices),
        )
        self._validate_frame_data(frame_data, path)
        return StructureSummary(
            n_frames=1,
            n_atoms=n_atoms,
            last_atoms=None,
            last_frame_data=frame_data,
            topology_stable=True,
            detected_format="vasp",
        )

    def _scan_xsd(self, path: Path) -> StructureSummary:
        frame_data = self._read_xsd_frame(path)
        self._validate_frame_data(frame_data, path)
        return StructureSummary(
            n_frames=1,
            n_atoms=int(frame_data.positions.shape[0]),
            last_atoms=None,
            last_frame_data=frame_data,
            topology_stable=True,
            detected_format="xsd",
        )

    def _scan_dmol_arc(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        return self._scan_dmol_arc_forward(path, cancellation)

    def _scan_dmol_arc_forward(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        frame_offsets: list[int] = []

        try:
            with path.open("r", encoding="utf-8") as handle:
                header = handle.readline()
                if not header.startswith("!BIOSYM archive"):
                    raise FastParserUnsupported("ARC header is not a BIOSYM archive")

                pbc_line = handle.readline().strip()
                if pbc_line == "PBC=ON":
                    pbc = (True, True, True)
                elif pbc_line == "PBC=OFF":
                    pbc = (False, False, False)
                else:
                    raise FastParserUnsupported("ARC PBC header is unsupported")

                first_numbers: tuple[int, ...] | None = None
                first_n_atoms: int | None = None
                topology_stable = True
                last_frame_data: FrameData | None = None

                while True:
                    cancellation.check()
                    offset = handle.tell()
                    line = handle.readline()
                    if line == "":
                        break
                    if not line.startswith("!DATE"):
                        continue

                    frame_offsets.append(offset)
                    frame_data = self._read_dmol_arc_frame_from_handle(handle, pbc)
                    self._validate_frame_data(frame_data, path)
                    numbers = _frame_numbers(frame_data)
                    n_atoms = _frame_atom_count(frame_data)
                    if first_numbers is None:
                        first_numbers = numbers
                        first_n_atoms = n_atoms
                    elif numbers != first_numbers or n_atoms != first_n_atoms:
                        topology_stable = False
                    last_frame_data = frame_data
                    if len(frame_offsets) > self.settings.viewer.ase.max_frames:
                        raise AppError(
                            "STRUCTURE_TOO_MANY_FRAMES",
                            f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                            413,
                        )
        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        if not frame_offsets or first_n_atoms is None or last_frame_data is None:
            raise FastParserUnsupported("ARC has no frames")

        return StructureSummary(
            n_frames=len(frame_offsets),
            n_atoms=first_n_atoms,
            last_atoms=None,
            last_frame_data=last_frame_data,
            topology_stable=topology_stable,
            detected_format="dmol-arc",
            fast_arc_index=FastArcIndex(frame_offsets=tuple(frame_offsets), n_atoms=first_n_atoms, pbc=pbc),
        )

    def _scan_xyz_like(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
        force_full_scan: bool = False,
    ) -> StructureSummary:
        """扫描 XYZ 格式文件。大文件使用快速估算模式，force_full_scan=True 时强制全量扫描。"""
        FAST_SCAN_THRESHOLD = 10 * 1024 * 1024  # 10MB
        file_size = path.stat().st_size

        looks_extxyz = _detected_xyz_like_format(path, fmt) == "extxyz" or _xyz_like_file_has_extxyz_marker(path)
        if looks_extxyz:
            try:
                if not force_full_scan and file_size >= FAST_SCAN_THRESHOLD:
                    return self._scan_extxyz_like_preview_fast(path, fmt, cancellation)
                return self._scan_extxyz_like_fast(path, fmt, cancellation)
            except FastParserUnsupported:
                pass

        if not force_full_scan and file_size >= FAST_SCAN_THRESHOLD:
            try:
                return self._scan_xyz_like_fast(path, fmt, cancellation)
            except FastParserUnsupported:
                pass

        return self._scan_xyz_like_forward(path, fmt, cancellation)

    def _scan_xyz_like_fast(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        """
        快速扫描 XYZ 格式大文件：采样前几帧估算总帧数，反向扫描获取最后一帧。
        适用于 >10MB 的 XYZ 文件，速度 <100ms。
        """
        cancellation = cancellation or StructureCancellationToken()
        detected_format = _detected_xyz_like_format(path, fmt)
        auto_detect_extxyz = _normalized_format(fmt) is None and path.suffix.lower() == ".xyz"
        file_size = path.stat().st_size

        # 阶段1：采样前几帧计算平均帧大小
        SAMPLE_FRAMES = 5
        sample_offsets: list[int] = []
        sample_lengths: list[int] = []
        sample_atom_counts: list[int] = []
        first_numbers: tuple[int, ...] | None = None
        n_atoms = 0

        try:
            with path.open("r", encoding="utf-8") as handle:
                for sample_idx in range(SAMPLE_FRAMES):
                    cancellation.check()
                    frame_offset = handle.tell()
                    line = handle.readline()
                    if line == "":
                        if sample_idx == 0:
                            raise FastParserUnsupported("Empty XYZ file")
                        break
                    if not line.strip():
                        continue

                    try:
                        atom_count = int(line.strip())
                    except ValueError as exc:
                        raise FastParserUnsupported(f"Invalid atom count at sample frame {sample_idx}") from exc

                    comment = handle.readline()
                    if comment == "":
                        raise FastParserUnsupported(f"Missing comment line at sample frame {sample_idx}")
                    if auto_detect_extxyz and sample_idx == 0 and _looks_like_extxyz_comment(comment):
                        return self._scan_extxyz_like_fast(path, fmt, cancellation)

                    numbers: list[int] = []
                    for atom_index in range(atom_count):
                        atom_line = handle.readline()
                        if atom_line == "":
                            raise FastParserUnsupported(f"Unexpected end in sample frame {sample_idx}")
                        fields = atom_line.split()
                        if len(fields) < 4:
                            raise FastParserUnsupported(f"Invalid atom line in sample frame {sample_idx}")
                        if detected_format == "xyz":
                            numbers.append(_atomic_number_from_symbol(fields[0]))

                    # 跳过可选的 VEC 行
                    while True:
                        extra_offset = handle.tell()
                        extra_line = handle.readline()
                        if extra_line.lstrip().startswith("VEC"):
                            continue
                        if extra_line:
                            handle.seek(extra_offset)
                        break

                    frame_length = handle.tell() - frame_offset
                    sample_offsets.append(frame_offset)
                    sample_lengths.append(frame_length)
                    sample_atom_counts.append(atom_count)

                    if sample_idx == 0:
                        if detected_format == "extxyz":
                            current_offset = handle.tell()
                            handle.seek(frame_offset)
                            extxyz_frame = self._read_extxyz_frame_text(path, handle.read(frame_length))
                            handle.seek(current_offset)
                            self._validate_structure_frame(extxyz_frame, path)
                            first_numbers = _frame_numbers(extxyz_frame)
                        else:
                            first_numbers = tuple(numbers)
                        n_atoms = atom_count
                        if atom_count <= 0:
                            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE found an empty frame in {path.name}", 400)
                        if atom_count > self.settings.viewer.ase.max_atoms:
                            raise AppError(
                                "STRUCTURE_TOO_MANY_ATOMS",
                                f"Structure has {atom_count} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                                413,
                            )

        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        if not sample_offsets:
            raise FastParserUnsupported("No sample frames found")

        # 阶段2：估算总帧数
        avg_frame_size = sum(sample_lengths) // len(sample_lengths)
        if avg_frame_size <= 0:
            raise FastParserUnsupported("Invalid average frame size")
        estimated_n_frames = file_size // avg_frame_size
        if estimated_n_frames <= 0:
            estimated_n_frames = 1
        if estimated_n_frames > self.settings.viewer.ase.max_frames:
            # 快速预览不限制帧数，仅估算
            pass

        # 阶段3：反向扫描获取最后一帧
        last_frame_offset = self._find_xyz_last_frame_offset(path)
        if last_frame_offset is None:
            raise FastParserUnsupported("Could not find last frame offset")

        cancellation.check()
        if detected_format == "xyz":
            last_frame_data = self._read_plain_xyz_frame_at(path, last_frame_offset, estimated_n_frames - 1)
        else:
            # ExtXYZ：需要读取完整帧文本
            try:
                with path.open("r", encoding="utf-8") as handle:
                    handle.seek(last_frame_offset)
                    atom_count = int(handle.readline().strip())
                    # 估算帧长度：2行头 + atom_count行坐标 + 可能的VEC行，每行~80字节
                    estimated_frame_length = (2 + atom_count + 3) * 80
                    handle.seek(last_frame_offset)
                    frame_text = handle.read(estimated_frame_length)
                    last_frame_data = self._read_extxyz_frame_text(path, frame_text)
            except Exception as exc:
                raise FastParserUnsupported(f"Could not read last ExtXYZ frame: {exc}") from exc

        self._validate_structure_frame(last_frame_data, path)

        # 构建快速索引（只包含采样的帧）
        fast_index = FastXyzIndex(
            frame_offsets=tuple(sample_offsets),
            frame_lengths=tuple(sample_lengths),
            atom_counts=tuple(sample_atom_counts),
            detected_format=detected_format,
            complete=False,
            n_frames=estimated_n_frames,
            last_frame_offset=last_frame_offset,
        )

        return StructureSummary(
            n_frames=estimated_n_frames,
            n_atoms=n_atoms,
            last_atoms=None,
            last_frame_data=last_frame_data if isinstance(last_frame_data, FrameData) else None,
            topology_stable=True,  # 假设拓扑稳定（常见情况）
            detected_format=detected_format,
            fast_xyz_index=fast_index,
            scan_completed=False,
        )

    def _scan_extxyz_like_preview_fast(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        n_frames = self._count_extxyz_lattice_frames(path, cancellation)
        if n_frames <= 0:
            raise FastParserUnsupported("No ExtXYZ Lattice markers found")

        first_frame_offset = self._find_extxyz_first_frame_offset(path)
        last_frame_offset = self._find_extxyz_last_frame_offset(path)
        if first_frame_offset is None or last_frame_offset is None:
            raise FastParserUnsupported("Could not locate ExtXYZ frame offsets")

        first_frame = self._read_extxyz_frame_text(path, self._read_xyz_frame_text_at_offset(path, first_frame_offset))
        last_frame = self._read_extxyz_frame_text(path, self._read_xyz_frame_text_at_offset(path, last_frame_offset))
        self._validate_structure_frame(first_frame, path)
        self._validate_structure_frame(last_frame, path)

        first_numbers = _frame_numbers(first_frame)
        last_numbers = _frame_numbers(last_frame)
        n_atoms = _frame_atom_count(first_frame)
        if n_atoms > self.settings.viewer.ase.max_atoms:
            raise AppError(
                "STRUCTURE_TOO_MANY_ATOMS",
                f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                413,
            )

        return StructureSummary(
            n_frames=n_frames,
            n_atoms=n_atoms,
            last_atoms=last_frame if isinstance(last_frame, Atoms) else None,
            last_frame_data=last_frame if isinstance(last_frame, FrameData) else None,
            topology_stable=n_atoms == _frame_atom_count(last_frame) and first_numbers == last_numbers,
            detected_format="extxyz",
            scan_completed=False,
        )

    def _count_extxyz_lattice_frames(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> int:
        cancellation = cancellation or StructureCancellationToken()
        marker = b"Lattice="
        overlap = b""
        count = 0
        with path.open("rb") as handle:
            while True:
                cancellation.check()
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break
                data = overlap + chunk
                count += data.count(marker)
                overlap = data[-(len(marker) - 1):]
        return count

    def _find_binary_marker_line_offsets(
        self,
        path: Path,
        marker: bytes,
        cancellation: StructureCancellationToken | None = None,
    ) -> list[tuple[int, int]]:
        cancellation = cancellation or StructureCancellationToken()
        overlap_size = max(4096, len(marker) - 1)
        overlap = b""
        marker_lines: list[tuple[int, int]] = []
        last_marker_offset = -1

        with path.open("rb") as handle:
            while True:
                cancellation.check()
                chunk_start = handle.tell()
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break

                data = overlap + chunk
                data_offset = chunk_start - len(overlap)
                search_start = 0
                while True:
                    marker_pos = data.find(marker, search_start)
                    if marker_pos == -1:
                        break
                    marker_offset = data_offset + marker_pos
                    search_start = marker_pos + 1
                    if marker_offset <= last_marker_offset:
                        continue

                    line_end = data.find(b"\n", marker_pos)
                    if line_end == -1:
                        continue
                    line_start = data.rfind(b"\n", 0, marker_pos) + 1
                    marker_lines.append((data_offset + line_start, data_offset + line_end + 1))
                    last_marker_offset = marker_offset

                overlap = data[-overlap_size:]

        return marker_lines

    def _count_binary_marker_occurrences(
        self,
        path: Path,
        marker: bytes,
        cancellation: StructureCancellationToken | None = None,
    ) -> tuple[int, int | None]:
        cancellation = cancellation or StructureCancellationToken()
        overlap_size = max(0, len(marker) - 1)
        overlap = b""
        count = 0
        first_offset: int | None = None

        with path.open("rb") as handle:
            while True:
                cancellation.check()
                chunk_start = handle.tell()
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break

                data = overlap + chunk
                data_offset = chunk_start - len(overlap)
                search_start = 0
                while True:
                    marker_pos = data.find(marker, search_start)
                    if marker_pos == -1:
                        break
                    marker_offset = data_offset + marker_pos
                    search_start = marker_pos + len(marker)
                    if marker_offset < chunk_start and chunk_start > 0:
                        continue
                    if first_offset is None:
                        first_offset = marker_offset
                    count += 1

                overlap = data[-overlap_size:] if overlap_size else b""

        return count, first_offset

    def _find_extxyz_frame_offsets_binary(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> tuple[list[int], list[int]]:
        frame_offsets, atom_counts = self._find_extxyz_frame_offsets_binary_for_marker(
            path,
            b"Lattice=",
            cancellation,
        )
        if frame_offsets:
            return frame_offsets, atom_counts
        return self._find_extxyz_frame_offsets_binary_for_marker(path, b"Properties=", cancellation)

    def _find_extxyz_frame_offsets_binary_for_marker(
        self,
        path: Path,
        marker: bytes,
        cancellation: StructureCancellationToken | None = None,
    ) -> tuple[list[int], list[int]]:
        cancellation = cancellation or StructureCancellationToken()
        overlap_size = max(4096, len(marker) - 1)
        overlap = b""
        frame_offsets: list[int] = []
        atom_counts: list[int] = []
        last_marker_offset = -1

        with path.open("rb") as handle:
            while True:
                cancellation.check()
                chunk_start = handle.tell()
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break

                data = overlap + chunk
                data_offset = chunk_start - len(overlap)
                search_start = 0
                while True:
                    marker_pos = data.find(marker, search_start)
                    if marker_pos == -1:
                        break
                    marker_offset = data_offset + marker_pos
                    search_start = marker_pos + 1
                    if marker_offset <= last_marker_offset:
                        continue

                    comment_start = data.rfind(b"\n", 0, marker_pos) + 1
                    if comment_start <= 0 and data_offset > 0:
                        continue
                    atom_line_end = comment_start - 1
                    atom_line_start = data.rfind(b"\n", 0, atom_line_end) + 1
                    if atom_line_start < 0:
                        continue
                    try:
                        atom_count = int(data[atom_line_start:atom_line_end].strip())
                    except ValueError:
                        continue
                    if atom_count <= 0:
                        continue

                    frame_offsets.append(data_offset + atom_line_start)
                    atom_counts.append(atom_count)
                    last_marker_offset = marker_offset

                overlap = data[-overlap_size:]

        return frame_offsets, atom_counts

    def _find_extxyz_first_frame_offset(self, path: Path) -> int | None:
        try:
            with path.open("rb") as handle:
                previous_offset: int | None = None
                previous_line: bytes | None = None
                while True:
                    offset = handle.tell()
                    line = handle.readline()
                    if line == b"":
                        return None
                    if b"Lattice=" in line and previous_line is not None and previous_offset is not None:
                        try:
                            atom_count = int(previous_line.strip())
                        except ValueError:
                            atom_count = -1
                        if atom_count > 0:
                            return previous_offset
                    previous_offset = offset
                    previous_line = line
        except OSError:
            return None

    def _find_extxyz_last_frame_offset(self, path: Path) -> int | None:
        for max_bytes in (1_048_576, 4_194_304, 16_777_216, 67_108_864):
            try:
                tail_data, _file_size, tail_offset = _tail_read_from_end(path, max_bytes=max_bytes)
            except OSError:
                return None

            lattice_pos = tail_data.rfind(b"Lattice=")
            if lattice_pos == -1:
                if tail_offset == 0:
                    return None
                continue

            comment_start = tail_data.rfind(b"\n", 0, lattice_pos) + 1
            if comment_start <= 0 and tail_offset > 0:
                continue

            atom_line_end = comment_start - 1
            atom_line_start = tail_data.rfind(b"\n", 0, atom_line_end) + 1
            if atom_line_start < 0:
                continue
            try:
                atom_count = int(tail_data[atom_line_start:atom_line_end].strip())
            except ValueError:
                continue
            if atom_count <= 0:
                continue
            return tail_offset + atom_line_start
        return None

    def _scan_extxyz_like_fast(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        frame_offsets: list[int] = []
        frame_lengths: list[int] = []
        atom_counts: list[int] = []
        file_size = path.stat().st_size

        try:
            frame_offsets, atom_counts = self._find_extxyz_frame_offsets_binary(path, cancellation)
        except AppError:
            raise
        except OSError as exc:
            raise FastParserUnsupported(f"Could not scan ExtXYZ frame headers: {exc}") from exc

        if not frame_offsets:
            raise FastParserUnsupported("No ExtXYZ frame headers found")

        for index, offset in enumerate(frame_offsets):
            next_offset = frame_offsets[index + 1] if index + 1 < len(frame_offsets) else file_size
            frame_lengths.append(max(0, next_offset - offset))

        first_frame = self._read_extxyz_frame_slice(path, frame_offsets[0], frame_lengths[0])
        last_frame = self._read_extxyz_frame_slice(path, frame_offsets[-1], frame_lengths[-1])
        self._validate_structure_frame(first_frame, path)
        self._validate_structure_frame(last_frame, path)

        first_numbers = _frame_numbers(first_frame)
        last_numbers = _frame_numbers(last_frame)
        topology_stable = atom_counts[0] == atom_counts[-1] and first_numbers == last_numbers
        n_atoms = atom_counts[0]
        if n_atoms > self.settings.viewer.ase.max_atoms:
            raise AppError(
                "STRUCTURE_TOO_MANY_ATOMS",
                f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                413,
            )

        fast_index = FastXyzIndex(
            frame_offsets=tuple(frame_offsets),
            frame_lengths=tuple(frame_lengths),
            atom_counts=tuple(atom_counts),
            detected_format="extxyz",
        )

        return StructureSummary(
            n_frames=len(frame_offsets),
            n_atoms=n_atoms,
            last_atoms=last_frame if isinstance(last_frame, Atoms) else None,
            last_frame_data=last_frame if isinstance(last_frame, FrameData) else None,
            topology_stable=topology_stable,
            detected_format="extxyz",
            fast_xyz_index=fast_index,
        )

    def _scan_xyz_like_forward(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        frame_offsets: list[int] = []
        frame_lengths: list[int] = []
        atom_counts: list[int] = []
        frames_seen = 0
        first_numbers: tuple[int, ...] | None = None
        n_atoms = 0
        topology_stable = True
        last_atoms: Atoms | None = None
        last_frame_data: FrameData | None = None
        detected_format = _detected_xyz_like_format(path, fmt)
        auto_detect_extxyz = _normalized_format(fmt) is None and path.suffix.lower() == ".xyz"

        try:
            with path.open("r", encoding="utf-8") as handle:
                while True:
                    cancellation.check()
                    frame_offset = handle.tell()
                    line = handle.readline()
                    if line == "":
                        break
                    if not line.strip():
                        continue
                    try:
                        atom_count = int(line.strip())
                    except ValueError as exc:
                        raise ValueError(f"Expected atom count at frame {frames_seen + 1}") from exc

                    comment = handle.readline()
                    if comment == "":
                        raise ValueError(f"Missing comment line at frame {frames_seen + 1}")
                    if auto_detect_extxyz and frames_seen == 0 and _looks_like_extxyz_comment(comment):
                        detected_format = "extxyz"

                    numbers: list[int] = []
                    for atom_index in range(atom_count):
                        atom_line = handle.readline()
                        if atom_line == "":
                            raise ValueError(f"Unexpected end of file in frame {frames_seen + 1}")
                        fields = atom_line.split()
                        if len(fields) < 4:
                            raise ValueError(f"Invalid atom line in frame {frames_seen + 1}")
                        if detected_format == "xyz":
                            numbers.append(_atomic_number_from_symbol(fields[0]))

                    while True:
                        extra_offset = handle.tell()
                        extra_line = handle.readline()
                        if extra_line.lstrip().startswith("VEC"):
                            continue
                        if extra_line:
                            handle.seek(extra_offset)
                        break

                    frame_length = handle.tell() - frame_offset
                    if detected_format == "extxyz":
                        current_offset = handle.tell()
                        handle.seek(frame_offset)
                        extxyz_frame = self._read_extxyz_frame_text(path, handle.read(frame_length))
                        handle.seek(current_offset)
                        self._validate_structure_frame(extxyz_frame, path)
                        frame_numbers = _frame_numbers(extxyz_frame)
                    else:
                        if atom_count <= 0:
                            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE found an empty frame in {path.name}", 400)
                        if atom_count > self.settings.viewer.ase.max_atoms:
                            raise AppError(
                                "STRUCTURE_TOO_MANY_ATOMS",
                                f"Structure has {atom_count} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                                413,
                            )
                        atoms = None
                        frame_numbers = tuple(numbers)

                    if first_numbers is None:
                        first_numbers = frame_numbers
                        n_atoms = atom_count
                    elif frame_numbers != first_numbers:
                        topology_stable = False

                    frame_offsets.append(frame_offset)
                    frame_lengths.append(frame_length)
                    atom_counts.append(atom_count)
                    frames_seen += 1
                    if frames_seen > self.settings.viewer.ase.max_frames:
                        raise AppError(
                            "STRUCTURE_TOO_MANY_FRAMES",
                            f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                            413,
                        )

                    if detected_format == "extxyz":
                        if isinstance(extxyz_frame, FrameData):
                            last_frame_data = extxyz_frame
                            last_atoms = None
                        else:
                            last_atoms = extxyz_frame
                            last_frame_data = None
                    elif atoms is not None:
                        last_atoms = atoms
                    cancellation.check()
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        if frames_seen == 0:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE did not find any frames in {path.name}", 400)

        fast_index = FastXyzIndex(
            frame_offsets=tuple(frame_offsets),
            frame_lengths=tuple(frame_lengths),
            atom_counts=tuple(atom_counts),
            detected_format=detected_format,
        )
        if detected_format == "xyz":
            last_frame_data = self._read_plain_xyz_frame_at(path, frame_offsets[-1], frames_seen - 1)
            self._validate_frame_data(last_frame_data, path)
        elif last_atoms is None and last_frame_data is None:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE did not find any frames in {path.name}", 400)

        return StructureSummary(
            n_frames=frames_seen,
            n_atoms=n_atoms,
            last_atoms=last_atoms,
            last_frame_data=last_frame_data,
            topology_stable=topology_stable,
            detected_format=detected_format,
            fast_xyz_index=fast_index,
        )

    def _scan_structure_with_ase(
        self,
        path: Path,
        fmt: str | None,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        cancellation = cancellation or StructureCancellationToken()
        frames_seen = 0
        first_numbers: tuple[int, ...] | None = None
        n_atoms = 0
        last_atoms: Atoms | None = None
        loaded_frames: list[Atoms] = []
        retain_loaded_frames = True
        max_loaded_frames = max(1, self.settings.viewer.ase.binary_chunk_frames * 2)
        topology_stable = True
        file_incomplete = False

        try:
            iterator = iread(str(path), index=":", format=fmt, do_not_split_by_at_sign=True)
            for atoms in iterator:
                cancellation.check()
                self._validate_atoms(atoms, path)
                numbers = tuple(int(value) for value in atoms.get_atomic_numbers())
                if first_numbers is None:
                    first_numbers = numbers
                    n_atoms = len(atoms)
                elif numbers != first_numbers:
                    topology_stable = False
                frames_seen += 1
                if frames_seen > self.settings.viewer.ase.max_frames:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_FRAMES",
                        f"Structure has more than {self.settings.viewer.ase.max_frames} frames",
                        413,
                    )
                if retain_loaded_frames:
                    if frames_seen <= max_loaded_frames:
                        loaded_frames.append(atoms)
                    else:
                        loaded_frames.clear()
                        retain_loaded_frames = False
                last_atoms = atoms
                cancellation.check()
        except AppError:
            raise
        except Exception as exc:
            if frames_seen > 0 and last_atoms is not None:
                logger.warning(
                    "Incomplete structure file %s: parsed %d frames before error (will retry with partial data): %s",
                    path.name, frames_seen, exc,
                )
                file_incomplete = True
            else:
                logger.error("Failed to parse structure file %s (no frames parsed): %s", path.name, exc)
                raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        if last_atoms is None or frames_seen == 0:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE did not find any frames in {path.name}", 400)

        if file_incomplete:
            logger.info("Returning partial structure data for %s: %d frames, file_incomplete=True", path.name, frames_seen)

        return StructureSummary(
            n_frames=frames_seen,
            n_atoms=n_atoms,
            last_atoms=last_atoms,
            topology_stable=topology_stable,
            detected_format=fmt,
            loaded_frames=tuple(loaded_frames) if retain_loaded_frames else None,
            file_incomplete=file_incomplete,
        )

    def _read_frame(self, path: Path, index: int, fmt: str | None, summary: StructureSummary | None = None) -> Atoms | FrameData:
        if summary and summary.loaded_frames is not None:
            try:
                atoms = summary.loaded_frames[index]
            except IndexError:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404) from None
            self._validate_atoms(atoms, path)
            return atoms

        if summary and summary.last_frame_data is not None and summary.n_frames == 1:
            if index != 0:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            self._validate_frame_data(summary.last_frame_data, path)
            return summary.last_frame_data

        if summary and summary.fast_xdatcar_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_xdatcar_frame(path, summary.fast_xdatcar_index, index)
            self._validate_frame_data(frame, path)
            return frame

        if summary and summary.fast_xdatcar_variable_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_xdatcar_variable_frame(path, summary.fast_xdatcar_variable_index, index)
            self._validate_frame_data(frame, path)
            return frame

        if summary and summary.fast_arc_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_dmol_arc_frame(path, summary.fast_arc_index, index)
            self._validate_frame_data(frame, path)
            return frame

        if summary and summary.fast_outcar_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_outcar_frame(path, summary.fast_outcar_index, index)
            self._validate_frame_data(frame, path)
            return frame

        if summary and summary.fast_native_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_native_frame(path, summary.fast_native_index.detected_format, index)
            self._validate_structure_frame(frame, path)
            return frame

        if summary and summary.fast_xyz_index:
            if index >= summary.n_frames:
                raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
            frame = self._read_xyz_like_frame(path, summary.fast_xyz_index, index)
            self._validate_structure_frame(frame, path)
            return frame

        try:
            atoms = read(str(path), index=index, format=fmt, do_not_split_by_at_sign=True)
            if isinstance(atoms, list):
                if not atoms:
                    raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)
                atoms = atoms[0]
            self._validate_atoms(atoms, path)
            return atoms
        except AppError:
            raise
        except Exception:
            pass

        try:
            for frame_index, atoms in enumerate(iread(str(path), index=":", format=fmt, do_not_split_by_at_sign=True)):
                if frame_index == index:
                    self._validate_atoms(atoms, path)
                    return atoms
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {index}", 404)

    def _read_frame_range(self, path: Path, start: int, count: int, fmt: str | None, summary: StructureSummary | None = None, profile: dict[str, Any] | None = None) -> list[Atoms | FrameData]:
        if summary and summary.loaded_frames is not None:
            _profile_set(profile, "read_path", "loaded_frames")
            end = min(start + count, summary.n_frames)
            frames = list(summary.loaded_frames[start:end])
            for atoms in frames:
                self._validate_atoms(atoms, path)
            return frames

        if summary and summary.fast_xdatcar_index:
            _profile_set(profile, "read_path", "fast_xdatcar")
            end = min(start + count, summary.n_frames)
            frames = self._read_xdatcar_frame_range(path, summary.fast_xdatcar_index, start, end)
            for frame in frames:
                self._validate_frame_data(frame, path)
            return frames

        if summary and summary.fast_xdatcar_variable_index:
            _profile_set(profile, "read_path", "fast_xdatcar_variable")
            end = min(start + count, summary.n_frames)
            frames = self._read_xdatcar_variable_frame_range(path, summary.fast_xdatcar_variable_index, start, end)
            for frame in frames:
                self._validate_frame_data(frame, path)
            return frames

        if summary and summary.fast_arc_index:
            _profile_set(profile, "read_path", "fast_arc")
            end = min(start + count, summary.n_frames)
            frames = self._read_dmol_arc_frame_range(path, summary.fast_arc_index, start, end)
            for frame in frames:
                self._validate_frame_data(frame, path)
            return frames

        if summary and summary.fast_outcar_index:
            _profile_set(profile, "read_path", "fast_outcar")
            end = min(start + count, summary.n_frames)
            frames = self._read_outcar_frame_range(path, summary.fast_outcar_index, start, end)
            for frame in frames:
                self._validate_frame_data(frame, path)
            return frames

        if summary and summary.fast_native_index:
            _profile_set(profile, "read_path", f"fast_native:{summary.fast_native_index.detected_format}")
            end = min(start + count, summary.n_frames)
            frames = self._read_native_frame_range(path, summary.fast_native_index.detected_format, start, end)
            for frame in frames:
                self._validate_structure_frame(frame, path)
            return frames

        if summary and summary.fast_xyz_index:
            _profile_set(profile, "read_path", f"fast_xyz:{summary.fast_xyz_index.detected_format}")
            end = min(start + count, summary.n_frames)
            if summary.fast_xyz_index.detected_format == "xyz":
                frames = self._read_plain_xyz_frame_range(
                    path,
                    summary.fast_xyz_index,
                    start,
                    end,
                    summary=summary,
                    profile=profile,
                )
            else:
                frames = [
                    self._read_xyz_like_frame(path, summary.fast_xyz_index, frame_index)
                    for frame_index in range(start, end)
                ]
            for frame in frames:
                self._validate_structure_frame(frame, path)
            return frames

        _profile_set(profile, "read_path", "ase_iread")
        end = start + count
        frames: list[Atoms] = []
        try:
            iterator = iread(str(path), index=slice(start, end), format=fmt, do_not_split_by_at_sign=True)
            for atoms in iterator:
                self._validate_atoms(atoms, path)
                frames.append(atoms)
        except Exception:
            frames = []

        if frames:
            return frames[:count]

        try:
            for frame_index, atoms in enumerate(iread(str(path), index=":", format=fmt, do_not_split_by_at_sign=True)):
                if frame_index < start:
                    continue
                if frame_index >= end:
                    break
                self._validate_atoms(atoms, path)
                frames.append(atoms)
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

        return frames

    def _read_native_frame(self, path: Path, detected_format: str, index: int) -> Atoms | FrameData:
        try:
            if detected_format == "traj":
                with Trajectory(str(path), "r") as trajectory:
                    return _frame_data_from_trajectory(trajectory, index)
            if detected_format == "db":
                with connect(path, serial=True) as database:
                    selection = database.count() if index == -1 else index + 1
                    return _frame_data_from_db_row(database.get(selection))
            raise ValueError(f"Unsupported native indexed format: {detected_format}")
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_native_frame_range(self, path: Path, detected_format: str, start: int, end: int) -> list[Atoms | FrameData]:
        try:
            if detected_format == "traj":
                with Trajectory(str(path), "r") as trajectory:
                    return [_frame_data_from_trajectory(trajectory, index) for index in range(start, end)]
            if detected_format == "db":
                with connect(path, serial=True) as database:
                    safe_end = min(end, database.count())
                    return [_frame_data_from_db_row(database.get(index + 1)) for index in range(start, safe_end)]
            raise ValueError(f"Unsupported native indexed format: {detected_format}")
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_xdatcar_frame(self, path: Path, index: FastXdatcarIndex, frame_index: int) -> FrameData:
        try:
            offset = index.frame_offsets[frame_index]
        except IndexError:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {frame_index}", 404) from None

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                return self._read_xdatcar_frame_from_handle(handle, index)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_xdatcar_frame_range(self, path: Path, index: FastXdatcarIndex, start: int, end: int) -> list[FrameData]:
        frames: list[FrameData] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for frame_index in range(start, end):
                    handle.seek(index.frame_offsets[frame_index])
                    frames.append(self._read_xdatcar_frame_from_handle(handle, index))
            return frames
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_xdatcar_frame_from_handle(self, handle: Any, index: FastXdatcarIndex) -> FrameData:
        text = "".join(handle.readline() for _ in range(index.n_atoms))
        scaled = np.fromstring(text, sep=" ", dtype=np.float64)
        expected = index.n_atoms * 3
        if scaled.size != expected:
            raise ValueError(f"Expected {expected} XDATCAR coordinates, found {scaled.size}")
        scaled = scaled.reshape((index.n_atoms, 3))
        cell = np.asarray(index.cell, dtype="<f4")
        positions = np.asarray(scaled @ cell.astype(np.float64), dtype="<f4")
        return FrameData(
            positions=positions,
            numbers=np.asarray(index.numbers, dtype=np.int32),
            symbols=index.symbols,
            cell=cell,
            pbc=(True, True, True),
            fixed_indices=index.fixed_indices,
        )

    def _read_xdatcar_variable_frame(
        self,
        path: Path,
        index: FastXdatcarVariableIndex,
        frame_index: int,
    ) -> FrameData:
        try:
            header_offset = index.header_offsets[frame_index]
        except IndexError:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {frame_index}", 404) from None
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(header_offset)
                return self._read_xdatcar_variable_frame_from_handle(handle)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_xdatcar_variable_frame_range(
        self,
        path: Path,
        index: FastXdatcarVariableIndex,
        start: int,
        end: int,
    ) -> list[FrameData]:
        frames: list[FrameData] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for frame_index in range(start, end):
                    handle.seek(index.header_offsets[frame_index])
                    frames.append(self._read_xdatcar_variable_frame_from_handle(handle))
            return frames
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_xdatcar_variable_frame_from_handle(self, handle: Any) -> FrameData:
        handle.readline()
        scale_values = _parse_vasp_scaling_factor(handle.readline())
        raw_cell = np.array([handle.readline().split()[:3] for _ in range(3)], dtype=np.float64)
        if scale_values[0] < 0.0:
            scale_values = np.cbrt(-1.0 * scale_values / np.linalg.det(raw_cell))
        cell = raw_cell * scale_values

        symbol_tokens = handle.readline().split()
        count_tokens = handle.readline().split()
        if not symbol_tokens or not count_tokens:
            raise ValueError("XDATCAR variable frame header is incomplete")
        counts = [int(value) for value in count_tokens]
        if len(symbol_tokens) != len(counts):
            raise ValueError("XDATCAR variable frame symbols and counts do not match")

        symbols: list[str] = []
        numbers: list[int] = []
        for symbol_token, count in zip(symbol_tokens, counts):
            symbol = _normalize_symbol(symbol_token)
            number = _atomic_number_from_normalized_symbol(symbol)
            symbols.extend([symbol] * count)
            numbers.extend([number] * count)
        n_atoms = sum(counts)
        if n_atoms <= 0:
            raise ValueError("XDATCAR variable frame has no atoms")

        marker = handle.readline()
        if not marker.startswith("Direct configuration="):
            raise ValueError("XDATCAR variable frame is missing Direct configuration marker")
        text = "".join(handle.readline() for _ in range(n_atoms))
        scaled = np.fromstring(text, sep=" ", dtype=np.float64)
        expected = n_atoms * 3
        if scaled.size != expected:
            raise ValueError(f"Expected {expected} XDATCAR coordinates, found {scaled.size}")
        positions = np.asarray(scaled.reshape((n_atoms, 3)) @ cell, dtype="<f4")
        return FrameData(
            positions=positions,
            numbers=np.asarray(numbers, dtype=np.int32),
            symbols=tuple(symbols),
            cell=np.asarray(cell, dtype="<f4"),
            pbc=(True, True, True),
        )

    def _read_outcar_frame(self, path: Path, index: FastOutcarIndex, frame_index: int) -> FrameData:
        try:
            offset = index.frame_offsets[frame_index]
        except IndexError:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {frame_index}", 404) from None

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                return self._read_outcar_frame_from_handle(handle, index, frame_index)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_outcar_frame_range(self, path: Path, index: FastOutcarIndex, start: int, end: int) -> list[FrameData]:
        frames: list[FrameData] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for frame_index in range(start, end):
                    handle.seek(index.frame_offsets[frame_index])
                    frames.append(self._read_outcar_frame_from_handle(handle, index, frame_index))
            return frames
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_outcar_frame_from_handle(self, handle: Any, index: FastOutcarIndex, frame_index: int) -> FrameData:
        positions: list[list[float]] = []
        forces: list[list[float]] = []
        for _ in range(index.n_atoms):
            fields = handle.readline().split()
            if len(fields) < 3:
                raise ValueError("Unexpected end of OUTCAR coordinates")
            positions.append([float(fields[0]), float(fields[1]), float(fields[2])])
            if len(fields) >= 6:
                forces.append([float(fields[3]), float(fields[4]), float(fields[5])])

        energy = index.energies[frame_index] if frame_index < len(index.energies) else None
        fmax = index.fmaxes[frame_index] if frame_index < len(index.fmaxes) else _outcar_fmax_from_forces(
            forces,
            index.n_atoms,
            index.fixed_indices,
        )
        cell = index.cells[frame_index] if frame_index < len(index.cells) else index.cell
        return FrameData(
            positions=np.asarray(positions, dtype="<f4"),
            numbers=np.asarray(index.numbers, dtype=np.int32),
            symbols=index.symbols,
            cell=np.asarray(cell, dtype="<f4"),
            pbc=(True, True, True),
            fixed_indices=index.fixed_indices,
            energy=energy,
            fmax=fmax,
        )

    def _scan_outcar(
        self,
        path: Path,
        cancellation: StructureCancellationToken | None = None,
    ) -> StructureSummary:
        """快速扫描 OUTCAR 文件，支持不完整文件。完全绕过 ASE 的 iread_vasp_out。"""
        cancellation = cancellation or StructureCancellationToken()
        frame_offsets: list[int] = []
        frame_energies: list[float | None] = []
        frame_fmaxes: list[float | None] = []
        file_incomplete = False
        is_md_task = False
        warnings: list[str] = []

        try:
            with path.open("r", encoding="utf-8") as handle:
                # 阶段1：按 ASE 语义扫描 header，直到第一个 Iteration。
                n_atoms = 0
                potcar_symbols: list[str] = []
                ion_types: list[int] = []

                while True:
                    line = handle.readline()
                    if not line:
                        raise FastParserUnsupported("OUTCAR header incomplete: no Iteration found")
                    cancellation.check()

                    if _outcar_line_sets_md_mode(line):
                        is_md_task = True

                    potcar_symbol = _parse_outcar_potcar_symbol(line)
                    if potcar_symbol is not None:
                        potcar_symbols.append(potcar_symbol)

                    parsed_ion_types = _parse_outcar_ions_per_type(line)
                    if parsed_ion_types is not None:
                        ion_types = parsed_ion_types

                    if "NIONS =" in line:
                        try:
                            n_atoms = int(line.split("NIONS =")[1].split()[0])
                        except (IndexError, ValueError):
                            raise FastParserUnsupported("Cannot parse NIONS from OUTCAR")
                        if n_atoms <= 0:
                            raise FastParserUnsupported("Invalid NIONS value in OUTCAR")

                    if "Iteration" in line:
                        break

                if n_atoms <= 0:
                    raise FastParserUnsupported("OUTCAR header incomplete: no NIONS found")
                species = _outcar_species_from_potcar_lines(potcar_symbols)
                symbols_list = list(_outcar_symbols_from_header(species, ion_types, n_atoms))
                numbers_list = [_atomic_number_from_normalized_symbol(symbol) for symbol in symbols_list]
                fixed_indices, constraint_warning = _read_outcar_fixed_indices_from_neighbor(path, n_atoms)
                if constraint_warning:
                    warnings.append(constraint_warning)

                # 阶段2：扫描 ionic chunks，组合每帧自己的 cell、POSITION/TOTAL-FORCE 和 ASE 能量块。
                handle.seek(0)
                frame_cells: list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = []
                current_cell: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    cancellation.check()

                    if not is_md_task and _outcar_line_sets_md_mode(line):
                        is_md_task = True

                    if "direct lattice vectors" in line:
                        cell = _read_outcar_cell_after_lattice_header(handle)
                        if cell is not None:
                            current_cell = cell
                        continue

                    if "POSITION" in line and "TOTAL-FORCE" in line:
                        pos_start = handle.tell()
                        next_line = handle.readline()
                        if not next_line:
                            break
                        if "---" in next_line:
                            # 有 dashed line，坐标从下一行开始
                            pos_start = handle.tell()
                        else:
                            # 无 dashed line：next_line 就是第一行坐标
                            # pos_start 保持不变（指向第一行坐标开头）
                            # 但需要 seek 回去，因为 readline 已经消费了这一行
                            handle.seek(pos_start)

                        # 读取 n_atoms 行坐标验证完整性
                        forces: list[list[float]] = []
                        coord_lines = 0
                        for _ in range(n_atoms):
                            cl = handle.readline()
                            if not cl:
                                break
                            parts = cl.split()
                            if len(parts) >= 3:
                                coord_lines += 1
                            if len(parts) >= 6:
                                try:
                                    forces.append([float(parts[3]), float(parts[4]), float(parts[5])])
                                except ValueError:
                                    pass

                        if coord_lines < n_atoms:
                            file_incomplete = True
                        else:
                            frame_offsets.append(pos_start)
                            frame_cells.append(current_cell if current_cell is not None else ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
                            frame_energies.append(None)
                            frame_fmaxes.append(_outcar_fmax_from_forces(forces, n_atoms, fixed_indices))
                        continue

                    if "FREE ENERGIE OF THE ION-ELECTRON SYSTEM" in line and frame_offsets:
                        try:
                            handle.readline()
                            free_energy = _extract_outcar_energy(handle.readline())
                            handle.readline()
                            energy_zero = _extract_outcar_energy_zero(handle.readline())
                        except OSError:
                            free_energy = None
                            energy_zero = None
                        frame_energies[-1] = energy_zero if energy_zero is not None else free_energy

                # 阶段3：如果没有完整帧，回退
                if not frame_offsets:
                    raise FastParserUnsupported("No complete frames found in OUTCAR")

                # 阶段4：从后往前尝试读取完整帧
                positions = []
                selected_offset = None
                selected_index = -1
                for offset in reversed(frame_offsets):
                    handle.seek(offset)
                    pos_list = []
                    ok = True
                    for _ in range(n_atoms):
                        cl = handle.readline()
                        if not cl:
                            ok = False
                            break
                        parts = cl.split()
                        if len(parts) >= 3:
                            pos_list.append([float(parts[0]), float(parts[1]), float(parts[2])])
                        else:
                            ok = False
                            break
                    if ok and len(pos_list) == n_atoms:
                        positions = pos_list
                        selected_offset = offset
                        selected_index = frame_offsets.index(offset)
                        if offset != frame_offsets[-1]:
                            file_incomplete = True
                        break

                if not positions:
                    raise FastParserUnsupported("No complete frame found in OUTCAR")

                n_frames = len(frame_offsets)
                if selected_offset != frame_offsets[-1]:
                    n_frames = frame_offsets.index(selected_offset) + 1
                    frame_offsets = frame_offsets[:n_frames]
                    frame_cells = frame_cells[:n_frames]
                    frame_energies = frame_energies[:n_frames]
                    frame_fmaxes = frame_fmaxes[:n_frames]
                if n_atoms > self.settings.viewer.ase.max_atoms:
                    raise AppError(
                        "STRUCTURE_TOO_MANY_ATOMS",
                        f"Structure has {n_atoms} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                        413,
                    )

                # 构建 FastOutcarIndex
                fast_index = FastOutcarIndex(
                    frame_offsets=tuple(frame_offsets),
                    n_atoms=n_atoms,
                    symbols=tuple(symbols_list),
                    numbers=tuple(numbers_list),
                    cell=frame_cells[selected_index] if 0 <= selected_index < len(frame_cells) else frame_cells[-1],
                    cells=tuple(frame_cells),
                    fixed_indices=tuple(fixed_indices),
                    energies=tuple(frame_energies),
                    fmaxes=tuple(frame_fmaxes),
                )

                # 构建 FrameData
                selected_cell = frame_cells[selected_index] if 0 <= selected_index < len(frame_cells) else frame_cells[-1]
                cell_arr = np.array(selected_cell, dtype=np.float64)
                pos_arr = np.array(positions, dtype=np.float64)
                frame_data = FrameData(
                    positions=pos_arr,
                    numbers=np.array(numbers_list, dtype=np.int32),
                    symbols=tuple(symbols_list),
                    cell=cell_arr,
                    pbc=(True, True, True),
                    fixed_indices=tuple(fixed_indices),
                    energy=frame_energies[selected_index] if 0 <= selected_index < len(frame_energies) else None,
                    fmax=frame_fmaxes[selected_index] if 0 <= selected_index < len(frame_fmaxes) else None,
                )

                if is_md_task:
                    warnings.append("vasp_outcar_md_may_lack_structure")

                return StructureSummary(
                    n_frames=n_frames,
                    n_atoms=n_atoms,
                    last_atoms=None,
                    last_frame_data=frame_data,
                    topology_stable=True,
                    detected_format="vasp-out",
                    fast_outcar_index=fast_index,
                    file_incomplete=file_incomplete,
                    warnings=tuple(warnings),
                )

        except FastParserUnsupported:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise FastParserUnsupported(f"OUTCAR fast parser failed: {exc}") from exc

    def _read_dmol_arc_frame(self, path: Path, index: FastArcIndex, frame_index: int) -> FrameData:
        try:
            offset = index.frame_offsets[frame_index]
        except IndexError:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {frame_index}", 404) from None

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                date_line = handle.readline()
                if not date_line.startswith("!DATE"):
                    raise ValueError("ARC frame does not start with !DATE")
                return self._read_dmol_arc_frame_from_handle(handle, index.pbc)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_dmol_arc_frame_range(self, path: Path, index: FastArcIndex, start: int, end: int) -> list[FrameData]:
        frames: list[FrameData] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for frame_index in range(start, end):
                    handle.seek(index.frame_offsets[frame_index])
                    date_line = handle.readline()
                    if not date_line.startswith("!DATE"):
                        raise ValueError("ARC frame does not start with !DATE")
                    frames.append(self._read_dmol_arc_frame_from_handle(handle, index.pbc))
            return frames
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_dmol_arc_frame_from_handle(self, handle: Any, pbc: tuple[bool, bool, bool]) -> FrameData:
        cell = np.zeros((3, 3), dtype="<f4")
        if all(pbc):
            cell_line = handle.readline()
            fields = cell_line.split()
            if len(fields) < 7 or fields[0] != "PBC":
                raise FastParserUnsupported("ARC periodic frame is missing cell parameters")
            cellpar = np.array([float(value) for value in fields[1:7]], dtype=np.float64)
            cell = np.asarray(cellpar_to_cell(cellpar), dtype="<f4")

        symbols: list[str] = []
        numbers: list[int] = []
        positions: list[list[float]] = []
        while True:
            line = handle.readline()
            if line == "":
                raise ValueError("Unexpected end of ARC frame")
            if line.startswith("end"):
                break
            fields = line.split()
            if len(fields) < 8:
                raise ValueError("Invalid atom line in ARC frame")
            symbol = _normalize_symbol(fields[7])
            number = _atomic_number_from_normalized_symbol(symbol)
            symbols.append(symbol)
            numbers.append(number)
            positions.append([float(fields[1]), float(fields[2]), float(fields[3])])

        trailing = handle.readline()
        if trailing and not trailing.startswith("end"):
            raise FastParserUnsupported("ARC frame terminator is unsupported")

        return FrameData(
            positions=np.asarray(positions, dtype="<f4"),
            numbers=np.asarray(numbers, dtype=np.int32),
            symbols=tuple(symbols),
            cell=cell,
            pbc=pbc,
        )

    def _read_xyz_like_frame(self, path: Path, index: FastXyzIndex, frame_index: int) -> Atoms | FrameData:
        try:
            offset = index.frame_offsets[frame_index]
            length = index.frame_lengths[frame_index]
        except IndexError:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", f"Frame index is outside range: {frame_index}", 404) from None

        if index.detected_format == "extxyz":
            return self._read_extxyz_frame_slice(path, offset, length)

        return self._read_plain_xyz_frame_at(path, offset, frame_index)

    def _read_xyz_frame_text_at_offset(self, path: Path, offset: int) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                first_line = handle.readline()
                if first_line == b"":
                    raise ValueError("Unexpected end of file at XYZ frame header")
                atom_count = int(first_line.strip())
                lines = [first_line]

                comment_line = handle.readline()
                if comment_line == b"":
                    raise ValueError("Unexpected end of file at XYZ comment line")
                lines.append(comment_line)

                for _ in range(atom_count):
                    atom_line = handle.readline()
                    if atom_line == b"":
                        raise ValueError("Unexpected end of file in XYZ atom block")
                    lines.append(atom_line)

                while True:
                    extra_offset = handle.tell()
                    extra_line = handle.readline()
                    if extra_line.lstrip().startswith(b"VEC"):
                        lines.append(extra_line)
                        continue
                    if extra_line:
                        handle.seek(extra_offset)
                    break

            return b"".join(lines).decode("utf-8")
        except Exception as exc:
            raise FastParserUnsupported(f"Could not read XYZ frame text: {exc}") from exc

    def _read_plain_xyz_frame_at(self, path: Path, offset: int, frame_index: int) -> FrameData:
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                return self._read_plain_xyz_frame_from_handle(handle, path, frame_index)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_plain_xyz_frame_range(
        self,
        path: Path,
        index: FastXyzIndex,
        start: int,
        end: int,
        summary: StructureSummary | None = None,
        profile: dict[str, Any] | None = None,
    ) -> list[FrameData]:
        frames: list[FrameData] = []
        try:
            stable_stride = _stable_plain_xyz_frame_stride(index)
            if (
                summary is not None
                and summary.topology_stable
                and 0 <= start < end <= len(index.frame_offsets)
                and index.complete
            ):
                _profile_set(profile, "fixed_plain_xyz_path", "complete_index_sequential_np_fromstring")
                return self._read_plain_xyz_fixed_frame_range(path, index, start, end, profile=profile)

            if (
                summary is not None
                and summary.topology_stable
                and stable_stride is not None
                and 0 <= start < end
            ):
                _profile_set(profile, "fixed_plain_xyz_path", "stable_stride_sequential_np_fromstring")
                return self._read_plain_xyz_fixed_frame_range(
                    path,
                    index,
                    start,
                    end,
                    profile=profile,
                    stable_stride=stable_stride,
                )

            with path.open("r", encoding="utf-8") as handle:
                if index.complete:
                    for frame_index in range(start, end):
                        handle.seek(index.frame_offsets[frame_index])
                        frames.append(self._read_plain_xyz_frame_from_handle(handle, path, frame_index))
                    return frames

                if stable_stride is not None:
                    base_offset = index.frame_offsets[0]
                    for frame_index in range(start, end):
                        handle.seek(base_offset + frame_index * stable_stride)
                        frames.append(self._read_plain_xyz_frame_from_handle(handle, path, frame_index))
                    return frames

                # The fast preview index only stores sample offsets, not a
                # complete frame-index -> offset map. If constant stride cannot
                # be proven, fall back to a correct forward scan from frame 0.
                file_size = path.stat().st_size
                handle.seek(index.frame_offsets[0])
                for frame_index in range(0, end):
                    if handle.tell() >= file_size:
                        break
                    frame = self._read_plain_xyz_frame_from_handle(handle, path, frame_index)
                    if frame_index >= start:
                        frames.append(frame)
            return frames
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_plain_xyz_fixed_frame_range(
        self,
        path: Path,
        index: FastXyzIndex,
        start: int,
        end: int,
        profile: dict[str, Any] | None = None,
        stable_stride: int | None = None,
    ) -> list[FrameData]:
        with path.open("r", encoding="utf-8") as handle:
            start_offset = index.frame_offsets[start] if index.complete else index.frame_offsets[0] + start * stable_stride
            t_seek_0 = _profile_start(profile)
            handle.seek(start_offset)
            _profile_add_ms(profile, "fixed_plain_xyz_seek_ms", t_seek_0)
            frames: list[FrameData] = []
            reference_symbols: tuple[str, ...] | None = None
            reference_numbers: np.ndarray | None = None
            for frame_index in range(start, end):
                frame, reference_symbols, reference_numbers = self._read_plain_xyz_fixed_frame_from_handle(
                    handle,
                    frame_index,
                    reference_symbols,
                    reference_numbers,
                    profile=profile,
                )
                frames.append(frame)
            return frames

    def _try_build_direct_fixed_binary_payload(
        self,
        *,
        path: Path,
        summary: StructureSummary,
        start: int,
        count: int,
        n_frames_total: int,
        profile: dict[str, Any] | None,
    ) -> bytes | None:
        xdatcar_payload = self._try_build_xdatcar_fixed_binary_payload(
            path=path,
            summary=summary,
            start=start,
            count=count,
            n_frames_total=n_frames_total,
            profile=profile,
        )
        if xdatcar_payload is not None:
            return xdatcar_payload
        return self._try_build_plain_xyz_fixed_binary_payload(
            path=path,
            summary=summary,
            start=start,
            count=count,
            n_frames_total=n_frames_total,
            profile=profile,
        )

    def _try_build_plain_xyz_fixed_binary_payload(
        self,
        *,
        path: Path,
        summary: StructureSummary,
        start: int,
        count: int,
        n_frames_total: int,
        profile: dict[str, Any] | None,
    ) -> bytes | None:
        index = summary.fast_xyz_index
        if (
            index is None
            or index.detected_format != "xyz"
            or not summary.topology_stable
            or summary.last_frame_data is None
            or count <= 0
        ):
            return None

        stable_stride = _stable_plain_xyz_frame_stride(index)
        if index.complete:
            if start < 0 or start + count > len(index.frame_offsets):
                return None
            start_offset = index.frame_offsets[start]
            direct_path = "complete_index_direct_arrays"
        elif stable_stride is not None:
            start_offset = index.frame_offsets[0] + start * stable_stride
            direct_path = "stable_stride_direct_arrays"
        else:
            return None

        t_read_0 = _profile_start(profile)
        arrays = self._read_plain_xyz_fixed_chunk_arrays(
            path=path,
            start_offset=start_offset,
            count=count,
            n_atoms=summary.n_atoms,
            symbols=summary.last_frame_data.symbols,
            numbers=summary.last_frame_data.numbers,
            profile=profile,
        )
        _profile_add_ms(profile, "read_frame_range_ms", t_read_0)
        _profile_set(profile, "read_path", f"fast_xyz_direct_arrays:{index.detected_format}")
        _profile_set(profile, "fixed_plain_xyz_path", direct_path)

        t_build_0 = _profile_start(profile)
        payload = self._build_fixed_binary_payload_from_arrays(
            path=path,
            arrays=arrays,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=summary.detected_format,
            profile=profile,
        )
        _profile_add_ms(profile, "build_payload_total_ms", t_build_0)
        return payload

    def _try_build_xdatcar_fixed_binary_payload(
        self,
        *,
        path: Path,
        summary: StructureSummary,
        start: int,
        count: int,
        n_frames_total: int,
        profile: dict[str, Any] | None,
    ) -> bytes | None:
        index = summary.fast_xdatcar_index
        if index is None or not summary.topology_stable or count <= 0:
            return None
        if start < 0 or start + count > len(index.frame_offsets):
            return None

        t_read_0 = _profile_start(profile)
        arrays = self._read_xdatcar_fixed_chunk_arrays(
            path=path,
            index=index,
            start=start,
            count=count,
            profile=profile,
        )
        _profile_add_ms(profile, "read_frame_range_ms", t_read_0)
        _profile_set(profile, "read_path", "fast_xdatcar_direct_arrays")

        t_build_0 = _profile_start(profile)
        payload = self._build_fixed_binary_payload_from_arrays(
            path=path,
            arrays=arrays,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=summary.detected_format,
            profile=profile,
        )
        _profile_add_ms(profile, "build_payload_total_ms", t_build_0)
        return payload

    def _read_plain_xyz_fixed_chunk_arrays(
        self,
        *,
        path: Path,
        start_offset: int,
        count: int,
        n_atoms: int,
        symbols: tuple[str, ...],
        numbers: np.ndarray,
        profile: dict[str, Any] | None,
    ) -> StructureChunkArrays:
        if n_atoms <= 0 or len(symbols) != n_atoms:
            raise AppError("STRUCTURE_PARSE_FAILED", f"Invalid fixed XYZ topology in {path.name}", 400)

        coord_segments: list[str] = []
        coord_starts: list[int] | None = None
        energy = np.full((count,), np.nan, dtype="<f4")
        fmax = np.full((count,), np.nan, dtype="<f4")

        with path.open("r", encoding="utf-8") as handle:
            t_seek_0 = _profile_start(profile)
            handle.seek(start_offset)
            _profile_add_ms(profile, "fixed_plain_xyz_seek_ms", t_seek_0)

            for local_index in range(count):
                t_header_0 = _profile_start(profile)
                atom_count = int(handle.readline().strip())
                comment = handle.readline()
                _profile_add_ms(profile, "fixed_plain_xyz_header_read_ms", t_header_0)
                if atom_count != n_atoms:
                    raise ValueError(f"Unexpected atom count change in frame chunk at local index {local_index}")
                if comment == "":
                    raise ValueError(f"Missing comment line in frame chunk at local index {local_index}")

                parsed_energy = _extract_plain_xyz_comment_energy(comment)
                parsed_fmax = _extract_plain_xyz_comment_fmax(comment)
                if parsed_energy is not None:
                    energy[local_index] = parsed_energy
                if parsed_fmax is not None:
                    fmax[local_index] = parsed_fmax

                t_read_0 = _profile_start(profile)
                atom_lines = [handle.readline() for _ in range(n_atoms)]
                _profile_add_ms(profile, "fixed_plain_xyz_atom_readline_ms", t_read_0)
                if any(line == "" for line in atom_lines):
                    raise ValueError(f"Unexpected end of file in frame chunk at local index {local_index}")

                t_split_0 = _profile_start(profile)
                if coord_starts is None:
                    coord_starts = []
                    for atom_index, atom_line in enumerate(atom_lines):
                        symbol_token, coord_start = _plain_xyz_symbol_and_coord_start(atom_line)
                        symbol = _normalize_symbol(symbol_token)
                        if symbol != symbols[atom_index]:
                            raise ValueError(f"Unexpected atom symbol in frame chunk at local index {local_index}")
                        coord_starts.append(coord_start)
                        coord_segments.append(atom_line[coord_start:].strip())
                else:
                    for atom_index, atom_line in enumerate(atom_lines):
                        coord_start = coord_starts[atom_index]
                        symbol = symbols[atom_index]
                        if (
                            len(atom_line) <= coord_start
                            or not atom_line.startswith(symbol)
                            or not atom_line[len(symbol)].isspace()
                        ):
                            symbol_token, coord_text = _split_plain_xyz_atom_line(atom_line)
                            if _normalize_symbol(symbol_token) != symbol:
                                raise ValueError(f"Unexpected atom symbol in frame chunk at local index {local_index}")
                            coord_segments.append(coord_text)
                        else:
                            coord_segments.append(atom_line[coord_start:].strip())
                _profile_add_ms(profile, "fixed_plain_xyz_split_ms", t_split_0)

        t_join_0 = _profile_start(profile)
        coord_text = "\n".join(coord_segments)
        _profile_add_ms(profile, "fixed_plain_xyz_coord_join_ms", t_join_0)
        t_parse_0 = _profile_start(profile)
        positions_flat = np.fromstring(coord_text, sep=" ", dtype=np.float32)
        _profile_add_ms(profile, "fixed_plain_xyz_np_fromstring_ms", t_parse_0)
        if positions_flat.size != count * n_atoms * 3:
            raise ValueError("Invalid fixed XYZ coordinate block")

        positions = positions_flat.reshape((count, n_atoms, 3))
        cells = np.zeros((count, 3, 3), dtype="<f4")
        tags = np.zeros((count, n_atoms), dtype="<i4")
        fixed_mask = np.zeros((count, n_atoms), dtype=np.uint8)

        return StructureChunkArrays(
            count=count,
            positions=positions,
            cells=cells,
            tags=tags,
            fixed_mask=fixed_mask,
            energy=energy,
            fmax=fmax,
            symbols=symbols,
            numbers=np.asarray(numbers, dtype=np.int32),
            pbc=(False, False, False),
        )

    def _read_xdatcar_fixed_chunk_arrays(
        self,
        *,
        path: Path,
        index: FastXdatcarIndex,
        start: int,
        count: int,
        profile: dict[str, Any] | None,
    ) -> StructureChunkArrays:
        if index.n_atoms <= 0 or len(index.symbols) != index.n_atoms or len(index.numbers) != index.n_atoms:
            raise AppError("STRUCTURE_PARSE_FAILED", f"Invalid XDATCAR topology in {path.name}", 400)

        coord_segments: list[str] = []
        with path.open("r", encoding="utf-8") as handle:
            for local_index in range(count):
                frame_index = start + local_index
                try:
                    frame_offset = index.frame_offsets[frame_index]
                except IndexError:
                    raise ValueError(f"XDATCAR frame index is outside range: {frame_index}") from None
                t_seek_0 = _profile_start(profile)
                handle.seek(frame_offset)
                _profile_add_ms(profile, "fixed_xdatcar_seek_ms", t_seek_0)

                t_read_0 = _profile_start(profile)
                frame_lines = [handle.readline() for _ in range(index.n_atoms)]
                _profile_add_ms(profile, "fixed_xdatcar_coord_readline_ms", t_read_0)
                if any(line == "" for line in frame_lines):
                    raise ValueError(f"Unexpected end of XDATCAR frame chunk at local index {local_index}")
                coord_segments.extend(line.strip() for line in frame_lines)

        t_join_0 = _profile_start(profile)
        coord_text = "\n".join(coord_segments)
        _profile_add_ms(profile, "fixed_xdatcar_coord_join_ms", t_join_0)
        t_parse_0 = _profile_start(profile)
        scaled_flat = np.fromstring(coord_text, sep=" ", dtype=np.float32)
        _profile_add_ms(profile, "fixed_xdatcar_np_fromstring_ms", t_parse_0)
        expected = count * index.n_atoms * 3
        if scaled_flat.size != expected:
            raise ValueError(f"Expected {expected} XDATCAR coordinates, found {scaled_flat.size}")

        t_transform_0 = _profile_start(profile)
        scaled = scaled_flat.reshape((count, index.n_atoms, 3))
        cell = np.asarray(index.cell, dtype="<f4")
        positions = np.asarray(scaled @ cell.astype(np.float32), dtype="<f4")
        cells = np.broadcast_to(cell, (count, 3, 3)).copy()
        _profile_add_ms(profile, "fixed_xdatcar_transform_ms", t_transform_0)

        tags = np.zeros((count, index.n_atoms), dtype="<i4")
        fixed_mask = np.zeros((count, index.n_atoms), dtype=np.uint8)
        for atom_index in index.fixed_indices:
            if 0 <= atom_index < index.n_atoms:
                fixed_mask[:, atom_index] = 1
        energy = np.full((count,), np.nan, dtype="<f4")
        fmax = np.full((count,), np.nan, dtype="<f4")

        return StructureChunkArrays(
            count=count,
            positions=positions,
            cells=cells,
            tags=tags,
            fixed_mask=fixed_mask,
            energy=energy,
            fmax=fmax,
            symbols=index.symbols,
            numbers=np.asarray(index.numbers, dtype=np.int32),
            pbc=(True, True, True),
        )

    def _read_plain_xyz_frame_from_handle(self, handle: Any, path: Path, frame_index: int) -> FrameData:
        atom_count = int(handle.readline().strip())
        comment = handle.readline()
        symbols: list[str] = []
        numbers = np.empty((atom_count,), dtype=np.int32)
        positions = np.empty((atom_count, 3), dtype="<f4")
        for atom_index in range(atom_count):
            fields = handle.readline().split()
            if len(fields) < 4:
                raise ValueError(f"Invalid atom line in frame {frame_index}")
            symbol = _normalize_symbol(fields[0])
            symbols.append(symbol)
            numbers[atom_index] = _atomic_number_from_normalized_symbol(symbol)
            positions[atom_index] = (float(fields[1]), float(fields[2]), float(fields[3]))
        return FrameData(
            positions=positions,
            numbers=numbers,
            symbols=tuple(symbols),
            cell=np.zeros((3, 3), dtype="<f4"),
            pbc=(False, False, False),
            energy=_extract_plain_xyz_comment_energy(comment),
            fmax=_extract_plain_xyz_comment_fmax(comment),
        )

    def _read_plain_xyz_fixed_frame_from_handle(
        self,
        handle: Any,
        frame_index: int,
        reference_symbols: tuple[str, ...] | None,
        reference_numbers: np.ndarray | None,
        profile: dict[str, Any] | None = None,
    ) -> tuple[FrameData, tuple[str, ...], np.ndarray]:
        t_header_0 = _profile_start(profile)
        atom_count = int(handle.readline().strip())
        comment = handle.readline()
        _profile_add_ms(profile, "fixed_plain_xyz_header_read_ms", t_header_0)
        if comment == "":
            raise ValueError(f"Missing comment line at frame {frame_index}")

        coord_segments: list[str] = []
        coord_segments_extend = coord_segments.append

        if reference_symbols is None or reference_numbers is None:
            symbols_list: list[str] = []
            numbers = np.empty((atom_count,), dtype=np.int32)
            for atom_index in range(atom_count):
                t_readline_0 = _profile_start(profile)
                atom_line = handle.readline()
                _profile_add_ms(profile, "fixed_plain_xyz_atom_readline_ms", t_readline_0)
                if atom_line == "":
                    raise ValueError(f"Unexpected end of file in frame {frame_index}")
                t_split_0 = _profile_start(profile)
                symbol_token, coord_text = _split_plain_xyz_atom_line(atom_line)
                _profile_add_ms(profile, "fixed_plain_xyz_split_ms", t_split_0)
                t_symbol_0 = _profile_start(profile)
                symbol = _normalize_symbol(symbol_token)
                symbols_list.append(symbol)
                numbers[atom_index] = _atomic_number_from_normalized_symbol(symbol)
                _profile_add_ms(profile, "fixed_plain_xyz_symbol_parse_ms", t_symbol_0)
                coord_segments_extend(coord_text)
            reference_symbols = tuple(symbols_list)
            reference_numbers = numbers
        else:
            if atom_count != len(reference_symbols):
                raise ValueError(f"Unexpected atom count change in frame {frame_index}")
            for atom_index in range(atom_count):
                t_readline_0 = _profile_start(profile)
                atom_line = handle.readline()
                _profile_add_ms(profile, "fixed_plain_xyz_atom_readline_ms", t_readline_0)
                if atom_line == "":
                    raise ValueError(f"Unexpected end of file in frame {frame_index}")
                t_split_0 = _profile_start(profile)
                _symbol_token, coord_text = _split_plain_xyz_atom_line(atom_line)
                _profile_add_ms(profile, "fixed_plain_xyz_split_ms", t_split_0)
                coord_segments_extend(coord_text)

        t_join_0 = _profile_start(profile)
        coord_text = "\n".join(coord_segments)
        _profile_add_ms(profile, "fixed_plain_xyz_coord_join_ms", t_join_0)
        t_parse_0 = _profile_start(profile)
        positions_flat = np.fromstring(coord_text, sep=" ", dtype=np.float32)
        _profile_add_ms(profile, "fixed_plain_xyz_np_fromstring_ms", t_parse_0)
        if positions_flat.size != atom_count * 3:
            raise ValueError(f"Invalid coordinate block in frame {frame_index}")
        t_reshape_0 = _profile_start(profile)
        positions = positions_flat.reshape((atom_count, 3))
        _profile_add_ms(profile, "fixed_plain_xyz_reshape_ms", t_reshape_0)

        t_frame_0 = _profile_start(profile)
        numbers_copy = reference_numbers.copy()
        t_frame_build_0 = _profile_start(profile)
        frame = FrameData(
            positions=positions,
            numbers=numbers_copy,
            symbols=reference_symbols,
            cell=np.zeros((3, 3), dtype="<f4"),
            pbc=(False, False, False),
            energy=_extract_plain_xyz_comment_energy(comment),
            fmax=_extract_plain_xyz_comment_fmax(comment),
        )
        _profile_add_ms(profile, "fixed_plain_xyz_numbers_copy_ms", t_frame_0)
        _profile_add_ms(profile, "fixed_plain_xyz_frame_build_ms", t_frame_build_0)
        return (
            frame,
            reference_symbols,
            reference_numbers,
        )

    def _read_extxyz_frame_slice(self, path: Path, offset: int, length: int) -> Atoms | FrameData:
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                frame_text = handle.read(length)
            return self._read_extxyz_frame_text(path, frame_text)
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_extxyz_frame_text(self, path: Path, frame_text: str) -> Atoms | FrameData:
        try:
            return self._read_extxyz_frame_data(frame_text)
        except FastParserUnsupported:
            pass
        try:
            atoms = read(StringIO(frame_text), index=0, format="extxyz")
            if isinstance(atoms, list):
                if not atoms:
                    raise ValueError("empty extxyz frame")
                atoms = atoms[0]
            return atoms
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _read_extxyz_frame_data(self, frame_text: str) -> FrameData:
        lines = frame_text.splitlines()
        if len(lines) < 2:
            raise FastParserUnsupported("ExtXYZ frame is incomplete")
        natoms = int(lines[0].strip())
        comment = lines[1].strip()
        info = key_val_str_to_dict(comment) if comment else {}

        pbc_value = info.pop("pbc", None)
        lattice_value = info.pop("Lattice", None)
        if lattice_value is not None:
            lattice_array = np.asarray(lattice_value, dtype=np.float64)
            if lattice_array.shape != (3, 3):
                raise FastParserUnsupported("ExtXYZ lattice shape is unsupported")
            cell = np.asarray(lattice_array.T, dtype="<f4")
            default_pbc = (True, True, True)
        else:
            cell = np.zeros((3, 3), dtype="<f4")
            default_pbc = (False, False, False)

        if pbc_value is None:
            pbc = default_pbc
        else:
            pbc_array = np.asarray(pbc_value, dtype=bool).reshape(-1)
            if pbc_array.size == 1:
                pbc = (bool(pbc_array[0]),) * 3
            elif pbc_array.size == 3:
                pbc = tuple(bool(value) for value in pbc_array.tolist())  # type: ignore[arg-type]
            else:
                raise FastParserUnsupported("ExtXYZ pbc shape is unsupported")

        properties_string = info.pop("Properties", "species:S:1:pos:R:3")
        properties, property_names, dtype, converters = parse_properties(properties_string)

        supported_arrays = {"symbols", "numbers", "positions", "tags", "move_mask", "forces"}
        for property_name in property_names:
            ase_name, _cols = properties[property_name]
            if ase_name not in supported_arrays and not _is_force_property_name(ase_name):
                raise FastParserUnsupported(f"ExtXYZ property {ase_name} is delegated to ASE")

        data_rows = []
        atom_lines = lines[2 : 2 + natoms]
        if len(atom_lines) != natoms:
            raise FastParserUnsupported("ExtXYZ atom block is incomplete")
        for atom_line in atom_lines:
            values = atom_line.split()
            row = tuple(converter(value) for converter, value in zip(converters, values))
            data_rows.append(row)

        try:
            data = np.array(data_rows, dtype=dtype)
        except Exception as exc:
            raise FastParserUnsupported("ExtXYZ atom data could not be packed") from exc

        arrays: dict[str, np.ndarray] = {}
        for property_name in property_names:
            ase_name, cols = properties[property_name]
            if cols == 1:
                arrays[ase_name] = np.asarray(data[property_name])
            else:
                arrays[ase_name] = np.vstack([data[property_name + str(column)] for column in range(cols)]).T

        numbers_array = arrays.pop("numbers", None)
        symbols_array = arrays.pop("symbols", None)
        if numbers_array is None and symbols_array is None:
            raise FastParserUnsupported("ExtXYZ requires species or atomic numbers")
        if symbols_array is not None:
            symbols = tuple(_normalize_symbol(str(value)) for value in symbols_array.tolist())
            numbers = np.asarray([_atomic_number_from_normalized_symbol(symbol) for symbol in symbols], dtype=np.int32)
        else:
            numbers = np.asarray(numbers_array, dtype=np.int32)
            symbols = tuple(chemical_symbols[int(value)] for value in numbers.tolist())

        positions_array = arrays.pop("positions", None)
        if positions_array is None:
            raise FastParserUnsupported("ExtXYZ positions are missing")
        positions = np.asarray(positions_array, dtype="<f4")

        tags_array = arrays.pop("tags", None)
        tags = None if tags_array is None else np.asarray(tags_array, dtype=np.int32)

        fixed_indices: tuple[int, ...] = ()
        move_mask = arrays.pop("move_mask", None)
        if move_mask is not None:
            mask = np.asarray(move_mask)
            if mask.ndim == 1:
                fixed_indices = tuple(int(index) for index, movable in enumerate(mask.astype(bool).tolist()) if not movable)
            else:
                raise FastParserUnsupported("ExtXYZ vector move_mask is delegated to ASE")

        forces_array = arrays.pop("forces", None)
        if forces_array is None:
            forces_array = _pop_prefixed_property_array(arrays, "forces")
        if forces_array is None:
            forces_array = _pop_prefixed_property_array(arrays, "force")
        fmax = _extract_fmax_from_forces(forces_array, natoms, fixed_indices)

        energy = None
        for key in ("energy", "free_energy", "REF_energy", "ref_energy", "DFT_energy", "dft_energy"):
            if key in info:
                energy = _safe_float(info.get(key))
                if energy is not None:
                    break

        if arrays:
            raise FastParserUnsupported("ExtXYZ contains unsupported arrays")

        return FrameData(
            positions=positions,
            numbers=numbers,
            symbols=symbols,
            cell=cell,
            pbc=pbc,
            tags=tags,
            fixed_indices=fixed_indices,
            energy=energy,
            fmax=fmax,
        )

    def _read_xsd_frame(self, path: Path) -> FrameData:
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            atom_tree_root = root.find("AtomisticTreeRoot")
            if atom_tree_root is None:
                raise FastParserUnsupported("XSD missing AtomisticTreeRoot")

            symmetry_system = atom_tree_root.find("SymmetrySystem")
            if symmetry_system is None:
                raise FastParserUnsupported("Non-periodic XSD is delegated to ASE")
            mapping_set = symmetry_system.find("MappingSet")
            mapping_family = None if mapping_set is None else mapping_set.find("MappingFamily")
            system = None if mapping_family is None else mapping_family.find("IdentityMapping")
            if system is None:
                raise FastParserUnsupported("XSD periodic mapping structure is unsupported")

            cell_rows: list[list[float]] = []
            scaled_positions: list[list[float]] = []
            symbols: list[str] = []
            numbers: list[int] = []
            atom_id_to_index: dict[str, int] = {}
            fixed_indices: set[int] = set()
            restricted_object_ids: set[str] = set()

            for node in system:
                if node.tag == "Atom3d":
                    atom_index = len(symbols)
                    atom_id = node.get("ID")
                    if atom_id:
                        atom_id_to_index[atom_id] = atom_index
                    symbol_raw = node.get("Components")
                    if not symbol_raw:
                        raise FastParserUnsupported("XSD atom symbol is missing")
                    symbol = _normalize_symbol(symbol_raw)
                    try:
                        number = _atomic_number_from_normalized_symbol(symbol)
                    except ValueError as exc:
                        raise FastParserUnsupported("XSD contains unsupported atom symbols") from exc
                    xyz = node.get("XYZ")
                    if xyz:
                        coords = [float(value) for value in xyz.split(",")]
                    else:
                        coords = [0.0, 0.0, 0.0]
                    if len(coords) != 3:
                        raise FastParserUnsupported("XSD atom coordinates are invalid")
                    symbols.append(symbol)
                    numbers.append(number)
                    scaled_positions.append(coords)
                    if _xsd_restricts_full_position(node.get("RestrictedProperties")):
                        fixed_indices.add(atom_index)
                elif node.tag == "SpaceGroup":
                    for key in ("AVector", "BVector", "CVector"):
                        vector = node.get(key)
                        if vector is None:
                            raise FastParserUnsupported("XSD cell vector is missing")
                        coords = [float(value) for value in vector.split(",")]
                        if len(coords) != 3:
                            raise FastParserUnsupported("XSD cell vector is invalid")
                        cell_rows.append(coords)
                elif node.tag == "CompleteRestriction":
                    if _xsd_restricts_full_position(node.get("RestrictsProperties")):
                        restricted_object_ids.update(_parse_xsd_restricts_objects(node.get("RestrictsObjects")))

            if len(cell_rows) != 3 or not symbols:
                raise FastParserUnsupported("XSD periodic frame is incomplete")

            for atom_id in restricted_object_ids:
                atom_index = atom_id_to_index.get(atom_id)
                if atom_index is not None:
                    fixed_indices.add(atom_index)

            cell = np.asarray(cell_rows, dtype="<f4")
            scaled = np.asarray(scaled_positions, dtype=np.float64)
            positions = np.asarray(scaled @ cell.astype(np.float64), dtype="<f4")
            return FrameData(
                positions=positions,
                numbers=np.asarray(numbers, dtype=np.int32),
                symbols=tuple(symbols),
                cell=cell,
                pbc=(True, True, True),
                fixed_indices=tuple(sorted(fixed_indices)),
            )
        except FastParserUnsupported:
            raise
        except Exception as exc:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE could not parse {path.name}: {exc}", 400) from exc

    def _validate_atoms(self, atoms: Atoms, path: Path) -> None:
        atom_count = len(atoms)
        if atom_count <= 0:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE found an empty frame in {path.name}", 400)
        if atom_count > self.settings.viewer.ase.max_atoms:
            raise AppError(
                "STRUCTURE_TOO_MANY_ATOMS",
                f"Structure has {atom_count} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                413,
            )

    def _validate_frame_data(self, frame: FrameData, path: Path) -> None:
        atom_count = int(frame.positions.shape[0])
        if atom_count <= 0:
            raise AppError("STRUCTURE_PARSE_FAILED", f"ASE found an empty frame in {path.name}", 400)
        if atom_count > self.settings.viewer.ase.max_atoms:
            raise AppError(
                "STRUCTURE_TOO_MANY_ATOMS",
                f"Structure has {atom_count} atoms; limit is {self.settings.viewer.ase.max_atoms}",
                413,
            )
        if frame.positions.shape != (atom_count, 3):
            raise AppError("STRUCTURE_PARSE_FAILED", f"Invalid positions array in {path.name}", 400)
        if frame.numbers.shape[0] != atom_count or len(frame.symbols) != atom_count:
            raise AppError("STRUCTURE_PARSE_FAILED", f"Invalid atom metadata in {path.name}", 400)
        if frame.cell.shape != (3, 3):
            raise AppError("STRUCTURE_PARSE_FAILED", f"Invalid cell array in {path.name}", 400)

    def _validate_structure_frame(self, frame: Atoms | FrameData, path: Path) -> None:
        if isinstance(frame, FrameData):
            self._validate_frame_data(frame, path)
        else:
            self._validate_atoms(frame, path)

    def _response_frame_from_summary(self, summary: StructureSummary, frame_index: int) -> AseFrame:
        if summary.last_frame_data is not None:
            return self._frame_from_data(summary.last_frame_data, frame_index)
        if summary.last_atoms is None:
            raise AppError("STRUCTURE_PARSE_FAILED", "Structure summary has no frame data", 400)
        return self._frame_from_atoms(summary.last_atoms, frame_index)

    def _frame_response(self, frame: Atoms | FrameData, frame_index: int) -> AseFrame:
        if isinstance(frame, FrameData):
            return self._frame_from_data(frame, frame_index)
        return self._frame_from_atoms(frame, frame_index)

    def _frame_from_data(self, frame: FrameData, frame_index: int) -> AseFrame:
        digits = self.settings.viewer.ase.json_round_digits
        tags = frame.tags
        if tags is None:
            tag_values = [0] * len(frame.symbols)
        else:
            tag_values = [int(value) for value in np.asarray(tags, dtype=np.int32).tolist()]

        return AseFrame(
            frame_index=frame_index,
            positions=_rounded_float32_list(frame.positions, digits),
            cell=_rounded_float32_list(frame.cell, digits),
            pbc=[bool(value) for value in frame.pbc],
            tags=tag_values,
            fixed_indices=[int(value) for value in frame.fixed_indices],
            energy=frame.energy,
            fmax=frame.fmax,
            symbols=list(frame.symbols),
            numbers=[int(value) for value in np.asarray(frame.numbers, dtype=np.int32).tolist()],
        )

    def _frame_from_atoms(self, atoms: Atoms, frame_index: int) -> AseFrame:
        digits = self.settings.viewer.ase.json_round_digits
        positions = _rounded_float32_list(atoms.get_positions(), digits)
        cell = _rounded_float32_list(atoms.cell.array, digits)
        energy, fmax = _extract_energy_fmax(atoms)

        return AseFrame(
            frame_index=frame_index,
            positions=positions,
            cell=cell,
            pbc=[bool(value) for value in atoms.get_pbc()],
            tags=[int(value) for value in np.asarray(atoms.get_tags(), dtype=np.int32).tolist()],
            fixed_indices=_extract_fixed_indices(atoms),
            energy=energy,
            fmax=fmax,
            symbols=atoms.get_chemical_symbols(),
            numbers=[int(value) for value in atoms.get_atomic_numbers()],
        )

    def _build_binary_payload(
        self,
        *,
        path: Path,
        frames: list[Atoms],
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        t_normalize_0 = _profile_start(profile)
        normalized = [_normalize_atoms_atom_level(frame) for frame in frames]
        _profile_add_ms(profile, "normalize_atoms_ms", t_normalize_0)
        return self._build_binary_payload_from_normalized(
            path=path,
            frames=normalized,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=detected_format,
            profile=profile,
        )

    def _build_binary_payload_from_data(
        self,
        *,
        path: Path,
        frames: list[FrameData],
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        t_normalize_0 = _profile_start(profile)
        normalized = [_normalize_frame_data_atom_level(frame) for frame in frames]
        _profile_add_ms(profile, "normalize_frame_data_ms", t_normalize_0)
        return self._build_binary_payload_from_normalized(
            path=path,
            frames=normalized,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=detected_format,
            profile=profile,
        )

    def _build_fixed_binary_payload_from_arrays(
        self,
        *,
        path: Path,
        arrays: StructureChunkArrays,
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        _profile_set(profile, "topology", "fixed")
        count = arrays.count
        n_atoms = int(arrays.positions.shape[1])
        binary_arrays: dict[str, dict[str, Any]] = {}
        binary_parts: list[bytes] = []
        offset = 0

        def add_array(name: str, array: np.ndarray, *, alignment: int) -> None:
            nonlocal offset
            if alignment > 1:
                padding_size = (alignment - (offset % alignment)) % alignment
                if padding_size:
                    binary_parts.append(b"\0" * padding_size)
                    offset += padding_size
            contiguous = np.ascontiguousarray(array)
            t_add_array_0 = _profile_start(profile)
            data = contiguous.tobytes()
            _profile_add_ms(profile, "add_array_tobytes_ms", t_add_array_0)
            binary_arrays[name] = {
                "offset": offset,
                "byte_length": len(data),
                "shape": list(contiguous.shape),
            }
            binary_parts.append(data)
            offset += len(data)

        add_array("positions", arrays.positions, alignment=4)
        add_array("cells", arrays.cells, alignment=4)
        add_array("tags", arrays.tags, alignment=4)
        add_array("energy", arrays.energy, alignment=4)
        add_array("fmax", arrays.fmax, alignment=4)
        add_array("fixed_mask", arrays.fixed_mask, alignment=1)

        header = {
            "version": 1,
            "dtype": "float32-le",
            "int_dtype": "int32-le",
            "start": start,
            "count": count,
            "n_frames_total": n_frames_total,
            "n_atoms": n_atoms,
            "topology_stable": True,
            "path": str(path),
            "name": path.name,
            "format": detected_format,
            "symbols": list(arrays.symbols),
            "numbers": [int(value) for value in np.asarray(arrays.numbers, dtype=np.int32).tolist()],
            "pbc": [bool(value) for value in arrays.pbc],
            "arrays": binary_arrays,
            "nan_means_null": ["energy", "fmax"],
        }
        t_header_0 = _profile_start(profile)
        header_bytes = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header_padding = b"\0" * ((4 - (len(header_bytes) % 4)) % 4)
        _profile_add_ms(profile, "header_json_ms", t_header_0)

        t_join_0 = _profile_start(profile)
        payload = (
            STRUCTURE_BINARY_MAGIC
            + struct.pack("<I", len(header_bytes))
            + header_bytes
            + header_padding
            + b"".join(binary_parts)
        )
        _profile_add_ms(profile, "payload_join_ms", t_join_0)
        _profile_set(profile, "payload_bytes", len(payload))
        return payload

    @staticmethod
    def _are_normalized_frames_fixed_topology(frames: list[dict]) -> tuple[bool, int | None, tuple[int, ...] | None]:
        if not frames:
            return True, 0, ()
        first_n = frames[0]["atom_count"]
        first_numbers = frames[0]["numbers"]
        for frame in frames[1:]:
            if frame["atom_count"] != first_n or frame["numbers"] != first_numbers:
                return False, None, None
        return True, first_n, first_numbers

    def _build_binary_payload_from_normalized(
        self,
        *,
        path: Path,
        frames: list[dict],
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        if not frames:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", "Requested frame chunk is empty", 404)

        t_topology_0 = _profile_start(profile)
        is_fixed, n_atoms, first_numbers = self._are_normalized_frames_fixed_topology(frames)
        _profile_add_ms(profile, "topology_check_ms", t_topology_0)
        count = len(frames)
        _profile_set(profile, "topology", "fixed" if is_fixed else "variable")

        if is_fixed and n_atoms is not None:
            positions = np.empty((count, n_atoms, 3), dtype="<f4")
            cells = np.empty((count, 3, 3), dtype="<f4")
            tags = np.empty((count, n_atoms), dtype="<i4")
            fixed_mask = np.zeros((count, n_atoms), dtype=np.uint8)
            energy = np.full((count,), np.nan, dtype="<f4")
            fmax = np.full((count,), np.nan, dtype="<f4")

            t_array_fill_0 = _profile_start(profile)
            for local_index, frame in enumerate(frames):
                positions[local_index] = frame["positions"]
                cells[local_index] = frame["cell"]
                tags[local_index] = frame["tags"]
                for atom_index in frame["fixed_indices"]:
                    fixed_mask[local_index, atom_index] = 1
                if frame["energy"] is not None:
                    energy[local_index] = frame["energy"]
                if frame["fmax"] is not None:
                    fmax[local_index] = frame["fmax"]
            _profile_add_ms(profile, "array_fill_ms", t_array_fill_0)

            arrays: dict[str, dict[str, Any]] = {}
            binary_parts: list[bytes] = []
            offset = 0

            def add_array(name: str, array: np.ndarray, *, alignment: int) -> None:
                nonlocal offset
                if alignment > 1:
                    padding_size = (alignment - (offset % alignment)) % alignment
                    if padding_size:
                        binary_parts.append(b"\0" * padding_size)
                        offset += padding_size
                contiguous = np.ascontiguousarray(array)
                t_add_array_0 = _profile_start(profile)
                data = contiguous.tobytes()
                _profile_add_ms(profile, "add_array_tobytes_ms", t_add_array_0)
                arrays[name] = {
                    "offset": offset,
                    "byte_length": len(data),
                    "shape": list(contiguous.shape),
                }
                binary_parts.append(data)
                offset += len(data)

            add_array("positions", positions, alignment=4)
            add_array("cells", cells, alignment=4)
            add_array("tags", tags, alignment=4)
            add_array("energy", energy, alignment=4)
            add_array("fmax", fmax, alignment=4)
            add_array("fixed_mask", fixed_mask, alignment=1)

            header = {
                "version": 1,
                "dtype": "float32-le",
                "int_dtype": "int32-le",
                "start": start,
                "count": count,
                "n_frames_total": n_frames_total,
                "n_atoms": n_atoms,
                "topology_stable": True,
                "path": str(path),
                "name": path.name,
                "format": detected_format,
                "symbols": frames[0]["symbols"],
                "numbers": [int(value) for value in first_numbers],
                "pbc": [bool(value) for value in frames[0]["pbc"]],
                "arrays": arrays,
                "nan_means_null": ["energy", "fmax"],
            }
            t_header_0 = _profile_start(profile)
            header_bytes = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            header_padding = b"\0" * ((4 - (len(header_bytes) % 4)) % 4)
            _profile_add_ms(profile, "header_json_ms", t_header_0)

            t_join_0 = _profile_start(profile)
            payload = (
                STRUCTURE_BINARY_MAGIC
                + struct.pack("<I", len(header_bytes))
                + header_bytes
                + header_padding
                + b"".join(binary_parts)
            )
            _profile_add_ms(profile, "payload_join_ms", t_join_0)
            _profile_set(profile, "payload_bytes", len(payload))
            return payload

        # Variable-topology path
        return self._build_variable_binary_payload(
            path=path,
            frames=frames,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=detected_format,
        )

    def _build_binary_payload_from_frames(
        self,
        *,
        path: Path,
        frames: list[Atoms | FrameData],
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        if not frames:
            raise AppError("FRAME_INDEX_OUT_OF_RANGE", "Requested frame chunk is empty", 404)
        if isinstance(frames[0], FrameData):
            frame_data = []
            for frame in frames:
                if not isinstance(frame, FrameData):
                    raise AppError("STRUCTURE_PARSE_FAILED", "Mixed frame data is not supported", 400)
                frame_data.append(frame)
            return self._build_binary_payload_from_data(
                path=path,
                frames=frame_data,
                start=start,
                n_frames_total=n_frames_total,
                detected_format=detected_format,
                profile=profile,
            )

        atoms_frames = []
        for frame in frames:
            if isinstance(frame, FrameData):
                raise AppError("STRUCTURE_PARSE_FAILED", "Mixed frame data is not supported", 400)
            atoms_frames.append(frame)
        return self._build_binary_payload(
            path=path,
            frames=atoms_frames,
            start=start,
            n_frames_total=n_frames_total,
            detected_format=detected_format,
            profile=profile,
        )

    def _build_variable_binary_payload(
        self,
        *,
        path: Path,
        frames: list[dict],
        start: int,
        n_frames_total: int,
        detected_format: str | None,
        profile: dict[str, Any] | None = None,
    ) -> bytes:
        _profile_set(profile, "topology", "variable")
        count = len(frames)
        frame_atom_counts = np.empty((count,), dtype=np.int32)
        total_atoms = 0
        for index, frame in enumerate(frames):
            n = frame["atom_count"]
            frame_atom_counts[index] = n
            total_atoms += n

        if total_atoms <= 0:
            raise AppError("STRUCTURE_PARSE_FAILED", "Variable-topology chunk has no atoms", 400)

        positions = np.empty((total_atoms, 3), dtype="<f4")
        numbers = np.empty((total_atoms,), dtype=np.int32)
        tags = np.empty((total_atoms,), dtype="<i4")
        fixed_mask = np.zeros((total_atoms,), dtype=np.uint8)
        cells = np.empty((count, 3, 3), dtype="<f4")
        pbc = np.empty((count, 3), dtype=np.uint8)
        energy = np.full((count,), np.nan, dtype="<f4")
        fmax = np.full((count,), np.nan, dtype="<f4")

        t_array_fill_0 = _profile_start(profile)
        offset = 0
        for local_index, frame in enumerate(frames):
            n = frame["atom_count"]
            end = offset + n
            positions[offset:end] = frame["positions"]
            numbers[offset:end] = frame["numbers_array"]
            tags[offset:end] = frame["tags"]
            for atom_index in frame["fixed_indices"]:
                fixed_mask[offset + atom_index] = 1
            cells[local_index] = frame["cell"]
            pbc[local_index] = [1 if v else 0 for v in frame["pbc"]]
            if frame["energy"] is not None:
                energy[local_index] = frame["energy"]
            if frame["fmax"] is not None:
                fmax[local_index] = frame["fmax"]
            offset = end
        _profile_add_ms(profile, "array_fill_ms", t_array_fill_0)

        arrays: dict[str, dict[str, Any]] = {}
        binary_parts: list[bytes] = []
        array_offset = 0

        def add_array(name: str, array: np.ndarray, *, alignment: int) -> None:
            nonlocal array_offset
            if alignment > 1:
                padding_size = (alignment - (array_offset % alignment)) % alignment
                if padding_size:
                    binary_parts.append(b"\0" * padding_size)
                    array_offset += padding_size
            contiguous = np.ascontiguousarray(array)
            t_add_array_0 = _profile_start(profile)
            data = contiguous.tobytes()
            _profile_add_ms(profile, "add_array_tobytes_ms", t_add_array_0)
            arrays[name] = {
                "offset": array_offset,
                "byte_length": len(data),
                "shape": list(contiguous.shape),
            }
            binary_parts.append(data)
            array_offset += len(data)

        add_array("frame_atom_counts", frame_atom_counts, alignment=4)
        add_array("positions", positions, alignment=4)
        add_array("numbers", numbers, alignment=4)
        add_array("tags", tags, alignment=4)
        add_array("fixed_mask", fixed_mask, alignment=1)
        add_array("cells", cells, alignment=4)
        add_array("pbc", pbc, alignment=1)
        add_array("energy", energy, alignment=4)
        add_array("fmax", fmax, alignment=4)

        header = {
            "version": 1,
            "dtype": "float32-le",
            "int_dtype": "int32-le",
            "frame_encoding": "variable-atoms-v1",
            "start": start,
            "count": count,
            "n_frames_total": n_frames_total,
            "n_atoms": 0,
            "topology_stable": False,
            "path": str(path),
            "name": path.name,
            "format": detected_format,
            "symbols": [],
            "numbers": [],
            "pbc": [False, False, False],
            "arrays": arrays,
            "nan_means_null": ["energy", "fmax"],
        }
        t_header_0 = _profile_start(profile)
        header_bytes = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header_padding = b"\0" * ((4 - (len(header_bytes) % 4)) % 4)
        _profile_add_ms(profile, "header_json_ms", t_header_0)

        t_join_0 = _profile_start(profile)
        payload = (
            STRUCTURE_BINARY_MAGIC
            + struct.pack("<I", len(header_bytes))
            + header_bytes
            + header_padding
            + b"".join(binary_parts)
        )
        _profile_add_ms(profile, "payload_join_ms", t_join_0)
        _profile_set(profile, "payload_bytes", len(payload))
        return payload


def _normalize_atoms_atom_level(atoms: Atoms) -> dict[str, Any]:
    numbers_array = np.asarray(atoms.get_atomic_numbers(), dtype=np.int32)
    n_atoms = len(atoms)
    fixed_indices = tuple(_extract_fixed_indices(atoms))
    energy, fmax = _extract_energy_fmax(atoms)
    return {
        "atom_count": n_atoms,
        "positions": np.asarray(atoms.get_positions(), dtype="<f4"),
        "numbers": tuple(int(value) for value in numbers_array.tolist()),
        "numbers_array": numbers_array,
        "symbols": atoms.get_chemical_symbols(),
        "cell": np.asarray(atoms.cell.array, dtype="<f4"),
        "pbc": tuple(bool(value) for value in atoms.get_pbc()),
        "tags": np.asarray(atoms.get_tags(), dtype="<i4"),
        "fixed_indices": fixed_indices,
        "energy": energy,
        "fmax": fmax,
    }


def _normalize_frame_data_atom_level(frame: FrameData) -> dict[str, Any]:
    numbers_array = np.asarray(frame.numbers, dtype=np.int32)
    n_atoms = int(numbers_array.shape[0])
    tags = np.zeros((n_atoms,), dtype="<i4") if frame.tags is None else np.asarray(frame.tags, dtype="<i4")
    return {
        "atom_count": n_atoms,
        "positions": np.asarray(frame.positions, dtype="<f4"),
        "numbers": tuple(int(value) for value in numbers_array.tolist()),
        "numbers_array": numbers_array,
        "symbols": list(frame.symbols),
        "cell": np.asarray(frame.cell, dtype="<f4"),
        "pbc": tuple(bool(value) for value in frame.pbc),
        "tags": tags,
        "fixed_indices": tuple(int(value) for value in frame.fixed_indices),
        "energy": frame.energy,
        "fmax": frame.fmax,
    }


def _frame_atom_count(frame: Atoms | FrameData) -> int:
    if isinstance(frame, FrameData):
        return int(frame.positions.shape[0])
    return len(frame)


def _frame_numbers(frame: Atoms | FrameData) -> tuple[int, ...]:
    if isinstance(frame, FrameData):
        return tuple(int(value) for value in np.asarray(frame.numbers, dtype=np.int32).tolist())
    return tuple(int(value) for value in frame.get_atomic_numbers())


def _frame_data_from_db_row(row: Any) -> FrameData:
    numbers = np.asarray(row.numbers, dtype=np.int32)
    positions = np.asarray(row.positions, dtype="<f4")
    cell = np.asarray(row.cell, dtype="<f4")
    pbc_values = np.asarray(row.pbc, dtype=bool).reshape(-1)
    if pbc_values.size != 3:
        raise ValueError("ASE DB row has invalid pbc")

    tags_value = row.get("tags")
    tags = None if tags_value is None else np.asarray(tags_value, dtype=np.int32)
    fixed_indices = tuple(_extract_fixed_indices_from_constraints(row.constraints, int(numbers.shape[0])))
    energy = _safe_float(row.get("energy"))
    if energy is None:
        energy = _safe_float(row.get("free_energy"))
    fmax = _extract_fmax_from_forces(row.get("forces"), int(numbers.shape[0]), fixed_indices)

    return FrameData(
        positions=positions,
        numbers=numbers,
        symbols=tuple(chemical_symbols[int(value)] for value in numbers),
        cell=cell,
        pbc=tuple(bool(value) for value in pbc_values.tolist()),  # type: ignore[arg-type]
        tags=tags,
        fixed_indices=fixed_indices,
        energy=energy,
        fmax=fmax,
    )


def _frame_data_from_trajectory(trajectory: Any, index: int) -> FrameData:
    backend = trajectory.backend[index]
    if "numbers" in backend:
        numbers = np.asarray(backend.numbers, dtype=np.int32)
        pbc_values = np.asarray(backend.pbc, dtype=bool).reshape(-1)
        constraints = backend.get("constraints", "[]")
    else:
        numbers = np.asarray(trajectory.numbers, dtype=np.int32)
        pbc_values = np.asarray(trajectory.pbc, dtype=bool).reshape(-1)
        constraints = trajectory.constraints
    if pbc_values.size != 3:
        raise ValueError("ASE trajectory frame has invalid pbc")

    tags_value = backend.get("tags")
    tags = None if tags_value is None else np.asarray(tags_value, dtype=np.int32)
    decoded_constraints = _decode_constraints(constraints)
    fixed_indices = tuple(_extract_fixed_indices_from_constraints(decoded_constraints, int(numbers.shape[0])))

    energy = None
    forces = None
    if "calculator" in backend:
        calculator = backend.calculator
        for key in ("energy", "free_energy"):
            if key in calculator:
                energy = _safe_float(calculator.get(key))
                if energy is not None:
                    break
        if "forces" in calculator:
            forces = calculator.get("forces")

    positions = np.asarray(backend.positions, dtype="<f4")
    cell = np.asarray(backend.cell, dtype="<f4")
    if energy is not None and decoded_constraints:
        constraint_atoms = Atoms(
            positions=np.asarray(backend.positions, dtype=np.float64),
            numbers=numbers,
            cell=np.asarray(backend.cell, dtype=np.float64),
            pbc=pbc_values,
            constraint=decoded_constraints,
        )
        energy = _apply_constraint_energy_correction(energy, constraint_atoms)

    return FrameData(
        positions=positions,
        numbers=numbers,
        symbols=tuple(chemical_symbols[int(value)] for value in numbers),
        cell=cell,
        pbc=tuple(bool(value) for value in pbc_values.tolist()),  # type: ignore[arg-type]
        tags=tags,
        fixed_indices=fixed_indices,
        energy=energy,
        fmax=_extract_fmax_from_forces(forces, int(numbers.shape[0]), fixed_indices),
    )


def _rounded_float32_list(values: Any, digits: int) -> list[list[float]]:
    array = np.asarray(values, dtype=np.float32).astype(np.float64)
    return np.round(array, digits).tolist()


def _normalized_format(fmt: str | None) -> str | None:
    if fmt is None:
        return None
    normalized = fmt.strip().lower()
    return normalized or None


def _is_xyz_like_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized in {"xyz", "extxyz"}
    return path.suffix.lower() in {".xyz", ".extxyz"}


def _is_native_indexed_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized in {"traj", "db"}
    return path.suffix.lower() in {".traj", ".db"}


def _is_vasp_xdatcar_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized in {"vasp-xdatcar", "xdatcar"}
    return "XDATCAR" in path.name.upper()


def _is_vasp_outcar_format(path: Path, fmt: str | None) -> bool:
    if fmt is not None:
        return fmt.lower() in {"vasp-out", "outcar"}
    return "OUTCAR" in path.name.upper()


def _outcar_line_sets_md_mode(line: str) -> bool:
    if "IBRION" not in line:
        return False
    match = re.search(r"\bIBRION\s*=\s*(-?\d+)", line, re.IGNORECASE)
    return match is not None and match.group(1) == "0"


def _extract_outcar_energy(line: str) -> float | None:
    if "TOTEN" not in line:
        return None
    match = re.search(r"\bTOTEN\s*=\s*(" + _FLOAT_PATTERN + r")", line, re.IGNORECASE)
    if match is None:
        return None
    return _safe_float(match.group(1).replace("D", "E").replace("d", "e"))


def _extract_outcar_energy_zero(line: str) -> float | None:
    if "energy(sigma->0)" not in line:
        return None
    match = re.search(r"energy\(sigma->0\)\s*=\s*(" + _FLOAT_PATTERN + r")", line, re.IGNORECASE)
    if match is None:
        return None
    return _safe_float(match.group(1).replace("D", "E").replace("d", "e"))


def _parse_outcar_potcar_symbol(line: str) -> str | None:
    if "POTCAR:" not in line:
        return None
    parts = line.strip().split()
    if "1/r" in line and len(parts) > 1:
        raw = parts[1]
    elif len(parts) > 2:
        raw = parts[2]
    else:
        return None
    raw = raw.split("_")[0]
    symbol = "".join(ch for ch in raw if ch.isalpha())
    if not symbol:
        return None
    normalized = _normalize_symbol(symbol)
    return normalized if normalized in atomic_numbers else None


def _outcar_species_from_potcar_lines(symbols: list[str]) -> list[str]:
    if not symbols:
        return []
    half = sum(divmod(len(symbols), 2))
    return symbols[:half]


def _outcar_symbols_from_header(species: list[str], ion_types: list[int], n_atoms: int) -> tuple[str, ...]:
    if species and ion_types and len(species) == len(ion_types):
        symbols = [symbol for symbol, count in zip(species, ion_types) for _ in range(count)]
        if len(symbols) == n_atoms:
            return tuple(symbols)
    raise FastParserUnsupported("OUTCAR header does not contain matching species and ion counts")


def _parse_outcar_ions_per_type(line: str) -> list[int] | None:
    if "ions per type" not in line:
        return None
    try:
        return [int(value) for value in line.split("=")[1].split()]
    except (IndexError, ValueError):
        return None


def _read_outcar_cell_after_lattice_header(handle: Any) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None:
    rows: list[tuple[float, float, float]] = []
    for _ in range(3):
        line = handle.readline()
        fields = line.split()
        if len(fields) < 3:
            return None
        try:
            rows.append((float(fields[0]), float(fields[1]), float(fields[2])))
        except ValueError:
            return None
    return tuple(rows)  # type: ignore[return-value]


def _read_outcar_fixed_indices_from_neighbor(path: Path, n_atoms: int) -> tuple[tuple[int, ...], str | None]:
    for filename in ("CONTCAR", "POSCAR"):
        constraint_path = path.parent / filename
        if not constraint_path.is_file():
            continue
        try:
            atoms = read(str(constraint_path), format="vasp", do_not_split_by_at_sign=True)
            if isinstance(atoms, list):
                atoms = atoms[0] if atoms else None
            if not isinstance(atoms, Atoms):
                return (), "vasp_outcar_constraints_unreadable"
            fixed_indices = tuple(_extract_fixed_indices(atoms))
            if len(atoms) != n_atoms:
                return (), "vasp_outcar_constraints_unreadable"
            return fixed_indices, None
        except Exception:
            return (), "vasp_outcar_constraints_unreadable"
    return (), "vasp_outcar_constraints_missing"


def _outcar_fmax_from_forces(
    forces: list[list[float]],
    n_atoms: int,
    fixed_indices: tuple[int, ...] | list[int] = (),
) -> float | None:
    if len(forces) != n_atoms:
        return None
    return _extract_fmax_from_forces(np.asarray(forces, dtype=np.float64), n_atoms, fixed_indices)


def _is_vasp_poscar_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized in {"vasp", "poscar", "contcar"}
    upper_name = path.name.upper()
    return upper_name in {"POSCAR", "CONTCAR"} or path.suffix.lower() == ".vasp"


def _is_xsd_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized == "xsd"
    return path.suffix.lower() == ".xsd"


def _xsd_restricts_full_position(value: str | None) -> bool:
    if not value:
        return False
    properties = [part.strip().lower() for part in value.split(",") if part.strip()]
    return "fractionalxyz" in properties or "xyz" in properties


def _parse_xsd_restricts_objects(value: str | None) -> set[str]:
    if not value:
        return set()
    _, _, tail = value.partition(":")
    body = tail or value
    object_ids: set[str] = set()
    for part in body.split(","):
        item = part.strip()
        if not item:
            continue
        if "+" in item:
            item = item.split("+", 1)[0].strip()
        if item:
            object_ids.add(item)
    return object_ids


def _is_dmol_arc_format(path: Path, fmt: str | None) -> bool:
    normalized = _normalized_format(fmt)
    if normalized is not None:
        return normalized in {"dmol-arc", "arc"}
    return path.suffix.lower() == ".arc"


def _detected_native_indexed_format(path: Path, fmt: str | None) -> str:
    normalized = _normalized_format(fmt)
    if normalized in {"traj", "db"}:
        return normalized
    if path.suffix.lower() == ".db":
        return "db"
    return "traj"


def _detected_xyz_like_format(path: Path, fmt: str | None) -> str:
    normalized = _normalized_format(fmt)
    if normalized == "extxyz":
        return "extxyz"
    if normalized == "xyz":
        return "xyz"
    if path.suffix.lower() == ".extxyz":
        return "extxyz"
    return "xyz"


def _looks_like_extxyz_comment(comment: str) -> bool:
    return "Properties=" in comment or "Lattice=" in comment


def _xyz_like_file_has_extxyz_marker(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(1_048_576)
    except OSError:
        return False
    return b"Lattice=" in head or b"Properties=" in head


def _extract_plain_xyz_comment_energy(comment: str) -> float | None:
    match = _PLAIN_XYZ_ENERGY_RE.search(comment)
    if match is None:
        return None
    return _safe_float(match.group(1).replace("D", "E").replace("d", "e"))


def _extract_plain_xyz_comment_fmax(comment: str) -> float | None:
    match = _PLAIN_XYZ_FMAX_RE.search(comment)
    if match is None:
        return None
    return _safe_float(match.group(1).replace("D", "E").replace("d", "e"))


def _stable_plain_xyz_frame_stride(index: FastXyzIndex) -> int | None:
    if not index.frame_lengths or not index.atom_counts:
        return None
    first_length = index.frame_lengths[0]
    first_atom_count = index.atom_counts[0]
    if first_length <= 0 or first_atom_count <= 0:
        return None
    if any(length != first_length for length in index.frame_lengths):
        return None
    if any(atom_count != first_atom_count for atom_count in index.atom_counts):
        return None
    if not index.complete:
        if index.n_frames is None or index.last_frame_offset is None:
            return None
        expected_last_offset = index.frame_offsets[0] + (index.n_frames - 1) * first_length
        if expected_last_offset != index.last_frame_offset:
            return None
    return first_length


def _split_plain_xyz_atom_line(line: str) -> tuple[str, str]:
    parts = line.strip().split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError("Invalid atom line")
    return parts[0], parts[1]


def _plain_xyz_symbol_and_coord_start(line: str) -> tuple[str, int]:
    stripped_start = len(line) - len(line.lstrip())
    cursor = stripped_start
    while cursor < len(line) and not line[cursor].isspace():
        cursor += 1
    if cursor == stripped_start:
        raise ValueError("Invalid atom line")
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    if cursor >= len(line):
        raise ValueError("Invalid atom line")
    return line[stripped_start:cursor].strip(), cursor


def _is_force_property_name(name: str) -> bool:
    normalized = name.lower()
    return normalized == "forces" or normalized == "force" or normalized.endswith("_forces") or normalized.endswith("_force")


def _pop_prefixed_property_array(arrays: dict[str, np.ndarray], suffix: str) -> np.ndarray | None:
    suffix = suffix.lower()
    for key in list(arrays):
        normalized = key.lower()
        if normalized == suffix or normalized.endswith(f"_{suffix}"):
            return arrays.pop(key)
    return None


def _normalize_symbol(symbol: str) -> str:
    return symbol.lower().capitalize()


def _atomic_number_from_symbol(symbol: str) -> int:
    return _atomic_number_from_normalized_symbol(_normalize_symbol(symbol))


def _atomic_number_from_normalized_symbol(symbol: str) -> int:
    number = atomic_numbers.get(symbol)
    if number is None or number <= 0 or symbol not in chemical_symbols:
        raise ValueError(f"Unknown chemical symbol: {symbol}")
    return int(number)


def _parse_vasp_scaling_factor(line: str) -> np.ndarray:
    values = np.array([float(value) for value in line.split()[:3]], dtype=np.float64)
    if values.size not in {1, 3}:
        raise ValueError("VASP scaling factor must contain one or three values")
    if values.size == 3 and any(value < 0.0 for value in values):
        raise ValueError("VASP three-value scaling factors must be positive")
    return values


def _xdatcar_frame_number_from_marker(line: str) -> int | None:
    match = re.search(r"\bDirect\s+configuration\s*=\s*(\d+)", line, re.IGNORECASE)
    if match is None:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _extract_energy_fmax(atoms: Atoms) -> tuple[float | None, float | None]:
    calc = getattr(atoms, "calc", None)
    results = getattr(calc, "results", {}) or {}

    energy = None
    for key in ("energy", "free_energy"):
        if key in results:
            energy = _safe_float(results.get(key))
            if energy is not None:
                break
    energy = _apply_constraint_energy_correction(energy, atoms)

    forces = results.get("forces")
    if forces is None:
        return energy, None

    fixed_indices = _extract_fixed_indices(atoms)
    return energy, _extract_fmax_from_forces(forces, len(atoms), fixed_indices)


def _apply_constraint_energy_correction(energy: float | None, atoms: Atoms) -> float | None:
    if energy is None:
        return None
    corrected = energy
    for constraint in getattr(atoms, "constraints", []) or []:
        adjust = getattr(constraint, "adjust_potential_energy", None)
        if adjust is None:
            continue
        try:
            correction = _safe_float(adjust(atoms))
        except Exception:
            continue
        if correction is not None:
            corrected += correction
    return corrected


def _extract_fmax_from_forces(forces: Any, n_atoms: int, fixed_indices: tuple[int, ...] | list[int] = ()) -> float | None:
    if forces is None:
        return None

    force_array = np.asarray(forces, dtype=np.float32)
    if (
        force_array.ndim != 2
        or force_array.shape[1] != 3
        or force_array.shape[0] != n_atoms
        or force_array.size == 0
    ):
        return None

    if fixed_indices:
        fixed_index_array = np.asarray(fixed_indices, dtype=np.intp).reshape(-1)
        movable_mask = np.ones(force_array.shape[0], dtype=bool)
        movable_mask[fixed_index_array] = False
        force_array = force_array[movable_mask]
        if force_array.size == 0:
            return None

    fmax = float(np.max(np.linalg.norm(force_array, axis=1)))
    if not math.isfinite(fmax):
        return None
    return fmax


def _extract_fixed_indices(atoms_image: Atoms) -> list[int]:
    return _extract_fixed_indices_from_constraints(getattr(atoms_image, "constraints", []) or [], len(atoms_image))


def _decode_constraints(constraints: Any) -> Any:
    if isinstance(constraints, str):
        try:
            constraints = decode(constraints)
        except Exception:
            return []
    decoded: list[Any] = []
    for constraint in constraints or []:
        if isinstance(constraint, dict):
            try:
                decoded.append(dict2constraint(constraint))
            except Exception:
                decoded.append(constraint)
        else:
            decoded.append(constraint)
    return decoded


def _extract_fixed_indices_from_constraints(constraints: Any, n_atoms: int) -> list[int]:
    fixed: set[int] = set()

    for constraint in constraints or []:
        if isinstance(constraint, dict):
            if constraint.get("name") != "FixAtoms":
                continue
            kwargs = constraint.get("kwargs") or {}
            indices = kwargs.get("indices", kwargs.get("index", []))
        else:
            if constraint.__class__.__name__ != "FixAtoms":
                continue
            if hasattr(constraint, "get_indices"):
                indices = constraint.get_indices()
            else:
                indices = getattr(constraint, "index", [])
        for index in np.asarray(indices, dtype=np.int32).reshape(-1):
            atom_index = int(index)
            if 0 <= atom_index < n_atoms:
                fixed.add(atom_index)

    return sorted(fixed)
