// Backup / restore / upgrade-failure acceptance for the installed app (this slice).
//
// Three isolated data roots, no Pod, no GPU, no TinyFish, and no touch of the real data root
// or of the real credential entries:
//
//   Phase A  fresh install -> real data -> create a verified backup -> mutate -> restore
//            through the real UI -> the exact previous state is back.
//   Phase C  a tampered backup (a document changed after verification) is refused, and the
//            live data is left completely untouched.
//   Phase D  a database that cannot be migrated (write-protected) makes the upgrade fail:
//            the app shows the recovery message, the data is intact, a verified pre-upgrade
//            backup exists, the revision is unchanged and no empty database replaces it.
//
// Usage (from apps/desktop): node e2e/backup-smoke.mjs

import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..", "..");
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
const PYTHON = path.resolve(
  REPO,
  "apps",
  "backend",
  ".venv",
  "Scripts",
  "python.exe",
);
const BACKEND = path.resolve(REPO, "apps", "backend");
const CDP_PORT = 9245;
const EMAIL = "backup-owner@example.com";
const PASSWORD = "backup-acceptance-12345";

let failures = 0;
let skipped = 0;
let backendPort = 0;
let active = null;
let activeChild = null;

/** Kill only the process tree this harness started; never anything by image name. */
function killTree(child) {
  if (!child || child.exitCode !== null) return;
  try {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  } catch {
    /* best effort */
  }
}

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

async function fetchJson(url, options) {
  const response = await fetch(url, {
    ...options,
    signal: AbortSignal.timeout(20000),
  });
  return response.json();
}

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
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

// `ALEX_DEVICE_DIR` only moves `device.json`; the device credential is a machine-wide entry
// (`Alex LLM/device-credential` by default). Each phase borrows its own target and this run
// deletes them all, so no phase can leave the real app a credential that is not its own.
const deviceCredentials = new Set();

function isolated(name) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), `alex-backup-${name}-`));
  const deviceCredentialTarget = `Alex LLM/device-credential-backup-${name}`;
  deviceCredentials.add(deviceCredentialTarget);
  return {
    work,
    dataRoot: path.join(work, "data"),
    deviceDir: path.join(work, "cred"),
    credentialName: `backup-${name}-${Date.now().toString(36)}`,
    deviceCredentialTarget,
  };
}

/** Every device credential this run wrote, so none survives it. */
function cleanupDeviceCredentials() {
  return [...deviceCredentials]
    .map((target) => deleteCredential(target))
    .every(Boolean);
}

// Session credentials are machine-wide too. Each phase notes the ids of its own isolated database
// before removing it, so the run can delete exactly those entries and nothing of the real install.
const sessionCredentials = new Map();

function collectSessionCredentials(scope) {
  const database = path.join(scope.dataRoot, "data", "alex.db");
  if (!fs.existsSync(database)) return;
  try {
    const ids = JSON.parse(
      execFileSync(
        PYTHON,
        [
          "-c",
          `import sqlite3,sys,json;db=sqlite3.connect(sys.argv[1]);print(json.dumps([row[0] for row in db.execute("SELECT id FROM auth_sessions")]))`,
          database,
        ],
        { encoding: "utf8" },
      ).trim(),
    );
    for (const id of ids) sessionCredentials.set(id, `Alex LLM/session/${id}`);
  } catch {
    /* a phase whose database never migrated has no sessions to clean */
  }
}

function cleanupSessionCredentials() {
  return [...sessionCredentials.values()].map(deleteCredential).every(Boolean);
}

function launchApp(scope) {
  const env = {
    ...process.env,
    ALEX_LLM_DATA_DIR: scope.dataRoot,
    ALEX_DEVICE_DIR: scope.deviceDir,
    ALEX_DEVICE_CREDENTIAL_TARGET: scope.deviceCredentialTarget,
    ALEX_GATEWAY_CREDENTIAL_NAME: scope.credentialName,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
  };
  delete env.ALEX_GATEWAY_URL;
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: false });
}

