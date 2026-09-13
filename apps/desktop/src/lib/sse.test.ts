import { describe, expect, it } from "vitest";
import { consumeSSE, type ServerEvent } from "./sse";
import { validateBackendUrl } from "./api";

describe("stream transport", () => {
  it("handles one-byte UTF-8 chunks and CRLF delimiters", async () => {
    const text =
      'event: delta\r\ndata: {"content":"Привет 🌍"}\r\n\r\nevent: done\ndata: {}\n\n';
    const bytes = new TextEncoder().encode(text);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    });
    const events: ServerEvent[] = [];
    await consumeSSE(stream, (event) => events.push(event));
    expect(events).toEqual([
      { event: "delta", data: { content: "Привет 🌍" } },
      { event: "done", data: {} },
    ]);
  });
  it("ignores SSE comments and supports multiple frames in one chunk", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(
          new TextEncoder().encode(
            ': ping\n\nevent: delta\ndata: {"content":"a"}\n\nevent: delta\ndata: {"content":"b"}\n\n',
          ),
        );
        c.close();
      },
    });
    const result: string[] = [];
    await consumeSSE(stream, (event) =>
      result.push(String(event.data.content)),
    );
    expect(result.join("")).toBe("ab");
  });
});
describe("backend address security", () => {
  it("allows local HTTP and remote HTTPS", () => {
    expect(validateBackendUrl("http://127.0.0.1:8000/")).toBe(
      "http://127.0.0.1:8000",
    );
    expect(validateBackendUrl("https://api.example.com")).toBe(
      "https://api.example.com",
    );
  });
  it.each([
    "http://example.com",
    "javascript:alert(1)",
    "https://user:pass@example.com",
    "https://example.com?key=abc",
  ])("rejects %s", (url) => {
    expect(() => validateBackendUrl(url)).toThrow();
  });
});
