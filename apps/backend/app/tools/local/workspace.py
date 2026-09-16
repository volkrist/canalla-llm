import re
from dataclasses import dataclass, field

CODING = re.compile(
    r"(?i)("
    r"pytest|unittest|исправ(ь|ьте)\s+тест|failing tests|почини тесты|"
    r"git status|git diff|отрефактор|refactor|formatter|линтер|linter|"
    r"обнови (тестовый )?проект|почини|patch|напиши (код|тест)|"
    r"inspect (the )?project|проверь проект"
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
            "Inspect git_status/git_diff before edits. Read the failing source, then patch_file "
            "using expected_before_sha256 from that read_file (sha256=<hex>), or write_file the "
            "corrected file. Do not create unrelated scratch copies. After code changes run tests "
            'with run_python argv ["-m", "pytest", "-q"] and cwd at the project root. '
            "If the hash mismatches, stop with CONFLICT and re-read. "
            "Do not git_push, git_reset --hard, or force-push unless the user explicitly asks; "
            "those still require confirmation. Never put Git credentials in tool arguments."
        )


def looks_like_coding(prompt: str) -> bool:
    return bool(CODING.search(prompt or ""))


def workspace_from_settings(settings) -> CodingWorkspace:
    roots = list(getattr(settings, "workspace_roots", None) or [])
    root = roots[0] if roots else ""
    return CodingWorkspace(root=root, git_root=root or None)
