from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any


_OPERATIONAL_FIELDS = (
    "event",
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "job_id",
    "worker_pid",
    "executor_pid",
    "child_pids",
    "termination_reason",
    "exit_code",
    "error_code",
)


class JsonFormatter(logging.Formatter):
    """把平台运行日志格式化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _OPERATIONAL_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_platform_logging() -> logging.Logger:
    logger = logging.getLogger("momo")
    level_name = os.environ.get("MOMO_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    if not any(getattr(handler, "_momo_json_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._momo_json_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_platform_logger(name: str) -> logging.Logger:
    configure_platform_logging()
    return logging.getLogger(f"momo.{name}")
