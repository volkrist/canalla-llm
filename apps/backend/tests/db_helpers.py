"""Typed accessors for rows a test requires.

These tests drive rows they have just written, so an empty query result is a broken
scenario rather than an expected outcome. The helpers fail loudly instead of returning
``None`` and letting the problem surface later as a confusing attribute error.
"""

from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


def require_row(db: Session, model: type[ModelT], key: object) -> ModelT:
    """The row with ``key``; a missing row fails the test."""
    row = db.get(model, key)
    if row is None:
        raise AssertionError(f"{model.__name__} row is missing")
    return row


def require_scalar(db: Session, statement: Select[tuple[ModelT]]) -> ModelT:
    """The single row ``statement`` selects; no row fails the test."""
    row = db.scalar(statement)
    if row is None:
        raise AssertionError("query returned no row")
    return row
