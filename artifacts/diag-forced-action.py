"""Free diagnostic: what does next_forced_action return for the live LA tail?"""

import os
import sys

sys.path.insert(0, "apps/backend")

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "diag-only-" + "a" * 32)

from app.tools.local.task import LocalTaskController  # noqa: E402


class Workspace:
    root = r"C:\Users\Volkr\Canalla Acceptance\LA"
    test_command = ["python", "-m", "pytest", "-q"]
    test_via = "run_python"


class Context:
    autonomous = True
    coding_task = True
    computer_mode = "ask"
    workspace = Workspace()
    task_id = "diag-task"


CLASSES = {"LocalTask": None}


def main() -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    from app.database import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    with database.SessionLocal() as db:
        from app.models import User  # noqa: F401
        from app.tools.models import LocalTask

        user = database.SessionLocal()
        row = LocalTask(
            id="diag-task",
            user_id="u1",
            chat_id="c1",
            title="LA",
            status="EXECUTING",
            original_user_request="собери проект",
            tool_budget=40,
            file_change_budget=20,
            facts={"tests_passed": False, "verification_stale": True, "scope": {"primary_root": Workspace.root}},
            verification={"tests": "stale"},
            checkpoint={
                "commands": [
                    {"tool": "write_file"},
                    {"tool": "run_python"},
                    {"tool": "run_python"},
                    {"tool": "run_process"},
                    {"tool": "write_file"},
                    {"tool": "list_directory"},
                ],
                "test_command": ["python", "-m", "pytest", "-q"],
                "test_via": "run_python",
            },
        )
        db.add(row)
        db.commit()
        print("FORCED:", LocalTaskController().next_forced_action(Context()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
