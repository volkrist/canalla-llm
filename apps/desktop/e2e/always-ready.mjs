// COMPUTER + TOR always ready — installed-app acceptance.
//
// Product requirement: after a NORMAL launch, with no button pressed, the Computer and Tor chips
// must reach a green, proven "ready" on their own:
//
//   * Computer — the host loop pairs this device and heartbeats by itself (no "Подключить");
//   * Tor — the backend discovers or starts a Tor process, waits for the SOCKS listener and proves
//     a real circuit through it before the chip turns green; a cold bootstrap is reported as
//     "подключается", never as an error.
//
// It then relaunches the very same install (same data root, same device directory) and proves the
// restart path: the session and the pairing come back, the device is not paired a second time and
// both chips turn green again without a click.
//
// The app is the REAL installed build; the data root, the device directory and the credential
// names are isolated, so nothing here touches the operator's own Canalla data. The device
// credential target matters: `ALEX_DEVICE_DIR` only moves `device.json`, the credential itself is
// a machine-wide Credential Manager entry, so this run borrows its own target and deletes it again.
// No GPU, no Pod, no TinyFish, no paid provider.
//
// Usage (from apps/desktop): node e2e/always-ready.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
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
const PYTHON = path.resolve(
  __dirname,
  "..",
  "..",
  "backend",
  ".venv",
  "Scripts",
  "python.exe",
);
const CDP_PORT = 9245;
const PASSWORD = "always-ready-passphrase-1";
const DATA_ROOT = fs.mkdtempSync(
  path.join(os.tmpdir(), "canalla-always-ready-data-"),
);
const DEVICE_DIR = fs.mkdtempSync(
  path.join(os.tmpdir(), "canalla-always-ready-cred-"),
);
const EMAIL = "always-ready@example.com";
const STAMP = Date.now().toString(36);
// Reused by both launches on purpose: a second pairing would mean the restart did not restore.
const DEVICE_CREDENTIAL_TARGET = `Alex LLM/device-credential-always-ready-${STAMP}`;
const RELEASE_GATEWAY_CREDENTIAL = `always-ready-${STAMP}`;
// A cold Tor needs a consensus before it can build any circuit: the window is generous, the
// wait is passive (the chip is polled, nothing is clicked), and nothing is skipped on failure.
const READY_TIMEOUT_MS = Number(process.env.ALWAYS_READY_TIMEOUT_MS || 420000);

const failures = [];
let child = null;
let backendPort = 0;
let token = null;

function check(name, ok, detail = "") {
  console.log(
    `${ok ? "PASS" : "FAIL"} ${name}${detail ? `  [${detail}]` : ""}`,
  );
  if (!ok) failures.push(name);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/** cmdkey cannot address a target that contains spaces, so delete through the OS API. */
const CREDENTIAL_DELETER = `
import ctypes, sys
from ctypes import wintypes

api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
api.CredDeleteW.restype = wintypes.BOOL
if api.CredDeleteW(sys.argv[1], 1, 0):
    sys.exit(0)
# ERROR_NOT_FOUND counts as clean: there is nothing of ours left to delete.
sys.exit(0 if ctypes.get_last_error() == 1168 else 1)
`;

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

function credentialTargets() {
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    "cmdkey /list | Select-String 'Alex LLM'",
  ]);
  // cmdkey wraps long lines, so drop ALL whitespace before matching, then rebuild the real
  // target name (whose display form contains a space).
  const text = (raw.stdout || Buffer.from(""))
    .toString("latin1")
    .replace(/\s+/g, "");
  const keys = text.match(/AlexLLM\/[A-Za-z0-9._/-]+/g) || [];
  return [
    ...new Set(keys.map((key) => "Alex LLM/" + key.slice("AlexLLM/".length))),
  ];
}

