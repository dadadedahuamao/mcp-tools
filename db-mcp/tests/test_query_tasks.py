from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.query_tasks import QueryTaskManager


def test_query_task_tracks_completion_and_result() -> None:
    manager = QueryTaskManager(max_workers=1)
    try:
        task = manager.submit("reporting", lambda: {"rows": [{"id": 1}]})
        assert task["status"] in {"queued", "running", "completed"}

        assert manager.get_result(task["query_id"])["result"] == {"rows": [{"id": 1}]}
        assert manager.get_status(task["query_id"])["status"] == "completed"
    finally:
        manager.shutdown()


def test_query_task_refuses_to_return_result_before_completion() -> None:
    import threading

    started = threading.Event()
    release = threading.Event()
    manager = QueryTaskManager(max_workers=1)
    try:
        task = manager.submit("reporting", lambda: (started.set(), release.wait(2), {"rows": []})[-1])
        assert started.wait(1)
        pending = manager.get_result(task["query_id"])
        assert pending["status"] == "running"
        assert pending["result"] is None
        release.set()
    finally:
        manager.shutdown()


def test_query_task_exposes_safe_failure_code() -> None:
    manager = QueryTaskManager(max_workers=1)
    try:
        task = manager.submit("reporting", lambda: (_ for _ in ()).throw(ValueError("safe validation failure")))
        result = manager.get_result(task["query_id"])

        assert result["status"] == "failed"
        assert result["error"] == {"code": "query_error", "message": "查询任务执行失败"}
    finally:
        manager.shutdown()
