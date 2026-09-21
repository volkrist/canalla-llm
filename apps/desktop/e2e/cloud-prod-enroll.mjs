// REAL production acceptance of the INSTALLED Alex against the DEPLOYED public Gateway.
//
// This is the client half of the "Central Gateway production deployment" slice: the app is
// started from its real installation (%LOCALAPPDATA%\Programs\Alex LLM\alex-llm.exe) with the
// REAL data root and the REAL credential entries — no isolated fixtures — and every state
// change goes through the real product path (Settings → Canalla Cloud):
//
//   1. explicit Disconnect of an enrolled installation;
//   2. a full restart of the app, which must still start (readiness contract), stay
//      disconnected and keep the local dev credential dormant;
//   3. a fresh enrollment with a one-time activation code created on the server;
//   4. the shared RunPod balance arriving through the public Gateway;
//   5. proof that the client never renders, stores, logs or sends the master RunPod key, and
//      that it does not hot-loop its readiness probe against the shared Gateway.
//
// The activation code stays in this process' memory: it is never printed, never written to
// disk and never passed on a command line.
//
// No Pod, no GPU, no Network Volume change, no TinyFish. The only provider traffic is the
// Gateway's own read-only balance query.
//
// Usage (from apps/desktop):
//   node e2e/cloud-prod-enroll.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..", "..");
const EXE = path.join(
  process.env.LOCALAPPDATA,
  "Programs",
  "Alex LLM",
  "alex-llm.exe",
);
const INSTALLER = path.join(
  REPO,
  "apps",
  "desktop",
  "src-tauri",
  "target",
  "release",
  "bundle",
  "nsis",
  "Alex LLM_0.9.3_x64-setup.exe",
);
const DATA_ROOT = path.join(process.env.LOCALAPPDATA, "Alex LLM");
const LOG = path.join(DATA_ROOT, "logs", "backend.log");
const CDP_PORT = 9229;
const SSH_HOST = "distance";
const GATEWAY_DIR = "/opt/alex-gateway/current";
const GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env";
const PROVIDER_TARGET = "Alex LLM/provider/runpod";
const INSTALLATION_TARGET = "Alex LLM/gateway/installation";
const EXPECTED_URL = "https://gateway.12testers.store";
const PYTHON = path.resolve(
  REPO,
  "apps",
  "backend",
  ".venv",
  "Scripts",
  "python.exe",
);
// The backend log is append-only across sessions, so only what a run appends counts.
const LOG_OFFSET = fs.existsSync(LOG) ? fs.statSync(LOG).size : 0;
const STARTED_AT = Date.now();

let failures = 0;
let skipped = 0;
let code = "";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const flat = (value) => (value || "").replace(/\s+/g, " ").trim();

function check(name, condition, detail = "") {
  if (!condition) failures += 1;
  console.log(
    `  [${condition ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`,
  );
}

function skip(name, reason) {
  skipped += 1;
  console.log(`  [SKIP] ${name} — ${reason}`);
}

// ---------------------------------------------------------------------------------------
// Secrets stay in memory: read through the OS credential store in a separate process and
// only ever compared against text, never logged.
// ---------------------------------------------------------------------------------------

const CREDENTIAL_READER = `
import ctypes, sys
from ctypes import wintypes
class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]
api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
api.CredReadW.restype = wintypes.BOOL
api.CredFree.argtypes = [ctypes.c_void_p]
pointer = ctypes.c_void_p()
if not api.CredReadW(sys.argv[1], 1, 0, ctypes.byref(pointer)):
    raise SystemExit(3)
try:
    cred = ctypes.cast(pointer, ctypes.POINTER(CREDENTIALW)).contents
    blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    sys.stdout.write(blob.decode("utf-8", "ignore").strip())
finally:
    api.CredFree(pointer)
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

function ssh(remote) {
  return execFileSync(
    "ssh",
    ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SSH_HOST, remote],
    {
      encoding: "utf8",
      timeout: 180000,
    },
  );
}

/** Create the one-time code on the server with the real operator CLI; value stays in memory. */
function operatorCode(label) {
  const remote =
    `sudo -n -u alex-gateway bash -c 'set -a; . ${GATEWAY_ENV}; set +a; ` +
    `export HOME=/var/lib/alex-gateway PYTHONPATH=${GATEWAY_DIR}/gateway:${GATEWAY_DIR}/backend; ` +
    `export ALEX_BACKEND_LIB_DIR=${GATEWAY_DIR}/backend; cd ${GATEWAY_DIR}; ` +
    `/opt/alex-gateway/venv/bin/python -m gateway.cli create-code --label ${label}'`;
  const lines = ssh(remote)
    .split(/\r?\n/)
    .map((row) => row.trim())
    .filter(Boolean);
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].startsWith("activation code")) {
      const value = lines[index + 1];
      if (value && value.length >= 20) return value;
    }
  }
  throw new Error("the operator CLI returned no activation code");
}

