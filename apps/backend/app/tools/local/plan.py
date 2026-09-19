"""Deterministic actionable TaskPlan. Does not invent bug causes."""

from .workspace import looks_like_coding

RESEARCH_HINT = (
    r"(?i)(документац|official|актуальн|latest docs|fastapi|интернет|"
    r"web search|посмотри.*docs|найди.*источник)"
)
COMPUTER_HINT = (
    r"(?i)("
    r"рабоч(ий|ем)\s+стол|desktop|"
    r"alex-llm-e2e|hello\.txt|grounded\.txt|"
    r"создай.*(папк|файл|директор)|"
    r"установи.*(jq|winget|програм)|"
    r"посчитай.*(sha256|хеш|hash)|sha256|hash_file|"
    r"перечитай|скопируй файл|перемест|"
    r"найди.*файл|найди в этой папке|информаци.*(компьютер|windows)|"
    r"версию windows|свободное место|"
    r"прочитай файл|что внутри|"
    r"удалить.*delete-me|открой тестовую форму|"
    r"оформи тестовый|тестов(ую|ый)\s+(форм|товар)|"
    r"start-sleep|запусти.*(процесс|powershell)|останови.*(процесс|его)"
    r")"
)
WRITE_HINT = (
    r"(?i)("
    r"создай|запиши|измени|добавь|скопируй|перемест|удал|"
    r"исправ|commit|закоммить|install|установи|patch|write|"
    r"checkout|отправ|submit|оформи"
    r")"
)
COMMIT_HINT = r"(?i)(\bgit\s+commit\b|\bcommit\b|закоммить|сделай commit)"
PUSH_HINT = r"(?i)(\bgit\s+push\b|\bpush\b|запуш|отправь.*(remote|репозитор))"


def needs_research(prompt: str) -> bool:
    import re

    return bool(re.search(RESEARCH_HINT, prompt or ""))


def looks_like_computer(prompt: str) -> bool:
    import re

    return bool(re.search(COMPUTER_HINT, prompt or ""))


def looks_like_write(prompt: str) -> bool:
    import re

    return bool(re.search(WRITE_HINT, prompt or ""))


def looks_like_commit_request(prompt: str) -> bool:
    import re

    return bool(re.search(COMMIT_HINT, prompt or ""))


def looks_like_push_request(prompt: str) -> bool:
    import re

    return bool(re.search(PUSH_HINT, prompt or ""))


def looks_like_autonomous(prompt: str) -> bool:
    import re

    if looks_like_coding(prompt):
        return True
    return bool(
        re.search(
            r"(?i)("
            r"полностью|пока не|не заверш|дай отч[её]т|"
            r"найди причин|исправ(ь|ьте).*(проект|тест|код|ошиб)|"
            r"run until|inspect (the )?project|"
            r"проверь.*(проект|тест).*(исправ|почини)"
            r")",
            prompt or "",
        )
    )


def default_plan(prompt: str, *, coding: bool, research: bool, tor: bool) -> list[dict]:
    steps = []
    if coding:
        steps.extend(
            [
                _step(
                    "inspect", "Inspect project", "List the workspace and read project metadata.", "local_fs"
                ),
                _step(
                    "git", "Inspect git state", "Record HEAD, branch, status and current diff.", "local_git"
                ),
                _step(
                    "discover",
                    "Identify test and build commands",
                    "Read pyproject.toml, package.json, Cargo.toml or README for test/lint commands.",
                    "local_fs",
                ),
                _step(
                    "baseline",
                    "Run baseline tests",
                    "Run the project test command and record failing test names.",
                    "local_process",
                    verify=True,
                ),
                _step(
                    "diagnose",
                    "Diagnose failures",
                    "Read the failing source and test output. Do not assume a root cause yet.",
                    "local_fs",
                ),
            ]
        )
        if research or tor:
            steps.append(
                _step(
                    "research",
                    "Research official documentation",
                    "Fetch current official docs only if the failure needs an external API or library contract.",
                    "search" if not tor else "tor_search",
                )
            )
        steps.extend(
            [
                _step(
                    "fix",
                    "Edit failing source",
                    "Patch the failing files in place using expected_before_sha256 from read_file.",
                    "local_fs",
                ),
                _step(
                    "retest",
                    "Re-run affected tests",
                    "Run the same test command and check that previously failing tests pass.",
                    "local_process",
                    verify=True,
                ),
                _step(
                    "review",
                    "Review git diff",
                    "Inspect git status and git diff for unintended file changes.",
                    "local_git",
                    verify=True,
                ),
                _step(
                    "report",
                    "Final verification and report",
                    "Confirm success criteria, summarize files changed, tests and remaining limits.",
                    "verify",
                    verify=True,
                ),
            ]
        )
        return steps
    if research or tor:
        channel = "tor_search" if tor else "search"
        return [
            _step(
                "clarify",
                "Clarify the research question",
                "Restate the question and success criteria.",
                "plan",
            ),
            _step(
                "search",
                "Search primary sources",
                "Search official documentation and primary sources, not an arbitrary large set of pages.",
                channel,
            ),
            _step("read", "Read selected sources", "Fetch a small number of authoritative pages.", "fetch"),
            _step(
                "check",
                "Cross-check collected sources",
                "Confirm claims against collected D/W/T labels before answering.",
                "verify",
                verify=True,
            ),
            _step(
                "report",
                "Write the source-backed report",
                "Cite sources and list remaining limits.",
                "verify",
                verify=True,
            ),
        ]
    return [
        _step("inspect", "Inspect the request", "Identify the goal and the next safe action.", "plan"),
        _step("act", "Execute required tools", "Call only tools that advance the current step.", "execute"),
        _step(
            "verify",
            "Verify the result",
            "Check that success criteria hold before completing.",
            "verify",
            verify=True,
        ),
    ]


def _step(key, title, description, category, verify=False):
    return {
        "key": key,
        "title": title,
        "description": description,
        "tool_category": category,
        "verification_required": verify,
        "status": "PENDING",
        "depends_on": [],
        "max_attempts": 3,
    }


def revision_steps(reason: str) -> list[dict]:
    return [
        _step(
            "investigate",
            "Investigate new failure",
            "Read the new error, test name and path. " + (reason or "")[:200],
            "local_fs",
        ),
        _step(
            "fix_again",
            "Fix the newly found issue",
            "Edit the newly identified source, then re-run the affected checks.",
            "local_fs",
        ),
        _step(
            "retest_again",
            "Re-run verification after the extra fix",
            "Run the affected tests again and do not complete until they pass.",
            "local_process",
            verify=True,
        ),
    ]
