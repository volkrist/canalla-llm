// Navigation must never wait for a generation. The sidebar is the only way to open a new
// chat, switch chats or reach Settings, so none of those controls may be disabled by the
// busy flag: a model that streams for minutes must not freeze the app around it.

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import Sidebar from "../components/Sidebar";
import type { Chat, User } from "../types";

const chat: Chat = {
  id: "chat-1",
  title: "Длинная задача",
  pinned: false,
  project_id: null,
  created_at: "2026-10-20T10:00:00Z",
  updated_at: "2026-10-20T10:00:00Z",
};

const user: User = {
  id: "user-1",
  email: "owner@example.com",
  display_name: "Owner",
  role: "user",
  created_at: "2026-10-20T10:00:00Z",
};

function render(busy: boolean) {
  return renderToStaticMarkup(
    <Sidebar
      chats={[chat]}
      selected={chat.id}
      user={user}
      busy={busy}
      open
      onClose={() => {}}
      onSelect={() => {}}
      onDelete={() => {}}
      onUpdate={async () => {}}
      onExport={() => {}}
      onUsage={() => {}}
      onAdmin={() => {}}
      onSettings={() => {}}
      onLogout={() => {}}
    />,
  );
}

describe("sidebar navigation", () => {
  it("keeps New Chat, chat switching and Settings enabled while busy", () => {
    const html = render(true);
    expect(html).not.toMatch(/<button[^>]*class="new-chat"[^>]*disabled/);
    expect(html).not.toMatch(/<button[^>]*class="chat-select"[^>]*disabled/);
    expect(html).toMatch(
      /<button class="settings-link"><svg[^>]*>[\s\S]*?Settings<\/button>/,
    );
    expect(html).not.toMatch(/class="settings-link"[^>]*disabled/);
  });

  it("still disables the destructive chat actions while busy", () => {
    const html = render(true);
    expect(html).toMatch(/aria-label="Удалить Длинная задача"[^>]*disabled/);
  });
});
