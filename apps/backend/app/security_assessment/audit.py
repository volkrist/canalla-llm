"""Static and configuration review for an authorized workspace.

Every rule here answers a question that can be checked from the file text alone, and every finding
carries the file, the line and a redacted piece of evidence. The point is a report a developer can
act on, not a score: a rule that cannot say *where* and *why* is noise and is not included.

Nothing in this module touches the filesystem or the network. The controller feeds it text it read
through the product's own permission-checked `read_file`/`list_directory` tools, so the analysis can
never read something the task was not allowed to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_FILES = 60
MAX_BYTES = 600_000
MAX_FINDINGS = 200
SOURCE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".rb", ".go", ".java", ".php")
CONFIG_NAMES = (
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "setup.py",
    "settings.py",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    ".env",
    ".env.example",
)

PLACEHOLDERS = re.compile(
    r"(?i)(your|example|changeme|placeholder|xxx|dummy|redacted|<|>|\$\{|os\.environ|getenv|"
    r"process\.env|secret[-_]?ref|vault)"
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Finding:
    rule: str
    severity: str
    confidence: str
    file: str
    line: int | None
    cwe: str
    evidence: str
    advice: str
    kind: str = "static"
    extra: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {
            "rule": self.rule,
            "kind": self.kind,
            "severity": self.severity,
            "confidence": self.confidence,
            "file": self.file,
            "line": self.line,
            "cwe": self.cwe,
            "evidence": self.evidence[:200],
            "advice": self.advice,
            **self.extra,
        }


def _is_test_path(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return "/tests/" in lowered or "/test/" in lowered or PathName(lowered).startswith("test_")


def PathName(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _redact_secret(line: str) -> str:
    """Never repeat a credential in a report that is stored in the database and shown in chat."""
    return re.sub(
        r"([=:]\s*[\"'])([^\"']{6,})([\"'])",
        r"\1***\3",
        line.strip(),
    )[:200]


def _literal_only(argument: str) -> bool:
    value = argument.strip()
    return bool(re.fullmatch(r"[rbf]?[\"'][^\"']*[\"']\s*\)?", value))


RULES = (
    (
        "shell_true",
        re.compile(r"subprocess\.[A-Za-z_]+\(.*shell\s*=\s*True"),
        "high",
        "CWE-78",
        "Команда выполняется через shell. Передайте список аргументов и уберите shell=True.",
    ),
    (
        "eval_dynamic",
        re.compile(r"\b(?:eval|exec)\s*\("),
        "high",
        "CWE-95",
        "Динамическое исполнение кода. Не передавайте в eval/exec данные из запроса или файла.",
    ),
    (
        "sql_string_build",
        re.compile(r"\.execute\s*\(\s*(?:f[\"']|[^)]*(?:\+|%|\.format\s*\())"),
        "high",
        "CWE-89",
        "SQL собирается строкой. Используйте параметризованный запрос с placeholder-ами.",
    ),
    (
        "tls_verify_disabled",
        re.compile(r"verify\s*=\s*False"),
        "high",
        "CWE-295",
        "Проверка TLS-сертификата отключена. Верните verify=True.",
    ),
    (
        "pickle_load",
        re.compile(r"\bpickle\.loads?\s*\("),
        "medium",
        "CWE-502",
        "pickle исполняет код при десериализации. Используйте JSON или подписанный формат.",
    ),
    (
        "yaml_load_unsafe",
        re.compile(r"\byaml\.load\s*\("),
        "medium",
        "CWE-502",
        "yaml.load без SafeLoader исполняет произвольные объекты. Используйте safe_load.",
    ),
    (
        "weak_password_hash",
        re.compile(r"(?i)(?:md5|sha1)\s*\("),
        "medium",
        "CWE-916",
        "MD5/SHA1 непригодны для паролей. Используйте bcrypt/argon2/scrypt.",
    ),
    (
        "debug_enabled",
        re.compile(r"(?i)(?:DEBUG\s*=\s*True|\.run\([^)]*debug\s*=\s*True)"),
        "medium",
        "CWE-489",
        "Режим отладки включён. В production он раскрывает трассировки и консоль.",
    ),
    (
        "hardcoded_secret",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)\s*[=:]\s*"
            r"[\"'][^\"']{12,}[\"']"
        ),
        "high",
        "CWE-798",
        "Секрет записан в исходниках. Храните его в переменной окружения или хранилище секретов.",
    ),
)


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    test_path = _is_test_path(path)
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "*")):
            continue
        for rule, pattern, severity, cwe, advice in RULES:
            match = pattern.search(line)
            if not match:
                continue
            if rule == "eval_dynamic":
                argument = line[match.end() :].strip()
                if _literal_only(argument):
                    continue
            if rule == "hardcoded_secret" and PLACEHOLDERS.search(line):
                continue
            if rule in {"sql_string_build", "shell_true"} and _literal_only(
                line.split("(", 1)[-1].rstrip(")")
            ):
                continue
            confidence = "high"
            effective = severity
            if test_path:
                confidence = "medium"
                effective = "low" if severity != "critical" else severity
            evidence = _redact_secret(line) if rule == "hardcoded_secret" else line[:200]
            findings.append(
                Finding(
                    rule=rule,
                    severity=effective,
                    confidence=confidence,
                    file=path,
                    line=number,
                    cwe=cwe,
                    evidence=evidence,
                    advice=advice,
                )
            )
            if len(findings) >= MAX_FINDINGS:
                return findings
    return findings


def is_source(name: str) -> bool:
    return name.lower().endswith(SOURCE_SUFFIXES)


def is_config(name: str) -> bool:
    lowered = name.lower()
    return lowered in {item.lower() for item in CONFIG_NAMES} or lowered.startswith(".env")


def scan_files(files: dict[str, str]) -> list[Finding]:
    """Scan a bounded set of {relative path: text} that the controller already read."""
    findings: list[Finding] = []
    budget = MAX_BYTES
    scanned = 0
    for path in sorted(files):
        if scanned >= MAX_FILES or budget <= 0:
            break
        text = files[path]
        if not text:
            continue
        budget -= len(text.encode("utf-8", "ignore"))
        scanned += 1
        findings.extend(scan_text(path, text))
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


def report(
    findings: list[dict],
    *,
    files_scanned: int,
    packages_checked: int,
    advisories: str,
    skipped: list[str] | None = None,
) -> dict:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.get("severity", "medium")] = counts.get(finding.get("severity", "medium"), 0) + 1
    blocking = counts.get("critical", 0) + counts.get("high", 0)
    return {
        "findings": findings,
        "counts": counts,
        "blocking": blocking,
        "ok": blocking == 0,
        "files_scanned": files_scanned,
        "packages_checked": packages_checked,
        "advisories": advisories,
        "skipped": (skipped or [])[:20],
        "summary": (
            f"Подтверждённых проблем высокого риска: {blocking}."
            if blocking
            else "Проблем высокого риска не найдено."
        ),
    }


def sort_findings(findings: list[dict]) -> list[dict]:
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), 4),
            str(item.get("file") or ""),
            int(item.get("line") or 0),
        ),
    )
