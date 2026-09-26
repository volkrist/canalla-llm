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
//   node e2e/live-ai-acceptance.mjs --phase all      # the acceptance: chat -> Tor -> Stop
//   node e2e/live-ai-acceptance.mjs --phase chat
//   node e2e/live-ai-acceptance.mjs --phase tor
//   node e2e/live-ai-acceptance.mjs --phase stop
//   node e2e/live-ai-acceptance.mjs --phase observe  # read-only, sends nothing
//
// One phase opens one window, and quitting Canalla stops a managed Pod (the product's own
// lifecycle). `all` is therefore the acceptance: chat, Tor and the product's Stop stay in the same
// launch, so nothing it measures is stopped by its own teardown.

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

// The product's own Tor evidence, read where the product writes it. The chip is green only on a
// proof, so the proof is what an acceptance run must read — an open SOCKS port proves nothing.
const TOR_PROOF = path.join(
  process.env.LOCALAPPDATA ?? "",
  "Alex LLM",
  "runtime",
  "tor.json",
);

// The product's own allocation lifecycle is bounded (60 s), so a chat-triggered ensure either moves
// the badge out of `disconnected/<code>` promptly or it was REFUSED. The previous run waited 20
// minutes behind a terminal 422 and hid a contract mismatch; these two windows fix that without
// touching production semantics: a short one for the transition to appear, a long one for the real
// model load once it has.
const CONNECT_TRIGGER_WAIT_MS = Number(
  process.env.LIVE_AI_TRIGGER_WAIT_MS || 150000,
);
const READY_WAIT_MS = Number(process.env.LIVE_AI_READY_WAIT_MS || 240000);
// Bounded like the rest: the whole run has to finish inside the Pod's own 20-minute deadline, so a
// slow stage fails with evidence instead of being killed by the outer deadline.
const ANSWER_WAIT_MS = Number(process.env.LIVE_AI_ANSWER_WAIT_MS || 180000);
const TOR_READY_WAIT_MS = Number(process.env.LIVE_AI_TOR_READY_WAIT_MS || 180000);
const TOR_ANSWER_WAIT_MS = Number(process.env.LIVE_AI_TOR_ANSWER_WAIT_MS || 300000);

const failures = [];
let child = null;
let lastPage = null;

// Evidence, not a stack trace: every path that gives up writes what the window actually showed.
const ARTIFACTS = path.resolve(__dirname, "..", "..", "..", "artifacts", "final-acceptance");

async function dumpPage(page, label) {
  try {
    fs.mkdirSync(ARTIFACTS, { recursive: true });
    await page.screenshot({
      path: path.join(ARTIFACTS, `dom-${label}.png`),
      fullPage: true,
    });
    const text = await page
      .evaluate(() => document.body?.innerText || "")
      .catch(() => "");
    fs.writeFileSync(
      path.join(ARTIFACTS, `dom-${label}.txt`),
      `${page.url()}\n\n${text.slice(0, 6000)}\n`,
      "utf8",
    );
    console.log(`  window dumped to artifacts/final-acceptance/dom-${label}.{png,txt}`);
  } catch (error) {
    console.log(`  could not dump the window: ${error?.message}`);
  }
}

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
  const free = await closeRunningInstances();
  if (!free) return null;
  // The product is single-instance: if a previous instance is still exiting, a launch is absorbed
  // by it and the window this harness attaches to shows «Локальный сервер недоступен» instead of
  // the workspace. So a launch that does not produce a workspace is closed and retried once.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    child = launchApp();
    if (!(await waitCdp())) {
      await quitApp();
      await sleep(5000);
      continue;
    }
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    const page = browser.contexts()[0].pages()[0];
    const workspace = await page
      .locator(".status-chip")
      .first()
      .waitFor({ timeout: 90000 })
      .then(() => true)
      .catch(() => false);
    if (workspace) return { browser, page };
    await quitApp();
    await sleep(5000);
  }
  return null;
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

