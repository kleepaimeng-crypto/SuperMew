"""服务端诊断只记录稳定标签与无源码/locals/异常文本的堆栈，不进入公开协议。"""

import json
import logging
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    stage: str
    reason: str
    exception_type: str | None = None
    stack: tuple[dict, ...] = ()
    cause_code: str | None = None
    counts: dict[str, int] | None = None


def capture(stage, reason, error=None, *, cause_code=None):
    # 不调用str(error)、format_exception或输出Pydantic input/SQL参数/HTTP header。
    frames = traceback.extract_tb(error.__traceback__)[-12:] if error is not None else ()
    return Diagnostic(
        stage,
        reason,
        type(error).__name__ if error is not None else None,
        tuple(
            {"file": Path(f.filename).name, "function": f.name, "line": f.lineno} for f in frames
        ),
        cause_code,
    )


def emit(
    logger,
    event,
    run_id,
    code,
    diagnostic,
    *,
    thread_id=None,
    tool_call_id=None,
    attempt=None,
    subtask=None,
):
    record = {"event": event, "run_id": str(run_id), "code": code, **asdict(diagnostic)}
    if thread_id is not None:
        record["thread_id"] = str(thread_id)
    if tool_call_id is not None:
        record["tool_call_id"] = tool_call_id
    if attempt is not None:
        record["attempt"] = attempt
    if subtask is not None:
        record["subtask"] = subtask
    unexpected = event in {
        "execution_task_failed",
        "execution_terminal_write_failed",
        "execution_storage_unavailable",
        "execution_interrupt_save_failed",
    } or diagnostic.reason in {
        "handler_failed",
        "authorization_check_failed",
        "unexpected_task_failure",
    }
    logger.log(
        logging.ERROR if unexpected else logging.WARNING,
        "execution_diagnostic %s",
        json.dumps(record, ensure_ascii=False),
    )
