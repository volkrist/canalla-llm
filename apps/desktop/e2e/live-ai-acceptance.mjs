// STEP 5B.1 — the live AI acceptance, driven against the operator's own installed product.
//
// This run deliberately does NOT isolate the product data: the point of the step is the *actual*
// active policy of the *actual* install, which is enrolled in shared mode and therefore starts
// compute through the deployed Gateway. Only the WebView2 profile and the CDP port are isolated —
// the session, the device identity and the stored compute preferences are the operator's own.
//
// It performs exactly one model-required chat request, waits for the Pod the product starts by
// itself, sends one trivial generation, then reuses the same running Pod for one natural-language
// Tor request, and finally stops compute through the product's own control. It asserts nothing it
// does not observe: every check prints the value it read.
//
// Usage (from apps/desktop, with node):
//   node e2e/live-ai-acceptance.mjs --phase chat
//   node e2e/live-ai-acceptance.mjs --phase tor
//   node e2e/live-ai-acceptance.mjs --phase stop
//   node e2e/live-ai-acceptance.mjs --phase observe      # read-only, sends nothing

import { execFileSync, spawn, spawnSync } from "node:child_process";
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

const CHAT_PROMPT =
  process.env.LIVE_AI_CHAT_PROMPT || "Ответь одним словом: готово";
const TOR_PROMPT =
  process.env.LIVE_AI_TOR_PROMPT || "Открой https://example.com через Tor";

const failures = [];
let child = null;

function check(name, ok, detail = "") {
  console.log(
    `${ok ? "PASS" : "FAIL"} ${name}${detail ? `  [${detail}]` : ""}`,
  );
  if (!ok) failures.push(name);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  path.join(os.tmpdir(), "canalla-live-ai-webview-"),
);

