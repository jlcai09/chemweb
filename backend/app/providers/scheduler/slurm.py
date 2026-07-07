from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.app.core.errors import AppError
from backend.app.core.security import validate_job_id
from backend.app.models.job import SubmitCommand, SubmitJobResponse
from backend.app.models.queue import (
    CancelJobResponse,
    QueueJobActionResponse,
    QueueItem,
    QueueJobDetailResponse,
    QueueResponse,
)
from backend.app.providers.scheduler.base import SchedulerProvider


class SlurmProvider(SchedulerProvider):
    scheduler_name = "slurm"

    def _missing(self, command: str) -> bool:
        return shutil.which(command) is None

    def list_jobs(self, current_user_only: bool = False) -> QueueResponse:
        if self._missing("squeue"):
            return QueueResponse(
                scheduler=self.scheduler_name,
                available=False,
                message="squeue command was not found on this host",
                items=[],
            )

        json_response = self._list_jobs_json(current_user_only)
        if json_response is not None:
            return json_response
        return self._list_jobs_text(current_user_only)

    def _list_jobs_json(self, current_user_only: bool) -> QueueResponse | None:
        command = _squeue_command(current_user_only, "--json")
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        jobs = payload.get("jobs", [])
        items = [self._queue_item_from_json(job) for job in jobs if isinstance(job, dict)]
        if items and (
            all(not item.workdir for item in items)
            or all(not item.time_used for item in items)
        ):
            return None
        return QueueResponse(scheduler=self.scheduler_name, items=items)

    def _queue_item_from_json(self, job: dict[str, Any]) -> QueueItem:
        resources = job.get("resources") or {}
        state = job.get("job_state") or job.get("state") or job.get("JobState") or ""
        if isinstance(state, list):
            state = ",".join(str(item) for item in state)
        partition = job.get("partition") or job.get("partition_name") or ""
        reason = job.get("state_reason") or job.get("reason") or job.get("Reason") or job.get("nodes") or ""
        time_obj = job.get("time") if isinstance(job.get("time"), dict) else {}
        return QueueItem(
            job_id=str(job.get("job_id") or job.get("id") or job.get("JobId") or ""),
            name=str(job.get("name") or job.get("job_name") or job.get("JobName") or ""),
            user=str(job.get("user_name") or job.get("user") or job.get("UserId") or ""),
            state=str(state),
            partition=str(partition or job.get("Partition") or ""),
            nodes=_int_or_none(_first_present(job.get("nodes"), resources.get("nodes"), job.get("NumNodes"))),
            cpus=_int_or_none(_first_present(job.get("cpus"), resources.get("cpus"), job.get("NumCPUs"))),
            time_used=_format_slurm_elapsed(
                _first_present(
                    _unwrap_slurm_json_value(job.get("time_used")),
                    _unwrap_slurm_json_value(job.get("elapsed_time")),
                    _unwrap_slurm_json_value(job.get("run_time")),
                    _unwrap_slurm_json_value(job.get("runtime")),
                    job.get("RunTime"),
                    _unwrap_slurm_json_value(job.get("elapsed")),
                    job.get("Elapsed"),
                    job.get("ElapsedRaw"),
                    _unwrap_slurm_json_value(time_obj.get("elapsed")),
                    _unwrap_slurm_json_value(time_obj.get("elapsed_time")),
                    _unwrap_slurm_json_value(time_obj.get("used")),
                )
            ),
            time_limit=_format_slurm_limit(
                _first_present(
                    _unwrap_slurm_json_value(job.get("time_limit")),
                    job.get("TimeLimit"),
                    _unwrap_slurm_json_value(time_obj.get("limit")),
                )
            ),
            reason=str(reason),
            workdir=(
                job.get("working_directory")
                or job.get("work_dir")
                or job.get("workdir")
                or job.get("current_working_directory")
                or job.get("WorkDir")
            ),
        )

    def _list_jobs_text(self, current_user_only: bool) -> QueueResponse:
        fmt = "%i|%j|%u|%T|%M|%l|%D|%C|%R|%P|%Z"
        command = _squeue_command(current_user_only, "-h", "-o", fmt)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AppError("SQUEUE_TIMEOUT", "squeue timed out", 504) from exc

        if result.returncode != 0:
            return QueueResponse(
                scheduler=self.scheduler_name,
                available=False,
                message=result.stderr.strip() or "squeue failed",
                items=[],
            )

        items: list[QueueItem] = []
        for line in result.stdout.splitlines():
            parts = line.split("|")
            if len(parts) < 10:
                continue
            job_id, name, user, state, used, limit, nodes, cpus, reason, partition = parts[:10]
            workdir = parts[10] if len(parts) > 10 else None
            items.append(
                QueueItem(
                    job_id=job_id.strip(),
                    name=name.strip(),
                    user=user.strip(),
                    state=state.strip(),
                    partition=partition.strip(),
                    nodes=_int_or_none(nodes),
                    cpus=_int_or_none(cpus),
                    time_used=used.strip(),
                    time_limit=limit.strip(),
                    reason=reason.strip(),
                    workdir=workdir.strip() if workdir else None,
                )
            )
        return QueueResponse(scheduler=self.scheduler_name, items=items)

    def job_detail(self, job_id: str) -> QueueJobDetailResponse:
        validate_job_id(job_id)
        if self._missing("scontrol"):
            raise AppError("SCONTROL_NOT_FOUND", "scontrol command was not found on this host", 503)

        result = subprocess.run(
            ["scontrol", "show", "job", job_id],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            raise AppError("JOB_DETAIL_FAILED", result.stderr.strip() or "scontrol failed", 502)
        detail = _normalize_slurm_detail(_parse_key_value_output(result.stdout))
        return QueueJobDetailResponse(job_id=job_id, detail=detail)

    def cancel_job(self, job_id: str) -> CancelJobResponse:
        validate_job_id(job_id)
        if self._missing("scancel"):
            raise AppError("SCANCEL_NOT_FOUND", "scancel command was not found on this host", 503)

        result = subprocess.run(
            ["scancel", job_id],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        message = result.stderr.strip() or result.stdout.strip() or f"Cancelled job {job_id}"
        if result.returncode != 0:
            raise AppError("SCANCEL_FAILED", message, 502)
        return CancelJobResponse(
            success=True,
            scheduler=self.scheduler_name,
            job_id=job_id,
            message=message,
        )

    def hold_job(self, job_id: str) -> QueueJobActionResponse:
        return self._run_scontrol_action(job_id, "hold")

    def release_job(self, job_id: str) -> QueueJobActionResponse:
        return self._run_scontrol_action(job_id, "release")

    def _run_scontrol_action(self, job_id: str, action: str) -> QueueJobActionResponse:
        validate_job_id(job_id)
        if self._missing("scontrol"):
            raise AppError("SCONTROL_NOT_FOUND", "scontrol command was not found on this host", 503)

        result = subprocess.run(
            ["scontrol", action, job_id],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        message = result.stderr.strip() or result.stdout.strip() or f"{action.title()} requested for job {job_id}"
        if result.returncode != 0:
            raise AppError(f"SCONTROL_{action.upper()}_FAILED", message, 502)
        return QueueJobActionResponse(
            success=True,
            scheduler=self.scheduler_name,
            job_id=job_id,
            action=action,  # type: ignore[arg-type]
            command=f"scontrol {action}",
            message=message,
        )

    def submit_job(self, workdir: Path, script: str, command: SubmitCommand = "sbatch") -> SubmitJobResponse:
        if self._missing(command):
            raise AppError(
                f"{command.upper()}_NOT_FOUND",
                f"{command} command was not found on this host",
                503,
            )

        result = subprocess.run(
            [command, script],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        message = result.stdout.strip() or result.stderr.strip()
        if result.returncode != 0:
            raise AppError(f"{command.upper()}_FAILED", message or f"{command} failed", 502)
        match = _submitted_job_id(command, message)
        return SubmitJobResponse(
            success=True,
            scheduler=command,
            job_id=match.group(1) if match else None,
            message=message,
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_TIME_SENTINELS = {"UNLIMITED", "INFINITE", "NOT_SET", "N/A", "INVALID"}
_DIGITS_RE = re.compile(r"^\d+$")


def _unwrap_slurm_json_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("number", "value", "raw", "seconds"):
        if key in value:
            return value[key]
    return value


def _first_present(*values: Any) -> Any:
    """返回第一个非 None 且非空串的值；保留 0。

    Slurm 的 time_used / time_limit 取值用 `or` 串联时会把 0 当假值跳过，
    导致刚启动的作业（elapsed=0）落到错误的回退值上。这里显式区分 None/""。
    """
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def _seconds_to_d_hhmmss(seconds: int) -> str:
    """秒 → 与 squeue %M 一致的格式：< 1 天用 HH:MM:SS，>= 1 天用 D-HH:MM:SS。"""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_slurm_elapsed(value: Any) -> str:
    """已运行时间归一化：数字按秒格式化；已是字符串（含 sentinel）原样透传。

    新版 Slurm squeue --json 中 time.elapsed 为整数秒，str() 透传会显示裸秒数。
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # bool 是 int 子类，先排除
        return str(value)
    if isinstance(value, (int, float)):
        return _seconds_to_d_hhmmss(value)
    text = str(value).strip()
    if not text or text in _TIME_SENTINELS:
        return text
    if _DIGITS_RE.match(text):  # 某些版本回传字符串型秒数
        return _seconds_to_d_hhmmss(int(text))
    return text


def _format_slurm_limit(value: Any) -> str:
    """时间上限归一化：保守透传，不假设数字单位。

    Slurm 各版本/字段对 limit 的单位不统一（秒/分钟/sentinel/已格式化字符串），
    误判单位会得到荒谬结果，故对数字一律原样 str()，仅剥离空白。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value).strip()


def _squeue_command(current_user_only: bool, *args: str) -> list[str]:
    command = ["squeue"]
    if current_user_only:
        command.extend(["-u", getpass.getuser()])
    command.extend(args)
    return command


def _parse_key_value_output(text: str) -> dict[str, str]:
    detail: dict[str, str] = {}
    for token in text.replace("\n", " ").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        detail[key] = value
    return detail


def _normalize_slurm_detail(detail: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    time_used = _format_slurm_elapsed(
        _first_present(
            detail.get("time_used"),
            detail.get("RunTime"),
            detail.get("run_time"),
            detail.get("Elapsed"),
            detail.get("ElapsedRaw"),
        )
    )
    time_limit = _format_slurm_limit(
        _first_present(detail.get("time_limit"), detail.get("TimeLimit"), detail.get("time_limit_str"))
    )
    if time_used:
        normalized["time_used"] = time_used
    if time_limit:
        normalized["time_limit"] = time_limit
    for key, value in detail.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _submitted_job_id(command: SubmitCommand, message: str) -> re.Match[str] | None:
    if command == "sbatch":
        return re.search(r"Submitted batch job\s+(\S+)", message)
    return re.search(r"\b(\d+(?:\.[A-Za-z0-9_.-]+)?)\b", message)
