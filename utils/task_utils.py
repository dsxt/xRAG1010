# utils/task_utils.py

"""内存任务状态。导入接口靠它轮询，查询接口在流式时顺便推送进度。"""

import threading
from typing import Any, Dict, List

TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"

_lock = threading.Lock()
_tasks: Dict[str, Dict[str, Any]] = {}


def _new_task() -> Dict[str, Any]:
    return {
        "status": TASK_STATUS_PENDING,
        "done_list": [],
        "running_list": [],
        "result": {},
    }


def _ensure_task(task_id: str) -> Dict[str, Any]:
    task = _tasks.get(task_id)
    if task is None:
        task = _new_task()
        _tasks[task_id] = task
    return task


def _push_progress(task_id: str, payload: Dict[str, List[str]]) -> None:
    from utils.sse_utils import SSEEvent, push_to_session

    push_to_session(task_id, SSEEvent.PROGRESS, payload)


def add_running_task(task_id: str | None, node_name: str, is_stream: bool = False) -> None:
    """把节点记为运行中。无任务 ID 时直接跳过，方便本地直接跑图。"""
    if not task_id or not node_name:
        return
    with _lock:
        task = _ensure_task(task_id)
        if node_name not in task["running_list"]:
            task["running_list"].append(node_name)
        payload = {
            "done_list": list(task["done_list"]),
            "running_list": list(task["running_list"]),
        }
    if is_stream:
        _push_progress(task_id, payload)


def add_done_task(task_id: str | None, node_name: str, is_stream: bool = False) -> None:
    """把节点从运行中挪到已完成。重复调用不会写入两次。"""
    if not task_id or not node_name:
        return
    with _lock:
        task = _ensure_task(task_id)
        running = task["running_list"]
        if node_name in running:
            running.remove(node_name)
        if node_name not in task["done_list"]:
            task["done_list"].append(node_name)
        payload = {
            "done_list": list(task["done_list"]),
            "running_list": list(task["running_list"]),
        }
    if is_stream:
        _push_progress(task_id, payload)


def update_task_status(task_id: str | None, status: str, is_stream: bool = False) -> None:
    if not task_id:
        return
    with _lock:
        task = _ensure_task(task_id)
        task["status"] = status
    # is_stream 保留给调用方，进度推送由节点进出运行列表时触发
    _ = is_stream


def get_task_status(task_id: str) -> str:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return TASK_STATUS_PENDING
        return task["status"]


def get_done_task_list(task_id: str) -> List[str]:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return []
        return list(task["done_list"])


def get_running_task_list(task_id: str) -> List[str]:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return []
        return list(task["running_list"])


def set_task_result(task_id: str | None, key: str, value: Any) -> None:
    if not task_id or not key:
        return
    with _lock:
        task = _ensure_task(task_id)
        task["result"][key] = value


def get_task_result(task_id: str, key: str, default: Any = "") -> Any:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return default
        return task["result"].get(key, default)