async function withApp(scope, step) {
  const child = launchApp(scope);
  active = scope;
  activeChild = child;
  try {
    const deadline = Date.now() + 150000;
    let browser = null;
    let lastError = null;
    while (Date.now() < deadline && !browser) {
      try {
        const targets = await fetchJson(
          `http://127.0.0.1:${CDP_PORT}/json/list`,
        );
        if (targets.some((target) => target.type === "page" && target.url)) {
          browser = await chromium.connectOverCDP(
            `http://127.0.0.1:${CDP_PORT}`,
          );
        }
      } catch (error) {
        lastError = error;
        await sleep(1000);
      }
    }
    if (!browser) throw lastError || new Error("CDP never became ready");
    const page = browser.contexts()[0].pages()[0];
    try {
      return await step(page);
    } finally {
      await browser.close().catch(() => {});
    }
  } finally {
    await quitApp(child);
    active = null;
    activeChild = null;
  }
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

async function firstRun(page) {
  await page
    .getByRole("button", { name: "Создать владельца Canalla" })
    .waitFor({ timeout: 240000 });
  await page.getByLabel("Имя владельца (необязательно)").fill("Backup Owner");
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Пароль", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Создать владельца Canalla" }).click();
  await page
    .getByRole("button", { name: "New Chat" })
    .waitFor({ timeout: 180000 });
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

async function withRetry(action, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await action();
    } catch (error) {
      lastError = error;
      await sleep(1500);
    }
  }
  throw lastError || new Error("retry timeout");
}

async function ownerToken() {
  const login = await withRetry(async () => {
    backendPort = await findBackendPort(20000);
    const response = await fetchJson(
      `http://127.0.0.1:${backendPort}/auth/login`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
      },
    );
    if (!response.access_token)
      throw new Error(
        `login failed: ${JSON.stringify(response).slice(0, 120)}`,
      );
    return response;
  });
  return login.access_token;
}

async function counts(token) {
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
    messages,
    projects: projects.length,
    memories: memories.length,
    documents: documents.length,
  };
}

async function countsAfterRestore(token) {
  // After a restore the backend has restarted, so the session may need re-establishing.
  const fresh = await ownerToken();
  return counts(fresh || token);
}

async function seed(token) {
  const headers = {
    Authorization: "Bearer " + token,
    "Content-Type": "application/json",
  };
  const chat = await fetchJson(`http://127.0.0.1:${backendPort}/chats`, {
    method: "POST",
    headers,
    body: JSON.stringify({ title: "Копия" }),
  });
  await fetchJson(`http://127.0.0.1:${backendPort}/chats/${chat.id}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify({ content: "Сообщение до копии" }),
  });
  await fetchJson(`http://127.0.0.1:${backendPort}/projects`, {
    method: "POST",
    headers,
    body: JSON.stringify({ name: "Проект до копии" }),
  });
  await fetchJson(`http://127.0.0.1:${backendPort}/memory`, {
    method: "POST",
    headers,
    body: JSON.stringify({ content: "Память до копии", category: "fact" }),
  });
  const form = new FormData();
  form.append(
    "file",
    new Blob(["документ до копии"], { type: "text/plain" }),
    "до-копии.txt",
  );
  await fetch(`http://127.0.0.1:${backendPort}/documents`, {
    method: "POST",
    headers: { Authorization: "Bearer " + token },
    body: form,
  });
}

async function waitForBackups(token, predicate, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    try {
      last = await fetchJson(`http://127.0.0.1:${backendPort}/backup`, {
        headers: { Authorization: "Bearer " + token },
      });
      if (predicate(last)) return last;
    } catch {
      // A restore restarts the backend: connection errors here are expected, not fatal.
    }
    await sleep(1000);
  }
  return last;
}

function backupDirs(root) {
  const dir = path.join(root, "backups");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter(
      (name) =>
        !name.startsWith(".") &&
        fs.existsSync(path.join(dir, name, "manifest.json")),
    )
    .map((name) => path.join(dir, name));
}

// ------------------------------------------------------------------- phase A: backup/restore

