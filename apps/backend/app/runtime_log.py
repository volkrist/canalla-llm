"""Bounded backend log with secret redaction."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .product import PRODUCT, RUNTIME_PROTOCOL_VERSION, VERSION

SECRET_ENV = (
    "RUNPOD_API_KEY",
    "TINYFISH_API_KEY",
    "JWT_SECRET",
    "ALEX_RUNTIME_TOKEN",
    "LLM_API_KEY",
    "ALEX_GATEWAY_KEY",
)

# V03: one pattern set, applied both to the log *message* and to the *final formatted record* (which
# is where a traceback — and any secret it embeds — is appended, after a filter has run).
PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[\w.\-]+"), r"\1[redacted]"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|password|passwd|secret|access_token|authorization|jwt)\s*[:=]\s*)[^\s,;&]+"
        ),
        r"\1[redacted]",
    ),
    (re.compile(r"(?i)(cookie\s*[:=]\s*)[^\s,;&]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(cdp[^\s]*\s*[:=]\s*)[^\s,;&]+"), r"\1[redacted]"),
    (re.compile(r"\?[^\s\"']+"), "?[redacted]"),
)


def redact_text(value: str) -> str:
    """Redact secrets from any text: a log message, a URL, or a finished formatted record."""
    for pattern, repl in PATTERNS:
        value = pattern.sub(repl, value)
    for secret in (os.environ.get(name) or "" for name in SECRET_ENV):
        if secret and secret in value:
            value = value.replace(secret, "[redacted]")
    return value


class RedactingFormatter(logging.Formatter):
    """A formatter that redacts the *whole* record, including an appended exception traceback.

    The filter below cleans the message, but ``Formatter.format`` appends ``exc_text`` afterwards,
    so a secret embedded in an exception would otherwise reach the file unredacted (V03).
    """

    def format(self, record):
        return redact_text(super().format(record))


class RedactTicket(logging.Filter):
    def filter(self, record):
        def redact(value):
            if not isinstance(value, str):
                return value
            return redact_text(value)

        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        # The traceback is part of the record too: redact it here so no formatter can leak it.
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        elif record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


def configure_backend_logging(log_path: Path, *, port: int, mode: str) -> Path:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactTicket())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.addFilter(RedactTicket())
    logging.getLogger("alex.runtime").info(
        "startup product=%s version=%s protocol=%s mode=%s port=%s",
        PRODUCT,
        VERSION,
        RUNTIME_PROTOCOL_VERSION,
        mode,
        port,
    )
    return log_path
