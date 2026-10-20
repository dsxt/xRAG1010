# utils/sse_utils.py

"""按会话保存队列，把查询图里的进度和答案增量推给前端。"""

import asyncio
import contextvars
import json
import queue
import threading
from typing import Any, Dict


class SSEEvent:
    READY = "ready"
    PROGRESS = "progress"
    DELTA = "delta"
    FINAL = "final"
    ERROR = "error"


class _Stream:
    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.terminal: Dict[str, Any] | None = None


_lock = threading.Lock()
_streams: Dict[str, _Stream] = {}
_bound_stream: contextvars.ContextVar[_Stream | None] = contextvars.ContextVar(
    "bound_sse_stream",
    default=None,
)


def create_sse_queue(session_id: str) -> _Stream:
    """为一次查询新建队列。同一会话再次提问时，旧任务仍写自己绑定的队列。"""
    stream = _Stream()
    with _lock:
        _streams[session_id] = stream
    return stream


def bind_sse_stream(session_id: str) -> None:
    """把当前线程绑定到这次查询的队列。LangGraph 会把该上下文拷进工作线程。"""
    with _lock:
        stream = _streams.get(session_id)
    if stream is not None:
        _bound_stream.set(stream)


def _current_stream(session_id: str | None) -> _Stream | None:
    stream = _bound_stream.get()
    if stream is not None:
        return stream
    if not session_id:
        return None
    with _lock:
        return _streams.get(session_id)


def has_terminal_error(session_id: str | None) -> bool:
    stream = _current_stream(session_id)
    return bool(stream and stream.terminal and stream.terminal.get("event") == SSEEvent.ERROR)


def push_to_session(session_id: str | None, event: str, data: Dict[str, Any]) -> None:
    stream = _current_stream(session_id)
    if stream is None:
        return
    message = {"event": event, "data": data}
    if stream.terminal is not None:
        return
    if event in (SSEEvent.FINAL, SSEEvent.ERROR):
        stream.terminal = message
    stream.queue.put(message)


def _format_sse(event: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_generator(session_id: str, request):
    """把队列里的消息按 SSE 协议写出。收到 final 或 error 后结束。"""
    with _lock:
        stream = _streams.get(session_id)

    if stream is None:
        yield _format_sse(SSEEvent.ERROR, {"error": "会话不存在或尚未提交查询"})
        return

    # 浏览器在连接结束后会自动重连。队列已被读完时，把结束事件再发一次并立刻断开。
    if stream.terminal is not None and stream.queue.empty():
        terminal = stream.terminal
        yield _format_sse(terminal["event"], terminal["data"])
        return

    yield _format_sse(SSEEvent.READY, {"session_id": session_id})

    idle_seconds = 0.0
    while True:
        if await request.is_disconnected():
            break
        try:
            message = stream.queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            idle_seconds += 0.05
            if idle_seconds >= 15:
                idle_seconds = 0.0
                yield ": keepalive\n\n"
            continue

        idle_seconds = 0.0
        event_name = message.get("event") or SSEEvent.PROGRESS
        data = message.get("data") or {}
        yield _format_sse(event_name, data)
        if event_name in (SSEEvent.FINAL, SSEEvent.ERROR):
            break
