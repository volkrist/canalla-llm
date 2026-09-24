// Settings → Обновления, with a refused manifest: the refusal has to be *visible* - a typed alert on
// the panel - and the panel must offer no way to download or install it. Rendered with
// `renderToStaticMarkup` because this suite has no DOM (the same way the other panel tests run).

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import UpdatesPanel from "../components/UpdatesPanel";
import type { UpdateState, UpdateStore } from "../hooks/useUpdates";

function store(state: Partial<UpdateState>): UpdateStore {
  const full: UpdateState = {
    phase: "idle",
    current: "1.1.0",
    available: null,
    refusal: null,
    progress: 0,
    message: "",
    checkedAt: null,
    ...state,
  };
  return {
    state: full,
    checkNow: async () => full,
    download: async () => {},
    install: async () => false,
  };
}

function panel(state: Partial<UpdateState>): string {
  return renderToStaticMarkup(
    <UpdatesPanel
      autoCheck
      onAutoCheck={() => {}}
      busy={false}
      updates={store(state)}
    />,
  );
}

describe("a refused update in Settings", () => {
  it("shows the refusal with its typed reason instead of a silent 'no update'", () => {
    const markup = panel({
      phase: "refused",
      refusal: "version_mismatch",
      message: "Обновление отклонено: подпись выдана не для этой версии.",
    });

    expect(markup).toContain('data-testid="update-refused"');
    expect(markup).toContain('data-reason="version_mismatch"');
    expect(markup).toContain("Обновление отклонено");
  });

  it("offers neither a download nor an install for a refused manifest", () => {
    const markup = panel({
      phase: "refused",
      refusal: "artifact_name_mismatch",
      message:
        "Обновление отклонено: имя в подписи не совпадает с адресом загрузки.",
    });

    expect(markup).not.toContain('data-testid="update-install"');
    expect(markup).not.toContain("Скачать");
    expect(markup).not.toContain("Перезапустить и обновить");
  });

  it("still offers the download for a bound version (the control for the assertions above)", () => {
    const markup = panel({
      phase: "available",
      available: { version: "1.2.0", notes: null, publishedAt: null },
    });

    expect(markup).toContain("Скачать 1.2.0");
    expect(markup).not.toContain("update-refused");
  });
});
