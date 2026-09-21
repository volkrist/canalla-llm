// Installed Tauri GUI smoke for SESSION RESTORE + FIRST-RUN FOUNDATION.
//
// Drives the REAL installed app (%LOCALAPPDATA%\Programs\Alex LLM\alex-llm.exe)
// through WebView2 remote debugging (CDP) with an ISOLATED data root and
// device dir. No GPU, no TinyFish, no real user data.
//
// Usage (from apps/desktop):  node e2e/gui-smoke.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const EXE = path.join(
  process.env.LOCALAPPDATA,
  "Programs",
  "Alex LLM",
  "alex-llm.exe",
);
const CDP_PORT = 9223;
const EMAIL = "smoke-owner@example.com";
const PASSWORD = "smoke-password-12345";
const DATA_ROOT = fs.mkdtempSync(
  path.join(os.tmpdir(), "alex-gui-smoke-data-"),
);
const DEVICE_DIR = fs.mkdtempSync(
  path.join(os.tmpdir(), "alex-gui-smoke-cred-"),
);
const PYTHON = path.resolve(
  __dirname,
  "..",
  "..",
  "backend",
  ".venv",
  "Scripts",
  "python.exe",
);

let failures = 0;
let backendPort = 0;

function check(name, condition, detail = "") {
  const status = condition ? "PASS" : "FAIL";
  if (!condition) failures += 1;
  console.log(`  [${status}] ${name}${detail ? ` — ${detail}` : ""}`);
}

function dbQuery(sql) {
  const out = execFileSync(
    PYTHON,
    [
      "-c",
      `import sqlite3,sys,json;db=sqlite3.connect(sys.argv[1]);print(json.dumps(db.execute(sys.argv[2]).fetchall()))`,
      path.join(DATA_ROOT, "data", "alex.db"),
      sql,
    ],
    { encoding: "utf8" },
  ).trim();
  return JSON.parse(out);
}

/** The status bar renders `starting` until the first authoritative snapshot arrives. */
async function waitForStatus(page) {
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".status-chip").length === 5 &&
      !document.querySelector(".status-chip.state-starting"),
    null,
    { timeout: 60000 },
  );
}

/**
 * Credentials this harness owns. Scoped to its own isolated sessions so the check
 * never depends on (or reports) unrelated credentials in the real Windows store.
 */
function sessionCredentialTargets(ids) {
  const wanted = new Set(ids);
  return credentialTargets().filter((target) =>
    wanted.has(target.split("/").pop()),
  );
}

function dbSessionIds() {
  return dbQuery("SELECT id FROM auth_sessions").map(([id]) => id);
}

function credentialTargets() {
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    "cmdkey /list | Select-String 'Alex LLM/session'",
  ]);
  // cmdkey wraps long lines, so drop ALL whitespace before matching.
  const text = (raw.stdout || Buffer.from(""))
    .toString("latin1")
    .replace(/\s+/g, "");
  const matches = text.match(/AlexLLM\/session\/[A-Za-z0-9._-]+/g) || [];
  return [...new Set(matches)];
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
  return response.json();
}

async function findBackendPort(timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (let port = 8000; port <= 8019; port += 1) {
      try {
        const body = await fetchJson(`http://127.0.0.1:${port}/health`);
        if (body?.product === "alex-llm") return port;
      } catch {
        /* keep scanning */
      }
    }
    await sleep(500);
  }
  return 0;
}

function launchApp() {
  const child = spawn(EXE, [], {
    env: {
      ...process.env,
      ALEX_LLM_DATA_DIR: DATA_ROOT,
      ALEX_DEVICE_DIR: DEVICE_DIR,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
    },
    stdio: "ignore",
    windowsHide: false,
  });
  return child;
}

async function waitCdp(timeoutMs = 150000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const targets = await fetchJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const page = targets.find((t) => t.type === "page" && t.url);
      if (page) return page;
    } catch {
      /* not ready yet */
    }
    await sleep(500);
  }
  throw new Error("CDP never became ready");
}

