// Installer migration guard (1.0).
//
// The product renamed itself to Canalla LLM, so the programme moved to
// `%LOCALAPPDATA%\Programs\Canalla LLM`. A legacy `Programs\Alex LLM` folder must be
// cleaned up by the new installer — but ONLY its programme files. The user data root
// (`%LOCALAPPDATA%\Alex LLM`), the Windows Credential Manager entries and the Gateway
// enrollment must survive every install, upgrade and uninstall.
//
// This is a Node-context test: it reads the installer configuration with node:fs, is
// excluded from the browser TypeScript project (tsconfig.json) and runs under vitest.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The one removal an uninstall is allowed to make: the programme's own login entry. User data and
// credentials live elsewhere and must survive.
const LOGIN_ENTRY_REMOVAL =
  /^DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "Canalla LLM"$/;

const here = path.dirname(fileURLToPath(import.meta.url));
const tauriRoot = path.resolve(here, "..", "..", "src-tauri");
const hooksPath = path.join(tauriRoot, "nsis", "hooks.nsh");
const configPath = path.join(tauriRoot, "tauri.conf.json");

describe("installer migration", () => {
  const hooks = fs.readFileSync(hooksPath, "utf8");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));

  it("installs the current product into its own folder", () => {
    expect(config.productName).toBe("Canalla LLM");
    expect(hooks).toContain("$LOCALAPPDATA\\Programs\\${PRODUCTNAME}");
  });

  it("removes a legacy Alex LLM programme folder, guarded by its own binary", () => {
    expect(hooks).toContain("$LOCALAPPDATA\\Programs\\Alex LLM");
    expect(hooks).toMatch(
      /IfFileExists "\$0\\alex-llm\.exe" 0 legacy_program_done/,
    );
    expect(hooks).toMatch(/RMDir \/r "\$0"/);
  });

  it("cleans legacy shortcuts, autostart and the uninstall entry", () => {
    expect(hooks).toContain('Delete "$SMPROGRAMS\\Alex LLM.lnk"');
    expect(hooks).toContain('Delete "$DESKTOP\\Alex LLM.lnk"');
    expect(hooks).toContain(
      'DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Alex LLM"',
    );
    expect(hooks).toContain(
      'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "Alex LLM"',
    );
  });

  it("never touches the user data root, credentials or the data itself", () => {
    // Only executable NSIS lines matter; comments explain the rule, they never act.
    const code = hooks
      .split(/\r?\n/)
      .filter((line) => !line.trimStart().startsWith(";"))
      .join("\n");
    // A bare `%LOCALAPPDATA%\Alex LLM` (the data root) must not appear anywhere.
    expect(/LOCALAPPDATA\}\\Alex LLM/.test(code)).toBe(false);
    expect(code).not.toMatch(/cmdkey|CredDelete|CredRead/i);
    expect(code).not.toMatch(/alex\.db|documents|runtime|credentials/i);
  });

  it("keeps the uninstall hooks free of data or credential handling", () => {
    const pre = hooks.split("NSIS_HOOK_PREUNINSTALL")[1] ?? "";
    const post = hooks.split("NSIS_HOOK_POSTUNINSTALL")[1] ?? "";
    for (const body of [pre, post]) {
      const code = body
        .split(/\r?\n/)
        .filter((line) => !line.trimStart().startsWith(";"))
        .join("\n");
      // The programme's own login entry is the one thing an uninstall may remove; anything that
      // holds the user's data or credentials stays untouched.
      const remaining = code
        .split(/\r?\n/)
        .filter((line) => !LOGIN_ENTRY_REMOVAL.test(line.trim()))
        .join("\n");
      expect(remaining).not.toMatch(/RMDir|Delete|cmdkey|CredDelete|CredRead/i);
    }
    // ...and it is removed, not merely left behind.
    expect(post).toContain(
      'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "Canalla LLM"',
    );
  });

  it("never asks NSIS to delete application data on uninstall", () => {
    expect(config.bundle.windows.nsis.deleteAppDataOnUninstall ?? false).toBe(
      false,
    );
  });
});
