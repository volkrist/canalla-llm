// Drive the installed Canalla window over CDP for the T3/T4 acceptance runs.
//
// The window belongs to the running app: `browser.close()` would kill the only WebView, so the
// process always exits with `process.exit(0)` after the command has been carried out.
//
//   node ui-task.mjs send   <prompt-file> [--stay]  type the prompt into a new chat and send it
//   node ui-task.mjs state                          report the visible task state
//   node ui-task.mjs resume                         click the product's own Resume button
//   node ui-task.mjs answer                         print the last assistant message text
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const [, , command, ...args] = process.argv;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await chromium.connectOverCDP("http://127.0.0.1:9242");
const page = browser.contexts()[0].pages()[0];
await page.bringToFront().catch(() => {});
await page.waitForLoadState("domcontentloaded").catch(() => {});

const composer = page.getByLabel("Сообщение");

async function dismissAuthScreen() {
  const retry = page.getByRole("button", { name: "Повторить", exact: true });
  if (await retry.isVisible().catch(() => false)) {
    await retry.click({ timeout: 10000 }).catch(() => {});
    await sleep(4000);
  }
}

if (command === "send") {
  const prompt = readFileSync(args[0], "utf-8");
  await dismissAuthScreen();
  if (args[1] !== "--stay") {
    // A task is never typed into whatever chat happened to be open: the acceptance work gets its
    // own chat, so a real user conversation is never touched.
    const button = page.getByRole("button", { name: /New Chat/i });
    await button.waitFor({ state: "visible", timeout: 60000 });
    await button.click();
    await sleep(1500);
  }
  await composer.waitFor({ state: "visible", timeout: 90000 });
  await composer.fill(prompt);
  await composer.press("Enter");
  console.log(JSON.stringify({ sent: true, chars: prompt.length }));
} else if (command === "state") {
  const status = await page
    .locator("section p[role=status]")
    .first()
    .innerText()
    .catch(() => "");
  const visible = async (name) =>
    page
      .getByRole("button", { name, exact: true })
      .isVisible()
      .catch(() => false);
  console.log(
    JSON.stringify({
      status,
      resume: await visible("Resume"),
      stop: await visible("Stop"),
      pause: await visible("Pause"),
    }),
  );
} else if (command === "resume") {
  const button = page.getByRole("button", { name: "Resume", exact: true });
  await button.waitFor({ state: "visible", timeout: 60000 });
  await button.click();
  console.log(JSON.stringify({ resumed: true }));
} else if (command === "answer") {
  const texts = await page
    .locator(".message.assistant .markdown, .message.assistant")
    .allInnerTexts()
    .catch(() => []);
  console.log(JSON.stringify({ answer: (texts || []).slice(-1)[0] || "" }));
} else {
  console.error("unknown command: " + command);
}
process.exit(0);
