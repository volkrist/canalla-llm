// Installed Tauri GUI smoke for the CENTRAL RUNPOD GATEWAY slice (Canalla Cloud).
//
// Drives the REAL installed app (%LOCALAPPDATA%\Programs\Alex LLM\alex-llm.exe) through
// WebView2 remote debugging (CDP) with an ISOLATED data root and device dir, against a
// LOCAL test Gateway on http://127.0.0.1:9011.
//
// No Pod, no GPU, no Network Volume change, no TinyFish. The only provider traffic is
// the READ-ONLY balance query the Gateway performs for /balance. This harness never
// touches POST /compute/ensure or POST /compute/stop and never presses «Запустить AI».
//
// Secrets stay in this process' memory: the RunPod master key and the installation
// secret are read through the OS credential store only to prove they never appear in
// the DOM or in the app's backend log. They are never printed and never written to disk.
//
// Usage (from apps/desktop):
//   set ALEX_SMOKE_ACTIVATION_CODE=<one-time code from the Gateway operator CLI>
//   set ALEX_SMOKE_SETUP=<path to the freshly built *setup.exe>   (optional, for step J)
//   node e2e/cloud-smoke.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The product installs into Programs\Canalla LLM; a legacy Alex LLM install can still exist
// during an upgrade acceptance, so prefer the current name and fall back to the old one.
const EXE =
  [
    path.join(
      process.env.LOCALAPPDATA,
      "Programs",
      "Canalla LLM",
      "alex-llm.exe",
    ),
    path.join(process.env.LOCALAPPDATA, "Programs", "Alex LLM", "alex-llm.exe"),
  ].find((candidate) => fs.existsSync(candidate)) ??
  path.join(
    process.env.LOCALAPPDATA,
    "Programs",
    "Canalla LLM",
    "alex-llm.exe",
  );
const CDP_PORT = 9224;
const GATEWAY_URL = "http://127.0.0.1:9011";
const GATEWAY_DB = path.join(os.tmpdir(), "alex-gateway-smoke", "gateway.db");
const STAMP = `${Date.now()}`;
const GATEWAY_CREDENTIAL_NAME = `smoke-${STAMP}`;
const GATEWAY_CREDENTIAL_TARGET = `Alex LLM/gateway/${GATEWAY_CREDENTIAL_NAME}`;
const DEVICE_CREDENTIAL_TARGET = `Alex LLM/session-smoke-${STAMP}`;
const PROVIDER_TARGET = "Alex LLM/provider/runpod";
const CODE = (process.env.ALEX_SMOKE_ACTIVATION_CODE || "").trim();
const OWNER_EMAIL = "cloud-owner@example.com";
const SECOND_EMAIL = "cloud-second@example.com";
const PASSWORD = "cloud-smoke-password-12345";
const DATA_ROOT = fs.mkdtempSync(
  path.join(os.tmpdir(), "alex-cloud-smoke-data-"),
);
const DEVICE_DIR = fs.mkdtempSync(
  path.join(os.tmpdir(), "alex-cloud-smoke-cred-"),
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
const skipped = [];

function check(name, condition, detail = "") {
  const status = condition ? "PASS" : "FAIL";
  if (!condition) failures += 1;
  console.log(`  [${status}] ${name}${detail ? ` — ${detail}` : ""}`);
}

function skip(name, reason) {
  skipped.push({ name, reason });
  console.log(`  [SKIP] ${name} — ${reason}`);
}

function flat(text) {
  return String(text).replace(/\s+/g, " ").trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url, timeout = 3000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeout) });
  return response.json();
}

