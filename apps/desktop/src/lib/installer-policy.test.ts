// Uninstall / over-install policy guard (upgrade safety slice).
//
// Read the installer configuration and the generated NSIS script with node:fs, so this is a
// Node-context test: it is excluded from the browser TypeScript project (tsconfig.json) and
// runs under vitest only. Keep the imports on `node:` specifiers.
// The promise is: uninstalling removes the program, never the user's data, and never a
// Windows Credential Manager entry. This test reads the installer configuration and the
// generated NSIS script, so a future edit that would delete `%LOCALAPPDATA%\Alex LLM` or
// enable `deleteAppDataOnUninstall` fails here instead of on a user's machine.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const tauriRoot = path.resolve(here, "..", "..", "src-tauri");
const configPath = path.join(tauriRoot, "tauri.conf.json");
const hooksPath = path.join(tauriRoot, "nsis", "hooks.nsh");
const generatedPath = path.join(
  tauriRoot,
  "target",
  "release",
  "nsis",
  "x64",
  "installer.nsi",
);

function read(file: string): string {
  return fs.readFileSync(file, "utf8");
}

describe("installer policy", () => {
  it("installs per user into Programs, never into the data root", () => {
    const config = JSON.parse(read(configPath));
    expect(config.bundle.windows.nsis.installMode).toBe("currentUser");
    const hooks = read(hooksPath);
    expect(hooks).toContain("$LOCALAPPDATA\\Programs\\${PRODUCTNAME}");
    expect(hooks).toMatch(/NSIS_HOOK_PREINSTALL/);
  });

  it("has no uninstall hook that touches user data or credentials", () => {
    const hooks = read(hooksPath);
    const preUninstall = hooks.split("NSIS_HOOK_PREUNINSTALL")[1] ?? "";
    const postUninstall = hooks.split("NSIS_HOOK_POSTUNINSTALL")[1] ?? "";
    for (const body of [preUninstall, postUninstall]) {
      expect(body).not.toMatch(/RMDir|Delete|rmdir|cmdkey|CredDelete/i);
      expect(body).not.toMatch(/Alex LLM(?![/\\])/);
    }
  });

  it("never asks NSIS to delete application data on uninstall", () => {
    const config = JSON.parse(read(configPath));
    expect(config.bundle.windows.nsis.deleteAppDataOnUninstall ?? false).toBe(
      false,
    );
  });

  it("keeps the generated uninstaller away from the data root", () => {
    if (!fs.existsSync(generatedPath)) {
      // A fresh checkout has no generated script; the configuration checks above still hold.
      return;
    }
    const generated = read(generatedPath);
    const uninstall = generated.slice(generated.indexOf("Section Uninstall"));
    expect(uninstall.length).toBeGreaterThan(0);
    // Everything the uninstaller removes is addressed through $INSTDIR.
    expect(uninstall).not.toMatch(/Alex LLM\\data|Alex LLM\\runtime|cmdkey/i);
    expect(uninstall).toMatch(/\$INSTDIR/);
    // The only wholesale directory removals are Tauri's own bundle-id folders, never the
    // product data root (`%LOCALAPPDATA%\Alex LLM`) or a credential store.
    const wholesale = uninstall
      .split(/\r?\n/)
      .filter((line) => /RmDir \/r|RMDir \/r/.test(line));
    expect(wholesale.length).toBeGreaterThan(0);
    for (const line of wholesale) {
      expect(line).toContain("${BUNDLEID}");
      expect(/Alex LLM\\?(?!Programs)/.test(line)).toBe(false);
    }
  });
});