async function phaseA() {
  const scope = isolated("a");
  console.log("A. fresh install -> backup -> mutate -> restore through the UI");
  console.log(`   data root=${scope.dataRoot}`);
  await withApp(scope, async (page) => {
    await firstRun(page);
    backendPort = await findBackendPort();
    check(
      "A1 the isolated install is serving its backend",
      backendPort !== 0,
      `port ${backendPort}`,
    );
    const token = await ownerToken();
    await seed(token);
    const before = await counts(token);
    check(
      "A2 the fixture has chats, messages, project, memory and a document",
      before.chats === 1 &&
        before.messages === 1 &&
        before.projects === 1 &&
        before.memories === 1 &&
        before.documents === 1,
      JSON.stringify(before),
    );

    const dialog = await openSettingsSection(page, "Резервные копии");
    await dialog
      .getByRole("button", { name: "Создать копию" })
      .click({ timeout: 30000 });
    const listed = await waitForBackups(
      token,
      (payload) => payload.backups.length > 0 && payload.backups[0].verified,
    );
    check(
      "A3 the panel created a verified backup",
      listed.backups.length === 1 && listed.backups[0].verified,
      JSON.stringify(listed.backups[0] || {}).slice(0, 120),
    );
    const badgeDeadline = Date.now() + 60000;
    let panelText = "";
    while (Date.now() < badgeDeadline && !/Проверена/.test(panelText)) {
      await sleep(1000);
      panelText = await dialog.innerText().catch(() => "");
    }
    check(
      "A4 the panel shows the verified badge and the folder",
      /Проверена/.test(panelText) && /backups/.test(panelText),
      flat(panelText).slice(0, 100),
    );

    // Mutate everything a user cares about.
    const extra = await fetchJson(`http://127.0.0.1:${backendPort}/chats`, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title: "После копии" }),
    });
    check("A5 the fixture changed after the backup", Boolean(extra.id));
    await fetchJson(
      `http://127.0.0.1:${backendPort}/memory/${(await fetchJson(`http://127.0.0.1:${backendPort}/memory`, { headers: { Authorization: "Bearer " + token } }))[0].id}`,
      {
        method: "DELETE",
        headers: { Authorization: "Bearer " + token },
      },
    );
    for (const document of await fetchJson(
      `http://127.0.0.1:${backendPort}/documents`,
      {
        headers: { Authorization: "Bearer " + token },
      },
    )) {
      await fetch(
        `http://127.0.0.1:${backendPort}/documents/${document.id ?? document.key}`,
        {
          method: "DELETE",
          headers: { Authorization: "Bearer " + token },
        },
      );
    }
    const mutated = await counts(token);
    check(
      "A6 the mutation removed a memory and the document",
      mutated.memories === 0 && mutated.documents === 0,
      JSON.stringify(mutated),
    );

    // Restore through the real UI, including the confirmation step.
    await dialog
      .getByRole("button", { name: "Восстановить" })
      .click({ timeout: 30000 });
    const confirming = /Да, восстановить/.test(await dialog.innerText());
    check("A7 the panel asks for an explicit confirmation", confirming);
    await dialog
      .getByRole("button", { name: "Да, восстановить" })
      .click({ timeout: 30000 });
    const restored = await waitForBackups(
      token,
      (payload) => Boolean(payload.last_restore?.restored),
      300000,
    );
    check(
      "A8 the restore reported success",
      Boolean(restored.last_restore?.restored),
      JSON.stringify(restored.last_restore || {}).slice(0, 120),
    );
    if (!restored.last_restore?.restored) {
      const log = path.join(scope.dataRoot, "logs", "backend.log");
      if (fs.existsSync(log)) {
        const tail = fs
          .readFileSync(log, "utf8")
          .split(/\r?\n/)
          .slice(-12)
          .join("\n");
        console.log("      [info] backend log tail:\n" + tail);
      }
    }
    check(
      "A9 a safety backup of the replaced state exists",
      Boolean(restored.last_restore?.safety_backup_id),
      restored.last_restore?.safety_backup_id || "",
    );

    backendPort = 0;
    backendPort = await findBackendPort();
    const after = await withRetry(() => countsAfterRestore(token));
    check(
      "A10 the data is exactly the state that was backed up",
      JSON.stringify(after) === JSON.stringify(before),
      `${JSON.stringify(after)} vs ${JSON.stringify(before)}`,
    );
    const kept = backupDirs(scope.dataRoot);
    check(
      "A11 the backup and the safety copy are both kept on disk",
      kept.length >= 2,
      `${kept.length} copies`,
    );
    return { scope, before };
  });
  deleteCredential(`Alex LLM/gateway/${scope.credentialName}`);
  collectSessionCredentials(scope);
  fs.rmSync(scope.work, { recursive: true, force: true });
}

// -------------------------------------------------------------- phase C: tampered rejection

