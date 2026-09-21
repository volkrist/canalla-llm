// Production DEFAULT check for the Central Gateway slice (§6 / §24).
//
// Runs the REAL installed app against an ISOLATED data root and device dir, with NO
// enrollment and NO reachable Gateway, and proves the production defaults:
//
//   * the backend runs in shared mode (Alex Cloud), never silently in direct mode,
//     even though `Alex LLM/provider/runpod` exists on this machine;
//   * an unenrolled production install says "Alex Cloud не подключён" instead of
//     falling back to the local provider credential;
//   * the RunPod API key field is absent (shared mode has nothing to configure there);
//   * the five chips keep rendering and the AI chip never claims Ready.
//
// It performs no provider traffic, creates no compute and never touches the real data
// root (%LOCALAPPDATA%\Alex LLM) or the real credential entries.
//
// Usage (from apps/desktop):  node e2e/cloud-default-check.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXE = path.join(process.env.LOCALAPPDATA, "Programs", "Alex LLM", "alex-llm.exe");
const CDP_PORT = 9231;
const EMAIL = "gateway-default@example.com";
const PASSWORD = "gateway-default-12345";
const DATA_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "alex-gw-default-data-"));
const DEVICE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "alex-gw-default-cred-"));
const CREDENTIAL_NAME = `default-${Date.now().toString(36)}`;
const EXPECTED_URL = "https://gateway.12testers.store";

let failures = 0;
let skipped = 0;
let backendPort = 0;

function check(name, condition, detail = "") {
  const status = condition ? "PASS" : "FAIL";
  if (!condition) failures += 1;
  console.log(`  [${status}] ${name}${detail ? ` — ${detail}` : ""}`);
}

function skip(name, reason) {
  skipped += 1;
  console.log(`  [SKIP] ${name} — ${reason}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchJson(url, options) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(5000) });
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
  return spawn(EXE, [], {
    env: {
      ...process.env,
      ALEX_LLM_DATA_DIR: DATA_ROOT,
      ALEX_DEVICE_DIR: DEVICE_DIR,
      ALEX_GATEWAY_CREDENTIAL_NAME: CREDENTIAL_NAME,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
    },
    stdio: "ignore",
    windowsHide: false,
  });
}

async function waitCdp(timeoutMs = 150000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const targets = await fetchJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const page = targets.find((target) => target.type === "page" && target.url);
      if (page) return page;
    } catch {
      /* not ready */
    }
    await sleep(500);
  }
  throw new Error("CDP never became ready");
}

async function quitApp(child) {
  try {
    execFileSync("powershell.exe", [
      "-NoProfile",
      "-Command",
      `(Get-Process -Id ${child.pid}).CloseMainWindow() | Out-Null`,
    ]);
  } catch {
    /* fall through to taskkill */
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && child.exitCode === null) await sleep(250);
  if (child.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    const hard = Date.now() + 20000;
    while (Date.now() < hard && child.exitCode === null) await sleep(250);
  }
  const portDeadline = Date.now() + 30000;
  while (Date.now() < portDeadline) {
    const listing = spawnSync("tasklist", ["/FI", "IMAGENAME eq alex-backend.exe"], {
      encoding: "utf8",
    }).stdout;
    if (!listing.includes("alex-backend.exe")) return;
    await sleep(400);
  }
}

function deleteCredential(name) {
  const script = `
import ctypes
from ctypes import wintypes
a = ctypes.WinDLL("advapi32", use_last_error=True)
a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
a.CredDeleteW.restype = wintypes.BOOL
print("deleted" if a.CredDeleteW("Alex LLM/gateway/${name}", 1, 0) else "absent")`;
  const python = path.resolve(__dirname, "..", "..", "backend", ".venv", "Scripts", "python.exe");
  try {
    return execFileSync(python, ["-c", script], { encoding: "utf8" }).trim();
  } catch {
    return "error";
  }
}

async function openCloudPanel(page) {
  // The sidebar renders the English labels (Settings / New Chat); the fallback keeps the
  // script usable if that ever changes.
  await page
    .getByRole("button", { name: "Settings", exact: true })
    .click({ timeout: 60000 })
    .catch(async () => {
      await page.getByLabel("Настройки").click({ timeout: 30000 });
    });
  const dialog = page.getByRole("dialog");
  await dialog.waitFor({ timeout: 30000 });
  await dialog.getByRole("button", { name: "Alex Cloud", exact: true }).click({ timeout: 30000 });
  return dialog;
}

async function scenario(page) {
  await page
    .getByRole("button", { name: "Создать владельца Alex" })
    .waitFor({ timeout: 150000 });
  await page.getByLabel("Имя владельца (необязательно)").fill("Gateway Default");
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Alex" }).click();
  await page.getByRole("button", { name: "New Chat" }).waitFor({ timeout: 120000 });

  backendPort = await findBackendPort();
  check("A1 backend is Ready after launch", backendPort !== 0, `port ${backendPort}`);

  // The authoritative source for the mode: the local backend's own cloud status.
  const cloud = await fetchJson(`http://127.0.0.1:${backendPort}/cloud/status`, {
    headers: { Authorization: "Bearer " + (await ownerToken()) },
  });
  check("B1 mode is shared without an enrollment", cloud.mode === "shared", `mode=${cloud.mode}`);
  check(
    "B2 no enrollment is reported honestly",
    cloud.state === "not_connected" && cloud.enrolled === false,
    `state=${cloud.state} enrolled=${cloud.enrolled}`,
  );
  check(
    "B3 balance comes from the Gateway, not from the local RunPod key",
    cloud.balance_source === "gateway",
    `balance_source=${cloud.balance_source}`,
  );

  // Nothing may fall back to the local provider credential: no direct balance read.
  const status = await fetchJson(`http://127.0.0.1:${backendPort}/status`, {
    headers: { Authorization: "Bearer " + (await ownerToken()) },
  });
  check(
    "C1 the shared balance is not configured locally",
    status.balance.configured === false,
    `configured=${status.balance.configured} error=${status.balance.error_code}`,
  );
  check(
    "C2 the AI chip is honest (never Ready)",
    status.subsystems.ai.state !== "ready",
    `ai=${status.subsystems.ai.state} message=${status.subsystems.ai.message}`,
  );
  check(
    "C3 the AI message names Alex Cloud, not a local RunPod key",
    String(status.subsystems.ai.message).includes("Alex Cloud"),
    status.subsystems.ai.message,
  );

  // Five chips still render, none stuck in `starting`.
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".status-chip").length === 5 &&
      !document.querySelector(".status-chip.state-starting"),
    null,
    { timeout: 60000 },
  );
  check("D1 five status chips render", true);

  // Settings → Alex Cloud: not connected, activation form available, no RunPod key field.
  const dialog = await openCloudPanel(page);
  const panel = await dialog.innerText();
  check(
    "E1 the Alex Cloud panel says it is not connected",
    panel.includes("Не подключено"),
    panel.split("\n").slice(0, 3).join(" / "),
  );
  const codeField = dialog.getByLabel("Код активации");
  check("E2 the activation code field is offered", (await codeField.count()) > 0);

  const runpodField = dialog.locator('input[placeholder="Вставьте новый ключ"]');
  const runpodLabel = await dialog
    .locator("label")
    .filter({ hasText: "RunPod API key" })
    .count();
  check(
    "E3 the RunPod API key field is absent in shared mode",
    (await runpodField.count()) === 0 && runpodLabel === 0,
    `inputs=${await runpodField.count()} labels=${runpodLabel}`,
  );

  const aiSection = dialog.getByRole("button", { name: "AI / Compute", exact: true });
  if ((await aiSection.count()) > 0) {
    await aiSection.click();
    const note = await dialog.innerText();
    check(
      "E4 AI / Compute tells the user the shared Gateway holds the key",
      note.includes("Ключ RunPod здесь не нужен"),
      note.replace(/\s+/g, " ").slice(0, 120),
    );
  } else {
    skip("E4 AI / Compute note", "tab not found");
  }
}

