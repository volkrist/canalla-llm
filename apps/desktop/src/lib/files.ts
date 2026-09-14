import { isTauri } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { Api } from "./api";

export async function exportChats(
  api: Api,
  format: "json" | "markdown",
  id?: string,
) {
  const content = await api.exportChats(format, id);
  const extension = format === "json" ? "json" : "md";
  const filename = `alex-${id || "chats"}.${extension}`;
  if (isTauri()) {
    const path = await save({
      defaultPath: filename,
      filters: [
        {
          name: format === "json" ? "JSON" : "Markdown",
          extensions: [extension],
        },
      ],
    });
    if (path) await writeTextFile(path, content);
  } else {
    const url = URL.createObjectURL(
      new Blob([content], {
        type: format === "json" ? "application/json" : "text/markdown",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export async function openExternal(href: string) {
  const url = new URL(href);
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("Неподдерживаемая ссылка");
  if (isTauri()) await openUrl(url.toString());
  else window.open(url.toString(), "_blank", "noopener,noreferrer");
}
