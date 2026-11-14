"""The log filter must redact without breaking the formatter that reads the record afterwards.

Uvicorn's `AccessFormatter.formatMessage` unpacks `record.args` into five fields. A filter that
flattened the message and emptied `args` turned every access record into a `ValueError`, which
`logging` reports as a full traceback on stderr — a 152k-line flood during one acceptance attempt.
"""

from __future__ import annotations

import logging

from app.runtime_log import RedactTicket


def access_record(*args) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=args,
        exc_info=None,
    )


def test_an_access_record_keeps_the_five_fields_the_formatter_needs():
    record = access_record("127.0.0.1:1", "GET", "/health", "1.1", 200)
    assert RedactTicket().filter(record) is True
    assert isinstance(record.args, tuple)
    assert len(record.args) == 5
    assert record.getMessage() == '127.0.0.1:1 - "GET /health HTTP/1.1" 200'


def test_a_secret_in_an_access_path_is_redacted_without_losing_the_fields():
    record = access_record(
        "127.0.0.1:1", "GET", "/chats?api_key=supersecret", "1.1", 200
    )
    RedactTicket().filter(record)
    assert len(record.args) == 5
    rendered = record.getMessage()
    assert "supersecret" not in rendered
    assert "[redacted]" in rendered


def test_an_ordinary_record_is_flattened_and_redacted():
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="password=%s",
        args=("supersecret",),
        exc_info=None,
    )
    RedactTicket().filter(record)
    assert record.args == ()
    assert "supersecret" not in record.msg
    assert "[redacted]" in record.msg
