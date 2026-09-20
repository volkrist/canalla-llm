"""Deterministic RAG upload and memory seed proofs. Never swallow HTTP errors."""

from __future__ import annotations

import time
from pathlib import Path

from http_client import Client, HttpError


class HarnessSetupError(Exception):
    def __init__(self, reason: str, details: dict | None = None):
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


def _mime(path: Path) -> str:
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.lower(), "application/octet-stream")


def wait_rag_model(client: Client, timeout: float = 120.0) -> dict:
    status = client.get("/rag/model")
    if isinstance(status, dict) and status.get("ready"):
        return status
    try:
        client.post("/rag/model/prepare")
    except HttpError as error:
        raise HarnessSetupError("rag_model_prepare_failed", {"status": error.status, "body": error.body}) from error
    deadline = time.time() + timeout
    last = status
    while time.time() < deadline:
        last = client.get("/rag/model")
        if isinstance(last, dict) and last.get("ready"):
            return last
        if isinstance(last, dict) and last.get("state") in {"FAILED", "CANCELLED"}:
            raise HarnessSetupError("rag_model_not_ready", last)
        time.sleep(1.0)
    raise HarnessSetupError("rag_model_timeout", last if isinstance(last, dict) else {"raw": last})


def upload_one(client: Client, path: Path, project_id: str | None = None) -> dict:
    boundary = "----EvalBoundary7MA4YWxkTrZu0gW"
    data = path.read_bytes()
    filename = path.name
    mime = _mime(path)
    parts = [
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode("utf-8"),
        data,
        b"\r\n",
    ]
    if project_id:
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"project_id\"\r\n\r\n"
                f"{project_id}\r\n"
            ).encode("utf-8")
        )
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    body = b"".join(parts)
    try:
        uploaded = client.request(
            "POST",
            "/documents",
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            timeout=60,
        )
    except HttpError as error:
        raise HarnessSetupError(
            "document_upload_http_error",
            {"file": filename, "status": error.status, "body": error.body},
        ) from error
    if not isinstance(uploaded, dict) or not uploaded.get("id"):
        raise HarnessSetupError("document_id_missing", {"file": filename, "response": uploaded})
    return uploaded


def wait_indexed(client: Client, document_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = client.get(f"/documents/{document_id}")
        except HttpError as error:
            raise HarnessSetupError(
                "document_get_http_error",
                {"document_id": document_id, "status": error.status, "body": error.body},
            ) from error
        if (
            isinstance(last, dict)
            and last.get("status") == "ready"
            and last.get("indexed_at")
            and int(last.get("chunk_count") or 0) > 0
        ):
            return last
        if isinstance(last, dict) and last.get("status") == "failed":
            raise HarnessSetupError("document_index_failed", last)
        time.sleep(0.5)
    raise HarnessSetupError("document_index_timeout", last if isinstance(last, dict) else {"raw": last})


def upload_docs(client: Client, files: list[Path], *, user: dict | None = None) -> dict:
    if not files:
        raise HarnessSetupError("rag_fixture_empty")
    model = wait_rag_model(client)
    me = user or client.get("/auth/me")
    documents = []
    for path in files:
        if not path.is_file():
            raise HarnessSetupError("rag_fixture_missing", {"path": str(path)})
        uploaded = upload_one(client, path)
        indexed = wait_indexed(client, uploaded["id"])
        if indexed.get("id") != uploaded["id"]:
            raise HarnessSetupError("document_id_mismatch", {"uploaded": uploaded, "indexed": indexed})
        documents.append(indexed)
    listed = client.get("/documents") or []
    ids = {row.get("id") for row in listed if isinstance(row, dict)}
    missing = [row["id"] for row in documents if row["id"] not in ids]
    if missing:
        raise HarnessSetupError("document_not_listed_for_eval_user", {"missing": missing, "user": me})
    return {
        "ok": True,
        "user_id": me.get("id") if isinstance(me, dict) else None,
        "model": model,
        "documents": documents,
        "chunk_count": sum(int(row.get("chunk_count") or 0) for row in documents),
    }


def ensure_project(client: Client, name: str) -> str:
    created = None
    try:
        created = client.post("/projects", {"name": name, "description": "EVAL project"})
    except HttpError:
        created = None
    if isinstance(created, dict) and created.get("id"):
        return created["id"]
    projects = client.get("/projects") or []
    for row in projects:
        if row.get("name") == name:
            return row["id"]
    raise HarnessSetupError("project_create_failed", {"name": name, "projects": projects})


def seed_memory(client: Client, setup: dict, *, user: dict | None = None) -> dict:
    me = user or client.get("/auth/me")
    user_id = me.get("id") if isinstance(me, dict) else None
    project_id = None
    if setup.get("project"):
        project_id = ensure_project(client, setup["project"])
    profile_body = {
        "display_name": "Eval User",
        "custom_instructions": "EVAL synthetic user.",
        "use_memory": False if setup.get("use_memory") is False else True,
        "relevant_memory": False if setup.get("use_memory") is False else True,
        "max_memories": 12,
    }
    try:
        client.patch("/profile", profile_body)
    except HttpError as error:
        raise HarnessSetupError("profile_patch_failed", {"status": error.status, "body": error.body}) from error
    if setup.get("use_memory") is False:
        return {"ok": True, "disabled": True, "user_id": user_id, "items": []}
    items = list(setup.get("general") or []) + list(setup.get("pinned") or []) + list(setup.get("items") or [])
    seeded = []
    for index, item in enumerate(items):
        body = {
            "content": item.get("content"),
            "category": item.get("category") or "fact",
            "is_pinned": bool(item.get("is_pinned")),
            "is_active": True,
        }
        if project_id and (item.get("category") == "project" or setup.get("project")):
            body["project_id"] = project_id
        try:
            created = client.post("/memory", body)
        except HttpError as error:
            raise HarnessSetupError(
                "memory_create_http_error",
                {"index": index, "status": error.status, "body": error.body, "body_sent": body},
            ) from error
        if not isinstance(created, dict) or not created.get("id"):
            raise HarnessSetupError("memory_id_missing", {"index": index, "response": created})
        if created.get("is_active") is False:
            raise HarnessSetupError("memory_not_enabled", created)
        if body.get("project_id") and created.get("project_id") != body["project_id"]:
            raise HarnessSetupError("memory_project_mismatch", created)
        if created.get("is_pinned") != body["is_pinned"]:
            raise HarnessSetupError("memory_pinned_mismatch", created)
        seeded.append(created)
        time.sleep(0.05)
    listed = client.get("/memory") or []
    listed_ids = {row.get("id") for row in listed if isinstance(row, dict)}
    missing = [row["id"] for row in seeded if row["id"] not in listed_ids]
    if missing:
        raise HarnessSetupError("memory_not_listed_after_seed", {"missing": missing, "listed": listed})
    for row in seeded:
        fetched = client.get("/memory")
        match = next((item for item in fetched if item.get("id") == row["id"]), None)
        if not match or match.get("content") != row.get("content"):
            raise HarnessSetupError("memory_readback_mismatch", {"expected": row, "listed": fetched})
    return {
        "ok": True,
        "user_id": user_id,
        "project_id": project_id,
        "items": seeded,
        "order": [row.get("id") for row in seeded],
    }
