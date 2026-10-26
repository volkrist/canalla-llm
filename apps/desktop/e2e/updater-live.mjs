// Installed updater E2E: a real 1.2.3 desktop, a real 1.2.4 release, a real NSIS install.
//
// This is the one check a unit test cannot make: the client that is *installed* asks the deployed
// endpoint, the Tauri plugin verifies the signature over the downloaded bytes, and the NSIS
// installer replaces the programme while the user's data root survives.
//
// It must run on an isolated Windows machine (a CI runner or a VM), never against the owner's
// working installation: the updater installs into `%LOCALAPPDATA%\Programs\Canalla LLM`, which is
// the same path the owner uses. The data root and the device credentials *are* isolated below.
//
//   node e2e/updater-live.mjs
//
// Preconditions: the 1.2.3 build is installed, the deployed manifest offers a newer signed
// release, and Playwright is installed (`npm ci`). No GPU, no Pod, no account on the Gateway.

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
  "Canalla LLM",
  "alex-llm.exe",
);
const CDP_PORT = 9242;
const WORK = path.join(os.tmpdir(), "canalla-updater-e2e");
const DATA_ROOT = path.join(WORK, "data");
const DEVICE_DIR = path.join(WORK, "cred");
const CREDENTIAL_NAME = "updater-e2e";
const DEVICE_CREDENTIAL_TARGET = "Alex LLM/session-updater-e2e";
const EMAIL = "updater-e2e-owner@example.com";
const PASSWORD = "updater-e2e-password-12345";
const TARGET_VERSION = (process.env.CANALLA_TARGET_VERSION || "1.2.4").trim();
const FROM_VERSION = (process.env.CANALLA_FROM_VERSION || "1.2.3").trim();

let failures = 0;
let child = null;

function check(name, ok, detail = "") {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures += 1;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fileVersion(exe) {
  const out = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-Command",
      `(Get-Item -LiteralPath '${exe.replace(/'/g, "''")}').VersionInfo.FileVersion`,
    ],
    { encoding: "utf8" },
  ).stdout;
  return (out || "").trim();
}

function sqliteCounts(database) {
  const script = [
    "import json,sqlite3,sys",
    "db=sqlite3.connect(sys.argv[1])",
    "names=('users','conversations','messages')",
    "print(json.dumps({n: db.execute('SELECT COUNT(*) FROM ' + n).fetchone()[0] for n in names}))",
  ].join("\n");
  const out = execFileSync("python", ["-c", script, database], { encoding: "utf8" });
  return JSON.parse(out.trim());
}

function launchApp() {
  const env = {
    ...process.env,
    ALEX_LLM_DATA_DIR: DATA_ROOT,
    ALEX_DEVICE_DIR: DEVICE_DIR,
    ALEX_GATEWAY_CREDENTIAL_NAME: CREDENTIAL_NAME,
    ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
  };
  delete env.ALEX_GATEWAY_URL;
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: false });
}

async function waitCdp(timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const targets = await response.json();
      if (targets.some((target) => target.type === "page" && target.url)) return true;
    } catch {
      /* not ready */
    }
    await sleep(500);
  }
  throw new Error("CDP never became ready");
}

async function connect() {
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const context = browser.contexts()[0];
  const page = context.pages()[0];
  return { browser, page };
}

async function openUpdates(page) {
  const dialog = page.getByRole("dialog");
  if (!(await dialog.first().isVisible().catch(() => false))) {
    await page
      .getByRole("button", { name: "Settings", exact: true })
      .click({ timeout: 120000 })
      .catch(async () => {
        await page.getByLabel("Настройки").click({ timeout: 30000 });
      });
    await dialog.waitFor({ timeout: 60000 });
  }
  await dialog.getByRole("button", { name: "Обновления", exact: true }).click({
    timeout: 30000,
  });
  return dialog;
}

async function quitApp(proc) {
  if (!proc || proc.exitCode !== null) return;
  try {
    execFileSync("powershell.exe", [
      "-NoProfile",
      "-Command",
      `(Get-Process -Id ${proc.pid}).CloseMainWindow() | Out-Null`,
    ]);
  } catch {
    /* the app may already be gone */
  }
  const deadline = Date.now() + 40000;
  while (Date.now() < deadline && proc.exitCode === null) await sleep(250);
  if (proc.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
  }
}

