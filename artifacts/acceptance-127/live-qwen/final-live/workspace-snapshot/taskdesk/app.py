"""TaskDesk: Flask backend API + web UI."""
import os
import json

from flask import Flask, jsonify, request, render_template

from . import db
from .db import VALID_STATUSES


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    db.init_db(db_path)

    def _path():
        return app.config["DB_PATH"]

    # ---------- API ----------
    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "app": "taskdesk"})

    @app.get("/api/tasks")
    def api_list():
        search = request.args.get("q")
        status = request.args.get("status")
        if status and status not in VALID_STATUSES:
            return jsonify({"error": "invalid status"}), 400
        return jsonify({"tasks": db.list_tasks(search=search, status=status, path=_path())})

    @app.post("/api/tasks")
    def api_create():
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        description = data.get("description", "")
        status = data.get("status", "todo")
        if not title or not str(title).strip():
            return jsonify({"error": "title is required"}), 400
        if status not in VALID_STATUSES:
            return jsonify({"error": f"status must be one of {list(VALID_STATUSES)}"}), 400
        task = db.create_task(title, description, status, path=_path())
        return jsonify({"task": task}), 201

    @app.get("/api/tasks/<int:task_id>")
    def api_get(task_id):
        task = db.get_task(task_id, path=_path())
        if not task:
            return jsonify({"error": "not found"}), 404
        return jsonify({"task": task})

    @app.patch("/api/tasks/<int:task_id>")
    def api_update(task_id):
        data = request.get_json(silent=True) or {}
        if "status" not in data:
            return jsonify({"error": "status field required"}), 400
        status = data["status"]
        if status not in VALID_STATUSES:
            return jsonify({"error": f"status must be one of {list(VALID_STATUSES)}"}), 400
        task = db.set_status(task_id, status, path=_path())
        if not task:
            return jsonify({"error": "not found"}), 404
        return jsonify({"task": task})

    @app.delete("/api/tasks/<int:task_id>")
    def api_delete(task_id):
        if db.delete_task(task_id, path=_path()):
            return jsonify({"deleted": task_id})
        return jsonify({"error": "not found"}), 404

    # ---------- Web UI ----------
    @app.get("/")
    def index():
        return render_template("index.html", statuses=list(VALID_STATUSES))

    return app