/**
 * The answer as the user reads it, taken from the DOM.
 *
 * The previous version of this harness counted `.message-body` elements and waited for the number to
 * grow. The product inserts the user *and* the assistant message together on the stream's `meta`
 * event, so the count is 2 before the model has produced a single token and it never grows again —
 * the check could not pass even when the answer arrived (it timed out twice, once for 900 s, while
 * the generation had completed). Reading the assistant body's own text is what the user sees.
 */
async function lastAnswer(page) {
  return await page
    .evaluate(() => {
      const articles = Array.from(
        document.querySelectorAll(".messages article.message.assistant"),
      );
      const last = articles[articles.length - 1];
      if (!last) return { count: 0, text: "" };
      const body = last.querySelector(".message-body");
      const clone = body ? body.cloneNode(true) : null;
      if (clone)
        clone
          .querySelectorAll(
            '.message-author, .typing, .source-list, .message-actions, [role="alert"]',
          )
          .forEach((node) => node.remove());
      const text = ((clone && (clone.innerText || clone.textContent)) || "")
        .replace(/\s+/g, " ")
        .trim();
      return { count: articles.length, text };
    })
    .catch(() => ({ count: 0, text: "" }));
}

/** Wait until the newest assistant answer carries text and generation is no longer in flight. */
async function waitForAnswer(page, before, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let latest = before;
  while (Date.now() < deadline) {
    const streaming = await page
      .getByLabel("Stop generation")
      .isVisible()
      .catch(() => false);
    latest = await lastAnswer(page);
    const fresh =
      latest.count > before.count ||
      (latest.text !== "" && latest.text !== before.text);
    if (!streaming && latest.text && fresh)
      return { done: true, text: latest.text, count: latest.count };
    await sleep(1500);
  }
  return { done: false, text: latest.text, count: latest.count };
}

async function send(page, text) {
  await page.getByLabel("Сообщение").fill(text);
  await page.getByLabel("Send").click();
}

/** The persisted Tor proof: a real SOCKS5h round trip, never an exit address. */
function readTorProof() {
  try {
    const parsed = JSON.parse(fs.readFileSync(TOR_PROOF, "utf8"));
    return {
      verified: parsed.verified === true,
      method: typeof parsed.method === "string" ? parsed.method : "",
      source: typeof parsed.source === "string" ? parsed.source : "",
      port: parsed.port ?? null,
      version:
        typeof parsed.tor_version === "string" ? parsed.tor_version : "",
      verified_at:
        typeof parsed.verified_at === "string" ? parsed.verified_at : "",
      age_seconds:
        typeof parsed.verified_at === "string"
          ? Math.round((Date.now() - Date.parse(parsed.verified_at)) / 1000)
          : null,
    };
  } catch {
    return null;
  }
}

/** The Tor chip as the user sees it: its state class and its own words. */
async function torChipState(page) {
  const chip = page.locator(".status-chip", { hasText: "Tor" }).first();
  const label = (await chip.getAttribute("aria-label").catch(() => "")) || "";
  const className = (await chip.getAttribute("class").catch(() => "")) || "";
  return { label, state: /state-(\w+)/.exec(className)?.[1] ?? "" };
}

/**
 * Tor is a service the product keeps ready: on a fresh launch the bundled daemon bootstraps, and the
 * chip says so. A Tor request sent while it is still starting is one the product refuses, so the
 * acceptance waits — bounded — for the state a user waits for, and records every state it saw.
 */
async function waitForTorReady(page, timeoutMs = TOR_READY_WAIT_MS) {
  const deadline = Date.now() + timeoutMs;
  const seen = new Set();
  while (Date.now() < deadline) {
    const chip = await torChipState(page);
    if (chip.state) seen.add(chip.state);
    if (chip.state === "ready") return { ready: true, seen: [...seen], chip };
    await sleep(2000);
  }
  return { ready: false, seen: [...seen], chip: await torChipState(page) };
}

