"""Convenience launcher: python run.py"""
from taskdesk.app import create_app
import os

if __name__ == "__main__":
    port = int(os.environ.get("TASKDESK_PORT", "5000"))
    app = create_app()
    app.run(host="127.0.0.1", port=port, debug=False)
