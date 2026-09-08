"""受控只读查询的进程内任务管理。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Callable
from uuid import uuid4


@dataclass
class _Task:
    query_id: str
    data_source: str
    submitted_at: datetime
    future: Future[dict[str, object]]


class QueryTaskManager:
    """只保存任务状态和结果，不保存 SQL、参数或凭据。"""

    def __init__(self, *, max_workers: int = 3) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="db-mcp-query")
        self._tasks: dict[str, _Task] = {}
        self._lock = Lock()

    def submit(self, data_source: str, operation: Callable[[], dict[str, object]]) -> dict[str, object]:
        query_id = uuid4().hex
        task = _Task(
            query_id=query_id,
            data_source=data_source,
            submitted_at=datetime.now(UTC),
            future=self._executor.submit(operation),
        )
        with self._lock:
            self._tasks[query_id] = task
        return self._snapshot(task)

    def get_status(self, query_id: str) -> dict[str, object]:
        return self._snapshot(self._get(query_id))

    def get_result(self, query_id: str) -> dict[str, object]:
        task = self._get(query_id)
        snapshot = self._snapshot(task)
        if snapshot["status"] != "completed":
            return {**snapshot, "result": None}
        try:
            return {**snapshot, "result": task.future.result()}
        except Exception:
            return {**snapshot, "result": None}

    def cancel(self, query_id: str) -> dict[str, object]:
        task = self._get(query_id)
        cancelled = task.future.cancel()
        snapshot = self._snapshot(task)
        return {
            **snapshot,
            "cancelled": cancelled,
            "message": "任务已在执行，当前驱动无法安全中断；请等待受控查询超时。" if not cancelled and snapshot["status"] == "running" else None,
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _get(self, query_id: str) -> _Task:
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError("query_id 必须是非空字符串")
        with self._lock:
            task = self._tasks.get(query_id)
        if task is None:
            raise ValueError("查询任务不存在或已过期")
        return task

    @staticmethod
    def _snapshot(task: _Task) -> dict[str, object]:
        if task.future.cancelled():
            status = "cancelled"
        elif task.future.running():
            status = "running"
        elif task.future.done():
            failure = task.future.exception()
            status = "failed" if failure is not None else "completed"
        else:
            status = "queued"
        snapshot: dict[str, object] = {
            "query_id": task.query_id,
            "data_source": task.data_source,
            "status": status,
            "submitted_at": task.submitted_at.isoformat(),
            "cancellable": status == "queued",
        }
        if status == "failed":
            failure = task.future.exception()
            snapshot["error"] = {
                "code": getattr(failure, "code", "query_error"),
                "message": str(failure) if getattr(failure, "code", None) else "查询任务执行失败",
            }
        return snapshot
