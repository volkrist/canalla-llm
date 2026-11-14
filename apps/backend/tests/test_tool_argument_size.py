"""A real source file must be a valid `write_file` argument.

The LIVE attempt's TaskDesk failed because the model's `write_file` call was rejected as
`invalid_arguments`: the raw argument string was capped at 16,000 characters and the content field
at 20,000, so a normal FastAPI or HTML file could not be created at all.
"""

from __future__ import annotations

import json

from app.tools.executor import MAX_TOOL_ARGUMENT_CHARS
from app.tools.local.provider import MAX_WRITE_CONTENT_CHARS, WriteArgs


def test_a_real_source_file_is_a_valid_write_argument():
    content = "def handler():\n    return 1\n" * 3000
    assert len(content) > 60_000
    args = WriteArgs.model_validate({"path": "app/main.py", "content": content})
    assert args.content == content


def test_an_escaped_maximum_payload_fits_the_argument_bound():
    # JSON escaping can double the raw size, so the gate must never reject a payload the content
    # field itself accepts.
    assert MAX_TOOL_ARGUMENT_CHARS >= 2 * MAX_WRITE_CONTENT_CHARS


def test_a_realistic_tool_call_string_is_accepted_by_the_bound():
    body = json.dumps({"path": "static/app.js", "content": "console.log('x');\n" * 2500})
    assert len(body) > 16_000
    assert len(body) <= MAX_TOOL_ARGUMENT_CHARS
