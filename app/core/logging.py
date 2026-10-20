import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import ClassVar

from app.core.config import Environment, LogLevel
from app.core.request_context import get_request_id


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        existing_request_id = getattr(record, "request_id", None)

        record.request_id = existing_request_id or get_request_id()

        return True


class JsonFormatter(logging.Formatter):
    context_fields: ClassVar[tuple[str, ...]] = (
        "workspace_id",
        "campaign_id",
        "automation_id",
        "message_id",
        "channel",
        "provider",
        "status_code",
        "duration_ms",
    )

    def __init__(self, environment: Environment) -> None:
        super().__init__()
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "campaignhub",
            "environment": self.environment,
            "request_id": getattr(record, "request_id", None),
        }

        for field_name in self.context_fields:
            field_value = getattr(record, field_name, None)

            if field_value is not None:
                log_entry[field_name] = field_value

        if record.exc_info is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            log_entry["stack"] = self.formatStack(record.stack_info)

        return json.dumps(
            log_entry,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def configure_logging(
    *,
    log_level: LogLevel,
    environment: Environment,
) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": RequestContextFilter,
                }
            },
            "formatters": {
                "json": {
                    "()": JsonFormatter,
                    "environment": environment,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "json",
                    "filters": ["request_context"],
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "level": log_level,
                "handlers": ["console"],
            },
            "loggers": {
                "uvicorn": {
                    "level": log_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": log_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": log_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
            },
        }
    )