async function phaseC() {
  const scope = isolated("c");
  console.log();
  console.log("C. a tampered backup is refused and the live data is untouched");
  await withApp(scope, async (page) => {
    await firstRun(page);
    backendPort = await findBackendPort();
    const token = await ownerToken();
    await seed(token);
    const before = await counts(token);

    const dialog = await openSettingsSection(page, "Резервные копии");
    await dialog
      .getByRole("button", { name: "Создать копию" })
      .click({ timeout: 30000 });
    await waitForBackups(
      token,
      (payload) => payload.backups.length > 0 && payload.backups[0].verified,
    );
    const [backup] = backupDirs(scope.dataRoot);
    const documents = path.join(backup, "documents");
    const victim = path.join(documents, fs.readdirSync(documents)[0]);
    fs.writeFileSync(
      victim,
      Buffer.concat([fs.readFileSync(victim), Buffer.from("tampered")]),
    );
    check(
      "C1 the backup was tampered with on disk",
      true,
      path.basename(victim),
    );

    await dialog
      .getByRole("button", { name: "Восстановить" })
      .click({ timeout: 30000 });
    await dialog
      .getByRole("button", { name: "Да, восстановить" })
      .click({ timeout: 30000 });
    const reported = await waitForBackups(
      token,
      (payload) =>
        payload.last_restore && payload.last_restore.restored === false,
      300000,
    );
    const failure = reported.last_restore || {};
    check(
      "C2 the restore was refused",
      failure.restored === false,
      failure.code || "",
    );
    check(
      "C3 the refusal names the tampered backup",
      ["backup_tampered", "backup_corrupt", "backup_incomplete"].includes(
        failure.code,
      ),
      failure.code || "",
    );
    check(
      "C4 the message is human-readable",
      Boolean(failure.message) && !/Traceback/.test(failure.message),
      failure.message || "",
    );

    backendPort = 0;
    backendPort = await findBackendPort();
    const after = await withRetry(() => countsAfterRestore(token));
    check(
      "C5 the live data is untouched",
      JSON.stringify(after) === JSON.stringify(before),
      `${JSON.stringify(after)} vs ${JSON.stringify(before)}`,
    );
    const dialogText = await dialog.innerText();
    await dialog
      .getByRole("button", { name: "Проверить" })
      .first()
      .click({ timeout: 30000 });
    const markedDeadline = Date.now() + 60000;
    let marked = "";
    while (
      Date.now() < markedDeadline &&
      !/Повреждена|Не проверена/.test(marked)
    ) {
      await sleep(1000);
      marked = await dialog.innerText().catch(() => "");
    }
    check(
      "C6 a fresh verification marks the damaged copy",
      /Повреждена|Не проверена/.test(marked),
      flat(marked || dialogText).slice(0, 100),
    );
    return { scope };
  });
  deleteCredential(`Alex LLM/gateway/${scope.credentialName}`);
  collectSessionCredentials(scope);
  fs.rmSync(scope.work, { recursive: true, force: true });
}

// --------------------------------------------------- phase D: refused/failed migration UX