/** Reads a Windows generic credential into THIS process only. Never logged. */
const CREDENTIAL_READER = `
import ctypes
import sys
from ctypes import wintypes


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
api.CredReadW.restype = wintypes.BOOL
api.CredFree.argtypes = [ctypes.c_void_p]
pointer = ctypes.c_void_p()
if not api.CredReadW(sys.argv[1], 1, 0, ctypes.byref(pointer)):
    raise SystemExit(3)
credential = ctypes.cast(pointer, ctypes.POINTER(CREDENTIALW)).contents
sys.stdout.write(
    ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-8", "ignore")
)
api.CredFree(pointer)
`;

const CREDENTIAL_DELETER = `
import ctypes
import sys
from ctypes import wintypes

api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
api.CredDeleteW.restype = wintypes.BOOL
if not api.CredDeleteW(sys.argv[1], 1, 0):
    raise SystemExit(4)
`;

function readCredential(target) {
  try {
    const out = execFileSync(PYTHON, ["-c", CREDENTIAL_READER, target], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return out.trim() || null;
  } catch {
    return null;
  }
}

function credentialTargets() {
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    "cmdkey /list | Select-String 'Alex LLM'",
  ]);
  // cmdkey wraps long lines, so drop ALL whitespace before matching, then rebuild the
  // real target name (whose display form contains a space).
  const text = (raw.stdout || Buffer.from(""))
    .toString("latin1")
    .replace(/\s+/g, "");
  const keys = text.match(/AlexLLM\/[A-Za-z0-9._/-]+/g) || [];
  return [...new Set(keys.map(realTarget))];
}

/** `AlexLLM/...` (as listed) -> `Alex LLM/...` (as stored, the name used to delete). */
function realTarget(key) {
  return key.startsWith("AlexLLM/")
    ? "Alex LLM/" + key.slice("AlexLLM/".length)
    : key;
}

let cachedKeyPrefix = null;

/** The first 12 characters of the stored RunPod key, kept in memory only. */
function providerKeyPrefix() {
  if (cachedKeyPrefix === null) {
    const key = readCredential(PROVIDER_TARGET);
    cachedKeyPrefix = key ? key.slice(0, 12) : "";
  }
  return cachedKeyPrefix;
}

/** cmdkey cannot address a target that contains spaces, so delete through the OS API. */
function deleteCredential(target) {
  try {
    execFileSync(PYTHON, ["-c", CREDENTIAL_DELETER, target], {
      stdio: ["ignore", "ignore", "ignore"],
    });
    return true;
  } catch {
    return false;
  }
}

function dbQuery(sql, root = DATA_ROOT) {
  const out = execFileSync(
    PYTHON,
    [
      "-c",
      `import sqlite3,sys,json;db=sqlite3.connect(sys.argv[1]);print(json.dumps(db.execute(sys.argv[2]).fetchall()))`,
      path.join(root, "data", "alex.db"),
      sql,
    ],
    { encoding: "utf8" },
  ).trim();
  return JSON.parse(out);
}

function dbSessionIds() {
  return dbQuery("SELECT id FROM auth_sessions").map(([id]) => id);
}

function sessionCredentialTargets(ids) {
  const wanted = new Set(ids);
  return credentialTargets().filter((target) =>
    wanted.has(target.split("/").pop()),
  );
}

/** Read-only view of the Gateway's own database: no HTTP write, no provider call. */
function gatewayCompute() {
  const out = execFileSync(
    PYTHON,
    [
      "-c",
      [
        "import sqlite3,sys,json",
        "db=sqlite3.connect(sys.argv[1])",
        "control=db.execute('SELECT state, error_code, active_session_id, create_attempts FROM gateway_compute').fetchall()",
        "sessions=db.execute('SELECT COUNT(*) FROM gateway_sessions').fetchone()[0]",
        "events=db.execute('SELECT operation, result, error_code FROM audit_events ORDER BY created_at').fetchall()",
        "print(json.dumps({'control': control, 'sessions': sessions, 'events': events}))",
      ].join(";"),
      GATEWAY_DB,
    ],
    { encoding: "utf8" },
  ).trim();
  const body = JSON.parse(out);
  const row = body.control[0] || ["offline", null, null, 0];
  return {
    state: row[0],
    errorCode: row[1],
    activeSession: row[2],
    createAttempts: row[3],
    sessions: body.sessions,
    events: body.events,
  };
}