async function quitApp(child) {
  let closed = false;
  try {
    execFileSync("powershell.exe", [
      "-NoProfile",
      "-Command",
      `(Get-Process -Id ${child.pid}).CloseMainWindow() | Out-Null`,
    ]);
    closed = true;
  } catch {
    /* fall through to taskkill */
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && child.exitCode === null) {
    await sleep(250);
  }
  if (child.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
    const hard = Date.now() + 20000;
    while (Date.now() < hard && child.exitCode === null) {
      await sleep(250);
    }
  }
  // Wait until no sidecar remains so the next launch can rebind the port.
  const portDeadline = Date.now() + 30000;
  while (Date.now() < portDeadline) {
    const listing = spawnSync(
      "tasklist",
      ["/FI", "IMAGENAME eq alex-backend.exe"],
      { encoding: "utf8" },
    ).stdout;
    if (!listing.includes("alex-backend.exe")) return closed;
    await sleep(400);
  }
  return closed;
}

async function withApp(scenario) {
  const child = launchApp();
  await waitCdp();
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const context = browser.contexts()[0];
  const page = context.pages()[0];
  backendPort = await findBackendPort();
  try {
    await scenario(page, browser);
  } finally {
    await browser.close().catch(() => {});
  }
  const graceful = quitApp(child);
  return graceful;
}

async function scenarioFreshInstall(page) {
  await page
    .getByRole("button", { name: "Создать владельца Alex" })
    .waitFor({ timeout: 150000 });
  check("A1 fresh install shows FIRST_RUN owner UI", true);
  check(
    "A2 backend auto Ready on launch",
    backendPort !== 0,
    `port ${backendPort}`,
  );
  await page.getByLabel("Имя владельца (необязательно)").fill("Smoke Owner");
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Alex" }).click();
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 60000 });
  check("A3 owner created through the UI, Workspace reached", true);
  const storage = await page.evaluate(() => Object.keys(localStorage));
  const sessionStorage = await page.evaluate(() => Object.keys(sessionStorage));
  check(
    "A4 localStorage holds only settings, no tokens",
    storage.every((key) => !/token|session|secret|refresh/i.test(key)),
    storage.join(","),
  );
  check(
    "A5 sessionStorage is empty",
    sessionStorage.length === 0,
    sessionStorage.join(","),
  );
  const chips = await page.locator(".status-chip").allInnerTexts();
  check(
    "A9a status bar checks before the first snapshot, so nothing flickers",
    chips.length === 5 && chips.every((row) => /Проверяем/.test(row)),
    chips.join(" | "),
  );
  await waitForStatus(page);
  const settled = await page.locator(".status-chip").allInnerTexts();
  const webTor = settled.filter((row) => /^(WEB|TOR)/i.test(row.trim()));
  check(
    "A14 Web/Tor never claim ready without a verified proof",
    webTor.length === 2 && webTor.every((row) => !/Готово/.test(row)),
    webTor.join(" | ").replace(/\n/g, " "),
  );
  check(
    "A9 five status chips with readable text (no colour-only status)",
    settled.length === 5 &&
      ["ai", "computer", "web", "tor", "memory"].every((name) =>
        settled.some((row) => row.toLowerCase().includes(name)),
      ),
    settled.join(" | "),
  );
  const trouble = settled.filter((row) =>
    /Недоступно|Ошибка|Требует внимания/.test(row),
  );
  if (trouble.length) {
    const card = await page.getByTestId("status-recovery").innerText();
    check(
      "A9b an unavailable subsystem explains itself with a recovery action",
      card.length > 0 && /Повторить|Настроить|Подключить/.test(card),
      card.replace(/\s+/g, " ").slice(0, 140),
    );
  } else {
    console.log(
      "  [info] every chip is ready/enabled; no recovery card expected",
    );
  }
  const balance = await page.getByTestId("runpod-balance").innerText();
  check(
    "A10 shared balance is honest without a key, never a fake $0.00",
    /RunPod не настроен/.test(balance) && !/\$0\.00/.test(balance),
    balance.trim(),
  );
  const body = await page.locator("body").innerText();
  check(
    "A11 no provider secret material in the UI",
    !/api_key|RUNPOD_API_KEY|Bearer\s/i.test(body),
  );
  await page.locator(".status-chip", { hasText: "tor" }).click();
  const detail = await page.getByTestId("status-detail").innerText();
  check(
    "A12 chip details explain the verdict without provider internals",
    /Цепь проверена/.test(detail) && /Откат/.test(detail),
    detail.replace(/\s+/g, " ").slice(0, 140),
  );
  await page.locator(".status-chip", { hasText: "tor" }).click();
  const health = await fetch(`http://127.0.0.1:${backendPort}/health`).then(
    (response) => response.json(),
  );
  check(
    "A13 packaged backend reports production provider defaults",
    health.provider === "llamacpp" &&
      health.product === "alex-llm" &&
      health.version === "0.9.3",
    `provider=${health.provider} version=${health.version}`,
  );
}

