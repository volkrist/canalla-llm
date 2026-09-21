// Installer over-install acceptance: an OLD build with real user data, then a NEW build
// installed over it, on an isolated data root and isolated credential names.
//
//   node e2e/upgrade-over-install.mjs prepare   # run BEFORE building this slice
//   node e2e/upgrade-over-install.mjs verify    # run AFTER installing the new build
//
//   node e2e/upgrade-over-install.mjs prepare-legacy
//       the same fixture, then `alembic downgrade 0014` on the isolated database, so the
//       currently installed build can be re-checked against a real older schema without
//       an older installer at hand.
//
// `prepare` drives the installed (older) app: first-run owner, chats/messages, a project, a
// memory, an uploaded document, then an enrollment against the DEPLOYED Gateway with a
// one-time code created on the server over SSH. It writes the expected state to a file.
//
// `verify` drives the freshly installed (new) app against the same data root and asserts
// that everything survived: data counts, local session, schema revision, the pre-upgrade
// backup, the Gateway enrollment and the shared balance.
//
// No Pod, no GPU, no TinyFish: the only provider traffic is the Gateway's read-only balance.
//
// Usage (from apps/desktop): node e2e/upgrade-over-install.mjs prepare | verify

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
const MODE = (process.argv[2] || "").trim();
const CDP_PORT = 9241;
const WORK = path.join(os.tmpdir(), "alex-upgrade-acceptance");
const STATE_FILE = path.join(WORK, "state.json");
const DATA_ROOT = path.join(WORK, "data");
const DEVICE_DIR = path.join(WORK, "cred");
const CREDENTIAL_NAME = "upgrade-acceptance";
const DEVICE_CREDENTIAL_TARGET = "Alex LLM/session-upgrade-acceptance";
const PROVIDER_TARGET = "Alex LLM/provider/runpod";
const INSTALLATION_TARGET = `Alex LLM/gateway/${CREDENTIAL_NAME}`;
const EMAIL = "upgrade-owner@example.com";
const PASSWORD = "upgrade-acceptance-12345";
const SECOND_EMAIL = "upgrade-second@example.com";
const EXPECTED_URL = "https://gateway.12testers.store";
const BACKEND = path.resolve(__dirname, "..", "..", "backend");
const SSH_HOST = "distance";
const GATEWAY_DIR = "/opt/alex-gateway/current";
const GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env";
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
let code = "";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const flat = (value) => (value || "").replace(/\s+/g, " ").trim();

function check(name, condition, detail = "") {
  if (!condition) failures += 1;
  console.log(
    `  [${condition ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`,
  );
}

async function fetchJson(url, options) {
  const response = await fetch(url, {
    ...options,
    signal: AbortSignal.timeout(15000),
  });
  return response.json();
}

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
    return (
      execFileSync(PYTHON, ["-c", CREDENTIAL_READER, target], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim() || null
    );
  } catch {
    return null;
  }
}

const CREDENTIAL_DELETER = `
import ctypes, sys
from ctypes import wintypes
api = ctypes.WinDLL("advapi32", use_last_error=True)
api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
api.CredDeleteW.restype = wintypes.BOOL
sys.exit(0 if api.CredDeleteW(sys.argv[1], 1, 0) else 1)
`;

