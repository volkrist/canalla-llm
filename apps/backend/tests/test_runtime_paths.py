from pathlib import Path

from app.data_paths import default_root, ensure_layout, load_or_create_jwt, sqlite_url
from app.runtime_entry import prepare


def test_data_root_independent_of_cwd(tmp_path, monkeypatch):
    data = tmp_path / "Alex LLM"
    elsewhere = tmp_path / "repo"
    elsewhere.mkdir()
    monkeypatch.setenv("ALEX_LLM_DATA_DIR", str(data))
    monkeypatch.chdir(elsewhere)
    assert default_root() == data.resolve()
    layout = ensure_layout()
    assert layout["db"] == data.resolve() / "data" / "alex.db"
    assert layout["db"].parent.is_dir()
    assert not (elsewhere / "alex.db").exists()
    assert sqlite_url(layout["db"]).startswith("sqlite:///")
    assert "repo" not in sqlite_url(layout["db"])


def test_jwt_persists_and_is_not_placeholder(tmp_path):
    path = tmp_path / "runtime" / "jwt.secret"
    first = load_or_create_jwt(path)
    second = load_or_create_jwt(path)
    assert first == second
    assert len(first) >= 48
    assert not first.startswith("replace-")
    assert path.read_text(encoding="utf-8").strip() == first


def test_prepare_sets_isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ALEX_LLM_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("DOCUMENT_STORAGE_DIR", raising=False)
    layout = prepare()
    assert "alex.db" in os_environ("DATABASE_URL")
    assert "data-root" in os_environ("DATABASE_URL")
    assert os_environ("JWT_SECRET")
    assert "replace-" not in os_environ("JWT_SECRET")
    assert Path(os_environ("DOCUMENT_STORAGE_DIR")) == layout["documents"]


def os_environ(key: str) -> str:
    import os

    return os.environ[key]


def test_prepare_without_env_ignores_repo_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("ALEX_LLM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    layout = prepare()
    assert layout["root"] == (tmp_path / "Local" / "Alex LLM").resolve()
    assert layout["db"].is_relative_to(tmp_path / "Local")
    assert not (repo / "data").exists()
    assert not (repo / "alex.db").exists()


def test_main_returns_12_when_migrate_fails(tmp_path, monkeypatch):
    from app import runtime_entry

    monkeypatch.setenv("ALEX_LLM_DATA_DIR", str(tmp_path / "root"))
    monkeypatch.delenv("JWT_SECRET", raising=False)

    def fail():
        raise RuntimeError("alembic exploded")

    monkeypatch.setattr(runtime_entry, "migrate", fail)
    assert runtime_entry.main() == 12
