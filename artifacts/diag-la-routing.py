import re
import sys

sys.path.insert(0, ".")
from app.tools.local.plan import (  # noqa: E402
    RESEARCH_HINT,
    default_plan,
    looks_like_autonomous,
    looks_like_greenfield,
    needs_research,
)
from app.tools.local.workspace import looks_like_coding  # noqa: E402

PROMPT = (
    "В папке C:\\Users\\Volkr\\Canalla Acceptance\\LA собери небольшой Python-проект "
    "целиком самостоятельно. Нужны пакет la_app с __init__.py, core.py (функции normalize, "
    "summarize, validate), cli.py и tests/test_core.py; README.md с описанием. Затем запусти "
    "pytest -q. Если тесты падают — найди причину, исправь исходный код и повторяй до PASS. "
    "После зелёных тестов запусти python -m la_app.cli и сохрани его stdout в la_report.txt. "
    "Не останавливайся на первом успехе: проверь каждый модуль отдельно. В конце выведи "
    "LA_DONE и коротко перечисли, что создано и что проверено."
)

print("needs_research:", needs_research(PROMPT))
print("regex match:", re.search(RESEARCH_HINT, PROMPT))
print("coding:", looks_like_coding(PROMPT))
print("greenfield:", looks_like_greenfield(PROMPT))
print("autonomous:", looks_like_autonomous(PROMPT))
print("plan:")
for step in default_plan(PROMPT, coding=True, research=needs_research(PROMPT), tor=False):
    print("  ", step.get("id"), "|", step.get("title"), "|", step.get("tool"))
