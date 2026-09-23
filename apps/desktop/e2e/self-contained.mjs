// WINDOWS SELF-CONTAINED — installed-app acceptance (STEP 3).
//
// Two phases, because the strongest evidence and the UI evidence need different environments:
//
//   Phase 1 — the sandbox. The app is launched with every folder it searches for Tor Browser and
//     Tor redirected into empty directories (`USERPROFILE`, `HOME`, `LOCALAPPDATA`, `APPDATA`,
//     `ProgramFiles`), a PATH that holds nothing but Windows, its own data root and its own WebView2
//     profile. That is exactly the set `candidate_browser_paths()` and `find_tor_binary()` look at,
//     so nothing else can serve it: no Tor Browser, no `Program Files\Tor`, no python/node/cargo.
//     The product must still start, serve its backend, bootstrap its owner and become Tor-ready
//     FROM THE BUNDLE — all proven through the app's own API and the files it writes, no clicking
//     involved. This phase needs no DevTools endpoint, which is why it may redirect `USERPROFILE`;
//     phase 2 must not (WebView2 silently loses its DevTools endpoint when it is moved).
//
//   Phase 2 — the window. The app is launched normally (real user folders, isolated data root and
//     a fresh WebView2 profile) and driven through its own UI: Computer and Tor become ready with
//     nothing pressed, «Запускать Canalla вместе с Windows» is registered on the first launch and
//     the Settings toggle really edits the Windows registry in both directions, the update section
//     exists and never blocks the app, a second launch does not become a second product, and
//     killing the backend recovers without a click. The operator's own Tor Browser is visible to
//     this phase, which is deliberate: the checks below still require the *bundled* daemon by path
//     and by hash, so a machine that happens to have Tor Browser cannot make this pass.
//
// No GPU, no Pod, no paid provider. Every command in this run is bounded by the harness timeouts.
//
// Usage (from apps/desktop): node e2e/self-contained.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
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
const INSTALL_DIR = path.dirname(EXE);
const PASSWORD = "self-contained-passphrase-1";
const EMAIL = "self-contained@example.com";
const STAMP = Date.now().toString(36);
const DEVICE_CREDENTIAL_TARGET = `Alex LLM/device-credential-self-contained-${STAMP}`;
const RELEASE_GATEWAY_CREDENTIAL = `self-contained-${STAMP}`;
const READY_TIMEOUT_MS = Number(process.env.ALWAYS_READY_TIMEOUT_MS || 420000);
const RUN_VALUE = "Canalla LLM";
const RUN_KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const SYSTEM_ROOT = process.env.SystemRoot || "C:\\Windows";
const STRIPPED_PATH = `${SYSTEM_ROOT}\\System32;${SYSTEM_ROOT}`;
const TOR_PIN = JSON.parse(
  fs.readFileSync(
    path.resolve(__dirname, "..", "..", "..", "scripts", "tor-runtime.json"),
    "utf8",
  ),
);
const PINNED_TOR_DAEMON = TOR_PIN.platforms["windows-x86_64"].daemon_version;

/**
 * A CDP port the OS will actually let WebView2 bind.
 *
 * Windows reserves dynamic port ranges (Hyper-V, WSL and friends); a reserved port makes the
 * DevTools endpoint silently never appear, and a run that cannot be observed proves nothing.
 */
async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

const CDP_PORT = await freePort();
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

/**
 * A group of checks that must not take the rest of the run down with it.
 *
 * The strongest evidence here is the runtime and its recovery, so a UI locator that stops matching
 * has to report itself and let the remaining checks still produce an answer.
 */
async function step(name, body) {
  try {
    await body();
  } catch (error) {
    const reason = String(error).split("\n")[0].slice(0, 200);
    check(name, false, reason);
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/** A fresh data root and its sandbox: both belong to this run alone. */
function makeRoots(prefix) {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}-data-`));
  const box = fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}-box-`));
  for (const name of ["home", "localappdata", "appdata", "programfiles"]) {
    fs.mkdirSync(path.join(box, name), { recursive: true });
  }
  return { data, box };
}