async function main() {
  if (!fs.existsSync(EXE)) {
    throw new Error(`no installed build at ${EXE}`);
  }
  // A run that starts on anything but the old version proves nothing about an update.
  const before = fileVersion(EXE);
  console.log(`installed build: ${before} (want ${FROM_VERSION})`);
  if (!before.startsWith(FROM_VERSION)) {
    throw new Error(`the installed build is ${before}, not ${FROM_VERSION}`);
  }

  fs.rmSync(WORK, { recursive: true, force: true });
  fs.mkdirSync(DATA_ROOT, { recursive: true });
  fs.mkdirSync(DEVICE_DIR, { recursive: true });

  child = launchApp();
  await waitCdp();
  let { browser, page } = await connect();
  try {
    await page
      .getByRole("button", { name: "Создать владельца Canalla" })
      .waitFor({ timeout: 240000 });
    await page.getByLabel("Имя владельца (необязательно)").fill("Updater Owner");
    await page.getByLabel("Email", { exact: true }).fill(EMAIL);
    await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Создать владельца Canalla" }).click();
    await page.getByRole("button", { name: "New Chat" }).waitFor({ timeout: 180000 });
    check("E1 the old build starts with an isolated data root", true, before);

    const dialog = await openUpdates(page);
    // The check runs on mount; pressing the button again makes this deterministic.
    await dialog
      .getByRole("button", { name: "Проверить обновления" })
      .click({ timeout: 30000 })
      .catch(() => {});
    await dialog
      .getByText(TARGET_VERSION, { exact: false })
      .first()
      .waitFor({ timeout: 60000 });
    const panel = await dialog.innerText();
    check(
      "E2 the installed client discovers the newer release",
      panel.includes(TARGET_VERSION),
      TARGET_VERSION,
    );

    const database = path.join(DATA_ROOT, "data", "alex.db");
    const countsBefore = sqliteCounts(database);
    check("E3 the isolated database has the owner", countsBefore.users >= 1);

    await dialog
      .getByRole("button", { name: `Скачать ${TARGET_VERSION}` })
      .click({ timeout: 60000 });
    const installButton = page.getByTestId("update-install");
    await installButton.waitFor({ timeout: 600000 });
    check("E4 the signed package downloaded and passed verification", true);

    // Installing exits the app and runs the NSIS installer.
    await installButton.click({ timeout: 60000 });
  } finally {
    await quitApp(child);
    await browser.close().catch(() => {});
  }

  // The installer replaces the programme; wait for the new version to be on disk.
  const deadline = Date.now() + 420000;
  let now = fileVersion(EXE);
  while (Date.now() < deadline && !now.startsWith(TARGET_VERSION)) {
    await sleep(1000);
    now = fileVersion(EXE);
  }
  check("E5 the installer replaced the programme", now.startsWith(TARGET_VERSION), now);

  child = launchApp();
  await waitCdp();
  ({ browser, page } = await connect());
  try {
    await page
      .getByRole("button", { name: "New Chat" })
      .waitFor({ timeout: 240000 });
    const dialog = await openUpdates(page);
    await dialog.getByRole("button", { name: "О программе", exact: true }).click({
      timeout: 30000,
    });
    const about = await dialog.innerText();
    check(
      "E6 the relaunched app reports the new version",
      about.includes(TARGET_VERSION),
      TARGET_VERSION,
    );

    const database = path.join(DATA_ROOT, "data", "alex.db");
    const countsAfter = sqliteCounts(database);
    check(
      "E7 the user's data survived the update",
      countsAfter.users === countsBefore.users &&
        countsAfter.conversations >= countsBefore.conversations &&
        countsAfter.messages >= countsBefore.messages,
      `users ${countsBefore.users}->${countsAfter.users}, chats ${countsBefore.conversations}->${countsAfter.conversations}, messages ${countsBefore.messages}->${countsAfter.messages}`,
    );

    // The now-current client must not be offered its own version again.
    await dialog.getByRole("button", { name: "Обновления", exact: true }).click({
      timeout: 30000,
    });
    await dialog
      .getByRole("button", { name: "Проверить обновления" })
      .click({ timeout: 30000 })
      .catch(() => {});
    await sleep(4000);
    const updates = await dialog.innerText();
    const available = /Доступная версия\s*\n?\s*([^\n]+)/.exec(updates)?.[1]?.trim();
    check(
      "E8 the updated client is not offered its own version",
      !available || available === "нет",
      `available=${available ?? "<unknown>"}`,
    );
  } finally {
    await quitApp(child);
    await browser.close().catch(() => {});
  }

  console.log(`\n${failures === 0 ? "UPDATER INSTALLED E2E: PASS" : "UPDATER INSTALLED E2E: FAIL"}`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch(async (error) => {
  console.error(`UPDATER INSTALLED E2E: FAIL — ${error?.stack || error}`);
  await quitApp(child);
  process.exit(1);
});