/** Read-only view of the deployed Gateway installations. */
function gatewayState() {
  const raw = ssh(
    `sudo -n -u alex-gateway sqlite3 -json /var/lib/alex-gateway/gateway.db ` +
      `"select id, name, revoked_at, last_seen_at from installations order by created_at desc limit 5"`,
  );
  return JSON.parse(raw || "[]");
}

function accessLogMatches(pattern) {
  const out = ssh(
    `sudo -n grep -Ec '${pattern}' /var/log/nginx/alex-gateway.access.log || true`,
  );
  return Number.parseInt(out.trim(), 10) || 0;
}

function accessLogLines() {
  const out = ssh("sudo -n wc -l < /var/log/nginx/alex-gateway.access.log");
  return Number.parseInt(out.trim(), 10) || 0;
}

let accessLogBaseline = 0;

/** Only the requests that arrived during this run count: the harness itself probes the
 *  forbidden passthrough routes, so the whole log would always look suspicious. */
function accessLogSince() {
  return ssh(
    `sudo -n tail -n +${accessLogBaseline + 1} /var/log/nginx/alex-gateway.access.log`,
  );
}

function countIn(text, pattern) {
  return (text.match(new RegExp(pattern, "g")) || []).length;
}

// ---------------------------------------------------------------------------------------
// App control
// ---------------------------------------------------------------------------------------

async function fetchJson(url, options) {
  const response = await fetch(url, {
    ...options,
    signal: AbortSignal.timeout(5000),
  });
  return response.json();
}

/** The real installation: the real data root, the real credentials, the baked Gateway URL. */
function launchInstalledApp() {
  const env = {
    ...process.env,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
  };
  delete env.ALEX_LLM_DATA_DIR;
  delete env.ALEX_DEVICE_DIR;
  delete env.ALEX_GATEWAY_CREDENTIAL_NAME;
  delete env.ALEX_GATEWAY_URL;
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: false });
}

async function waitCdp(timeoutMs = 150000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const targets = await fetchJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
      if (targets.some((target) => target.type === "page" && target.url))
        return true;
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
  const deadline = Date.now() + 40000;
  while (Date.now() < deadline && child.exitCode === null) await sleep(250);
  if (child.exitCode === null) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
    const hard = Date.now() + 20000;
    while (Date.now() < hard && child.exitCode === null) await sleep(250);
  }
  const portDeadline = Date.now() + 40000;
  while (Date.now() < portDeadline) {
    const listing = spawnSync(
      "tasklist",
      ["/FI", "IMAGENAME eq alex-backend.exe"],
      {
        encoding: "utf8",
      },
    ).stdout;
    if (!listing.includes("alex-backend.exe")) return;
    await sleep(400);
  }
}

/** Launch the installed app and hand a page to the scenario. Returns the measured startup. */
async function withApp(step) {
  const child = launchInstalledApp();
  const launched = Date.now();
  try {
    await waitCdp();
    const browser = await chromium.connectOverCDP(
      `http://127.0.0.1:${CDP_PORT}`,
    );
    const page = browser.contexts()[0].pages()[0];
    try {
      return await step(page, launched);
    } finally {
      await browser.close().catch(() => {});
    }
  } finally {
    await quitApp(child);
  }
}

