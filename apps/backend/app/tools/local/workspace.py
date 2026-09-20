import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CODING = re.compile(
    r"(?i)("
    r"pytest|unittest|"
    r"исправ(ь|ьте).{0,40}(тест|код|проект|проблем|провер)|"
    r"failing tests|почини(\s+\w+){0,3}\s*тест|"
    r"git status|git diff|отрефактор|refactor|formatter|линтер|linter|"
    r"обнови(\s+\w+){0,3}\s+проект|"
    r"почини|patch|напиши (код|тест)|"
    r"inspect (the )?project|"
    r"проверь(\s+\w+){0,4}\s+проект|"
    r"запусти(те)?(\s+\w+){0,3}\s+тест|"
    r"прогони(\s+\w+){0,3}\s+тест|"
    r"падают тест|синтаксическ|тестов\w* проект"
    r")"
)


@dataclass
class CodingWorkspace:
    root: str = ""
    git_root: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    changed_files: list[str] = field(default_factory=list)
    test_command: list[str] = field(default_factory=lambda: ["-m", "pytest", "-q"])
    test_via: str = "run_python"
    lint_command: list[str] = field(default_factory=list)
    build_command: list[str] = field(default_factory=list)

    def as_prompt(self) -> str:
        root = self.root or "(not set)"
        git = self.git_root or "unknown"
        test = " ".join(self.test_command) or "pytest -q"
        via = (
            f"run_python argv {self.test_command!r}"
            if self.test_via == "run_python"
            else f"run_process executable={self.test_command[0]!r} argv={self.test_command[1:]!r}"
        )
        return (
            f"CodingWorkspace root={root}; git_root={git}; branch={self.branch or 'unknown'}. "
            "The project to inspect and fix is exactly that root. Start with list_directory on "
            "that root, then read_file of the existing source and tests already there. "
            "Inspect git_status/git_diff before edits. Edit the failing source in place with "
            "patch_file or write_file; copy sha256 from read_file (sha256=<hex>) into "
            "patch_file.expected_before_sha256. Do not write scratch notes or copy the project "
            "to a parallel path. After code changes run tests with "
            f"{via} and cwd at that same project root (discovered command: {test}). "
            "When tests report all passed, inspect git_status/git_diff, then stop calling tools "
            "and answer. If the hash mismatches, stop with CONFLICT and re-read. Do not git_push, "
            "git_reset --hard, or force-push unless the user explicitly asks; those still require "
            "confirmation. Never put Git credentials in tool arguments."
        )


def looks_like_coding(prompt: str) -> bool:
    return bool(CODING.search(prompt or ""))


def _is_git_root(path: Path) -> bool:
    return (path / ".git").exists()


def _first_git_workspace(roots: list[str]) -> tuple[str, str | None]:
    parsed = [Path(item) for item in roots if item]
    if not parsed:
        return "", None
    for root in parsed:
        if _is_git_root(root):
            return str(root), str(root)
        try:
            children = sorted(
                (child for child in root.iterdir() if child.is_dir()), key=lambda item: item.name
            )
        except OSError:
            children = []
        for child in children:
            if _is_git_root(child):
                return str(child), str(child)
    chosen = str(parsed[0])
    return chosen, chosen


def discover_commands(root: str) -> dict:
    path = Path(root) if root else None
    result = {
        "test_command": ["-m", "pytest", "-q"],
        "test_via": "run_python",
        "lint_command": [],
        "build_command": [],
    }
    if not path or not path.exists():
        return result
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            text = ""
        if "pytest" in text:
            result["test_command"] = ["-m", "pytest", "-q"]
            result["test_via"] = "run_python"
        if "ruff" in text:
            result["lint_command"] = ["-m", "ruff", "check", "."]
    package = path / "package.json"
    if package.is_file():
        try:
            scripts = (json.loads(package.read_text(encoding="utf-8")) or {}).get("scripts") or {}
        except (OSError, json.JSONDecodeError, TypeError):
            scripts = {}
        if "test" in scripts:
            result["test_command"] = ["npm", "test"]
            result["test_via"] = "run_process"
        if "lint" in scripts:
            result["lint_command"] = ["npm", "run", "lint"]
        if "build" in scripts:
            result["build_command"] = ["npm", "run", "build"]
    cargo = path / "Cargo.toml"
    if cargo.is_file():
        result["test_command"] = ["cargo", "test"]
        result["test_via"] = "run_process"
        result["build_command"] = ["cargo", "check"]
    makefile = path / "Makefile"
    if makefile.is_file():
        try:
            text = makefile.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            text = ""
        if re.search(r"(?m)^test:", text):
            result["test_command"] = ["make", "test"]
            result["test_via"] = "run_process"
    return result


def workspace_from_settings(settings) -> CodingWorkspace:
    roots = list(getattr(settings, "workspace_roots", None) or [])
    root, git_root = _first_git_workspace(roots)
    discovered = discover_commands(root)
    return CodingWorkspace(root=root, git_root=git_root, **discovered)
