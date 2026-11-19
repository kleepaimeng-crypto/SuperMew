"""服务端诊断只记录稳定标签与无源码/locals/异常文本的堆栈，不进入公开协议。"""

import json
import logging
import os
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    stage: str
    reason: str
    exception_type: str | None = None
    stack: tuple[dict, ...] = ()
    cause_code: str | None = None
    counts: dict[str, int] | None = None
    issues: tuple[dict, ...] = ()


def validation_detail(stage, error, schema):
    fields = set(schema.model_fields)
    issues = tuple(
        {
            "field": ".".join(
                str(part) if isinstance(part, int) or part in fields else "<extra>"
                for part in item["loc"]
            ),
            "rule": item["type"],
        }
        for item in error.errors(include_input=False, include_context=False, include_url=False)[:8]
    )
    return replace(capture(stage, "schema_validation_failed", error), issues=issues)


@contextmanager
def diagnostic_log(directory: Path):
    """每进程独立滚动文件；只接收受控诊断，不采集HTTP Header或普通日志。"""
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / f"execution-{os.getpid()}.jsonl",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.addFilter(lambda record: record.msg == "execution_diagnostic %s")

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            return record.getMessage().removeprefix("execution_diagnostic ")

    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("minimew")
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()


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
    identity=None,
):
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "code": code,
        **asdict(diagnostic),
    }
    if run_id is not None:
        record["run_id"] = str(run_id)
    if identity is not None:
        from minimew.execution.identity import CaseIdentity

        if isinstance(identity, CaseIdentity):
            record.update(
                job_id=str(identity.job_id),
                case_id=str(identity.case_id),
                attempt_id=str(identity.attempt_id),
            )
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