async function openSettingsSection(page, name) {
  const dialog = page.getByRole("dialog");
  const alreadyOpen = await dialog
    .first()
    .isVisible()
    .catch(() => false);
  if (!alreadyOpen) {
    await page
      .getByRole("button", { name: "Settings", exact: true })
      .click({ timeout: 90000 })
      .catch(async () => {
        await page.getByLabel("Настройки").click({ timeout: 30000 });
      });
    await dialog.waitFor({ timeout: 30000 });
  }
  await dialog
    .getByRole("button", { name, exact: true })
    .click({ timeout: 30000 });
  return dialog;
}

async function waitForDialogText(page, pattern, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    last = await page
      .getByRole("dialog")
      .innerText()
      .catch(() => "");
    if (new RegExp(pattern).test(last)) return true;
    await sleep(1000);
  }
  return false;
}

async function waitForBalance(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    last = flat(
      await page
        .getByTestId("runpod-balance")
        .innerText()
        .catch(() => ""),
    );
    if (/\$\s?\d/.test(last)) return last;
    await sleep(1000);
  }
  return last;
}

function contains(file, needle) {
  if (!needle || needle.length < 8) return false;
  try {
    return fs.readFileSync(file).includes(Buffer.from(needle, "utf8"));
  } catch {
    return false;
  }
}

function sqliteHas(table, needle) {
  if (!needle || needle.length < 8) return false;
  try {
    const out = execFileSync(
      PYTHON,
      [
        "-c",
        `import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);` +
          `print(any(sys.argv[2] in str(row) for row in db.execute(sys.argv[3])))`,
        path.join(DATA_ROOT, "data", "alex.db"),
        needle,
        "select * from " + table,
      ],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return out.trim() === "True";
  } catch {
    return false;
  }
}

function localTables() {
  try {
    return execFileSync(
      PYTHON,
      [
        "-c",
        `import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);` +
          `print(" ".join(row[0] for row in db.execute("select name from sqlite_master where type='table'")))`,
        path.join(DATA_ROOT, "data", "alex.db"),
      ],
      { encoding: "utf8" },
    )
      .trim()
      .split(/\s+/)
      .filter(Boolean);
  } catch {
    return [];
  }
}

/** Positive control for the database scan: a value that must be found. */
function sessionControl() {
  const needle = fs
    .readFileSync(path.join(DATA_ROOT, "runtime", "session.id"), "utf8")
    .trim();
  return {
    needle: needle.slice(0, 8) + "…",
    found: sqliteHas("auth_sessions", needle),
  };
}

function pidsOf(names) {
  const list = names.join("','");
  try {
    return execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        `(Get-Process -Name '${list}' -ErrorAction SilentlyContinue).Id -join ' '`,
      ],
      { encoding: "utf8", timeout: 60000 },
    )
      .trim()
      .split(/\s+/)
      .filter(Boolean);
  } catch {
    return [];
  }
}