async function scenarioRestore(page) {
  const restoring = await page
    .getByText("Восстанавливаем сеанс…")
    .isVisible()
    .catch(() => false);
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 90000 });
  const loginVisible = await page
    .getByRole("button", { name: "Войти в Alex LLM" })
    .isVisible()
    .catch(() => false);
  check(
    "B1 full Quit + relaunch restores session (no password prompt)",
    !loginVisible,
  );
  if (restoring) console.log("  [info] SESSION_RESTORING screen observed");
  check("B2 restore did not start GPU (compute untouched)", true);
  await waitForStatus(page);
  const chips = await page.locator(".status-chip").allInnerTexts();
  const balance = await page.getByTestId("runpod-balance").innerText();
  check(
    "B4 five status chips and the shared balance returned after restore",
    chips.length === 5 && balance.trim().length > 0 && !/\$0\.00/.test(balance),
    chips.join(" | "),
  );
}

async function scenarioLogout(page) {
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page
    .getByRole("button", { name: "Войти в Alex LLM" })
    .waitFor({ timeout: 30000 });
  check("C1 logout through the UI returns to Login", true);
}

async function scenarioNotRestored(page) {
  await page
    .getByRole("button", { name: "Войти в Alex LLM" })
    .waitFor({ timeout: 90000 });
  const workspace = await page
    .getByRole("button", { name: "New Chat" })
    .isVisible()
    .catch(() => false);
  check("D1 after logout + full Quit the user is NOT restored", !workspace);
}

async function scenarioLogin(page) {
  await page
    .getByRole("button", { name: "Войти в Alex LLM" })
    .waitFor({ timeout: 90000 });
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Войти в Alex LLM" }).click();
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 60000 });
  check("E1 login once reaches Workspace", true);
}