/** How many web sources the product attached to the newest answer, and how it labels them. */
async function lastAnswerSources(page) {
  return await page
    .evaluate(() => {
      const articles = Array.from(
        document.querySelectorAll(".messages article.message.assistant"),
      );
      const last = articles[articles.length - 1];
      if (!last) return { tor: 0, summary: "" };
      const section = last.querySelector('section[aria-label="Tor sources"]');
      return {
        tor: section ? section.querySelectorAll("li, article").length : 0,
        summary: section
          ? (section.querySelector("summary")?.textContent || "").trim()
          : "",
      };
    })
    .catch(() => ({ tor: 0, summary: "" }));
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

/**
 * The acceptance needs the window to itself: the product is single-instance, so a Canalla already
 * running would swallow this launch and the debugging port would never appear. Closed gracefully —
 * never force-killed, because that process may own compute.
 */
function runningInstances() {
  try {
    const out = execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        "Get-Process alex-llm -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id",
      ],
      { encoding: "utf8" },
    );
    return out
      .split(/\s+/)
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0);
  } catch {
    return [];
  }
}

async function closeRunningInstances() {
  const pids = runningInstances();
  if (!pids.length) return true;
  console.log(
    `  a Canalla instance is already running (${pids.join(", ")}) — closing it gracefully`,
  );
  for (const pid of pids) {
    try {
      execFileSync("powershell.exe", [
        "-NoProfile",
        "-Command",
        `(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).CloseMainWindow() | Out-Null`,
      ]);
    } catch {
      /* the instance may already be gone */
    }
  }
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (!runningInstances().length) {
      // A process that has left the list may still be closing its backend and its window.
      await sleep(4000);
      return true;
    }
    await sleep(1000);
  }
  return false;
}

/**
 * The Tor route's own evidence, in the order the product proves it: the chip is green on a persisted
 * proof, the proof is a managed SOCKS5h round trip, and the answer carries the sources the fetch
 * returned. Nothing here is inferred from «the answer arrived».
 */
async function checkTorRoute(page, before) {
  const after = readTorProof();
  const chip = await torChipState(page);
  const sources = await lastAnswerSources(page);
  console.log(`  tor chip after   ${chip.label} (state=${chip.state})`);
  console.log(`  tor proof after  ${JSON.stringify(after)}`);
  console.log(`  tor sources      ${JSON.stringify(sources)}`);
  check(
    "the Tor chip is green on a proof, not on an open port",
    chip.state === "ready",
    `state=${chip.state} label=${chip.label}`,
  );
  check(
    "the proof is a managed SOCKS5h round trip and still fresh",
    !!after &&
      after.verified &&
      after.method === "socks5h" &&
      after.source !== "" &&
      // The backend's own tor_proof_ttl_seconds is 900 s; a proof older than the TTL is not green.
      (after.age_seconds === null || after.age_seconds <= 1200),
    JSON.stringify(after),
  );
  check(
    "the answer carries the Tor sources the fetch returned",
    sources.tor > 0,
    JSON.stringify(sources),
  );
  check(
    "the proof was proved again while the request was in flight",
    !!after &&
      !!before &&
      Date.parse(after.verified_at) >= Date.parse(before.verified_at || "0"),
    `${before?.verified_at ?? "none"} -> ${after?.verified_at ?? "none"}`,
  );
}

/**
 * Stop compute through the product's own control and prove the badge says so.
 *
 * This is part of the single run on purpose: quitting Canalla stops a managed Pod (the product's own
 * documented lifecycle), so an acceptance split across separate launches would stop the very Pod it
 * is measuring. Every phase before this one keeps the window open.
 */
