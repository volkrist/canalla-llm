"""Browser GUI adapter: OBSERVE → ACT → VERIFY with Playwright.

Used for real applications the agent builds (a local web app) and for any browser workflow the
user authorized. Each step returns the observed state *after* the action, so "the button was
clicked" is never treated as "the form appeared".

Selectors are role/name or `data-testid` first; raw CSS is a last resort. A step that does not
produce its expected change is reported as `ok=False` with the observed state, and the caller
decides what to do — the adapter never retries the same click forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GuiStep:
    action: str
    role: str = ""
    name: str = ""
    selector: str = ""
    value: str = ""
    expect: str = ""
    url: str = ""
    timeout_ms: int = 8000

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GuiStep":
        return cls(
            action=str(payload.get("action") or ""),
            role=str(payload.get("role") or ""),
            name=str(payload.get("name") or ""),
            selector=str(payload.get("selector") or ""),
            value=str(payload.get("value") or ""),
            expect=str(payload.get("expect") or ""),
            url=str(payload.get("url") or ""),
            timeout_ms=int(payload.get("timeout_ms") or 8000),
        )


@dataclass
class StepResult:
    action: str
    ok: bool
    observed: str = ""
    url: str = ""
    title: str = ""
    screenshot: str = ""
    error: str = ""
    console_errors: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": self.ok,
            "observed": self.observed[:600],
            "url": self.url,
            "title": self.title[:200],
            "screenshot": self.screenshot,
            "error": self.error[:300],
            "console_errors": self.console_errors[:5],
        }


def _locator(page, step: GuiStep):
    if step.role and step.name:
        return page.get_by_role(step.role, name=step.name)
    if step.selector.startswith("testid="):
        return page.get_by_test_id(step.selector.split("=", 1)[1])
    if step.selector:
        return page.locator(step.selector)
    raise ValueError("no_locator")


def run_gui(url: str, steps: list[GuiStep], *, screenshot_dir: str = "",
            headless: bool = True) -> dict[str, Any]:
    """Run the script in one browser session and return every observed state."""
    from playwright.sync_api import sync_playwright

    results: list[StepResult] = []
    console_errors: list[str] = []
    shots = Path(screenshot_dir) if screenshot_dir else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            results.append(_observe(page, "goto", "", shots, 1, console_errors))
            for index, step in enumerate(steps, start=2):
                result = _perform(page, step, shots, index, console_errors)
                results.append(result)
                if not result.ok:
                    break
        finally:
            browser.close()
    return {
        "ok": bool(results) and all(item.ok for item in results),
        "steps": [item.public() for item in results],
        "console_errors": console_errors[:10],
    }


def _perform(page, step: GuiStep, shots: Path | None, index: int,
             console_errors: list[str]) -> StepResult:
    try:
        if step.action == "goto":
            page.goto(step.url or step.value, wait_until="domcontentloaded",
                      timeout=step.timeout_ms)
        elif step.action == "click":
            _locator(page, step).click(timeout=step.timeout_ms)
        elif step.action == "fill":
            _locator(page, step).fill(step.value, timeout=step.timeout_ms)
        elif step.action == "press":
            _locator(page, step).press(step.value or "Enter", timeout=step.timeout_ms)
        elif step.action == "select":
            _locator(page, step).select_option(step.value, timeout=step.timeout_ms)
        elif step.action == "wait":
            page.wait_for_timeout(min(max(step.timeout_ms, 50), 15000))
        elif step.action == "expect_text":
            pass
        else:
            return StepResult(step.action, False, error=f"unknown_action:{step.action}")
        # Auto-waiting is Playwright's job; a short settle keeps assertions honest.
        page.wait_for_timeout(150)
        return _observe(page, step.action, step.expect, shots, index, console_errors)
    except Exception as error:  # noqa: BLE001 - a GUI failure is data for the agent
        observed = ""
        try:
            observed = page.locator("body").inner_text(timeout=2000)[:600]
        except Exception:  # noqa: BLE001
            pass
        return StepResult(step.action, False, observed=observed, error=f"{type(error).__name__}: {error}",
                          console_errors=console_errors[:5])


def _observe(page, action: str, expect: str, shots: Path | None, index: int,
             console_errors: list[str]) -> StepResult:
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    shot = ""
    if shots is not None:
        path = shots / f"{index:02d}-{action}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            shot = str(path)
        except Exception:  # noqa: BLE001
            shot = ""
    ok = (expect in body) if expect else True
    return StepResult(
        action=action,
        ok=ok,
        observed=body[:600],
        url=page.url,
        title=page.title(),
        screenshot=shot,
        error="" if ok else f"expected text not found: {expect!r}" if expect else "",
        console_errors=console_errors[:5],
    )


def window_present(title: str) -> bool:
    """A conservative native-window probe used by the verifier.

    It lists top-level window titles through the Windows API and matches a substring. It is
    read-only: no focus change, no clicking. On a non-Windows host it reports False, which the
    verifier surfaces as an unsupported/failed check — never as a PASS.
    """
    import sys

    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = False
    wanted = title.lower()

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if wanted in buffer.value.lower():
            found = True
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found
