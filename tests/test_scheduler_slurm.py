import json
import subprocess

import pytest

from backend.app.providers.scheduler.slurm import (
    _format_slurm_elapsed,
    _format_slurm_limit,
    _first_present,
    _normalize_slurm_detail,
    _seconds_to_d_hhmmss,
    SlurmProvider,
)


# ── _seconds_to_d_hhmmss ─────────────────────────────────────────

@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "00:00:00"),
        (1, "00:00:01"),
        (60, "00:01:00"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
        # 用户实例：4d0h38m26s = 347906s
        (347906, "4-00:38:26"),
        (86400, "1-00:00:00"),
    ],
)
def test_seconds_to_d_hhmmss(seconds, expected):
    assert _seconds_to_d_hhmmss(seconds) == expected


# ── _first_present ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "values,expected",
    [
        ((0, "x"), 0),                # 零值保留
        ((None, "y"), "y"),           # 跳过 None
        (("", "y"), "y"),             # 跳过空串
        ((None, "", "y"), "y"),       # 跳过 None 和空串
        ((None, "", ""), ""),         # 全是空 → ""
        (("a", "b"), "a"),            # 首个非空赢
        ((), ""),                     # 无参数 → ""
    ],
)
def test_first_present(values, expected):
    assert _first_present(*values) == expected


# ── _format_slurm_elapsed ──────────────────────────────────────

@pytest.mark.parametrize(
    "val,expected",
    [
        # 整数秒 → 格式化
        (0, "00:00:00"),
        (3661, "01:01:01"),
        (86400, "1-00:00:00"),
        (347906, "4-00:38:26"),      # 用户实例
        # 纯数字字符串 → 转换
        ("347906", "4-00:38:26"),
        ("0", "00:00:00"),
        # 已格式化字符串 → 原样透传
        ("4-00:38:26", "4-00:38:26"),
        ("0:00:00", "0:00:00"),
        # sentinel 透传
        ("UNLIMITED", "UNLIMITED"),
        ("NOT_SET", "NOT_SET"),
        ("INFINITE", "INFINITE"),
        # 空 / None
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_format_slurm_elapsed(val, expected):
    assert _format_slurm_elapsed(val) == expected


# ── _format_slurm_limit ─────────────────────────────────────────

@pytest.mark.parametrize(
    "val,expected",
    [
        # 已格式化字符串 → 原样
        ("10-00:00:00", "10-00:00:00"),
        ("UNLIMITED", "UNLIMITED"),
        ("NOT_SET", "NOT_SET"),
        # 数字 → 原样 str，不转换单位
        (864000, "864000"),
        (14400, "14400"),
        (0, "0"),
        # None / 空
        (None, ""),
        ("", ""),
    ],
)
def test_format_slurm_limit(val, expected):
    assert _format_slurm_limit(val) == expected


def test_normalize_slurm_detail_adds_standard_time_fields():
    detail = _normalize_slurm_detail(
        {
            "JobId": "36910",
            "RunTime": "4-05:19:15",
            "TimeLimit": "10-00:00:00",
            "JobState": "RUNNING",
        }
    )

    assert list(detail)[:2] == ["time_used", "time_limit"]
    assert detail["time_used"] == "4-05:19:15"
    assert detail["time_limit"] == "10-00:00:00"
    assert detail["RunTime"] == "4-05:19:15"
    assert detail["TimeLimit"] == "10-00:00:00"


def test_slurm_queue_json_accepts_scontrol_style_fields():
    item = SlurmProvider()._queue_item_from_json(
        {
            "JobId": "36910",
            "JobName": "RuO2--sol-IS",
            "UserId": "hfwang(1002)",
            "JobState": "RUNNING",
            "Reason": "None",
            "Partition": "q2",
            "NumNodes": "1",
            "NumCPUs": "48",
            "RunTime": "4-05:19:15",
            "TimeLimit": "10-00:00:00",
            "WorkDir": "/home/hfwang/work",
        }
    )

    assert item.job_id == "36910"
    assert item.name == "RuO2--sol-IS"
    assert item.user == "hfwang(1002)"
    assert item.state == "RUNNING"
    assert item.partition == "q2"
    assert item.nodes == 1
    assert item.cpus == 48
    assert item.time_used == "4-05:19:15"
    assert item.time_limit == "10-00:00:00"
    assert item.reason == "None"
    assert item.workdir == "/home/hfwang/work"


def test_slurm_list_jobs_falls_back_to_text_when_json_has_no_runtime(monkeypatch):
    calls: list[list[str]] = []

    def fake_which(command: str) -> str:
        return f"/usr/bin/{command}"

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        if "--json" in command:
            payload = {
                "jobs": [
                    {
                        "job_id": 36910,
                        "name": "RuO2--sol-IS",
                        "user_name": "hfwang",
                        "job_state": "RUNNING",
                        "partition": "q2",
                        "working_directory": "/home/hfwang/work",
                    }
                ]
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="36910|RuO2--sol-IS|hfwang|RUNNING|4-05:19:15|10-00:00:00|1|48|None|q2|/home/hfwang/work\n",
            stderr="",
        )

    monkeypatch.setattr("backend.app.providers.scheduler.slurm.shutil.which", fake_which)
    monkeypatch.setattr("backend.app.providers.scheduler.slurm.subprocess.run", fake_run)

    response = SlurmProvider().list_jobs()

    assert calls[0] == ["squeue", "--json"]
    assert calls[1] == ["squeue", "-h", "-o", "%i|%j|%u|%T|%M|%l|%D|%C|%R|%P|%Z"]
    assert response.items[0].time_used == "4-05:19:15"
    assert response.items[0].time_limit == "10-00:00:00"