function launchApp() {
  return spawn(EXE, [], {
    env: {
      ...process.env,
      ALEX_LLM_DATA_DIR: DATA_ROOT,
      ALEX_DEVICE_DIR: DEVICE_DIR,
      ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
      ALEX_GATEWAY_CREDENTIAL_NAME: RELEASE_GATEWAY_CREDENTIAL,
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
      if (targets.some((target) => target.type === "page" && target.url))
        return true;
    } catch {
      /* not ready yet */
    }
    await sleep(500);
  }
  return false;
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

/** The owner session this run created through the UI: used to read the device list back. */
async function ownerToken() {
  if (token) return token;
  const login = await fetchJson(`http://127.0.0.1:${backendPort}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  token = login.access_token;
  return token;
}

async function devices() {
  const call = async () =>
    fetchJson(`http://127.0.0.1:${backendPort}/tools/devices`, {
      headers: { Authorization: `Bearer ${await ownerToken()}` },
    });
  try {
    return await call();
  } catch {
    // The sidecar restarted: the in-memory token may be stale, so ask for a fresh one once.
    token = null;
    return call();
  }
}

/** The sidecar processes and their parents, straight from the OS: the kill must hit our child only. */
function sidecarProcesses() {
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    `Get-CimInstance Win32_Process -Filter "Name='alex-backend.exe'" | Select-Object ProcessId,ParentProcessId | ConvertTo-Csv -NoTypeInformation`,
  ]);
  return (raw.stdout || Buffer.from(""))
    .toString("utf8")
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((line) =>
      line
        .replace(/"/g, "")
        .split(",")
        .map((value) => Number(value.trim())),
    )
    .filter(
      ([pid, parent]) => Number.isInteger(pid) && Number.isInteger(parent),
    )
    .map(([pid, parent]) => ({ pid, parent }));
}

async function waitForNewSidecar(deadPid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = sidecarProcesses().find((item) => item.pid !== deadPid);
    if (found) return found.pid;
    await sleep(1000);
  }
  return null;
}

async function quitApp() {
  if (!child) return;
  try {
    execFileSync("powershell.exe", [
      "-NoProfile",
      "-Command",
      `(Get-Process -Id ${child.pid}).CloseMainWindow() | Out-Null`,
    ]);
  } catch {
    /* fall through to the hard kill */
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && child.exitCode === null) await sleep(250);
  if (child.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  }
  const portDeadline = Date.now() + 30000;
  while (Date.now() < portDeadline) {
    const listing = spawnSync(
      "tasklist",
      ["/FI", "IMAGENAME eq alex-backend.exe"],
      {
        encoding: "utf8",
      },
    ).stdout;
    if (!listing.includes("alex-backend.exe")) {
      child = null;
      return;
    }
    await sleep(400);
  }
  child = null;
}

async function register(page) {
  // A fresh isolated root opens the first-run owner flow, not the login tabs.
  await page
    .getByRole("button", { name: "Создать владельца Canalla" })
    .waitFor({ timeout: 180000 });
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Canalla" }).click();
}

/** The chip's own text, straight from the DOM the user sees. */
async function chipState(page, name) {
  const chip = page.locator(".status-chip", { hasText: name }).first();
  const label = await chip.getAttribute("aria-label").catch(() => null);
  if (label) return label;
  return (await chip.innerText().catch(() => "")).replace(/\s+/g, " ").trim();
}

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

/** The popover separates service health from usage policy for both subsystems. */
async function checkPopover(page, name) {
  const chip = page.locator(".status-chip", { hasText: name }).first();
  await chip.click();
  const text = (await page.getByTestId("status-detail").innerText()).replace(
    /\s+/g,
    " ",
  );
  await chip.click();
  return text;
}

async function openApp() {
  child = launchApp();
  if (!(await waitCdp())) return null;
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const page = browser.contexts()[0].pages()[0];
  return { browser, page };
}