/** The status bar renders `starting` until the first authoritative snapshot arrives. */
async function waitForStatus(page) {
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".status-chip").length === 5 &&
      !document.querySelector(".status-chip.state-starting"),
    null,
    { timeout: 90000 },
  );
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
      // The installation-global cloud settings the product reads from the environment.
      ALEX_GATEWAY_URL: GATEWAY_URL,
      ALEX_GATEWAY_CREDENTIAL_NAME: GATEWAY_CREDENTIAL_NAME,
      ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
    },
    stdio: "ignore",
    windowsHide: false,
  });
  return child;
}

async function waitCdp(timeoutMs = 180000) {
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

async function withApp(scenario, phase) {
  const alive = await fetchJson(`${GATEWAY_URL}/health`).catch(() => null);
  check(
    `0.x local test Gateway reachable for ${phase}`,
    alive?.ready === true,
    alive
      ? ""
      : "the Gateway process is gone: dependent checks are environment-limited",
  );
  const child = launchApp();
  let failure = null;
  let browser = null;
  try {
    await waitCdp();
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    const context = browser.contexts()[0];
    const page = context.pages()[0];
    backendPort = await findBackendPort();
    await scenario(page, browser);
  } catch (error) {
    failure = error;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  // The app is always closed, so a failing check can never leave a stray process.
  const graceful = await quitApp(child);
  if (failure) throw failure;
  return graceful;
}

// --------------------------------------------------------------------------- UI helpers

function dialog(page) {
  return page.locator("dialog.settings-dialog");
}

async function openSettingsSection(page, section) {
  // The dialog is modal and it survives tab switches, so only open it when it is closed.
  if ((await dialog(page).count()) === 0) {
    await page.getByRole("button", { name: "Settings" }).first().click();
    await dialog(page).waitFor({ timeout: 30000 });
  }
  // Scoped to the dialog: the workspace has its own buttons with similar names.
  await dialog(page)
    .getByRole("button", { name: section, exact: true })
    .click();
}

async function closeSettings(page) {
  await dialog(page).getByRole("button", { name: "Закрыть настройки" }).click();
  await page
    .locator("dialog.settings-dialog")
    .waitFor({ state: "hidden", timeout: 20000 })
    .catch(() => {});
}

async function dialogText(page) {
  return flat(await dialog(page).innerText());
}

/** The panel starts with an unknown cloud state, so wait until the first read landed. */
async function waitForCloudPanelLoaded(page, timeout = 90000) {
  await page
    .waitForFunction(
      () =>
        !/проверяем/.test(
          document.querySelector("dialog.settings-dialog")?.innerText || "",
        ),
      null,
      { timeout },
    )
    .catch(() => {});
}

async function waitForDialogText(page, pattern, timeout = 120000) {
  try {
    await page.waitForFunction(
      ([source]) =>
        new RegExp(source).test(
          document.querySelector("dialog.settings-dialog")?.innerText || "",
        ),
      [pattern],
      { timeout },
    );
    return true;
  } catch {
    return false;
  }
}

async function waitForWorkspace(page, timeout = 180000) {
  try {
    await page.getByRole("button", { name: "New Chat" }).waitFor({ timeout });
    return true;
  } catch {
    return false;
  }
}

async function balanceText(page) {
  return flat(await page.getByTestId("runpod-balance").innerText());
}

async function waitForBalance(page, timeout = 120000) {
  try {
    await page.waitForFunction(
      () =>
        /\$\s?\d/.test(
          document.querySelector('[data-testid="runpod-balance"]')
            ?.textContent || "",
        ),
      null,
      { timeout },
    );
  } catch {
    /* fall through: the caller reports the honest value it sees */
  }
  return balanceText(page);
}

// --------------------------------------------------------------------------- scenarios

async function scenarioFirstRunAndEnroll(page) {
  await page
    .getByRole("button", { name: "Создать владельца Canalla" })
    .waitFor({ timeout: 180000 });
  check("A1 first run asks for the Alex owner (FIRST_RUN owner UI)", true);
  check(
    "A2 the desktop-owned backend is Ready on launch",
    backendPort !== 0,
    `port ${backendPort}`,
  );
  await page
    .getByLabel("Имя владельца (необязательно)")
    .fill("Cloud Smoke Owner");
  await page.getByLabel("Email", { exact: true }).fill(OWNER_EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Canalla" }).click();
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 90000 });
  check("A3 the owner is created through the app UI", true);

  await waitForStatus(page);
  const chips = (await page.locator(".status-chip").allInnerTexts()).map(flat);
  check(
    "B1 five status chips render and none is stuck on Проверяем",
    chips.length === 5 && !chips.some((row) => /Проверяем/.test(row)),
    chips.join(" | "),
  );
  check(
    "B2 the AI chip does not claim Готово while no compute runs",
    Boolean(chips[0]) && !/Готово/.test(chips[0]),
    chips[0],
  );

  // C1 — Settings offers the Canalla Cloud tab with the one-time activation flow.
  await openSettingsSection(page, "Canalla Cloud");
  const cloudTab = await dialogText(page);
  check(
    "C1 Settings offers the Canalla Cloud tab with the activation code and «Подключить»",
    /Canalla Cloud/.test(cloudTab) &&
      cloudTab.includes("Адрес Gateway") &&
      cloudTab.includes("Код активации") &&
      (await dialog(page)
        .getByRole("button", { name: "Подключить" })
        .count()) === 1,
    cloudTab.slice(0, 120),
  );
  await openSettingsSection(page, "AI / Compute");
  console.log(
    `  [info] before enrollment AI / Compute shows the direct-mode RunPod key input: ${
      (await dialog(page).getByLabel("RunPod API key").count()) === 1
    }`,
  );

  // D — enroll this installation with the one-time activation code.
  await openSettingsSection(page, "Canalla Cloud");
  const address = dialog(page).getByLabel("Адрес Gateway");
  if (!(await address.inputValue()).trim()) await address.fill(GATEWAY_URL);
  check(
    "D1 the Gateway address is the local test Gateway",
    (await address.inputValue()).trim() === GATEWAY_URL,
    (await address.inputValue()).trim(),
  );
  await dialog(page).getByLabel("Код активации").fill(CODE);
  await dialog(page).getByRole("button", { name: "Подключить" }).click();
  const connected = await waitForDialogText(
    page,
    "Canalla Cloud\\s*·\\s*Подключено",
    180000,
  );
  const enrolled = await dialogText(page);
  check(
    "D2 the panel reports «Canalla Cloud · Подключено»",
    connected,
    enrolled.slice(0, 160),
  );
  check(
    "D3 the app restarted its owned backend with the shared environment",
    /перезапущен/.test(enrolled),
    enrolled.slice(0, 160),
  );
  check(
    "D4 the panel shows the installation and the Gateway without any secret",
    /Установка:/.test(enrolled) &&
      /Gateway:\s*http:\/\/127\.0\.0\.1:9011/.test(enrolled),
  );
  // I (panel scope) — the panel is unmounted when Settings closes, so read it now.
  const panelHtml = await dialog(page).innerHTML();
  const keyPrefix = providerKeyPrefix();
  check(
    "I1 the Canalla Cloud panel DOM never contains an installation secret",
    !/installation_secret/i.test(panelHtml) &&
      !/installation-secret/i.test(panelHtml),
  );
  check(
    "I2 the Canalla Cloud panel DOM never contains the RunPod key",
    !keyPrefix || !panelHtml.includes(keyPrefix),
    keyPrefix
      ? "compared against the stored provider credential"
      : "no provider credential found",
  );
  check(
    "I3 the Canalla Cloud panel never renders the activation code",
    !(await dialog(page).innerText()).includes(CODE),
  );

  // F — the shared AI controls exist and nothing can be started by accident.
  const startAi = dialog(page).getByRole("button", { name: "Запустить AI" });
  const stopAi = dialog(page).getByRole("button", { name: "Остановить AI" });
  check(
    "F1 the Canalla Cloud panel exposes the shared AI controls",
    (await startAi.count()) === 1 && (await startAi.isVisible()),
  );
  check(
    "F2 «Остановить AI» is disabled because no managed session exists",
    (await stopAi.count()) === 1 && (await stopAi.isDisabled()),
  );

  // C2/C3 — in shared mode the RunPod key input is gone.
  await openSettingsSection(page, "AI / Compute");
  const computeTab = await dialogText(page);
  const keyInputs = await dialog(page).getByLabel("RunPod API key").count();
  const saveKey = await dialog(page)
    .getByRole("button", { name: "Сохранить ключ" })
    .count();
  check(
    "C2 AI / Compute shows the muted shared-mode note about Ключ RunPod",
    /Ключ RunPod здесь не нужен/.test(computeTab),
    computeTab.slice(0, 120),
  );
  check(
    "C3 shared mode offers no RunPod key input",
    keyInputs === 0 && saveKey === 0,
    `inputs=${keyInputs} saveButtons=${saveKey}`,
  );

  // E — the shared balance becomes visible (settings closed, status bar read).
  await closeSettings(page);
  const balance = await waitForBalance(page, 180000);
  const gatewayBody = await fetchJson(`${GATEWAY_URL}/health`).catch(
    () => null,
  );
  if (/\$\s?\d/.test(balance)) {
    check("E1 the shared RunPod balance is visible in the app", true, balance);
  } else if (gatewayBody?.provider_configured === false) {
    skip(
      "E1 the shared RunPod balance is visible in the app",
      `the Gateway reported no provider account: ${balance}`,
    );
  } else {
    check("E1 the shared RunPod balance is visible in the app", false, balance);
  }

  // B3 + I (page scope) — honest chip after connecting, no secret material left.
  await waitForStatus(page);
  const settled = (await page.locator(".status-chip").allInnerTexts()).map(
    flat,
  );
  check(
    "B3 after connecting, the AI chip still reports an honest not-ready state",
    settled.length === 5 &&
      !/Готово/.test(settled[0]) &&
      !settled.some((row) => /Проверяем/.test(row)),
    settled.join(" | "),
  );
  const html = await page.content();
  check(
    "I4 the rendered page never contains an installation secret",
    !/installation_secret/i.test(html) && !/installation-secret/i.test(html),
  );
  check(
    "I5 the rendered page never contains the RunPod key",
    !keyPrefix || !html.includes(keyPrefix),
  );
}

async function scenarioLogoutAndSecondUser(page) {
  check(
    "G0 the owner session is active before logout",
    await waitForWorkspace(page, 150000),
  );
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  let loginShown = true;
  try {
    await page
      .getByRole("button", { name: "Войти в Canalla LLM" })
      .waitFor({ timeout: 60000 });
  } catch {
    loginShown = false;
  }
  check("G1 logout through the UI returns to the login screen", loginShown);

  await openSettingsSection(page, "Canalla Cloud");
  await waitForCloudPanelLoaded(page);
  const afterLogout = await dialogText(page);
  check(
    "G2 after logout Canalla Cloud is still connected and asks for no code",
    !afterLogout.includes("Код активации") &&
      !/Подключить/.test(afterLogout) &&
      /Gateway:\s*http/.test(afterLogout),
    afterLogout.slice(0, 160),
  );
  await closeSettings(page);

  await page.getByRole("button", { name: "Регистрация" }).click();
  await page.getByLabel("Email", { exact: true }).fill(SECOND_EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  check(
    "G3 a second local user registers through the app UI",
    await waitForWorkspace(page, 90000),
  );

  await openSettingsSection(page, "Canalla Cloud");
  const secondConnected = await waitForDialogText(
    page,
    "Canalla Cloud\\s*·\\s*Подключено",
    120000,
  );
  const second = await dialogText(page);
  check(
    "G4 the second local user is still connected and is asked for no code",
    secondConnected &&
      !second.includes("Код активации") &&
      !/Подключить/.test(second),
    second.slice(0, 160),
  );
  await closeSettings(page);
  await waitForStatus(page);
}

async function scenarioRestoreConnected(page, label) {
  const workspace = await waitForWorkspace(page, 180000);
  const loginVisible = await page
    .getByRole("button", { name: "Войти в Canalla LLM" })
    .isVisible()
    .catch(() => false);
  check(
    `${label}1 full Quit + relaunch restores the local session (no password prompt)`,
    workspace && !loginVisible,
  );
  await waitForStatus(page);
  await openSettingsSection(page, "Canalla Cloud");
  const connected = await waitForDialogText(
    page,
    "Canalla Cloud\\s*·\\s*Подключено",
    150000,
  );
  const panel = await dialogText(page);
  check(
    `${label}2 Canalla Cloud is still connected without entering an activation code`,
    connected && !panel.includes("Код активации") && !/Подключить/.test(panel),
    panel.slice(0, 160),
  );
  await closeSettings(page);
  const balance = await waitForBalance(page, 90000);
  check(
    `${label}3 the shared balance is shown again`,
    /\$\s?\d/.test(balance),
    balance,
  );
}

// -------------------------------------------------------------------------------- main

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(2);
  }
  if (!CODE) {
    console.error("ALEX_SMOKE_ACTIVATION_CODE is required");
    process.exit(2);
  }
  const stray = spawnSync("tasklist", ["/FI", "IMAGENAME eq alex-llm.exe"], {
    encoding: "utf8",
  }).stdout;
  if (stray.includes("alex-llm.exe")) {
    console.error("an alex-llm.exe process is already running; close it first");
    process.exit(2);
  }
  console.log("Canalla Cloud GUI smoke against the REAL installed app");
  console.log(`DATA_ROOT=${DATA_ROOT}`);
  console.log(`DEVICE_DIR=${DEVICE_DIR}`);
  console.log(
    `gateway=${GATEWAY_URL}  credential=${GATEWAY_CREDENTIAL_TARGET}`,
  );

  const health = await fetchJson(`${GATEWAY_URL}/health`).catch(() => null);
  check(
    "0.1 the local test Gateway answers with the expected protocol",
    health?.ready === true && health?.gateway_protocol_version === 1,
    health ? `provider_configured=${health.provider_configured}` : "no /health",
  );
  const computeBefore = gatewayCompute();
  console.log(
    `  [info] Gateway compute before: state=${computeBefore.state} sessions=${computeBefore.sessions} events=${JSON.stringify(computeBefore.events)}`,
  );

  console.log(
    "Scenario A-F — fresh install → owner → Canalla Cloud enrollment",
  );
  await withApp(scenarioFirstRunAndEnroll, "A-F");
  const usersA = dbQuery("SELECT id,email,is_owner FROM users");
  check(
    "A4 exactly one local user, owner flag set",
    usersA.length === 1 && usersA[0][2] === 1,
    JSON.stringify(usersA),
  );
  check(
    "A5 the device session credential of this isolated install exists",
    sessionCredentialTargets(dbSessionIds()).length === 1,
  );
  check(
    "D5 the enrollment landed in the throwaway credential entry",
    credentialTargets().includes(GATEWAY_CREDENTIAL_TARGET),
    GATEWAY_CREDENTIAL_TARGET,
  );
  const healthShared = await fetch(`http://127.0.0.1:${backendPort}/health`)
    .then((response) => response.json())
    .catch(() => null);
  console.log(
    `  [info] owned backend /health: ${JSON.stringify(healthShared)}`,
  );

  console.log("Scenario G — logout, then a different local user");
  await withApp(scenarioLogoutAndSecondUser, "G");
  const usersG = dbQuery("SELECT id,email FROM users ORDER BY created_at");
  check(
    "G5 two local users now exist and the second is the active session",
    usersG.length === 2,
    JSON.stringify(usersG.map((row) => row[1])),
  );

  console.log("Scenario H — full Quit + relaunch");
  await withApp((page) => scenarioRestoreConnected(page, "H"), "H");
  const usersH = dbQuery("SELECT email FROM users ORDER BY created_at");
  check(
    "H4 the restored session belongs to the second local user",
    usersH.length === 2,
    usersH.join(","),
  );

  console.log("Scenario I — secret hygiene in the app's own log");
  const logPath = path.join(DATA_ROOT, "logs", "backend.log");
  if (fs.existsSync(logPath)) {
    const log = fs.readFileSync(logPath, "utf8");
    const keyPrefix = providerKeyPrefix();
    const enrollment = readCredential(GATEWAY_CREDENTIAL_TARGET);
    let installationSecret = "";
    try {
      installationSecret =
        JSON.parse(enrollment || "{}").installation_secret || "";
    } catch {
      installationSecret = "";
    }
    check(
      "I6 the app's backend log contains no RunPod key value",
      !keyPrefix || !log.includes(keyPrefix),
      keyPrefix
        ? "compared against the stored provider credential"
        : "no provider credential",
    );
    check(
      "I7 the app's backend log contains no installation secret",
      !installationSecret || !log.includes(installationSecret),
      installationSecret
        ? "compared against the enrollment entry"
        : "no enrollment readable",
    );
    check(
      "I8 the app's backend log contains no activation code",
      !log.includes(CODE),
    );
    check(
      "I9 the app's backend log shows no secret-shaped assignments",
      !/RUNPOD_API_KEY\s*=\s*\S/.test(log) &&
        !/INSTALLATION_SECRET\s*=\s*\S/.test(log),
    );
  } else {
    check(
      "I6-I9 the app's backend log exists to be checked",
      false,
      "missing log",
    );
  }

  console.log("Scenario J — reinstall over the existing installation");
  const setup = process.env.ALEX_SMOKE_SETUP;
  if (!setup || !fs.existsSync(setup)) {
    skip(
      "J1-J3 reinstall keeps Canalla Cloud connected and the data root intact",
      "ALEX_SMOKE_SETUP was not provided",
    );
  } else {
    const installed = path.dirname(EXE);
    const uninstaller = path.join(installed, "uninstall.exe");
    const stamp = (target) => {
      try {
        const stat = fs.statSync(target);
        return `${stat.size}:${stat.mtimeMs}`;
      } catch {
        return "missing";
      }
    };
    const appBefore = stamp(EXE);
    const uninstallerBefore = stamp(uninstaller);
    const countsBefore = dbQuery(
      "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM chats), (SELECT COUNT(*) FROM auth_sessions)",
    )[0];
    console.log(`  [cmd] "${setup}" /S`);
    const installer = spawnSync(setup, ["/S"], { stdio: "inherit" });
    check(
      "J1 the NSIS setup re-ran silently with exit 0",
      installer.status === 0,
      `exit=${installer.status}`,
    );
    // NSIS keeps the packaged files' timestamps, so the uninstaller stamp (written by
    // the installer itself) is the honest evidence that the installation was redone.
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline && stamp(uninstaller) === uninstallerBefore) {
      await sleep(1000);
    }
    check(
      "J2 the reinstall actually rewrote the installation",
      stamp(uninstaller) !== uninstallerBefore &&
        stamp(uninstaller) !== "missing",
      `uninstaller ${uninstallerBefore} -> ${stamp(uninstaller)}`,
    );
    check(
      "J3 the installed app is still the freshly built package",
      stamp(EXE) === appBefore && appBefore !== "missing",
      `alex-llm.exe ${stamp(EXE)}`,
    );
    await withApp(
      (page) => scenarioRestoreConnected(page, "R"),
      "R (after reinstall)",
    );
    const countsAfter = dbQuery(
      "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM chats), (SELECT COUNT(*) FROM auth_sessions)",
    )[0];
    check(
      "J4 the local data root survived the reinstall unchanged",
      JSON.stringify(countsBefore) === JSON.stringify(countsAfter),
      `before=${JSON.stringify(countsBefore)} after=${JSON.stringify(countsAfter)}`,
    );
  }

  console.log("Cleanup and read-only Gateway verification");
  const computeAfter = gatewayCompute();
  check(
    "F3 the Gateway never created compute (state offline, no session row)",
    ["offline", "stopped"].includes(computeAfter.state) &&
      computeAfter.sessions === 0 &&
      !computeAfter.activeSession,
    `state=${computeAfter.state} sessions=${computeAfter.sessions} active=${computeAfter.activeSession}`,
  );
  check(
    "F4 the Gateway audited no ensure/stop/creating operation",
    computeAfter.events.every(
      ([operation]) =>
        !["ensure", "stop", "creating", "starting_pod"].includes(operation),
    ),
    JSON.stringify(computeAfter.events),
  );
  check(
    "K1 the throwaway gateway credential existed and was deleted",
    credentialTargets().includes(GATEWAY_CREDENTIAL_TARGET) &&
      deleteCredential(GATEWAY_CREDENTIAL_TARGET) &&
      !credentialTargets().includes(GATEWAY_CREDENTIAL_TARGET),
    GATEWAY_CREDENTIAL_TARGET,
  );
  const sessionTargets = sessionCredentialTargets(dbSessionIds());
  const removed = sessionTargets.filter(deleteCredential).length;
  check(
    "K2 this smoke's own session credentials were deleted",
    sessionCredentialTargets(dbSessionIds()).length === 0,
    `removed=${removed}`,
  );
  check(
    "K3 the isolated device credential target this smoke used is deleted",
    !credentialTargets().includes(DEVICE_CREDENTIAL_TARGET) ||
      deleteCredential(DEVICE_CREDENTIAL_TARGET),
    DEVICE_CREDENTIAL_TARGET,
  );
  check(
    "K4 the real installation-global RunPod credential is untouched",
    Boolean(readCredential(PROVIDER_TARGET)),
  );
  check(
    "K5 the real Canalla Cloud enrollment entry is untouched",
    credentialTargets().includes("Alex LLM/gateway/installation"),
  );
  check(
    "K6 the isolated device credential target is not the real device credential",
    DEVICE_CREDENTIAL_TARGET !== "Alex LLM/device-credential",
    DEVICE_CREDENTIAL_TARGET,
  );

  console.log();
  if (skipped.length) {
    for (const entry of skipped)
      console.log(`SKIPPED: ${entry.name} — ${entry.reason}`);
  }
  if (failures > 0) {
    console.error(`ALEX CLOUD GUI SMOKE FAILED: ${failures} failing check(s)`);
    process.exit(1);
  }
  console.log(
    "ALEX CLOUD GUI SMOKE PASS — GPU 0, no Pod, Volume untouched, read-only provider traffic only",
  );
  fs.rmSync(DATA_ROOT, { recursive: true, force: true });
  fs.rmSync(DEVICE_DIR, { recursive: true, force: true });
  process.exit(0);
}

main().catch((error) => {
  console.error("ALEX CLOUD GUI SMOKE ERROR:", error);
  process.exit(1);
});
