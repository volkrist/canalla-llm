// Installed Canalla 1.2.0 release acceptance — the checks the GUI has to prove, not the backend.
//
// `always-ready.mjs` already proves Computer and Tor come up green on their own and recover from a
// killed daemon and a killed sidecar. This run covers what is left, and all of it is visible state:
//
//   * the AI badge is RED Disconnected while nothing is configured — never Connected, and never
//     stuck on a transient «Проверяем состояние…» (Finding A, the 1.2.0 fix for an endless amber);
//   * the Memory chip is healthy;
//   * Settings carries the Обновления section and names the installed version;
//   * the autostart switch is a REAL Windows registration: ON, OFF and ON again are each verified
//     against `HKCU\…\Run`, not against the checkbox alone;
//   * launching the app a second time does not start a second desktop, backend or Tor.
//
// The app is the REAL installed build. The data root, the device directory and the credential names
// are isolated, so the operator's own Canalla data is untouched. The autostart value, however, is one
// machine-wide registry entry, so this run reads the operator's original value first and puts it back
// afterwards — a test that leaves the operator's autostart flipped would be a bug in the test.
//
// Usage (from apps/desktop): node e2e/release-1.2.0.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const CONFIG = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "src-tauri", "tauri.conf.json"),
    "utf8",
  ),
);
const PRODUCT_VERSION = CONFIG.version;

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

const RUN_KEY = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const AUTOSTART_NAME = "Canalla LLM";

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() =>
        resolve(typeof address === "object" && address ? address.port : 0),
      );
    });
  });
}

const CDP_PORT = await freePort();
const WEBVIEW_PROFILE = fs.mkdtempSync(
  path.join(os.tmpdir(), "canalla-release-webview-"),
);
const DATA_ROOT = fs.mkdtempSync(
  path.join(os.tmpdir(), "canalla-release-data-"),
);
const DEVICE_DIR = fs.mkdtempSync(
  path.join(os.tmpdir(), "canalla-release-cred-"),
);
const PASSWORD = "release-1.2.0-passphrase";
const EMAIL = "release-120@example.com";
const STAMP = Date.now().toString(36);
const DEVICE_CREDENTIAL_TARGET = `Alex LLM/device-credential-release-${STAMP}`;
const RELEASE_GATEWAY_CREDENTIAL = `release-120-${STAMP}`;

const SETTLE_TIMEOUT_MS = Number(
  process.env.RELEASE_SETTLE_TIMEOUT_MS || 90000,
);

const failures = [];
let child = null;
let backendPort = 0;
let token = null;
let originalAutostart = null;

function check(name, ok, detail = "") {
  console.log(
    `${ok ? "PASS" : "FAIL"} ${name}${detail ? `  [${detail}]` : ""}`,
  );
  if (!ok) failures.push(name);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function powershell(script) {
  return (
    spawnSync("powershell.exe", ["-NoProfile", "-Command", script], {
      encoding: "utf8",
    }).stdout || ""
  ).trim();
}

// ------------------------------------------------------------------ the Windows Run registration

function autostartValue() {
  const value = powershell(
    `(Get-ItemProperty -Path '${RUN_KEY}' -Name '${AUTOSTART_NAME}' -ErrorAction SilentlyContinue).'${AUTOSTART_NAME}'`,
  );
  return value === "" ? null : value;
}

function processCounts() {
  const counts = {};
  for (const name of ["alex-llm.exe", "alex-backend.exe", "tor.exe"]) {
    const listing =
      spawnSync("tasklist", ["/FI", `IMAGENAME eq ${name}`], {
        encoding: "utf8",
      }).stdout || "";
    counts[name] = (listing.match(new RegExp(name, "g")) || []).length;
  }
  return counts;
}

// ------------------------------------------------------------------ the app

function launchApp() {
  const env = {
    ...process.env,
    ALEX_LLM_DATA_DIR: DATA_ROOT,
    ALEX_DEVICE_DIR: DEVICE_DIR,
    ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
    ALEX_GATEWAY_CREDENTIAL_NAME: RELEASE_GATEWAY_CREDENTIAL,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
    WEBVIEW2_USER_DATA_FOLDER: WEBVIEW_PROFILE,
  };
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: true });
}

async function waitCdp(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      if (response.ok) return true;
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  return false;
}

