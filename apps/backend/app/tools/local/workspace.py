import re
from dataclasses import dataclass, field
from pathlib import Path

CODING = re.compile(
    r"(?i)("
    r"pytest|unittest|"
    r"исправ(ь|ьте)(\s+\w+){0,4}\s+(тест|код|проект|проблем)|"
    r"failing tests|почини(\s+\w+){0,3}\s*тест|"
    r"git status|git diff|отрефактор|refactor|formatter|линтер|linter|"
    r"обнови(\s+\w+){0,3}\s+проект|"
    r"почини|patch|напиши (код|тест)|"
    r"inspect (the )?project|"
    r"проверь(\s+\w+){0,4}\s+проект|"
    r"запусти(те)?(\s+\w+){0,3}\s+тест"
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
    build_command: list[str] = field(default_factory=list)

    def as_prompt(self) -> str:
        root = self.root or "(not set)"
        git = self.git_root or "unknown"
        return (
            f"CodingWorkspace root={root}; git_root={git}; branch={self.branch or 'unknown'}. "
            "The project to inspect and fix is exactly that root. Start with list_directory on "
            "that root, then read_file of the existing source and tests already there. "
            "Inspect git_status/git_diff before edits. Edit the failing source in place with "
            "patch_file or write_file; copy sha256 from read_file (sha256=<hex>) into "
            "patch_file.expected_before_sha256. Do not write scratch notes or copy the project "
            "to a parallel path. After code changes run tests with run_python argv "
            '["-m", "pytest", "-q"] and cwd at that same project root. When pytest reports all '
            "tests passed, stop calling tools and answer. If the hash mismatches, stop with "
            "CONFLICT and re-read. Do not git_push, git_reset --hard, or force-push unless the "
            "user explicitly asks; those still require confirmation. Never put Git credentials "
            "in tool arguments."
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


def workspace_from_settings(settings) -> CodingWorkspace:
    roots = list(getattr(settings, "workspace_roots", None) or [])
    root, git_root = _first_git_workspace(roots)
    return CodingWorkspace(root=root, git_root=git_root)
