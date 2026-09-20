"""The only logger. JSON lines on one stream, secrets redacted by key name.

Redaction is by KEY, not by value, at any depth of the `fields` passed to a
log call: a key whose lowercase name contains one of `DENY_SUBSTRINGS` is
replaced with "[redacted]". Values are not scanned, because a value scanner
has to know every secret to find it and this one does not have to.

There is one exception, and it exists because a library forced it. A Telegram
bot token lives inside the URL, and httpx logs every request URL at INFO. The
token is therefore scrubbed out of the rendered message text by shape - the
`/bot<token>/` segment - no matter which logger produced the line. This is a
value scanner, but for a pattern that is unmistakable and that no legitimate
log line needs to carry.

The stdlib root logger is routed through the same formatter so third-party
lines (uvicorn, sqlalchemy) come out as JSON too, with the message as the
event name.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import re
import sys
import traceback
from typing import Any, TextIO

DENY_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "otp",
    "sms_body",
    "raw_body",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
)
REDACTED = "[redacted]"
# https://api.telegram.org/bot<token>/method and .../file/bot<token>/<path>
_BOT_TOKEN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


def scrub(message: str) -> str:
    return _BOT_TOKEN.sub("/bot" + REDACTED, message)


def _silence_url_loggers() -> None:
    """httpx logs every request URL at INFO and a bot token lives inside that
    URL. The record would carry the token to ANY handler, including one this
    project did not install, so the line is never created. Applied on import
    as well as in configure_logging, because the guarantee should not depend
    on which entry point ran."""
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_silence_url_loggers()

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def bind_request_id(value: str | None, reset: contextvars.Token | None = None) -> contextvars.Token | None:
    """Attach a request id to every log line on this task. Pass the returned
    token back as `reset` to restore the previous value."""
    if reset is not None:
        _request_id.reset(reset)
        return None
    return _request_id.set(value)


def current_request_id() -> str | None:
    return _request_id.get()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if any(s in str(k).lower() for s in DENY_SUBSTRINGS) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "at": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "event": scrub(record.getMessage()),
        }
        request_id = _request_id.get()
        if request_id:
            line["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if fields:
            line.update(redact(fields))
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc, tb = record.exc_info
            line["err"] = {
                "type": exc_type.__name__,
                "message": str(exc),
                "stack": "".join(traceback.format_exception(exc_type, exc, tb)),
            }
        return json.dumps(line, ensure_ascii=False, default=str)


class StructLogger:
    """`log.info("event.name", key=value, ...)` - fields, not format strings."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, event: str, exc_info: bool = False, **fields: Any) -> None:
        self._log.log(level, event, extra={"fields": fields}, exc_info=exc_info)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, exc_info=True, **fields)


def get_logger(name: str) -> StructLogger:
    return StructLogger(name)


def configure_logging(level: str = "INFO", stream: TextIO | None = None, service: str = "dashboard") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs every request URL at INFO, and a Telegram bot token lives
    # INSIDE that URL. The formatter scrubs the token out of anything it
    # renders, but the safest line is the one never written: the record itself
    # would still carry the token for any other handler, so this logger is
    # raised to WARNING and the line is never created. Nothing is lost - the
    # calls this project makes log their own outcome.
    _silence_url_loggers()
    # uvicorn installs its own handlers; fold them into ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
