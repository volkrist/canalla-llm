"""Alex-side TinyFish Agent goal classification. Prompt text is not a permission boundary."""

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["READ_ONLY", "POTENTIAL_SIDE_EFFECT", "SIDE_EFFECT"]

SIDE_EFFECT = re.compile(
    r"(?i)("
    r"\bsubmit\b|\bsend\b.{0,24}(form|message|email|mail)|post a |posted |"
    r"\bbuy\b|\bpurchase\b|\bcheckout\b|\bbook\b|\bcancel\b.{0,16}(order|booking|account)|"
    r"\bdelete\b|\bupload\b|\blog ?in\b|\bsign ?in\b|\bsign ?up\b|password|credentials?|"
    r"account settings|financial|payment|credit card|checkout|"
    r"publish|tweet|commit external|wire transfer|"
    r"отправ(ь|ить)|оплат|купить|удал|опубли|войти|парол|"
    r"отправь форму|заполни форму|зарегистрир"
    r")"
)
POTENTIAL = re.compile(
    r"(?i)("
    r"\bclick\b|\btype\b|\bfill\b|\binteract\b|form field|press the button|"
    r"нажми|клик|введи|заполни"
    r")"
)
AGENT_TASK = re.compile(
    r"(?i)("
    r"исследуй|сравни.{0,48}раздел|три раздела|нескольк[оаих].{0,24}страниц|"
    r"multi[- ]page|across (several|multiple) pages|autonomous web|web[- ]agent|"
    r"пройди несколько|собери (ссылки|источники).{0,24}нескольк|"
    r"найди три раздела|research the official site"
    r")"
)
BROWSER_TASK = re.compile(
    r"(?i)("
    r"открой.{0,40}браузер|в браузере|tinyfish browser|cloud browser|"
    r"посмотри страницу в браузере|open.{0,40}in (a |the )?browser|"
    r"перейди по ссылке|перейди по url|"
    r"render(ed)? page|js shell"
    r")"
)
SIMPLE_MATH = re.compile(
    r"(?i)^\s*((сколько будет|what('?s| is)|compute|посчитай)\s*)?\d+\s*[\+\-\×x\*\/]\s*\d+\s*[?.!]?\s*$"
)
HTTP_URL = re.compile(r"https?://[^\s<>\"']+")
PYTHON_SITE = re.compile(r"(?i)(официальн\w+\s+сайт\s+python|python\.org|official python (site|website))")


@dataclass(frozen=True)
class GoalClass:
    kind: Kind
    reason: str


NEGATED = re.compile(
    r"(?i)(do not|don't|never|без|не)\s+((log ?in|sign ?in|sign ?up|submit|buy|purchase|send|delete|upload|войти|отправ)[\s,]*)+"
)


def classify_agent_goal(goal: str, prompt: str = "") -> GoalClass:
    text = NEGATED.sub(" ", f"{goal or ''} {prompt or ''}").strip()
    if not text:
        return GoalClass("READ_ONLY", "empty_goal")
    if SIDE_EFFECT.search(text):
        return GoalClass("SIDE_EFFECT", "side_effect_language")
    if POTENTIAL.search(text):
        return GoalClass("POTENTIAL_SIDE_EFFECT", "potential_side_effect_language")
    return GoalClass("READ_ONLY", "read_only_research")


def looks_like_agent_task(prompt: str) -> bool:
    return bool(AGENT_TASK.search(prompt or ""))


def looks_like_browser_task(prompt: str) -> bool:
    return bool(BROWSER_TASK.search(prompt or ""))


def looks_like_simple_math(prompt: str) -> bool:
    return bool(SIMPLE_MATH.search((prompt or "").strip()))


def markdown_needs_browser(text: str) -> bool:
    value = (text or "").strip()
    if len(value) < 200 and re.search(r"(?i)loading|enable javascript|please wait|загрузка", value):
        return True
    return False


def first_http_url(text: str) -> str:
    match = HTTP_URL.search(text or "")
    if not match:
        return ""
    url = match.group(0).rstrip(").,]>\"'")
    if ".onion" in url.lower():
        return ""
    return url


def implied_start_url(prompt: str) -> str:
    url = first_http_url(prompt)
    if url:
        return url
    if PYTHON_SITE.search(prompt or ""):
        return "https://www.python.org/"
    return ""
