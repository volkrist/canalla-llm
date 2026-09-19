"""Paths for the evaluation pack. Never points at the main worktree."""

from __future__ import annotations

from pathlib import Path


EVAL_ROOT = Path(__file__).resolve().parents[1]
WORKTREE_ROOT = EVAL_ROOT.parent
FIXTURES = EVAL_ROOT / "fixtures"
SCHEMA = EVAL_ROOT / "schema"
SCORING = EVAL_ROOT / "scoring"
TASKS = EVAL_ROOT / "tasks"
REPORTS = EVAL_ROOT / "reports"
HARNESS = EVAL_ROOT / "harness"
WORK = EVAL_ROOT / ".work"
TRACES = FIXTURES / "traces"
CODING = FIXTURES / "coding"
RAG = FIXTURES / "rag"
MEMORY = FIXTURES / "memory"