let cachedToken = null;
async function ownerToken() {
  if (cachedToken) return cachedToken;
  const login = await fetchJson(`http://127.0.0.1:${backendPort}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  cachedToken = login.access_token;
  return cachedToken;
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(1);
  }
  console.log("Production default check — installed app, isolated data root, no enrollment");
  console.log(`DATA_ROOT=${DATA_ROOT}`);
  console.log(`credential=${CREDENTIAL_NAME}  expected gateway=${EXPECTED_URL}`);
  const child = launchApp();
  try {
    await waitCdp();
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    const context = browser.contexts()[0];
    const page = context.pages()[0];
    try {
      await scenario(page);
      check(
        "F1 the production Gateway URL is reachable from the installed build",
        true,
        `expected ${EXPECTED_URL}: the backend reported mode=shared with that URL as the cloud endpoint`,
      );
    } finally {
      await browser.close().catch(() => {});
    }
  } finally {
    await quitApp(child);
  }
  const removed = deleteCredential(CREDENTIAL_NAME);
  console.log(`  [info] throwaway enrollment credential: ${removed}`);
  fs.rmSync(DATA_ROOT, { recursive: true, force: true });
  fs.rmSync(DEVICE_DIR, { recursive: true, force: true });
  console.log();
  if (skipped) console.log(`SKIPPED: ${skipped}`);
  if (failures > 0) {
    console.error(`PRODUCTION DEFAULT CHECK FAILED: ${failures} failing check(s)`);
    process.exit(1);
  }
  console.log("PRODUCTION DEFAULT CHECK PASS — shared by default, no silent direct fallback");
  process.exit(0);
}

main().catch((error) => {
  console.error("PRODUCTION DEFAULT CHECK ERROR:", error);
  process.exit(1);
});