async function phaseD() {
  const scope = isolated("d");
  console.log();
  console.log(
    "D. a database that cannot be migrated: honest failure, data and backup kept",
  );
  const database = path.join(scope.dataRoot, "data", "alex.db");
  fs.mkdirSync(path.dirname(database), { recursive: true });
  fs.mkdirSync(path.join(scope.dataRoot, "documents"), { recursive: true });
  fs.mkdirSync(path.join(scope.dataRoot, "runtime"), { recursive: true });
  const env = {
    ...process.env,
    DATABASE_URL: "sqlite:///" + database.replace(/\\/g, "/"),
    JWT_SECRET: "k".repeat(64),
    ALEX_APP_RESOURCE_DIR: BACKEND,
  };
  execFileSync(PYTHON, ["-m", "alembic", "upgrade", "0014"], {
    cwd: BACKEND,
    env,
    stdio: "ignore",
  });
  execFileSync(
    PYTHON,
    [
      "-c",
      `import sqlite3,sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA foreign_keys=ON")
db.execute("INSERT INTO users (id,email,password_hash,created_at,display_name) VALUES ('u','fail@example.com','hash','2026-09-21','Failure Owner')")
db.execute("INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES ('c','u','До отказа','2026-09-21','2026-09-21')")
db.execute("INSERT INTO messages (id,chat_id,role,content,created_at) VALUES ('m','c','user','Сообщение до отказа','2026-09-21')")
db.commit()
print(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])`,
      database,
    ],
    { encoding: "utf8" },
  );
  fs.writeFileSync(
    path.join(scope.dataRoot, "runtime", "jwt.secret"),
    "k".repeat(64),
    "utf8",
  );
  fs.writeFileSync(
    path.join(scope.dataRoot, "runtime", "install.id"),
    "migration-failure-machine",
    "utf8",
  );
  const original = fs.statSync(database).mode;
  fs.chmodSync(database, 0o444); // write-protected: the migration must fail
  try {
    await withApp(scope, async (page) => {
      const seen = await page
        .getByText(
          "Не удалось обновить базу. Чат не запущен; данные не удалены.",
        )
        .waitFor({ timeout: 240000 })
        .then(() => true)
        .catch(() => false);
      check("D1 the app reports the honest recovery message", seen);
      const body = flat(
        await page.evaluate(() => document.body.innerText).catch(() => ""),
      );
      check(
        "D2 no stack trace is shown",
        !/Traceback|alembic\.util|OperationalError/.test(body),
      );
      await sleep(20000); // a restart loop would keep changing the log; give it time to show
    });
  } finally {
    fs.chmodSync(database, original);
  }

  const revisions = execFileSync(
    PYTHON,
    [
      "-c",
      `import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);` +
        `print(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])`,
      database,
    ],
    { encoding: "utf8" },
  ).trim();
  check(
    "D3 the database is still at the old revision with its data",
    revisions === "0014 1",
    revisions,
  );
  const backups = backupDirs(scope.dataRoot);
  check(
    "D4 a pre-upgrade backup was created before the attempt",
    backups.length === 1,
    `${backups.length} copies`,
  );
  if (backups.length === 1) {
    const verified = fs.existsSync(path.join(backups[0], "verification.json"));
    check("D5 that backup is verified", verified);
    const manifest = JSON.parse(
      fs.readFileSync(path.join(backups[0], "manifest.json"), "utf8"),
    );
    check(
      "D6 it captured the pre-migration revision",
      manifest.schema_revision === "0014",
      manifest.schema_revision,
    );
  }
  const record = JSON.parse(
    fs.readFileSync(
      path.join(scope.dataRoot, "runtime", "migration.json"),
      "utf8",
    ),
  );
  check(
    "D7 the migration record explains the failure",
    record.result === "failed",
    record.result,
  );
  check(
    "D8 the record points at the backup",
    Boolean(record.backup),
    record.backup || "",
  );
  const files = fs.readdirSync(path.join(scope.dataRoot, "data"));
  check(
    "D9 no empty database replaced the real one",
    files.filter((name) => name.startsWith("alex.db")).length === 1,
    files.join(","),
  );
  deleteCredential(`Alex LLM/gateway/${scope.credentialName}`);
  collectSessionCredentials(scope);
  fs.rmSync(scope.work, { recursive: true, force: true });
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error(`installed app not found: ${EXE}`);
    process.exit(1);
  }
  console.log(
    "Backup / restore / upgrade-failure acceptance (installed app, isolated roots)",
  );
  await phaseA();
  await phaseC();
  await phaseD();
  console.log();
  const cleaned = cleanupDeviceCredentials();
  const sessionsCleaned = cleanupSessionCredentials();
  check(
    "E1 every device credential this run wrote is deleted again",
    cleaned,
    [...deviceCredentials].join(", "),
  );
  check(
    "E2 every session credential this run wrote is deleted again",
    sessionsCleaned,
    `${sessionCredentials.size} session(s)`,
  );
  if (skipped) console.log(`SKIPPED: ${skipped}`);
  if (failures) {
    console.error(`BACKUP ACCEPTANCE FAILED: ${failures} check(s)`);
    process.exit(1);
  }
  console.log(
    "BACKUP ACCEPTANCE PASS — verified backups, safe restore, honest failures",
  );
}

main().catch((error) => {
  console.error("BACKUP ACCEPTANCE ERROR:", error);
  // Never leave an app instance behind: a surviving WebView2 user-data folder makes the
  // next run's debug port silently unavailable.
  killTree(activeChild);
  if (active?.credentialName)
    deleteCredential(`Alex LLM/gateway/${active.credentialName}`);
  cleanupDeviceCredentials();
  cleanupSessionCredentials();
  process.exit(1);
});