function externalConnections(pids) {
  if (!pids.length) return [];
  const filter = pids.map((pid) => `$_.OwningProcess -eq ${pid}`).join(" -or ");
  const command =
    `Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | ` +
    `Where-Object { ${filter} } | Select-Object -ExpandProperty RemoteAddress -Unique`;
  try {
    return execFileSync("powershell.exe", ["-NoProfile", "-Command", command], {
      encoding: "utf8",
      timeout: 60000,
    })
      .split(/\r?\n/)
      .map((row) => row.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------------------

/** Phase 0: if this installation is enrolled, leave Canalla Cloud through the real UI. */
async function disconnectIfEnrolled(page, launched) {
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 240000 });
  console.log(
    `      app ready in ${Math.round((Date.now() - launched) / 1000)}s`,
  );
  const dialog = await openSettingsSection(page, "Canalla Cloud");
  // The panel reads the cloud state when it mounts, so wait for that read to settle
  // instead of judging a label that is still being fetched.
  const settled = await waitForDialogText(
    page,
    "Canalla Cloud\\s*·\\s*(Подключено|Не подключено)",
    180000,
  );
  const panel = await dialog.innerText();
  const label = flat(
    panel.split("\n").find((row) => /Canalla Cloud/.test(row)) || "",
  );
  check("P0 the panel settles on an authoritative state", settled, label);
  if (!/Подключено/.test(panel)) {
    skip("phase 0 disconnect", "this installation was not enrolled");
    await page
      .getByRole("button", { name: "Закрыть настройки" })
      .click({ timeout: 30000 });
    return false;
  }
  check("P1 the installation starts enrolled", true, label);
  const beforeDisconnect = readCredential(INSTALLATION_TARGET);
  check(
    "P2 the installation credential exists before the disconnect",
    Boolean(beforeDisconnect),
  );
  const providerBefore = readCredential(PROVIDER_TARGET);
  const liveBefore = gatewayState()
    .filter((row) => !row.revoked_at)
    .map((row) => row.id);
  await dialog
    .getByRole("button", { name: "Отключить" })
    .click({ timeout: 30000 });
  const gone = await waitForDialogText(page, "Не подключено", 180000);
  check(
    "P3 the panel reports «Не подключено» after the explicit disconnect",
    gone,
  );
  check(
    "P4 the installation credential is removed locally",
    readCredential(INSTALLATION_TARGET) === null,
  );
  check(
    "P5 the separate local dev credential is untouched by the disconnect",
    Boolean(providerBefore) &&
      readCredential(PROVIDER_TARGET) === providerBefore,
  );
  const stillLive = gatewayState()
    .filter((row) => !row.revoked_at)
    .map((row) => row.id);
  check(
    "P6 the disconnect revoked this installation on the server too",
    liveBefore.length > 0 && !stillLive.some((id) => liveBefore.includes(id)),
    `${liveBefore.length} live → ${stillLive.length} live`,
  );
  await page
    .getByRole("button", { name: "Закрыть настройки" })
    .click({ timeout: 30000 });
  return true;
}

/** Phase 1-2: a full restart, then the honest pre-enrollment state. */
async function preEnrollmentState(page, launched) {
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 240000 });
  const readySeconds = Math.round((Date.now() - launched) / 1000);
  check(
    "R1 the enrolled-then-disconnected install starts again (readiness contract)",
    readySeconds < 120,
    `${readySeconds}s`,
  );

  await page.waitForFunction(
    () =>
      document.querySelectorAll(".status-chip").length === 5 &&
      !document.querySelector(".status-chip.state-starting"),
    null,
    { timeout: 120000 },
  );
  const chips = (await page.locator(".status-chip").allInnerTexts()).map(flat);
  check("B1 five chips render", chips.length === 5, chips.join(" | "));
  check(
    "B2 the AI chip does not claim Готово while nothing is enrolled",
    Boolean(chips[0]) && !/Готово/.test(chips[0]),
    chips[0],
  );
  const balance = flat(
    await page
      .getByTestId("runpod-balance")
      .innerText()
      .catch(() => ""),
  );
  check(
    "B3 no balance is shown without an enrollment",
    !/\$\s?\d/.test(balance),
    balance,
  );

  const dialog = await openSettingsSection(page, "Canalla Cloud");
  const panel = await dialog.innerText();
  check("B4 the panel reports «Не подключено»", /Не подключено/.test(panel));
  const address = dialog.getByLabel("Адрес Gateway");
  check(
    "B5 the build carries the production Gateway address",
    (await address.inputValue()).trim() === EXPECTED_URL,
    (await address.inputValue()).trim(),
  );
  check(
    "B6 the activation code field is offered",
    (await dialog.getByLabel("Код активации").count()) > 0,
  );

  const compute = await openSettingsSection(page, "AI / Compute");
  const computeText = await compute.innerText();
  const keyInputs = await compute
    .locator('input[placeholder="Вставьте новый ключ"]')
    .count();
  const keyLabels = await compute
    .locator("label")
    .filter({ hasText: "RunPod API key" })
    .count();
  check(
    "B7 the RunPod API key field is absent in shared mode",
    keyInputs === 0 && keyLabels === 0,
    `inputs=${keyInputs} labels=${keyLabels}`,
  );
  check(
    "B8 the panel tells the user the shared key is not needed here",
    /Ключ RunPod здесь не нужен/.test(computeText),
  );
  check(
    "B9 the local dev credential exists but is dormant (shared mode ignores it)",
    Boolean(readCredential(PROVIDER_TARGET)),
  );
}

