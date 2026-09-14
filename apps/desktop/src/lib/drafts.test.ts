import { afterEach, expect, test, vi } from "vitest";
import { clearDrafts, draftPrefix, readDraft, writeDraft } from "./drafts";
afterEach(() => vi.unstubAllGlobals());
test("drafts are isolated by backend, user and chat; clearing one account keeps others", () => {
  const storage: Record<string, string> = {};
  vi.stubGlobal(
    "localStorage",
    new Proxy(storage, {
      get(target, key) {
        if (key === "getItem") return (name: string) => target[name] || null;
        if (key === "setItem")
          return (name: string, value: string) => {
            target[name] = value;
          };
        if (key === "removeItem")
          return (name: string) => {
            delete target[name];
          };
        return target[key as string];
      },
    }),
  );
  const alice = draftPrefix("http://localhost:8000", "alice"),
    bob = draftPrefix("http://localhost:8000", "bob");
  writeDraft(alice, "chat", "Alice secret");
  writeDraft(bob, "chat", "Bob secret");
  expect(readDraft(alice, "chat")).toBe("Alice secret");
  expect(readDraft(alice, null)).toBe("");
  expect(readDraft(draftPrefix("https://other.example", "alice"), "chat")).toBe(
    "",
  );
  clearDrafts(alice);
  expect(readDraft(alice, "chat")).toBe("");
  expect(readDraft(bob, "chat")).toBe("Bob secret");
});