async function findBackendPort(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const listing =
      spawnSync("netstat", ["-ano", "-p", "TCP"], { encoding: "utf8" })
        .stdout || "";
    for (const line of listing.split(/\r?\n/)) {
      const match = line.match(/127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)/);
      if (!match) continue;
      const port = Number(match[1]);
      if (port === CDP_PORT) continue;
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        if (!response.ok) continue;
        const body = await response.json();
        if (body.product === "alex-llm") {
          backendPort = port;
          return port;
        }
      } catch {
        /* another listener */
      }
    }
    await sleep(1000);
  }
  return 0;
}

async function register(page) {
  await page
    .getByRole("button", { name: "Создать владельца Canalla" })
    .waitFor({ timeout: 180000 });
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Canalla" }).click();
}

async function chipState(page, name) {
  const chip = page.locator(".status-chip", { hasText: name }).first();
  const label = await chip.getAttribute("aria-label").catch(() => null);
  if (label) return label;
  return (await chip.innerText().catch(() => "")).replace(/\s+/g, " ").trim();
}

/**
 * Wait for a chip to reach one of the wanted states.
 *
 * A cold Tor needs a consensus before it can build any circuit, so this is a passive wait on the
 * chip the user sees — nothing is clicked and nothing is skipped. Sampling once right after the
 * owner is created would only measure how fast the harness is, not whether the product converges.
 */
async function waitForChip(page, name, states, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    last = await chipState(page, name);
    if (states.some((state) => last.includes(state)))
      return { ready: true, text: last };
    await sleep(2000);
  }
  return { ready: false, text: last };
}

/** Proof that the window still works: the Tor chip is still the ready one it was. */
async function appStillWorks(page) {
  return await chipState(page, "Tor");
}

async function quitApp() {
  if (!child) return;
  try {
    execFileSync("powershell.exe", [
      "-NoProfile",
      "-Command",
      `(Get-Process -Id ${child.pid} -ErrorAction SilentlyContinue).CloseMainWindow() | Out-Null`,
    ]);
  } catch {
    /* fall through */
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && child.exitCode === null) await sleep(250);
  if (child.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  }
  child = null;
}