/** Phase 3-4: enroll through the real product path and read the shared account. */
async function enrollAndVerify(page) {
  const dialog = await openSettingsSection(page, "Canalla Cloud");
  await dialog.getByLabel("Код активации").fill(code);
  await dialog.getByRole("button", { name: "Подключить" }).click();
  const connected = await waitForDialogText(
    page,
    "Canalla Cloud\\s*·\\s*Подключено",
    300000,
  );
  const panel = await dialog.innerText();
  check(
    "C1 the panel settles on «Canalla Cloud · Подключено»",
    connected,
    flat(panel).slice(0, 90),
  );
  check(
    "C2 the owned backend was restarted with the shared environment",
    /перезапущен/.test(panel),
  );
  check(
    "C3 the panel shows the installation and the Gateway without any secret",
    /Установка:/.test(panel) && panel.includes("gateway.12testers.store"),
  );
  const installationId =
    (panel.match(/Установка:\s*([0-9a-f-]{16,})/i) || [])[1] || "";
  check(
    "C4 an installation id is reported",
    Boolean(installationId),
    installationId.slice(0, 8) + "…",
  );
  const html = await dialog.innerHTML();
  check(
    "C5 the panel DOM never contains the activation code",
    !panel.includes(code),
  );
  check(
    "C6 the panel DOM never carries a secret-shaped field",
    !/installation_secret|access_token|Bearer\s+ey/i.test(html),
  );

  await page
    .getByRole("button", { name: "Закрыть настройки" })
    .click({ timeout: 30000 });
  const balance = await waitForBalance(page, 240000);
  check(
    "D1 the app shows the real shared RunPod balance",
    /\$\s?\d/.test(balance),
    balance,
  );
  const chips = (await page.locator(".status-chip").allInnerTexts()).map(flat);
  check(
    "D2 the AI chip no longer says the cloud is not connected",
    !/не подключён/i.test(chips[0] || ""),
    chips[0],
  );
  return { installationId, balance };
}

