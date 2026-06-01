import re
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]+"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
]

_URL_CREDENTIALS_PATTERN = re.compile(r"(https?://)([^/@\s]+)@")
_TELEGRAM_BOT_URL_PATTERN = re.compile(r"(https://api\.telegram\.org/bot)([^/\s]+)")


def redact_secrets(value: Any) -> str:
    text = _stringify(value)
    text = _URL_CREDENTIALS_PATTERN.sub(r"\1***@", text)
    text = _TELEGRAM_BOT_URL_PATTERN.sub(r"\1***", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def _stringify(value: Any) -> str:
    text = str(value)
    if text:
        return text
    if isinstance(value, BaseException):
        return value.__class__.__name__
    return repr(value)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def redact_event_dict(logger, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_value(value) for key, value in event_dict.items()}
