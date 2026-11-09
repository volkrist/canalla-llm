// Installed 1.2.6 UI: the task status line, in the real WebView2 window.
//
// The window is the REAL installed build (%LOCALAPPDATA%\Programs\Canalla LLM\alex-llm.exe), driven
// over CDP. The data root, device directory and credential names are isolated, so the owner's
// chats, pairing and Canalla Cloud enrollment are never read or written. No GPU, no Pod, no paid
// provider: task payloads are served to the renderer from the shapes the backend now produces, and
// this run proves the installed frontend renders them honestly.
//
// Usage (from apps/desktop): node ../../artifacts/acceptance-release-126/installed_ui_status.mjs

import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..", "..");
const EXE =
  [
    path.join(process.env.LOCALAPPDATA, "Programs", "Canalla LLM", "alex-llm.exe"),
    path.join(process.env.LOCALAPPDATA, "Programs", "Alex LLM", "alex-llm.exe"),
  ].find((candidate) => fs.existsSync(candidate)) ?? "";
const CDP_PORT = 9245;
const PYTHON = path.join(REPO, "apps", "backend", ".venv", "Scripts", "python.exe");
const DATA_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "canalla-ui-data-"));
const DEVICE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "canalla-ui-cred-"));
const SUFFIX = Date.now().toString(36);
const DEVICE_CREDENTIAL_TARGET = `Alex LLM/device-credential-ui-${SUFFIX}`;
const GATEWAY_CREDENTIAL_NAME = `canalla-ui-${SUFFIX}`;
const OUT = path.join(REPO, "artifacts", "acceptance-release-126");

const CREDENTIAL_DELETER = `
import ctypes, sys
from ctypes import wintypes
api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
api.CredDeleteW.restype = wintypes.BOOL
if api.CredDeleteW(sys.argv[1], 1, 0):
    sys.exit(0)
sys.exit(0 if ctypes.get_last_error() == 1168 else 1)
`;

function deleteCredential(target) {
  try {
    execFileSync(PYTHON, ["-c", CREDENTIAL_DELETER, target], { stdio: "ignore" });
  } catch {
    /* nothing of ours is left */
  }
}