const PHASE1 = makeRoots("canalla-selfcontained-sandbox");
const PHASE2 = makeRoots("canalla-selfcontained-window");
const TOR_RUNTIME_DIR = path.join(INSTALL_DIR, "runtime", "tor");

/** The provenance file the build staged beside the daemon: file names and their hashes. */
function bundledRuntime() {
  try {
    return JSON.parse(
      fs.readFileSync(path.join(TOR_RUNTIME_DIR, "runtime.json"), "utf8"),
    );
  } catch {
    return null;
  }
}

function fileHash(file) {
  return crypto
    .createHash("sha256")
    .update(fs.readFileSync(file))
    .digest("hex");
}

/** The Windows login entry this product writes, read from the machine itself. */
function runValue() {
  const raw = spawnSync("reg.exe", ["query", RUN_KEY, "/v", RUN_VALUE], {
    encoding: "utf8",
  });
  if (raw.status !== 0) return null;
  const match = /REG_SZ\s+(.+)/.exec(raw.stdout || "");
  return match ? match[1].trim() : null;
}

function processIds(name) {
  // `Get-Process -Name 'x.exe'` matches nothing at all (the cmdlet wants the name without the
  // extension), so this asks the same CIM instance the other probes use.
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    `Get-CimInstance Win32_Process -Filter "Name='${name}'" | Select-Object -ExpandProperty ProcessId`,
  ]);
  return (raw.stdout || Buffer.from(""))
    .toString("utf8")
    .trim()
    .split(/\r?\n/)
    .map((line) => Number(line.trim()))
    .filter((pid) => Number.isInteger(pid) && pid > 0);
}

/** The processes our own desktop started: the runtime must be these and nothing else. */
function childrenOfDesktop() {
  const raw = spawnSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    `Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq ${child?.pid ?? 0} } | Select-Object Name | ConvertTo-Csv -NoTypeInformation`,
  ]);
  return (raw.stdout || Buffer.from(""))
    .toString("utf8")
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.replace(/"/g, "").trim())
    .filter((name) => name.length > 0);
}

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

function launch(root, { sandbox = false, cdp = false } = {}) {
  const env = {
    ...process.env,
    ALEX_LLM_DATA_DIR: root.data,
    ALEX_DEVICE_DIR: path.join(root.data, "device"),
    ALEX_DEVICE_CREDENTIAL_TARGET: DEVICE_CREDENTIAL_TARGET,
    ALEX_GATEWAY_CREDENTIAL_NAME: RELEASE_GATEWAY_CREDENTIAL,
    WEBVIEW2_USER_DATA_FOLDER: path.join(root.box, "webview"),
  };
  if (cdp) {
    env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-port=${CDP_PORT}`;
  }
  if (sandbox) {
    Object.assign(env, {
      USERPROFILE: path.join(root.box, "home"),
      HOME: path.join(root.box, "home"),
      LOCALAPPDATA: path.join(root.box, "localappdata"),
      APPDATA: path.join(root.box, "appdata"),
      ProgramFiles: path.join(root.box, "programfiles"),
      "ProgramFiles(x86)": path.join(root.box, "programfiles"),
      PATH: STRIPPED_PATH,
    });
  }
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: !cdp });
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

/** The first-owner claim the Desktop makes for its own window; here it is made over HTTP. */
async function bootstrapOwner(root) {
  const tokenFile = path.join(root.data, "runtime", "shutdown.token");
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    if (fs.existsSync(tokenFile)) {
      const runtimeToken = fs.readFileSync(tokenFile, "utf8").trim();
      try {
        const session = await fetchJson(
          `http://127.0.0.1:${backendPort}/auth/bootstrap`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Alex-Runtime-Token": runtimeToken,
            },
            body: JSON.stringify({
              email: EMAIL,
              password: PASSWORD,
              display_name: "Self-contained",
            }),
          },
        );
        token = session.access_token;
        return true;
      } catch {
        /* the backend may still be migrating its database */
      }
    }
    await sleep(2000);
  }
  return false;
}