async function main() {
  console.log("CANALLA 1.2.0 — INSTALLED RELEASE ACCEPTANCE");
  console.log(`  app      ${EXE}`);
  console.log(`  version  ${PRODUCT_VERSION}`);
  console.log(`  data     ${DATA_ROOT}`);
  console.log(`  cred     ${DEVICE_CREDENTIAL_TARGET}\n`);

  // The operator's own autostart value, kept so this run can put it back.
  originalAutostart = autostartValue();
  console.log(
    `  autostart before this run: ${originalAutostart ?? "<not registered>"}\n`,
  );

  child = launchApp();
  if (!(await waitCdp())) {
    check("the installed app opens its debugging port", false);
    await quitApp();
    return 1;
  }

  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const page = browser.contexts()[0].pages()[0];
  await register(page);

  if (!(await findBackendPort())) {
    check("the installed app serves its own backend", false);
    await quitApp();
    return 1;
  }
  check(
    "the installed app serves its own backend",
    true,
    `port ${backendPort}`,
  );

  const health = await (
    await fetch(`http://127.0.0.1:${backendPort}/health`)
  ).json();
  check(
    "/health reports the released version",
    health.version === PRODUCT_VERSION && health.product === "alex-llm",
    `${health.product} ${health.version}`,
  );

  // ------------------------------------------------------------------ AI: red Disconnected, settled

  const badge = page.locator('[data-testid="ai-connection"]');
  await badge.waitFor({ timeout: 60000 });

  // "Settled" is the whole point of Finding A: the badge must reach a real state with no active
  // operation, not sit on a transient forever. Wait for a state that is not the checking code.
  let state = "";
  let code = "";
  const deadline = Date.now() + SETTLE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    state = (await badge.getAttribute("data-state")) || "";
    code = (await badge.getAttribute("data-code")) || "";
    if (state && code && code !== "checking") break;
    await sleep(1000);
  }
  const badgeText = (await badge.innerText()).replace(/\s+/g, " ").trim();

  check(
    "the AI badge settles out of the checking state",
    code !== "checking",
    `state=${state} code=${code}`,
  );
  check(
    "the AI badge is Disconnected while nothing is configured",
    state === "disconnected",
    `state=${state} code=${code} text=${badgeText}`,
  );
  check(
    "the AI badge never claims Connected without a model",
    !badgeText.includes("Connected") && state !== "connected",
    `text=${badgeText}`,
  );
  check(
    "the AI badge never sits on «Проверяем состояние…»",
    !badgeText.includes("Проверяем состояние") && code !== "checking",
    `text=${badgeText}`,
  );
  check(
    "the badge explains the missing configuration instead of promising a transition",
    ["not_configured", "credentials_missing", "configured_only"].includes(code),
    `code=${code}`,
  );

  // ------------------------------------------------------------------ the other chips

  const memory = await chipState(page, "Memory");
  check(
    "the Memory chip reports a healthy state",
    !/Ошибка|error/i.test(memory),
    memory,
  );

  const computer = await waitForChip(
    page,
    "Computer",
    ["Готово"],
    Number(process.env.RELEASE_CHIP_TIMEOUT_MS || 300000),
  );
  check("the Computer chip is ready", computer.ready, computer.text);

  const tor = await waitForChip(
    page,
    "Tor",
    ["Готово"],
    Number(process.env.RELEASE_CHIP_TIMEOUT_MS || 300000),
  );
  check("the Tor chip is ready", tor.ready, tor.text);

  // ------------------------------------------------------------------ Settings → Обновления

  await page.getByRole("button", { name: "Settings" }).first().click();
  // The dialog is a native `<dialog class="settings-dialog">`: it carries no `role` attribute, so a
  // `[role="dialog"]` selector never matches it.
  const dialog = page.locator("dialog.settings-dialog").first();
  await dialog.waitFor({ timeout: 30000 });
  check("Settings opens", await dialog.isVisible());

  await dialog.getByRole("button", { name: "Обновления", exact: true }).click();
  const updates = dialog.locator('section[aria-label="Обновления"]');
  await updates.waitFor({ timeout: 30000 });
  const updatesText = (await updates.innerText()).replace(/\s+/g, " ").trim();
  check("the Обновления section is present in Settings", true);
  check(
    "the updater names the installed version",
    updatesText.includes(PRODUCT_VERSION),
    updatesText.slice(0, 200),
  );
  check(
    "the updater offers a real check action",
    (await updates
      .getByRole("button", { name: "Проверить обновления" })
      .count()) > 0,
  );
  // Check against the deployed Gateway, which does not serve a manifest yet. The response has to
  // leave the app usable and honest — this is the live half of the «404 must be safe» case.
  await updates
    .getByRole("button", { name: "Проверить обновления" })
    .click()
    .catch(() => {});
  const updateNote = dialog.locator(
    '[data-testid="update-message"], [data-testid="update-refused"]',
  );
  await updateNote
    .first()
    .waitFor({ timeout: 60000 })
    .catch(() => {});
  const noteText = (
    await updateNote
      .first()
      .innerText()
      .catch(() => "")
  )
    .replace(/\s+/g, " ")
    .trim();
  check(
    "a check with no manifest settles into a message instead of hanging",
    noteText.length > 0,
    noteText,
  );
  check(
    "the app stays usable after a check that found nothing",
    (await appStillWorks(page)).includes("Готово"),
  );

  // ------------------------------------------------------------------ autostart ON / OFF / ON

  // The switch lives in «Общие» (with the theme and the font size), not in «Дополнительно» —
  // that section holds the backend URL and the technical-details toggle.
  await dialog.getByRole("button", { name: "Общие", exact: true }).click();
  const toggle = dialog.locator('[data-testid="autostart-toggle"]');
  const toggleAppeared = await toggle
    .waitFor({ timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  if (!toggleAppeared) {
    const seen = await dialog
      .getByRole("button")
      .allInnerTexts()
      .catch(() => []);
    console.log(
      `  diagnostics: dialog buttons = ${JSON.stringify(seen)}; text = ${(await dialog.innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 600)}`,
    );
  }
  check("the autostart switch is present in Settings", toggleAppeared);
  if (!toggleAppeared) {
    await quitApp();
    return 1;
  }
  const supported = !(await toggle.isDisabled());
  check("the app can register autostart on this machine", supported);

  const setToggle = async (want) => {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      if ((await toggle.isChecked()) === want) return true;
      await toggle.click();
      await sleep(1200);
    }
    return (await toggle.isChecked()) === want;
  };

  const reached = async (want) => {
    const deadline = Date.now() + 20000;
    let last = null;
    while (Date.now() < deadline) {
      last = autostartValue();
      if (want ? last !== null : last === null) return last;
      await sleep(800);
    }
    return last;
  };

  // ON: the default the product ships.
  await setToggle(true);
  const onValue = await reached(true);
  check(
    "autostart ON writes a real Run registration",
    onValue !== null,
    onValue ?? "<absent>",
  );
  check(
    "the ON registration points at the installed build",
    typeof onValue === "string" && onValue.includes("Programs"),
    onValue ?? "<absent>",
  );

  // OFF: the registration is removed, not merely unchecked in the UI.
  await setToggle(false);
  const offValue = await reached(false);
  check(
    "autostart OFF removes the Run registration",
    offValue === null,
    offValue ?? "<absent>",
  );
  check(
    "the switch agrees with the OS after OFF",
    (await toggle.isChecked()) === false,
  );

  // ON again: restored, and the checkbox still tells the truth.
  await setToggle(true);
  const onAgain = await reached(true);
  check(
    "autostart ON restores the registration",
    onAgain !== null,
    onAgain ?? "<absent>",
  );
  check(
    "the switch agrees with the OS after ON",
    (await toggle.isChecked()) === true,
  );

  await dialog
    .getByRole("button", { name: "Закрыть настройки" })
    .click()
    .catch(() => {});
  await page.keyboard.press("Escape").catch(() => {});

  // ------------------------------------------------------------------ single instance

  const before = processCounts();
  check(
    "exactly one desktop, one backend and one Tor serve this install",
    before["alex-llm.exe"] === 1 &&
      before["alex-backend.exe"] === 1 &&
      before["tor.exe"] === 1,
    JSON.stringify(before),
  );

  const second = launchApp();
  const secondExited = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), 25000);
    second.on("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
  await sleep(4000);
  const after = processCounts();

  check(
    "the second launch does not start a second runtime",
    after["alex-llm.exe"] === 1 &&
      after["alex-backend.exe"] === 1 &&
      after["tor.exe"] === 1,
    JSON.stringify(after),
  );
  check(
    "the second invocation hands over to the running instance",
    secondExited || second.exitCode !== null,
    `exited=${secondExited} code=${second.exitCode}`,
  );
  if (!secondExited && second.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(second.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  }

  // The primary window is still the one we are driving: a failed handover would have replaced it.
  check(
    "the first window is still the one serving the UI",
    browser.contexts()[0].pages().length === 1,
    `${browser.contexts()[0].pages().length} page(s)`,
  );
  const stillThere = await chipState(page, "Tor");
  check(
    "the first window is still usable",
    stillThere.includes("Готово"),
    stillThere,
  );

  // ------------------------------------------------------------------ clean quit

  await quitApp();
  await sleep(3000);
  const stopped = processCounts();
  check(
    "a full quit leaves no orphan desktop, backend or Tor",
    stopped["alex-llm.exe"] === 0 &&
      stopped["alex-backend.exe"] === 0 &&
      stopped["tor.exe"] === 0,
    JSON.stringify(stopped),
  );

  // ------------------------------------------------------------------ restore the operator's state

  if (originalAutostart === null) {
    powershell(
      `Remove-ItemProperty -Path '${RUN_KEY}' -Name '${AUTOSTART_NAME}' -ErrorAction SilentlyContinue`,
    );
  } else {
    powershell(
      `Set-ItemProperty -Path '${RUN_KEY}' -Name '${AUTOSTART_NAME}' -Value '${originalAutostart.replace(/'/g, "''")}'`,
    );
  }
  const restored = autostartValue();
  check(
    "the operator's own autostart value was put back",
    restored === originalAutostart,
    `${restored ?? "<absent>"} (was ${originalAutostart ?? "<absent>"})`,
  );

  console.log(
    `\n${failures.length === 0 ? "RELEASE ACCEPTANCE PASS" : `RELEASE ACCEPTANCE FAILED: ${failures.length} check(s)`}`,
  );
  if (failures.length)
    console.log(failures.map((name) => ` - ${name}`).join("\n"));
  return failures.length === 0 ? 0 : 1;
}

const code = await main();
process.exit(code);
