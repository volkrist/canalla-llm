// Settings navigation: the 14 sections are grouped instead of one flat grid, and every
// section stays reachable exactly once. A section that silently disappears from the
// navigation is a product regression, so it is pinned here.

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import SettingsDialog, { SETTINGS_GROUPS } from "../components/SettingsDialog";
import type { Settings } from "../types";

/** The sections the navigation must expose, exactly once each. */
const EXPECTED = [
  "Общие",
  "Чат",
  "Профиль",
  "Пользователи",
  "Проекты",
  "Память",
  "AI / Compute",
  "Canalla Cloud",
  "Computer",
  "Web / Tor",
  "Резервные копии",
  "Данные",
  "Дополнительно",
  "О программе",
];

const settings: Settings = {
  backendUrl: "http://127.0.0.1:8765",
  fontSize: 15,
  theme: "dark",
  language: "ru",
  enterSends: true,
  autoScroll: true,
  timestamps: true,
  technicalDetails: false,
};

describe("settings navigation", () => {
  it("keeps every section, exactly once", () => {
    const tabs = SETTINGS_GROUPS.flatMap((group) => [...group.tabs]);
    expect(tabs).toHaveLength(EXPECTED.length);
    expect([...tabs].sort()).toEqual([...EXPECTED].sort());
    expect(new Set(tabs).size).toBe(tabs.length);
  });

  it("groups the sections under named headings", () => {
    expect(SETTINGS_GROUPS.map((group) => group.title)).toEqual([
      "Общие",
      "Личное",
      "AI и инструменты",
      "Данные",
      "Система",
    ]);
    for (const group of SETTINGS_GROUPS)
      expect(group.tabs.length).toBeGreaterThan(0);
  });

  it("keeps the account sections together, profile with general", () => {
    const general = SETTINGS_GROUPS.find((group) => group.title === "Общие");
    expect([...(general?.tabs ?? [])]).toEqual(["Общие", "Чат", "Профиль"]);
    const personal = SETTINGS_GROUPS.find((group) => group.title === "Личное");
    expect([...(personal?.tabs ?? [])]).toEqual([
      "Пользователи",
      "Проекты",
      "Память",
    ]);
  });

  it("renders the headings and the section buttons in one dialog", () => {
    const html = renderToStaticMarkup(
      <SettingsDialog value={settings} onSave={() => {}} onClose={() => {}} />,
    );
    for (const group of SETTINGS_GROUPS) {
      expect(html).toContain(group.title);
      expect(html).toContain(`Разделы настроек: ${group.title}`);
    }
    for (const tab of EXPECTED) expect(html).toContain(`>${tab}</button>`);
    expect(html).toContain("Закрыть настройки");
  });
});
