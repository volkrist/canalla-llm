// Linux packaging guard (STEP 4A).
//
// The Windows bundle is described in `tauri.conf.json`; Linux gets an overlay, `tauri.linux.conf.json`,
// which Tauri merges over it when it builds for this platform. Two things can go wrong there and
// neither is visible on a Windows machine: a Windows-only resource name that the Linux build then
// cannot find (or worse, ships as a stray `.exe`), and a staging script that reaches a Linux
// checkout with CRLF line endings - which is not a shell script at all, and fails the build with a
// message about a missing file.
//
// This test reads the configuration with node:fs, so it is a Node-context test like its Windows
// counterpart and runs under vitest only.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const tauriRoot = path.resolve(here, "..", "..", "src-tauri");
const linuxConfigPath = path.join(tauriRoot, "tauri.linux.conf.json");
const stageScriptPath = path.join(tauriRoot, "stage-native-runtime.sh");

function linuxConfig(): {
  build: { beforeBundleCommand?: string };
  bundle: {
    targets: string[];
    icon: string[];
    resources: string[];
    linux: { deb: { depends: string[] } };
  };
} {
  return JSON.parse(fs.readFileSync(linuxConfigPath, "utf8"));
}

describe("the Linux bundle describes a Linux package", () => {
  it("builds a deb, which is the artifact the baseline promises", () => {
    const targets = linuxConfig().bundle.targets;
    expect(targets).toContain("deb");
    expect(targets).not.toContain("nsis");
  });

  it("ships no Windows-only resource name", () => {
    const resources = linuxConfig().bundle.resources;
    expect(resources.length).toBeGreaterThan(0);
    for (const resource of resources) {
      expect(resource).not.toMatch(/\.exe$/i);
      expect(resource).not.toMatch(/\.cmd$/i);
    }
    // The pieces the runtime acceptance needs are in the package, not merely in the tree.
    expect(resources).toContain("sidecar/alex-backend");
    expect(resources).toContain("runtime/tor");
  });

  it("uses PNG icons, which is what the Linux bundler reads", () => {
    const icons = linuxConfig().bundle.icon;
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      expect(icon).toMatch(/\.png$/);
      expect(fs.existsSync(path.join(tauriRoot, icon))).toBe(true);
    }
  });

  it("declares the one dependency the product cannot install for itself", () => {
    // `secret-tool` is libsecret's client: the credential store talks to the Secret Service through
    // it, and a package that omits it would install and then refuse to pair with a typed error.
    expect(linuxConfig().bundle.linux.deb.depends).toContain("libsecret-tools");
  });

  it("runs a staging script that exists and is a real shell script", () => {
    const command = linuxConfig().build.beforeBundleCommand ?? "";
    expect(command).toContain("stage-native-runtime.sh");
    const script = fs.readFileSync(stageScriptPath, "utf8");
    // A CRLF shell script fails on Linux with "No such file or directory", which is exactly the
    // build failure this repository already hit once.
    expect(script.includes("\r\n")).toBe(false);
    expect(script.startsWith("#!/usr/bin/env bash")).toBe(true);
    expect(script).toContain("fetch-tor-runtime.py");
    // It refuses to bundle a package whose backend is missing.
    expect(script).toContain("missing resource");
  });
});