async function main() {
  console.log(
    "COMPUTER + TOR ALWAYS READY — installed app, normal launch, no buttons",
  );
  console.log(`  app      ${EXE}`);
  console.log(`  data     ${DATA_ROOT}`);
  console.log(`  device   ${DEVICE_DIR}`);
  console.log(`  cred     ${DEVICE_CREDENTIAL_TARGET}`);
  // Tauri's WebView2 lives in one shared EBWebView folder, so a lingering instance would make this
  // launch attach to the old browser process and silently lose the debugging port.
  const stray = spawnSync("tasklist", ["/FI", "IMAGENAME eq alex-llm.exe"], {
    encoding: "utf8",
  }).stdout;
  if (stray.includes("alex-llm.exe")) {
    console.log("  [info] closing a leftover alex-llm.exe before the launch");
    spawnSync("taskkill", ["/IM", "alex-llm.exe", "/T", "/F"], {
      stdio: "ignore",
    });
    await sleep(2500);
  }
  let session = await openApp();
  if (!session) {
    check(
      "the installed app starts and exposes its window",
      false,
      "CDP never became ready",
    );
    await quitApp();
    return 1;
  }
  const { browser, page } = session;
  let firstDeviceId = null;
  try {
    await register(page);
    await page
      .getByRole("textbox", { name: "Сообщение", exact: true })
      .waitFor({ timeout: 60000 });
    backendPort = await findBackendPort();
    check(
      "the isolated install serves its own backend",
      backendPort !== 0,
      `port ${backendPort}`,
    );
    const paired = await devices();
    check(
      "the host pairs this device by itself — no «Подключить» was pressed",
      paired.length === 1,
      `${paired.length} device(s)`,
    );
    if (paired.length === 1) {
      firstDeviceId = paired[0].device_id;
      check(
        "the credential went to this run's own target, not the machine's real one",
        credentialTargets().includes(DEVICE_CREDENTIAL_TARGET),
        DEVICE_CREDENTIAL_TARGET,
      );
    }

    // Nothing is clicked from here on: the app must reach ready by itself.
    const computer = await waitForChip(page, "Computer", ["Готово"], 90000);
    check(
      "COMPUTER turns ready on its own (the host loop pairs and heartbeats without a button)",
      computer.ready,
      computer.text,
    );

    const tor = await waitForChip(page, "Tor", ["Готово"], READY_TIMEOUT_MS);
    check(
      "TOR turns ready on its own (discovery, managed start, SOCKS and a proven circuit)",
      tor.ready,
      tor.text,
    );

    // The popover must separate service health from usage policy.
    const torText = await checkPopover(page, "Tor");
    check(
      "the Tor popover reports health",
      /Состояние/.test(torText),
      torText.slice(0, 120),
    );
    check(
      "the Tor popover reports the policy as its own row",
      /Режим/.test(torText),
    );
    check(
      "the Tor popover names the SOCKS endpoint",
      /127\.0\.0\.1:\d+/.test(torText),
    );
    check("the Tor popover shows the proof", /Цепь проверена/.test(torText));

    const computerText = await checkPopover(page, "Computer");
    check(
      "the Computer popover reports health",
      /Состояние/.test(computerText),
    );
    check(
      "the Computer popover reports the mode as its own row",
      /Режим/.test(computerText),
    );
    check("the Computer popover proves pairing", /Сопряжён/.test(computerText));

    // The service survived the wait: no manual action was ever needed.
    const torAgain = await chipState(page, "Tor");
    check(
      "Tor is still ready after the popover round trip",
      torAgain.includes("Готово"),
      torAgain,
    );
  } finally {
    await browser.close().catch(() => {});
    await quitApp();
  }

  // Restart: same install, same roots, nothing pressed.
  console.log();
  console.log("restart — the same install launched again");
  session = await openApp();
  if (!session) {
    check("the app starts again after a full quit", false, "CDP never came up");
    return 1;
  }
  const { browser: browser2, page: page2 } = session;
  try {
    const restored = await page2
      .getByRole("textbox", { name: "Сообщение", exact: true })
      .waitFor({ timeout: 180000 })
      .then(() => true)
      .catch(() => false);
    check(
      "the relaunch restores the session and the pairing — no login, no first run",
      restored,
    );
    check(
      "no «Создать владельца Canalla» on the second run",
      !(await page2
        .getByRole("button", { name: "Создать владельца Canalla" })
        .isVisible()
        .catch(() => false)),
    );

    // The honest startup states («Подключается…», «Проверяем…», «восстанавливает соединение»)
    // are allowed; a red chip for a service nobody clicked would be the defect.
    const immediate = await chipState(page2, "Computer");
    check(
      "the Computer chip is never red after a restart",
      !/Недоступно|Ошибка|Не настроено|Выключено/.test(immediate),
      immediate,
    );

    const computer = await waitForChip(page2, "Computer", ["Готово"], 120000);
    check(
      "COMPUTER is ready again after the restart, still without a click",
      computer.ready,
      computer.text,
    );
    const tor = await waitForChip(page2, "Tor", ["Готово"], READY_TIMEOUT_MS);
    check(
      "TOR is ready again after the restart, still without a click",
      tor.ready,
      tor.text,
    );

    backendPort = await findBackendPort();
    const pairedAgain = await devices();
    check(
      "exactly one paired device after two launches (the host does not duplicate itself)",
      pairedAgain.length === 1,
      `${pairedAgain.length} device(s)`,
    );
    check(
      "the same device id survived the restart — the pairing was reused, not replaced",
      pairedAgain.length === 1 && pairedAgain[0].device_id === firstDeviceId,
      `${pairedAgain[0]?.device_id || "—"} vs ${firstDeviceId || "—"}`,
    );
    check(
      "that device is online again on its own heartbeat",
      pairedAgain.length === 1 && pairedAgain[0].online === true,
      String(pairedAgain[0]?.online),
    );

    // Crash recovery: the owned local service dies under a running app. Requirement: no button,
    // no broken session - the app must bring a healthy backend back and both chips must recover.
    console.log();
    console.log(
      "crash recovery — the owned sidecar is killed while the app runs",
    );
    const sidecars = sidecarProcesses();
    check(
      "exactly one sidecar serves this run (no duplicate, no orphan)",
      sidecars.length === 1,
      sidecars.map((item) => `${item.pid}<-${item.parent}`).join(", "),
    );
    const victim = sidecars[0];
    if (victim) {
      spawnSync("taskkill", ["/PID", String(victim.pid), "/T", "/F"], {
        stdio: "ignore",
      });
      await sleep(3000);
      check(
        "the sidecar really died (the crash is real, not simulated)",
        !sidecarProcesses().some((item) => item.pid === victim.pid),
        `pid ${victim.pid}`,
      );
      const duringCrash = await chipState(page2, "Computer");
      check(
        "the Computer chip never tells the user to press a button during recovery",
        !/Недоступно|Ошибка|Не настроено|Выключено/.test(duringCrash),
        duringCrash,
      );

      const respawned = await waitForNewSidecar(victim.pid, 180000);
      check(
        "the app brings a new sidecar up by itself, without a click",
        respawned !== null,
        respawned ? `pid ${respawned}` : "no new sidecar",
      );
      backendPort = await findBackendPort();
      check(
        "the recovered backend answers /health on its own port",
        backendPort !== 0,
        `port ${backendPort}`,
      );
      const computerRecovered = await waitForChip(
        page2,
        "Computer",
        ["Готово"],
        180000,
      );
      check(
        "COMPUTER is ready again after the crash, still without a click",
        computerRecovered.ready,
        computerRecovered.text,
      );
      const torRecovered = await waitForChip(
        page2,
        "Tor",
        ["Готово"],
        READY_TIMEOUT_MS,
      );
      check(
        "TOR is ready again after the crash (fresh managed process, fresh proof)",
        torRecovered.ready,
        torRecovered.text,
      );
      check(
        "the session survived the crash — no login, no first run",
        await page2
          .getByRole("textbox", { name: "Сообщение", exact: true })
          .isVisible()
          .catch(() => false),
      );
      const pairedAfterCrash = await devices();
      check(
        "still exactly one device, same id, after the crash",
        pairedAfterCrash.length === 1 &&
          pairedAfterCrash[0].device_id === firstDeviceId,
        `${pairedAfterCrash.length} device(s), ${pairedAfterCrash[0]?.device_id || "—"}`,
      );
      check(
        "the pairing is healthy again after the crash",
        pairedAfterCrash[0]?.online === true,
        String(pairedAfterCrash[0]?.online),
      );
    }
  } finally {
    await browser2.close().catch(() => {});
    await quitApp();
  }

  const deleted = deleteCredential(DEVICE_CREDENTIAL_TARGET);
  check(
    "the run's own device credential is cleaned up",
    deleted || !credentialTargets().includes(DEVICE_CREDENTIAL_TARGET),
    DEVICE_CREDENTIAL_TARGET,
  );
  console.log(
    failures.length
      ? `ALWAYS READY FAILED: ${failures.length} check(s) — ${failures.join("; ")}`
      : "ALWAYS READY PASS — Computer and Tor become green on a normal launch and after a restart, no buttons",
  );
  return failures.length ? 1 : 0;
}

main()
  .then((code) => process.exit(code))
  .catch(async (error) => {
    console.error("ALWAYS READY ERROR:", error);
    await quitApp();
    deleteCredential(DEVICE_CREDENTIAL_TARGET);
    process.exit(1);
  });