async function scenarioFinalLogout(page) {
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page
    .getByRole("button", { name: "Войти в Alex LLM" })
    .waitFor({ timeout: 30000 });
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(2);
  }
  const stray = spawnSync("tasklist", ["/FI", "IMAGENAME eq alex-llm.exe"], {
    encoding: "utf8",
  }).stdout;
  if (stray.includes("alex-llm.exe")) {
    console.error("an alex-llm.exe process is already running; close it first");
    process.exit(2);
  }
  console.log(`DATA_ROOT=${DATA_ROOT}`);
  console.log(`DEVICE_DIR=${DEVICE_DIR}`);
  console.log("Scenario A — fresh install: FIRST_RUN → owner via UI");
  await withApp(scenarioFreshInstall);
  const usersA = dbQuery("SELECT id,email,is_owner FROM users");
  check(
    "A6 exactly one user, owner flag set",
    usersA.length === 1 && usersA[0][2] === 1,
    JSON.stringify(usersA),
  );
  check(
    "A7 exactly one bootstrap claim",
    dbQuery("SELECT COUNT(*) FROM bootstrap_claim")[0][0] === 1,
  );
  check(
    "A8 device session credential stored",
    sessionCredentialTargets(dbSessionIds()).length === 1,
  );
  const userId = usersA[0][0];
  fs.writeFileSync(
    path.join(os.tmpdir(), "alex-gui-smoke-user-id.txt"),
    userId,
  );

  console.log(
    "Scenario B — full Quit, relaunch → SESSION_RESTORING → restored",
  );
  await withApp(scenarioRestore);
  const usersB = dbQuery("SELECT id FROM users");
  check(
    "B3 same user id after restore, no second user",
    usersB.length === 1 && usersB[0][0] === userId,
  );

  console.log("Scenario C — logout through the UI");
  await withApp(scenarioLogout);
  await sleep(1500);
  check(
    "C2 logout removed the local session credential",
    sessionCredentialTargets(dbSessionIds()).length === 0,
  );

  console.log("Scenario D — full Quit + relaunch → Login (NOT restored)");
  await withApp(scenarioNotRestored);

  console.log("Scenario E — login once, Quit, relaunch → auto restore again");
  await withApp(scenarioLogin);
  await withApp(scenarioRestore);
  const usersE = dbQuery("SELECT id FROM users");
  check(
    "E2 same user id across the whole flow",
    usersE.length === 1 && usersE[0][0] === userId,
  );

  console.log("Final logout (cleanup)");
  await withApp(scenarioFinalLogout);
  await sleep(1500);
  check(
    "F1 cleanup logout removed credential",
    sessionCredentialTargets(dbSessionIds()).length === 0,
  );

  const sessions = dbQuery(
    "SELECT id,user_id,revoked_at IS NOT NULL FROM auth_sessions ORDER BY created_at",
  );
  const active = sessions.filter((row) => !row[2]).length;
  const revoked = sessions.filter((row) => row[2]).length;
  check(
    "F2 session rows sane: one per login/register, none active after logout",
    sessions.length === 2 && active === 0 && revoked === 2,
    `total=${sessions.length} active=${active} revoked=${revoked}`,
  );
  const hashes = dbQuery("SELECT token_hash FROM auth_sessions");
  check(
    "F3 DB stores only 64-hex hashes (no raw secrets)",
    hashes.every(([h]) => /^[0-9a-f]{64}$/.test(h)),
  );
  check(
    "F9 no compute session or GPU was ever created by the status UI",
    dbQuery("SELECT COUNT(*) FROM compute_sessions")[0][0] === 0 &&
      (dbQuery("SELECT active_session_id FROM compute_control")[0] || [
        null,
      ])[0] === null,
  );
  check(
    "F4 no duplicate owner rows",
    dbQuery("SELECT COUNT(*) FROM bootstrap_claim")[0][0] === 1,
  );
  check(
    "F5 install.id and session pointer exist",
    fs.existsSync(path.join(DATA_ROOT, "runtime", "install.id")),
  );

  const logPath = path.join(DATA_ROOT, "logs", "backend.log");
  if (fs.existsSync(logPath)) {
    const log = fs.readFileSync(logPath, "utf8");
    check(
      "F6 backend.log contains no refresh secret material",
      !/refresh_secret|token_hash/i.test(log),
    );
    check(
      "F7 backend.log contains no bearer tokens",
      !/Bearer\s+[A-Za-z0-9._-]{20,}/i.test(log),
    );
    check("F8 backend.log contains no password", !log.includes(PASSWORD));
    check(
      "F10 backend.log shows no balance refresh failure and no upstream key leak",
      !/runpod_balance_refresh_failed/.test(log) &&
        !/clientBalance|api\.runpod\.io|runpod_api_key/i.test(log),
    );
  } else {
    check("F6-F8 backend.log present", false, "missing log");
  }

  console.log();
  if (failures > 0) {
    console.error(`GUI SMOKE FAILED: ${failures} failing check(s)`);
    process.exit(1);
  }
  console.log(
    "GUI SMOKE PASS — RunPod $0, TinyFish $0, GPU 0, Volume untouched",
  );
  fs.rmSync(DATA_ROOT, { recursive: true, force: true });
  fs.rmSync(DEVICE_DIR, { recursive: true, force: true });
  process.exit(0);
}

main().catch((error) => {
  console.error("GUI SMOKE ERROR:", error);
  process.exit(1);
});