function launchApp() {
  const env = {
    ...process.env,
    // Only the browser profile is isolated. ALEX_LLM_DATA_DIR, ALEX_DEVICE_DIR and the credential
    // targets are left alone on purpose: this is the operator's real install and its real session.
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}`,
    WEBVIEW2_USER_DATA_FOLDER: WEBVIEW_PROFILE,
  };
  return spawn(EXE, [], { env, stdio: "ignore", windowsHide: true });
}

async function waitCdp(timeoutMs = 150000) {
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

async function openApp() {
  child = launchApp();
  if (!(await waitCdp())) return null;
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const page = browser.contexts()[0].pages()[0];
  return { browser, page };
}

async function aiState(page) {
  const badge = page.locator('[data-testid="ai-connection"]');
  return {
    state: (await badge.getAttribute("data-state").catch(() => "")) || "",
    code: (await badge.getAttribute("data-code").catch(() => "")) || "",
    text: (await badge.innerText().catch(() => "")).replace(/\s+/g, " ").trim(),
    label: (await badge.getAttribute("aria-label").catch(() => "")) || "",
  };
}

/** Wait for the AI badge to reach a state, reporting every distinct state it passed through. */
async function waitForAi(page, wanted, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const seen = new Set();
  while (Date.now() < deadline) {
    const current = await aiState(page);
    if (current.state) seen.add(`${current.state}/${current.code}`);
    if (wanted.includes(current.state)) {
      return { reached: current.state, seen: [...seen], at: Date.now() };
    }
    await sleep(1500);
  }
  return { reached: null, seen: [...seen], at: Date.now() };
}

/** The transcript as plain text, straight from the DOM the user reads. */
async function transcript(page) {
  return (
    await page
      .locator(".messages")
      .innerText()
      .catch(() => "")
  )
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function messagesInDom(page) {
  return await page.locator(".messages .message-body").count();
}

/** Wait until the generation stops being in flight and the transcript has grown by `grew`. */
async function waitForAnswer(page, before, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const streaming = await page
      .getByLabel("Stop generation")
      .isVisible()
      .catch(() => false);
    const now = await messagesInDom(page);
    if (!streaming && now > before) {
      return { done: true, messages: now };
    }
    await sleep(1500);
  }
  return { done: false, messages: await messagesInDom(page) };
}

async function send(page, text) {
  await page.getByLabel("Сообщение").fill(text);
  await page.getByLabel("Send").click();
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

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1]
    ? process.argv[index + 1]
    : fallback;
}

const PHASE = arg("--phase", "observe");

/**
 * Wait for the badge to leave the transient `checking` code.
 *
 * The chip row renders before the first status read lands, so sampling it the moment it appears
 * reads «Проверяем состояние AI» — a transient, not a state. A bounded wait for the settled answer
 * is what makes "Disconnected before the request" a fact rather than a race.
 */
async function settleAi(page, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  const seen = new Set();
  while (Date.now() < deadline) {
    const current = await aiState(page);
    if (current.code) seen.add(`${current.state}/${current.code}`);
    if (current.code && current.code !== "checking") return current;
    await sleep(1500);
  }
  return await aiState(page);
}

async function main() {
  console.log(`LIVE AI ACCEPTANCE — phase ${PHASE}`);
  console.log(`  app   ${EXE}`);
  console.log(`  data  the operator's own root (not isolated)\n`);

  const opened = await openApp();
  if (!opened) {
    check("the installed app exposes its debugging port", false);
    return 1;
  }
  const { page } = opened;

  // The chip row only exists once the workspace is up; the first-run flow would show instead.
  await page.locator(".status-chip").first().waitFor({ timeout: 180000 });
  const firstRun = await page
    .getByRole("button", { name: "Создать владельца Canalla" })
    .count();
  check(
    "the operator's session is restored (no first-run, no login)",
    firstRun === 0,
    `first-run buttons: ${firstRun}`,
  );

  const before = await settleAi(page);
  console.log(`  ai before   ${JSON.stringify(before)}`);
  const torChip =
    (await page
      .locator(".status-chip", { hasText: "Tor" })
      .first()
      .getAttribute("aria-label")
      .catch(() => "")) || "";
  const computerChip =
    (await page
      .locator(".status-chip", { hasText: "Computer" })
      .first()
      .getAttribute("aria-label")
      .catch(() => "")) || "";
  console.log(`  tor chip    ${torChip}`);
  console.log(`  computer    ${computerChip}`);

  if (PHASE === "observe") {
    console.log(`\n  transcript:\n${(await transcript(page)).slice(0, 400)}`);
    await quitApp();
    console.log(
      `\n${failures.length === 0 ? "OBSERVE PASS" : "OBSERVE FAILED"}`,
    );
    return failures.length === 0 ? 0 : 1;
  }

  if (PHASE === "stop") {
    const stop = page.getByRole("button", { name: "Остановить AI" }).first();
    const present = await stop.isVisible().catch(() => false);
    check("the product offers a Stop AI control", present);
    if (present) {
      await stop.click();
      const settled = await waitForAi(page, ["disconnected"], 180000);
      check(
        "AI returns to Disconnected after Stop AI",
        settled.reached === "disconnected",
        `reached=${settled.reached} seen=${settled.seen.join(" -> ")}`,
      );
    }
    await quitApp();
    console.log(`\n${failures.length === 0 ? "STOP PASS" : "STOP FAILED"}`);
    return failures.length === 0 ? 0 : 1;
  }

  // ---------------------------------------------------------------- one model-required chat

  await page.locator(".new-chat").click();
  await page.getByLabel("Сообщение").waitFor({ timeout: 60000 });
  const torMode = page.getByLabel("Tor mode");
  if (await torMode.count()) {
    console.log(
      `  tor mode selector: ${await torMode.inputValue().catch(() => "?")}`,
    );
  }

  const chatStarted = Date.now();
  await send(page, CHAT_PROMPT);
  console.log(`  sent: ${CHAT_PROMPT}`);

  const connected = await waitForAi(page, ["connected"], 1200000);
  const connectMs = Date.now() - chatStarted;
  console.log(`  ai states seen: ${connected.seen.join(" -> ")}`);
  check(
    "the chat started compute by itself (no Settings button, no resend)",
    connected.seen.some((entry) => entry.startsWith("connecting")) ||
      connected.reached === "connected",
    connected.seen.join(" -> "),
  );
  check(
    "Connecting appeared only while a transition was real",
    connected.seen.length === 0 ||
      connected.seen.every(
        (entry) =>
          entry.startsWith("connecting") ||
          entry.startsWith("disconnected") ||
          entry.startsWith("connected"),
      ),
    connected.seen.join(" -> "),
  );
  check(
    "the model became Ready and the chip turned Connected",
    connected.reached === "connected",
    `${connected.reached} after ${connectMs} ms`,
  );

  const answered = await waitForAnswer(page, await messagesInDom(page), 900000);
  check(
    "the chat produced an answer",
    answered.done,
    `${answered.messages} message bodies`,
  );
  const chatTranscript = await transcript(page);
  console.log(`\n  transcript after the chat:\n${chatTranscript}\n`);

  if (PHASE === "chat") {
    await quitApp();
    console.log(`\n${failures.length === 0 ? "CHAT PASS" : "CHAT FAILED"}`);
    if (failures.length)
      console.log(failures.map((name) => ` - ${name}`).join("\n"));
    return failures.length === 0 ? 0 : 1;
  }

  // ---------------------------------------------------------------- natural-language Tor, same Pod

  const torBefore = await messagesInDom(page);
  await send(page, TOR_PROMPT);
  console.log(`  sent: ${TOR_PROMPT}`);
  const torAnswered = await waitForAnswer(page, torBefore, 900000);
  check(
    "the Tor request produced an answer",
    torAnswered.done,
    `${torAnswered.messages} bodies`,
  );
  const torTranscript = await transcript(page);
  console.log(`\n  transcript after the Tor request:\n${torTranscript}\n`);

  await quitApp();
  console.log(`\n${failures.length === 0 ? "TOR PASS" : "TOR FAILED"}`);
  if (failures.length)
    console.log(failures.map((name) => ` - ${name}`).join("\n"));
  return failures.length === 0 ? 0 : 1;
}

const code = await main();
process.exit(code);