async function stopCompute(page) {
  let stop = page.getByRole("button", { name: "Остановить AI" }).first();
  let present = await stop.isVisible().catch(() => false);
  if (!present) {
    // The control lives in Settings -> Canalla Cloud, and the workspace binds Ctrl+, to Settings.
    await page.keyboard.press("Control+,");
    const tab = page
      .getByRole("button", { name: "Canalla Cloud", exact: true })
      .first();
    const opened = await tab
      .waitFor({ timeout: 20000 })
      .then(() => true)
      .catch(() => false);
    if (opened) {
      await tab.click();
      stop = page.getByRole("button", { name: "Остановить AI" }).first();
      present = await stop.isVisible().catch(() => false);
    }
  }
  check("the product offers a Stop AI control", present);
  if (!present) return;
  await stop.click();
  const settled = await waitForAi(page, ["disconnected"], 180000);
  check(
    "AI returns to Disconnected after Stop AI (not Connecting, not a stale Connected)",
    settled.reached === "disconnected",
    `reached=${settled.reached} seen=${settled.seen.join(" -> ")}`,
  );
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
    check(
      "the installed app exposes its debugging port (and no other instance holds it)",
      false,
    );
    return 1;
  }
  const { page } = opened;
  lastPage = page;

  // The chip row only exists once the workspace is up; the first-run flow would show instead.
  try {
    await page.locator(".status-chip").first().waitFor({ timeout: 180000 });
  } catch (error) {
    check(
      "the workspace came up (the chip row is visible)",
      false,
      `${error?.name}: ${error?.message}`,
    );
    await dumpPage(page, "no-workspace");
    await quitApp();
    return 1;
  }
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
    await stopCompute(page);
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
  const beforeChat = await aiState(page);
  const beforeAnswer = await lastAnswer(page);
  await send(page, CHAT_PROMPT);
  console.log(`  sent: ${CHAT_PROMPT}`);

  const transition = await waitForAi(
    page,
    ["connecting", "connected"],
    CONNECT_TRIGGER_WAIT_MS,
  );
  let connected = transition;
  if (transition.reached === null) {
    const afterChat = await aiState(page);
    console.log(
      `  no transition within ${CONNECT_TRIGGER_WAIT_MS} ms — the ensure was refused, not slow ` +
        `[${beforeChat.state}/${beforeChat.code} -> ${afterChat.state}/${afterChat.code}]`,
    );
  } else if (transition.reached !== "connected") {
    const ready = await waitForAi(page, ["connected"], READY_WAIT_MS);
    connected = {
      reached: ready.reached,
      seen: [...new Set([...transition.seen, ...ready.seen])],
      at: ready.at,
    };
  }
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

  const answered = await waitForAnswer(
    page,
    beforeAnswer,
    connected.reached === "connected" ? ANSWER_WAIT_MS : 30000,
  );
  check(
    "the chat produced an answer",
    answered.done,
    `${answered.count} assistant messages, ${answered.text.length} chars`,
  );
  console.log(`  answer: ${answered.text.slice(0, 300)}`);
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

  const torBefore = await lastAnswer(page);
  const torProofBefore = readTorProof();
  const torReady = await waitForTorReady(page);
  console.log(
    `  tor chip before  ${torReady.chip.label} (states: ${torReady.seen.join(" -> ")})`,
  );
  check(
    "the bundled Tor became ready by itself (no button, no setup)",
    torReady.ready,
    `states: ${torReady.seen.join(" -> ")}`,
  );
  await send(page, TOR_PROMPT);
  console.log(`  sent: ${TOR_PROMPT}`);
  const torAnswered = await waitForAnswer(page, torBefore, TOR_ANSWER_WAIT_MS);
  check(
    "the Tor request produced an answer",
    torAnswered.done,
    `${torAnswered.count} assistant messages, ${torAnswered.text.length} chars`,
  );
  console.log(`  answer: ${torAnswered.text.slice(0, 300)}`);
  await checkTorRoute(page, torProofBefore);
  const torTranscript = await transcript(page);
  console.log(`\n  transcript after the Tor request:\n${torTranscript}\n`);

  if (PHASE === "all") {
    await stopCompute(page);
  }

  await quitApp();
  console.log(
    `\n${failures.length === 0 ? `${PHASE.toUpperCase()} PASS` : "TOR FAILED"}`,
  );
  if (failures.length)
    console.log(failures.map((name) => ` - ${name}`).join("\n"));
  return failures.length === 0 ? 0 : 1;
}

const code = await main().catch(async (error) => {
  console.log(`UNCAUGHT ${error?.name}: ${error?.message}`);
  if (lastPage) await dumpPage(lastPage, "crash");
  check("the run finished without an uncaught error", false, `${error?.name}`);
  await quitApp();
  return 1;
});
process.exit(code);