let failures = 0;
function check(name, condition, detail = "") {
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures += 1;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function launchApp() {
  return spawn(EXE, [], {
    env: {
      ...process.env,
      ALEX_LLM_DATA_DIR: DATA_ROOT,
      ALEX_DEVICE_DIR: DEVICE_DIR,
      ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
      ALEX_GATEWAY_CREDENTIAL_NAME: GATEWAY_CREDENTIAL_NAME,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
    },
    stdio: "ignore",
    windowsHide: false,
  });
}

async function waitCdp(timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`, {
        signal: AbortSignal.timeout(2000),
      });
      const targets = await response.json();
      const page = targets.find((target) => target.type === "page" && target.url);
      if (page) return page;
    } catch {
      /* not ready yet */
    }
    await sleep(500);
  }
  throw new Error("CDP never became ready");
}

function stopApp(child) {
  execFileSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    `taskkill /T /F /PID ${child.pid} | Out-Null`,
  ]);
}

const STEPS = [
  {
    id: "s1",
    title: "Inspect",
    description: "",
    status: "COMPLETED",
    tool_category: "local_fs",
    verification_required: false,
    attempts: 1,
    result_summary: "",
    key: "inspect",
  },
  {
    id: "s2",
    title: "Write",
    description: "",
    status: "COMPLETED",
    tool_category: "local_fs",
    verification_required: false,
    attempts: 1,
    result_summary: "",
    key: "write",
  },
  {
    id: "s3",
    title: "Verify",
    description: "",
    status: "COMPLETED",
    tool_category: "verification",
    verification_required: true,
    attempts: 1,
    result_summary: "",
    key: "verify",
  },
];

function task(extra) {
  return {
    id: "installed-status-1",
    title: "Fix the parser",
    status: "VERIFYING",
    chat_id: null,
    workspace: "C:\\work",
    current_step: "verify",
    current_phase: "VERIFYING",
    plan_revision: 1,
    tool_calls_used: 9,
    tool_budget: 40,
    files_changed: 2,
    file_change_budget: 20,
    elapsed_runtime: 272,
    runtime_budget: 1800,
    last_error: null,
    completion_summary: null,
    message: "Working",
    phase: "verifying",
    terminal: false,
    waiting: false,
    current_action: "Run pytest",
    completed_count: 3,
    remaining_count: 0,
    last_progress_at: new Date().toISOString(),
    idle_seconds: 3,
    stalled: false,
    resume_available: false,
    budgets: {
      tools_used: 9,
      tools_max: 40,
      files_changed: 2,
      files_max: 20,
      seconds_used: 272,
      seconds_max: 1800,
    },
    web: {
      searches: 1,
      fetches: 0,
      tor_searches: 0,
      tor_fetches: 0,
      pages_requested: 0,
      pages_opened: 0,
      pages_failed: 0,
      by_origin: { model: 1, server_policy: 0, other: 0 },
    },
    steps: STEPS,
    ...extra,
  };
}

async function main() {
  if (!EXE) throw new Error("the installed Canalla executable was not found");
  fs.mkdirSync(OUT, { recursive: true });
  const child = launchApp();
  let browser;
  try {
    await waitCdp();
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    const context = browser.contexts()[0];
    const page = context.pages()[0] ?? (await context.waitForEvent("page"));
    await page.setViewportSize({ width: 1200, height: 820 });

    // A fresh isolated profile opens Canalla's own first-run owner form. (The public login screen
    // appears only when a user already exists, which an isolated data root never has.)
    const owner = page.getByRole("button", { name: "Создать владельца Canalla" });
    const register = page.getByRole("button", { name: "Регистрация", exact: true });
    const email = page.getByLabel("Email", { exact: true });
    await email.waitFor({ timeout: 180000 });
    if (await owner.isVisible().catch(() => false)) {
      await page.getByLabel("Имя владельца (необязательно)").fill("UI Smoke");
      await email.fill(`ui-${SUFFIX}@example.com`);
      await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
      await owner.click();
    } else if (await register.isVisible().catch(() => false)) {
      await register.click();
      await email.fill(`ui-${SUFFIX}@example.com`);
      await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
      await page.getByRole("button", { name: "Создать аккаунт", exact: true }).click();
    } else {
      await email.fill(`ui-${SUFFIX}@example.com`);
      await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
      await page.getByRole("button", { name: "Войти", exact: true }).click();
    }
    await page.getByTestId("ai-connection").waitFor({ timeout: 180000 });

    const composer = page.getByRole("textbox", { name: "Сообщение", exact: true });
    await composer.fill("Привет");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page
      .locator(".message.assistant")
      .last()
      .waitFor({ timeout: 120000 });

    async function showScenario(name, payload, marker, assertions) {
      await page.unroute("**/tasks?chat_id=*").catch(() => {});
      await page.route("**/tasks?chat_id=*", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([payload]),
        }),
      );
      // Re-selecting the chat refetches `/tasks`; the panel is already visible from the previous
      // scenario, so waiting for *this* payload's marker is what makes the assertions read the
      // new state instead of the previous one.
      await page.locator(".chat-select").first().click();
      await page.waitForFunction(
        (needle) =>
          (document.querySelector(".task-panel")?.textContent || "").includes(needle),
        marker,
        { timeout: 30000 },
      );
      const panel = page.locator(".task-panel");
      for (const [label, fn] of assertions) {
        const text = await panel.innerText();
        check(`${name}: ${label}`, fn(text), text.replace(/\s+/g, " ").slice(0, 180));
      }
      await page.screenshot({
        path: path.join(OUT, `installed-status-${name}.png`),
      });
    }

    await showScenario("verifying", task({}), "Run pytest", [
      ["says Работает", (text) => text.includes("Работает")],
      ["names the current action", (text) => text.includes("Run pytest")],
      ["never says Завершено", (text) => !text.includes("Завершено")],
      ["offers Stop", (text) => text.includes("Stop")],
    ]);

    await showScenario(
      "waiting-resource",
      task({
        phase: "waiting_resource",
        status: "WAITING_LLM",
        terminal: false,
        error_code: "offline",
        current_action: "",
        idle_seconds: 42,
      }),
      "Ждём вычислительные ресурсы",
      [
        ["says the compute resources are awaited", (text) => text.includes("Ждём вычислительные ресурсы")],
        ["shows the real reason", (text) => text.includes("offline")],
        ["does not say Работает", (text) => !text.includes("Работает")],
      ],
    );

    await showScenario(
      "partial",
      task({
        phase: "partial",
        status: "FAILED",
        terminal: true,
        stop_reason: "tests_not_verified",
        completion_summary: null,
        current_action: "Verify",
        completed_count: 2,
        remaining_count: 1,
        resume_available: true,
        steps: [...STEPS.slice(0, 2), { ...STEPS[2], status: "PENDING" }],
      }),
      "Не удалось завершить",
      [
        ["says it could not finish", (text) => text.includes("Не удалось завершить")],
        ["names the reason", (text) => text.includes("tests_not_verified")],
        ["offers Resume", (text) => text.includes("Resume")],
      ],
    );

    await showScenario(
      "page-limit",
      task({
        phase: "researching",
        status: "RESEARCHING",
        limit: {
          code: "page_limit",
          pages: 10,
          max_pages: 10,
          fetches: 3,
          max_fetch: 12,
          searches: 1,
          max_search: 5,
          calls: 4,
          max_calls: 40,
        },
      }),
      "Лимит страниц",
      [
        ["names the real page limit", (text) => text.includes("10 из 10 страниц")],
        ["names the fetch counter", (text) => text.includes("3 из 12 загрузок")],
      ],
    );

    await showScenario(
      "completed",
      task({
        phase: "completed",
        status: "COMPLETED",
        terminal: true,
        completion_summary: "Проект создан, 5 тестов пройдено.",
        current_action: "",
      }),
      "Завершено",
      [
        ["says Завершено", (text) => text.includes("Завершено")],
        ["states what was done", (text) => text.includes("5 тестов пройдено")],
        ["offers no Stop", (text) => !text.includes("Stop")],
      ],
    );

    // The composer must not run a second, contradicting clock while the task owns the turn.
    await page.unroute("**/tasks?chat_id=*").catch(() => {});
    console.log("");
  } finally {
    if (browser) await browser.close().catch(() => {});
    stopApp(child);
    deleteCredential(DEVICE_CREDENTIAL_TARGET);
    deleteCredential(`Alex LLM/gateway/${GATEWAY_CREDENTIAL_NAME}`);
    fs.rmSync(DATA_ROOT, { recursive: true, force: true });
    fs.rmSync(DEVICE_DIR, { recursive: true, force: true });
  }
  console.log(failures === 0 ? "INSTALLED-UI: PASS" : `INSTALLED-UI: FAIL (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error("INSTALLED-UI: FAIL —", error);
  process.exit(1);
});