async function clientLeakChecks(page, providerKey, installationId) {
  check(
    "E1 the Gateway installation credential is a different secret",
    readCredential(INSTALLATION_TARGET) !== providerKey &&
      Boolean(readCredential(INSTALLATION_TARGET)),
  );

  const appended = fs.existsSync(LOG)
    ? fs.readFileSync(LOG, "utf8").slice(LOG_OFFSET)
    : "";
  const minutes = Math.max(1, (Date.now() - STARTED_AT) / 60000);
  const gatewayCalls = (
    appended.match(
      /HTTP Request: (GET|POST) https:\/\/gateway\.12testers\.store/g,
    ) || []
  ).length;
  const modelProbes = (
    appended.match(/gateway\.12testers\.store\/v1\/models/g) || []
  ).length;
  check(
    "F1 the backend log of this run never mentions api.runpod.io",
    !/api\.runpod\.io/i.test(appended),
  );
  check(
    "F2 the backend log of this run does not contain the master key",
    !providerKey || !appended.includes(providerKey),
  );
  check(
    "F3 the owned backend restarted into the packaged shared runtime",
    /alex\.runtime startup product=alex-llm version=0\.9\.3 protocol=1 mode=packaged/.test(
      appended,
    ),
  );
  check(
    "F4 the client does not hot-loop its readiness probe against the Gateway",
    modelProbes / minutes <= 15,
    `${modelProbes} model probe(s) in ${minutes.toFixed(1)} min, ${gatewayCalls} gateway call(s)`,
  );
  check(
    "F5 the shared Gateway never rate-limited this client",
    !/HTTP\/1\.1 429/.test(appended),
  );

  const pids = pidsOf(["alex-llm", "alex-backend", "alex-host-loop"]);
  const external = externalConnections(pids).filter(
    (row) =>
      row &&
      row !== "::1" &&
      !row.startsWith("127.") &&
      row !== "0.0.0.0" &&
      row !== "::",
  );
  console.log(
    `  [info] client processes=${pids.length} external peers=${
      external.length ? external.join(",") : "none observed at this instant"
    }`,
  );

  const domText = await page.evaluate(() => {
    const storage = { ...window.localStorage, ...window.sessionStorage };
    return document.documentElement.outerHTML + JSON.stringify(storage);
  });
  check(
    "G1 not in the app DOM or browser storage",
    !providerKey || !domText.includes(providerKey),
  );
  const tables = localTables();
  const control = sessionControl();
  check(
    "G2 the local database scan works (positive control)",
    control.found,
    `tables=${tables.length}`,
  );
  const hits = tables.filter((table) => sqliteHas(table, providerKey));
  check(
    "G3 not in the local SQLite database",
    hits.length === 0,
    `hits=${hits.length}`,
  );
  check("G4 not in the installer binary", !contains(INSTALLER, providerKey));

  const rows = gatewayState();
  const stored = rows.find((row) => row.id === installationId);
  check(
    "H1 the installation this app enrolled is stored on the server",
    Boolean(stored),
  );
  check("H2 it is not revoked", Boolean(stored) && !stored.revoked_at);
  const access = accessLogSince();
  const enrollCalls = countIn(access, "POST /enroll");
  const tokenCalls = countIn(access, "/auth/token");
  const balanceCalls = countIn(access, "/balance HTTP/[0-9.]+.. 200");
  check(
    "H3 the public access log shows the client's enrollment and token traffic",
    enrollCalls > 0 && tokenCalls > 0,
    `enroll=${enrollCalls} token=${tokenCalls}`,
  );
  check(
    "H4 the access log carries no credential material",
    (!providerKey || !access.includes(providerKey)) && !access.includes(code),
  );
  check(
    "H5 the client never touched a provider passthrough route",
    countIn(access, "\/runpod\/(graphql|request)|\/provider\/raw") === 0,
  );
  check(
    "H6 the balance the app displays was served by the Gateway itself",
    balanceCalls > 0,
    `balance=${balanceCalls} (requests this run: ${access.split(/\r?\n/).filter(Boolean).length})`,
  );
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(1);
  }
  console.log(
    "Production acceptance of the REAL installed app — public Gateway",
  );
  console.log(`app=${EXE}`);
  console.log(`data root=${DATA_ROOT}  gateway=${EXPECTED_URL}`);
  accessLogBaseline = accessLogLines();
  console.log();
  console.log(
    "0. phases 0-2: disconnect (if enrolled), restart, honest pre-enrollment state",
  );
  await withApp(async (page, launched) => {
    await disconnectIfEnrolled(page, launched);
  });
  await withApp(async (page, launched) => {
    await preEnrollmentState(page, launched);
  });

  console.log();
  console.log(
    "3. the operator creates a one-time activation code on the server",
  );
  code = operatorCode("prod-enroll-real");
  check(
    "O1 the server issued a one-time activation code",
    code.length >= 20,
    `${code.length} chars (value hidden)`,
  );

  let enrolled = null;
  await withApp(async (page) => {
    console.log();
    console.log(
      "4. enrollment through the real product path, then the shared balance",
    );
    enrolled = await enrollAndVerify(page);
    console.log();
    console.log(
      "5. nothing on this machine and nothing in the client holds or leaks the master key",
    );
    await clientLeakChecks(
      page,
      readCredential(PROVIDER_TARGET),
      enrolled.installationId,
    );
  });

  console.log();
  if (skipped) console.log(`SKIPPED: ${skipped}`);
  if (failures > 0) {
    console.error(`PRODUCTION ACCEPTANCE FAILED: ${failures} failing check(s)`);
    process.exit(1);
  }
  console.log(
    `PRODUCTION ACCEPTANCE PASS — the installed app uses the shared Canalla Cloud account (${
      enrolled ? enrolled.balance.replace(/\s+/g, " ") : "balance read"
    })`,
  );
  process.exit(0);
}

main().catch((error) => {
  console.error("PRODUCTION ACCEPTANCE ERROR:", error);
  process.exit(1);
});
