import json
import logging
import pytest
from unittest.mock import patch


def test_get_logger_returns_bound_logger():
    from utils.logger import get_logger

    log = get_logger("test_module")
    assert log is not None
    assert hasattr(log, "info")
    assert hasattr(log, "error")
    assert hasattr(log, "debug")


def test_log_output_contains_required_fields():
    import structlog
    from utils.logger import get_logger, setup_logging

    setup_logging("development")
    log = get_logger("test_fields")

    with structlog.testing.capture_logs() as cap:
        log.info("test_event", job_id="abc123")

    assert len(cap) == 1
    entry = cap[0]
    assert "event" in entry
    assert entry["event"] == "test_event"
    assert entry["job_id"] == "abc123"


def test_log_json_in_production(capsys):
    from utils.logger import get_logger, setup_logging

    setup_logging("production")
    log = get_logger("test_json")

    log.info("json_event", key="value")

    setup_logging("development")

    captured = capsys.readouterr()
    line = captured.err.strip().split("\n")[-1]
    parsed = json.loads(line)
    assert parsed["event"] == "json_event"
    assert parsed["key"] == "value"
    assert "timestamp" in parsed
    assert "level" in parsed


def test_log_human_readable_in_dev(capsys):
    from utils.logger import get_logger, setup_logging

    setup_logging("development")
    log = get_logger("test_dev")

    log.info("dev_event", foo="bar")

    captured = capsys.readouterr()
    output = captured.err
    assert "dev_event" in output


def test_log_output_redacts_telegram_token(capsys):
    from utils.logger import setup_logging

    setup_logging("development")
    logging.getLogger("httpx").info(
        "HTTP Request: POST https://api.telegram.org/bot8684579329:AAF4oPjhITah80P7IAK4oetrQX2CJih2Ymk/sendMessage"
    )

    captured = capsys.readouterr()
    output = captured.err
    assert "AAF4oPjh" not in output
    assert "https://api.telegram.org/bot***/sendMessage" in output


def test_setup_logging_idempotent():
    from utils.logger import setup_logging

    setup_logging("development")
    setup_logging("production")
    setup_logging("development")