/** Credential Manager cannot be addressed by `cmdkey` for names with spaces. */
function deleteCredential(target) {
  try {
    execFileSync(PYTHON, ["-c", CREDENTIAL_DELETER, target], {
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
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
    if (lines[index].startsWith("activation code")) return lines[index + 1];
  }
  throw new Error("no activation code");
}

function revokeInstallation(installationId) {
  ssh(
    `sudo -n -u alex-gateway bash -c 'set -a; . ${GATEWAY_ENV}; set +a; ` +
      `export HOME=/var/lib/alex-gateway PYTHONPATH=${GATEWAY_DIR}/gateway:${GATEWAY_DIR}/backend; ` +
      `export ALEX_BACKEND_LIB_DIR=${GATEWAY_DIR}/backend; cd ${GATEWAY_DIR}; ` +
      `/opt/alex-gateway/venv/bin/python -m gateway.cli revoke --installation-id ${installationId} --reason acceptance_cleanup'`,
  );
}

function sqliteRevision(database) {
  const out = execFileSync(
    PYTHON,
    [
      "-c",
      `import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);print(db.execute("SELECT version_num FROM alembic_version").fetchone()[0])`,
      database,
    ],
    { encoding: "utf8" },
  );
  return out.trim();
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
    /* fall through */
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

async function openSettingsSection(page, name) {
  const dialog = page.getByRole("dialog");
  const open = await dialog
    .first()
    .isVisible()
    .catch(() => false);
  if (!open) {
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

async function closeSettings(page) {
  await page
    .getByRole("button", { name: "Закрыть настройки" })
    .click({ timeout: 30000 });
}

async function countBackups(root) {
  const backups = path.join(root, "backups");
  if (!fs.existsSync(backups)) return { total: 0, preUpgrade: 0, verified: 0 };
  let total = 0;
  let preUpgrade = 0;
  let verified = 0;
  for (const entry of fs.readdirSync(backups)) {
    const dir = path.join(backups, entry);
    if (
      !fs.statSync(dir).isDirectory() ||
      !fs.existsSync(path.join(dir, "manifest.json"))
    )
      continue;
    total += 1;
    if (entry.includes("pre_upgrade")) preUpgrade += 1;
    if (fs.existsSync(path.join(dir, "verification.json"))) verified += 1;
  }
  return { total, preUpgrade, verified };
}

async function apiCounts(token) {
  const headers = { Authorization: "Bearer " + token };
  const json = async (url) =>
    fetchJson(`http://127.0.0.1:${backendPort}${url}`, { headers });
  const chats = await json("/chats");
  const projects = await json("/projects");
  const memories = await json("/memory");
  const documents = await json("/documents");
  let messages = 0;
  for (const chat of chats) {
    const rows = await json(`/chats/${chat.id}/messages`);
    messages += rows.length;
  }
  return {
    chats: chats.length,
    projects: projects.length,
    memories: memories.length,
    documents: documents.length,
    messages,
  };
}

async function ownerToken() {
  const login = await fetchJson(`http://127.0.0.1:${backendPort}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  return login.access_token;
}

// ------------------------------------------------------------------------------ prepare

async function prepare() {
  fs.rmSync(WORK, { recursive: true, force: true });
  fs.mkdirSync(WORK, { recursive: true });
  // Start from a truly un-enrolled installation, whatever a previous run left behind.
  deleteCredential(INSTALLATION_TARGET);
  console.log(
    "PREPARE — the OLD installed build creates real user data and enrolls",
  );
  console.log(`data root=${DATA_ROOT}  credentials=${CREDENTIAL_NAME}`);

  const child = launchApp();
  try {
    await waitCdp();
    const browser = await chromium.connectOverCDP(
      `http://127.0.0.1:${CDP_PORT}`,
    );
    const page = browser.contexts()[0].pages()[0];
    try {
      await page
        .getByRole("button", { name: "Создать владельца Alex" })
        .waitFor({ timeout: 240000 });
      await page
        .getByLabel("Имя владельца (необязательно)")
        .fill("Upgrade Owner");
      await page.getByLabel("Email", { exact: true }).fill(EMAIL);
      await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
      await page
        .getByRole("button", { name: "Создать владельца Alex" })
        .click();
      await page
        .getByRole("button", { name: "New Chat" })
        .waitFor({ timeout: 180000 });

      backendPort = await findBackendPort();
      check(
        "P1 the old build is serving its backend",
        backendPort !== 0,
        `port ${backendPort}`,
      );
      const token = await ownerToken();
      const headers = {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      };

      // A second local user, so the upgrade has to preserve more than one account.
      await fetchJson(`http://127.0.0.1:${backendPort}/auth/register`, {
        method: "POST",
        headers,
        body: JSON.stringify({ email: SECOND_EMAIL, password: PASSWORD }),
      });

      const chat = await fetchJson(`http://127.0.0.1:${backendPort}/chats`, {
        method: "POST",
        headers,
        body: JSON.stringify({ title: "До обновления" }),
      });
      for (const text of ["Первое сообщение — юникод 🚀", "Второе сообщение"]) {
        await fetchJson(
          `http://127.0.0.1:${backendPort}/chats/${chat.id}/messages`,
          {
            method: "POST",
            headers,
            body: JSON.stringify({ content: text }),
          },
        );
      }
      await fetchJson(`http://127.0.0.1:${backendPort}/projects`, {
        method: "POST",
        headers,
        body: JSON.stringify({ name: "Проект до обновления" }),
      });
      await fetchJson(`http://127.0.0.1:${backendPort}/memory`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          content: "Память до обновления",
          category: "fact",
        }),
      });
      const form = new FormData();
      form.append(
        "file",
        new Blob(["документ до обновления"], { type: "text/plain" }),
        "до-обновления.txt",
      );
      const uploaded = await fetch(
        `http://127.0.0.1:${backendPort}/documents`,
        {
          method: "POST",
          headers: { Authorization: "Bearer " + token },
          body: form,
        },
      );
      check("P2 a document uploads", uploaded.ok, `status=${uploaded.status}`);

      const counts = await apiCounts(token);
      check(
        "P3 the fixture has users, chats, messages, project, memory and a document",
        counts.chats === 1 &&
          counts.messages === 2 &&
          counts.projects === 1 &&
          counts.memories === 1 &&
          counts.documents === 1,
        JSON.stringify(counts),
      );

      // Enroll against the deployed Gateway with a fresh one-time code.
      code = operatorCode("upgrade-acceptance");
      const dialog = await openSettingsSection(page, "Alex Cloud");
      const address = dialog.getByLabel("Адрес Gateway");
      if (!(await address.inputValue()).trim())
        await address.fill(EXPECTED_URL);
      await dialog.getByLabel("Код активации").fill(code);
      await dialog.getByRole("button", { name: "Подключить" }).click();
      const deadline = Date.now() + 300000;
      let connected = false;
      while (Date.now() < deadline && !connected) {
        const text = await dialog.innerText().catch(() => "");
        connected = /Alex Cloud · Подключено/.test(text);
        if (!connected) await sleep(1000);
      }
      const panel = await dialog.innerText();
      const installationId =
        (panel.match(/Установка:\s*([0-9a-f-]{16,})/i) || [])[1] || "";
      check(
        "P4 the old build is enrolled in Alex Cloud",
        connected,
        installationId.slice(0, 8) + "…",
      );
      await closeSettings(page);

      let balance = flat(
        await page
          .getByTestId("runpod-balance")
          .innerText()
          .catch(() => ""),
      );
      const balanceDeadline = Date.now() + 180000;
      while (Date.now() < balanceDeadline && !/\$\s?\d/.test(balance)) {
        await sleep(1500);
        balance = flat(
          await page
            .getByTestId("runpod-balance")
            .innerText()
            .catch(() => ""),
        );
      }
      check(
        "P5 the old build shows the shared balance",
        /\$\s?\d/.test(balance),
        balance,
      );

      const state = {
        createdAt: new Date().toISOString(),
        dataRoot: DATA_ROOT,
        credentialName: CREDENTIAL_NAME,
        email: EMAIL,
        counts,
        installationId,
        schemaRevision: sqliteRevision(path.join(DATA_ROOT, "data", "alex.db")),
        installationCredentialPresent: Boolean(
          readCredential(INSTALLATION_TARGET),
        ),
        providerCredentialPresent: Boolean(readCredential(PROVIDER_TARGET)),
        balanceShown: flat(balance),
      };
      fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf8");
      check(
        "P6 the fixture state is recorded",
        fs.existsSync(STATE_FILE),
        STATE_FILE,
      );
      console.log(`  [info] previous schema revision: ${state.schemaRevision}`);
    } finally {
      await browser.close().catch(() => {});
    }
  } finally {
    await quitApp(child);
  }

  if (MODE === "prepare-legacy") {
    // The app is closed: put the isolated database back to the previous revision so the
    // installed build has to migrate it (exactly what an older install looks like).
    execFileSync(PYTHON, ["-m", "alembic", "downgrade", "0014"], {
      cwd: BACKEND,
      env: {
        ...process.env,
        DATABASE_URL:
          "sqlite:///" +
          path.join(DATA_ROOT, "data", "alex.db").replace(/\\/g, "/"),
        JWT_SECRET: "k".repeat(64),
        ALEX_APP_RESOURCE_DIR: BACKEND,
      },
      stdio: "ignore",
    });
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    state.schemaRevision = sqliteRevision(
      path.join(DATA_ROOT, "data", "alex.db"),
    );
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf8");
    check(
      "P7 the isolated database sits at the previous revision",
      state.schemaRevision === "0014",
      state.schemaRevision,
    );
  }

  if (failures) {
    console.error(`PREPARE FAILED: ${failures} check(s)`);
    process.exit(1);
  }
  console.log(
    "PREPARE PASS — install the new build over this installation, then run: verify",
  );
}

// ------------------------------------------------------------------------------- verify

async function verify() {
  console.log("VERIFY — the NEW build against the data an older build created");
  if (!fs.existsSync(STATE_FILE)) {
    console.error(`no fixture state: run 'prepare' first (${STATE_FILE})`);
    process.exit(1);
  }
  const expected = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  if (!fs.existsSync(path.join(expected.dataRoot, "data", "alex.db"))) {
    console.error("the isolated data root is gone");
    process.exit(1);
  }
  console.log(
    `data root=${expected.dataRoot}  previous schema=${expected.schemaRevision}`,
  );

  const child = launchApp();
  let installationId = "";
  try {
    await waitCdp();
    const browser = await chromium.connectOverCDP(
      `http://127.0.0.1:${CDP_PORT}`,
    );
    const page = browser.contexts()[0].pages()[0];
    try {
      // No first-run screen: the local session of the old build must still work.
      await page
        .getByRole("button", { name: "New Chat" })
        .waitFor({ timeout: 240000 });
      check(
        "V1 the local session survived the over-install (no login screen)",
        true,
      );
      backendPort = await findBackendPort();
      const token = await ownerToken();
      const counts = await apiCounts(token);
      check(
        "V2 chats, messages, project, memory and document survived",
        JSON.stringify(counts) === JSON.stringify(expected.counts),
        `${JSON.stringify(counts)} vs ${JSON.stringify(expected.counts)}`,
      );

      const revision = sqliteRevision(
        path.join(expected.dataRoot, "data", "alex.db"),
      );
      check(
        "V3 the database migrated to the new revision",
        revision === "0015",
        revision,
      );

      const backups = await countBackups(expected.dataRoot);
      check(
        "V4 the upgrade took a verified pre-upgrade backup",
        backups.preUpgrade >= 1 && backups.verified >= 1,
        JSON.stringify(backups),
      );

      const dialog = await openSettingsSection(page, "Alex Cloud");
      // The panel reads the cloud state when it mounts: wait for that read to settle.
      const settleDeadline = Date.now() + 180000;
      let panel = "";
      while (Date.now() < settleDeadline) {
        panel = await dialog.innerText().catch(() => "");
        if (/Подключено|Не подключено|Недоступно|Статус недоступен/.test(panel))
          break;
        await sleep(1500);
      }
      installationId =
        (panel.match(/Установка:\s*([0-9a-f-]{16,})/i) || [])[1] || "";
      check(
        "V5 Alex Cloud is still connected after the over-install",
        /Подключено/.test(panel),
        flat(panel).slice(0, 90),
      );
      check(
        "V6 it is the same installation identity",
        Boolean(installationId) && installationId === expected.installationId,
        installationId.slice(0, 8) + "…",
      );
      await openSettingsSection(page, "Резервные копии");
      const panelText = await page.getByRole("dialog").innerText();
      check(
        "V7 the new build offers the backup panel with the stored copies",
        /Резервные копии/.test(panelText) && /Проверена/.test(panelText),
      );
      await closeSettings(page);

      const balanceDeadline = Date.now() + 180000;
      let balance = "";
      while (Date.now() < balanceDeadline) {
        balance = flat(
          await page
            .getByTestId("runpod-balance")
            .innerText()
            .catch(() => ""),
        );
        if (/\$\s?\d/.test(balance)) break;
        await sleep(1500);
      }
      check(
        "V8 the shared balance still arrives through the Gateway",
        /\$\s?\d/.test(balance),
        balance,
      );
    } finally {
      await browser.close().catch(() => {});
    }
  } finally {
    await quitApp(child);
  }

  if (installationId) {
    revokeInstallation(installationId);
    console.log(
      "  [info] the throwaway installation was revoked on the Gateway",
    );
  }
  if (failures) {
    console.error(`VERIFY FAILED: ${failures} check(s)`);
    process.exit(1);
  }
  console.log(
    "VERIFY PASS — the over-install preserved data, session, enrollment and balance",
  );
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(1);
  }
  if (MODE === "prepare" || MODE === "prepare-legacy") return prepare();
  if (MODE === "verify") return verify();
  console.error(
    "usage: node e2e/upgrade-over-install.mjs prepare|prepare-legacy|verify",
  );
  process.exit(2);
}

main().catch((error) => {
  console.error("UPGRADE ACCEPTANCE ERROR:", error);
  process.exit(1);
});
