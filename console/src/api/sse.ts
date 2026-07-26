import { buildAuthHeaders } from "./authHeaders";
import { getApiUrl } from "./config";

export class SseHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`SSE HTTP ${status}`);
    this.status = status;
  }
}

export async function streamJsonSse(
  path: string,
  signal: AbortSignal,
  onMessage: (message: Record<string, unknown>) => boolean | void,
): Promise<void> {
  const response = await fetch(getApiUrl(path), {
    headers: buildAuthHeaders(),
    signal,
  });
  if (!response.ok) throw new SseHttpError(response.status);
  if (!response.body) throw new Error("SSE response has no body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const payload = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (payload) {
        try {
          if (onMessage(JSON.parse(payload)) === false) {
            await reader.cancel();
            return;
          }
        } catch {
          // Ignore malformed server events; the next snapshot repairs state.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}
