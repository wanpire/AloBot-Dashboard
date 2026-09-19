"""Phase 1 task 2: one structured logger, and secrets that never reach it."""

import io
import json
import logging

from app.core.logging import bind_request_id, configure_logging, get_logger


def _capture():
    stream = io.StringIO()
    configure_logging(level="DEBUG", stream=stream, service="test")
    return stream


def _last_line(stream):
    lines = [l for l in stream.getvalue().splitlines() if l.strip()]
    return json.loads(lines[-1])


def test_lines_are_json_with_event_level_service_and_fields():
    stream = _capture()
    get_logger("x").info("boot.ok", count=3)
    rec = _last_line(stream)
    assert rec["event"] == "boot.ok"
    assert rec["level"] == "INFO"
    assert rec["service"] == "test"
    assert rec["count"] == 3
    assert "at" in rec


def test_secret_keys_are_redacted_by_name_at_any_depth():
    stream = _capture()
    get_logger("x").warning(
        "ingest.rejected",
        token="tok-SECRET-1",
        nested={"password": "pw-SECRET-2", "ok": 1},
        sms_body="OTP 123456 SECRET-3",
        authorization="Bearer SECRET-4",
    )
    out = stream.getvalue()
    for leak in ("SECRET-1", "SECRET-2", "SECRET-3", "SECRET-4"):
        assert leak not in out
    rec = _last_line(stream)
    assert rec["token"] == "[redacted]"
    assert rec["nested"] == {"password": "[redacted]", "ok": 1}


def test_request_id_is_attached_when_bound():
    stream = _capture()
    token = bind_request_id("req-42")
    try:
        get_logger("x").info("page.view")
    finally:
        bind_request_id(None, reset=token)
    assert _last_line(stream)["request_id"] == "req-42"
    get_logger("x").info("page.view")
    assert "request_id" not in _last_line(stream)


def test_exceptions_carry_type_and_message_not_a_second_copy():
    stream = _capture()
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("x").exception("sweep.failed")
    rec = _last_line(stream)
    assert rec["err"]["type"] == "ValueError"
    assert rec["err"]["message"] == "boom"
    assert "Traceback" in rec["err"]["stack"]


def test_stdlib_loggers_are_routed_through_the_same_formatter():
    stream = _capture()
    logging.getLogger("uvicorn.error").warning("plain %s", "text")
    rec = _last_line(stream)
    assert rec["event"] == "plain text"
    assert rec["level"] == "WARNING"
