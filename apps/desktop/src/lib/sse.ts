export interface ServerEvent {
  event: string;
  data: Record<string, unknown>;
}

// Fetch's chunks can split UTF-8 characters, lines and entire events arbitrarily.
export async function consumeSSE(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: ServerEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = () => {
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) });
    }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        dispatch();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      dispatch();
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