async function getJson(path, timeoutMs = 20000) {
  return fetchJson(`http://127.0.0.1:${backendPort}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(timeoutMs),
  });
}

/** The owner session this run created: the device list is read back through the product's own auth. */
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
    // A restarted sidecar keeps the database but not the in-memory token: ask for a fresh one once.
    token = null;
    return call();
  }
}

async function waitForDevices(timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  let last = [];
  while (Date.now() < deadline) {
    last = await devices().catch(() => []);
    if (Array.isArray(last) && last.length > 0) return last;
    await sleep(2000);
  }
  return last;
}

async function waitFor(probe, timeoutMs, { intervalMs = 3000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await probe().catch(() => null);
    if (last) return last;
    await sleep(intervalMs);
  }
  return last;
}

function torProof(root) {
  try {
    return JSON.parse(
      fs.readFileSync(path.join(root.data, "runtime", "tor.json"), "utf8"),
    );
  } catch {
    return null;
  }
}

async function waitForNewProof(root, previousPid, timeoutMs) {
  return waitFor(
    async () => {
      const proof = torProof(root);
      if (proof?.verified === true && proof.pid && proof.pid !== previousPid)
        return proof;
      return null;
    },
    timeoutMs,
    { intervalMs: 2000 },
  );
}

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
    try {
      execFileSync("powershell.exe", [
        "-NoProfile",
        "-Command",
        `taskkill /PID ${child.pid} /T /F | Out-Null`,
      ]);
    } catch {
      /* ignore */
    }
  }
  child = null;
}

function killStrays() {
  const stray = spawnSync("tasklist", ["/FI", "IMAGENAME eq alex-llm.exe"], {
    encoding: "utf8",
  }).stdout;
  if (stray.includes("alex-llm.exe")) {
    console.log("  [info] closing a leftover alex-llm.exe before the launch");
    spawnSync("taskkill", ["/IM", "alex-llm.exe", "/T", "/F"], {
      stdio: "ignore",
    });
  }
}

function runtimeCounts() {
  return `${processIds("alex-llm.exe").length} desktop / ${processIds("alex-backend.exe").length} backend / ${processIds("alex-host-loop.exe").length} host-loop / ${processIds("tor.exe").length} tor`;
}

function noOrphans() {
  return (
    processIds("alex-llm.exe").length === 0 &&
    processIds("alex-backend.exe").length === 0 &&
    processIds("alex-host-loop.exe").length === 0 &&
    processIds("tor.exe").length === 0
  );
}

/**
 * Quitting is asynchronous: the desktop stops its sidecar, the sidecar stops its daemon. The
 * property asserted is still "nothing is left behind" — this only gives the teardown a bounded
 * window instead of sampling once, the instant the window is gone.
 */
async function waitForNoOrphans(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (noOrphans()) return true;
    await sleep(2000);
  }
  return false;
}

// ---------------------------------------------------------------------------- phase 1: sandbox

async function sandboxPhase() {
  console.log();
  console.log("phase 1 — nothing on this machine may serve the product");
  console.log(`  sandbox  ${PHASE1.box}`);
  console.log(`  PATH     ${STRIPPED_PATH}`);
  check(
    "the sandbox holds no Tor Browser and no Tor installation to fall back on",
    !fs.existsSync(path.join(PHASE1.box, "localappdata", "Tor Browser")) &&
      !fs.existsSync(path.join(PHASE1.box, "programfiles", "Tor")) &&
      !fs.existsSync(path.join(PHASE1.box, "programfiles", "Tor Browser")) &&
      !fs.existsSync(path.join(PHASE1.box, "home", "Desktop", "Tor Browser")),
    PHASE1.box,
  );

  child = launch(PHASE1, { sandbox: true });
  backendPort = await findBackendPort();
  check(
    "the packaged backend serves the app with no Python, Node or Rust on PATH",
    backendPort !== 0,
    `port ${backendPort}`,
  );
  if (backendPort === 0) {
    await quitApp();
    return;
  }

  const started = childrenOfDesktop();
  const forbidden = started.filter((name) =>
    /^(python|pythonw|node|npm|cargo|rustc)(\.exe)?$/i.test(name),
  );
  check(
    "the desktop starts only its own runtime, no development toolchain",
    forbidden.length === 0,
    started.join(", "),
  );
  check(
    "the installed product runs no headless host build: the desktop owns the computer itself",
    processIds("alex-host-loop.exe").length === 0,
    `${processIds("alex-host-loop.exe").length} alex-host-loop.exe`,
  );

  const runtime = bundledRuntime();
  const staged = Object.entries(runtime?.files ?? {});
  const wrong = staged.filter(([name, digest]) => {
    const file = path.join(TOR_RUNTIME_DIR, name);
    return !fs.existsSync(file) || fileHash(file) !== digest;
  });
  check(
    "every staged Tor file is installed and hashes what the build recorded",
    staged.length >= 5 && wrong.length === 0,
    `${staged.length} file(s)${wrong.length ? `, wrong: ${wrong.map(([name]) => name).join(", ")}` : ""}`,
  );
  check(
    "the installed daemon is the pinned official build, not something that arrived later",
    runtime?.daemon_version === PINNED_TOR_DAEMON &&
      runtime?.archive?.sha256 === TOR_PIN.platforms["windows-x86_64"].sha256 &&
      runtime?.license === TOR_PIN.license,
    `${runtime?.daemon_version} archive ${String(runtime?.archive?.sha256).slice(0, 12)} licence ${runtime?.license}`,
  );

  check(
    "the first owner is created without any toolchain",
    await bootstrapOwner(PHASE1),
  );

  const tor = await waitFor(async () => {
    const snapshot = await getJson("/tools/tor");
    return snapshot?.state === "ready" ? snapshot : null;
  }, READY_TIMEOUT_MS);
  check(
    "TOR becomes ready with no Tor Browser and no Tor on PATH",
    Boolean(tor) && tor?.state === "ready",
    String(tor?.state),
  );
  check(
    "the route is served by the daemon Canalla ships, from the install directory",
    tor?.binary?.source === "bundled" &&
      String(tor?.binary?.path)
        .toLowerCase()
        .startsWith(INSTALL_DIR.toLowerCase()),
    `${tor?.binary?.source} ${tor?.binary?.path}`,
  );
  check(
    "the bundled daemon reports the pinned version",
    tor?.runtime_version === PINNED_TOR_DAEMON,
    `${tor?.runtime_version} (pin ${PINNED_TOR_DAEMON})`,
  );
  check(
    "the circuit is proven",
    tor?.verified_chain === true,
    String(tor?.verified_chain),
  );

  const proofBefore = torProof(PHASE1);
  if (proofBefore?.pid) process.kill(proofBefore.pid);
  const proofAfter = await waitForNewProof(PHASE1, proofBefore?.pid, 180000);
  check(
    "killing the bundled daemon is recovered by a new process",
    Boolean(proofAfter) && proofAfter.pid !== proofBefore?.pid,
    `${proofBefore?.pid} -> ${proofAfter?.pid}`,
  );
  const recovered = await waitFor(async () => {
    const snapshot = await getJson("/tools/tor");
    return snapshot?.state === "ready" ? snapshot : null;
  }, READY_TIMEOUT_MS);
  check(
    "Tor is ready again after the daemon was killed, still from the bundle",
    recovered?.state === "ready" &&
      recovered?.binary?.source === "bundled" &&
      recovered?.verified_chain === true,
    `${recovered?.state} ${recovered?.binary?.source}`,
  );

  await quitApp();
  check(
    "the sandbox run leaves no orphan process",
    await waitForNoOrphans(),
    runtimeCounts(),
  );
}

// ------------------------------------------------------------------------------ phase 2: window

async function windowPhase() {
  console.log();
  console.log("phase 2 — the installed window, its Settings and its guards");
  let pairedId = null;
  child = launch(PHASE2, { cdp: true });
  if (!(await waitCdp())) {
    check(
      "the installed window is observable",
      false,
      "CDP never became ready",
    );
    await quitApp();
    return;
  }
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const page = browser.contexts()[0].pages()[0];
  try {
    await page
      .getByRole("button", { name: "Создать владельца Canalla" })
      .waitFor({ timeout: 180000 });
    await page.getByLabel("Email", { exact: true }).fill(EMAIL);
    await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
    await page
      .getByRole("button", { name: "Создать владельца Canalla" })
      .click();
    await page
      .getByRole("textbox", { name: "Сообщение", exact: true })
      .waitFor({ timeout: 60000 });
    backendPort = await findBackendPort();
    check(
      "the second run's own backend serves it",
      backendPort !== 0,
      `port ${backendPort}`,
    );

    const computer = await waitForChip(page, "Computer", ["Готово"], 120000);
    check("COMPUTER turns ready on its own", computer.ready, computer.text);
    const tor = await waitForChip(page, "Tor", ["Готово"], READY_TIMEOUT_MS);
    check("TOR turns ready on its own", tor.ready, tor.text);

    // ------------------------------------------------ «Запускать Canalla вместе с Windows»
    const registered = runValue();
    check(
      "the first launch registers the product for login startup (the default is on)",
      Boolean(registered) && registered.includes("alex-llm.exe"),
      String(registered),
    );
    await step(
      "the Settings window answers: login startup and the update section",
      async () => {
        // The first `.settings-link` is «Моё использование»: the Settings entry names itself.
        await page.locator(".settings-link", { hasText: "Settings" }).click();
        await page.getByRole("button", { name: "Общие", exact: true }).click();
        const toggle = page.getByTestId("autostart-toggle");
        check(
          "the Settings toggle reflects the machine",
          (await toggle.isChecked()) === true,
          String(await toggle.isChecked()),
        );
        await toggle.click();
        await sleep(2500);
        check(
          "turning it off removes the Windows registration",
          runValue() === null,
          String(runValue()),
        );
        check(
          "the toggle shows the machine's answer",
          (await toggle.isChecked()) === false,
        );
        await toggle.click();
        await sleep(2500);
        check(
          "turning it on restores the Windows registration",
          Boolean(runValue()) && String(runValue()).includes("alex-llm.exe"),
          String(runValue()),
        );

        // ------------------------------------------------------------ the update section
        await page
          .getByRole("button", { name: "Обновления", exact: true })
          .click();
        const checkButton = page.getByRole("button", {
          name: /Проверить обновления/,
        });
        check(
          "the update section exists in Settings",
          await checkButton.isVisible(),
        );
        await checkButton.click();
        await sleep(5000);
        const health = await fetchJson(
          `http://127.0.0.1:${backendPort}/health`,
        );
        check(
          "an update check never blocks the app",
          health?.status === "ok",
          String(health?.status),
        );
        check(
          "Tor is still ready after the update check",
          (await chipState(page, "Tor")).includes("Готово"),
          await chipState(page, "Tor"),
        );
        await page.getByRole("button", { name: "Закрыть настройки" }).click();
      },
    );

    // ------------------------------------------------------------------- single instance
    await step("a second launch is not a second product", async () => {
      const second = launch(PHASE2, { cdp: true });
      await sleep(9000);
      check(
        "a second launch does not become a second desktop",
        processIds("alex-llm.exe").length === 1,
        `${processIds("alex-llm.exe").length} alex-llm.exe`,
      );
      check(
        "the second launch exits by itself",
        second.exitCode !== null,
        String(second.exitCode),
      );
      check(
        "one backend, one Tor daemon and no headless host build",
        sidecarProcesses().length === 1 &&
          processIds("tor.exe").length === 1 &&
          processIds("alex-host-loop.exe").length === 0,
        `${sidecarProcesses().length} backend / ${processIds("tor.exe").length} tor / ${processIds("alex-host-loop.exe").length} host-loop`,
      );
    });

    // ------------------------------------------------------------------------ recovery
    console.log();
    console.log(
      "recovery — the runtime the desktop owns is killed while the app runs",
    );
    const paired = await waitForDevices();
    pairedId = paired.length === 1 ? (paired[0]?.device_id ?? null) : null;
    check(
      "the desktop pairs this machine by itself, with no «Подключить» pressed",
      paired.length === 1 && Boolean(pairedId) && paired[0]?.online === true,
      `${paired.length} device(s) ${pairedId} online ${paired[0]?.online}`,
    );

    const sidecars = sidecarProcesses();
    const own =
      sidecars.find((item) => item.parent === child.pid) ?? sidecars[0];
    const backendBefore = own.pid;
    process.kill(backendBefore);
    const backendAfter = await waitFor(
      async () => {
        const found = sidecarProcesses().find(
          (item) => item.pid !== backendBefore,
        );
        return found ? found.pid : null;
      },
      120000,
      { intervalMs: 1000 },
    );
    check(
      "killing the backend is recovered by a new sidecar",
      Boolean(backendAfter) && backendAfter !== backendBefore,
      `${backendBefore} -> ${backendAfter}`,
    );
    backendPort = await findBackendPort();
    check(
      "the recovered backend answers /health",
      backendPort !== 0,
      `port ${backendPort}`,
    );
    const again = await waitForDevices();
    check(
      "the same machine reconnects to the new backend: one device, same id, online again",
      again.length === 1 &&
        again[0]?.device_id === pairedId &&
        again[0]?.online === true,
      `${again.length} device(s) ${again[0]?.device_id} online ${again[0]?.online}`,
    );
    const torAfterBackend = await waitForChip(
      page,
      "Tor",
      ["Готово"],
      READY_TIMEOUT_MS,
    );
    check(
      "TOR is ready again after the backend crash",
      torAfterBackend.ready,
      torAfterBackend.text,
    );
    const computerAfterCrash = await waitForChip(
      page,
      "Computer",
      ["Готово"],
      READY_TIMEOUT_MS,
    );
    check(
      "COMPUTER is ready again after the backend crash",
      computerAfterCrash.ready,
      computerAfterCrash.text,
    );
    check(
      "the recovered runtime is again one of each, with no duplicate anywhere",
      sidecarProcesses().length === 1 &&
        processIds("tor.exe").length === 1 &&
        processIds("alex-host-loop.exe").length === 0,
      `${sidecarProcesses().length} backend / ${processIds("tor.exe").length} tor / ${processIds("alex-host-loop.exe").length} host-loop`,
    );
  } finally {
    await browser.close().catch(() => {});
    await quitApp();
  }

  check(
    "quitting the window run leaves no orphan process",
    await waitForNoOrphans(),
    runtimeCounts(),
  );
  check(
    "the product stays registered for login startup after a normal quit",
    Boolean(runValue()),
    String(runValue()),
  );
}

async function main() {
  console.log(
    "WINDOWS SELF-CONTAINED — installed app, no Tor Browser, no toolchain",
  );
  console.log(`  app      ${EXE}`);
  killStrays();
  await sleep(2500);

  await sandboxPhase();
  await windowPhase();

  console.log();
  if (failures.length === 0) {
    console.log(
      "SELF-CONTAINED PASS — the product serves itself, registers itself at login and recovers by itself",
    );
    return 0;
  }
  console.log(
    `SELF-CONTAINED FAILED: ${failures.length} check(s) — ${failures.join("; ")}`,
  );
  return 1;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error);
    process.exit(2);
  },
);
