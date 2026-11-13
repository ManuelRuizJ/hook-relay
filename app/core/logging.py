"""Process-wide logging setup shared by the API and the worker.

JSON is the default: one object per line on stdout, which log shippers
(Loki, CloudWatch, Datadog...) parse without regexes. Fields passed via
``logger.info(..., extra={...})`` become top-level keys, so logs can be
filtered by ``event_id`` instead of grepping message text.

``LOG_FORMAT=text`` switches to a human-readable line for local work.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Literal

LogFormat = Literal["json", "text"]

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)
        # default=str keeps UUIDs and datetimes from breaking a log line.
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", fmt: LogFormat = "json") -> None:
    """Send every log record, including uvicorn's, through one stdout handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT)
    )

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; drop them so its startup and access
    # logs share the same format instead of mixing two styles in one stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
