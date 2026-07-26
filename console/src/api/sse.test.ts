import { describe, expect, it, vi } from "vitest";

import { streamJsonSse } from "./sse";

vi.mock("./authHeaders", () => ({
  buildAuthHeaders: () => ({
    Authorization: "Bearer test-token",
    "X-Agent-Id": "agent-1",
  }),
}));

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `http://localhost${path}`,
}));

describe("streamJsonSse", () => {
  it("sends auth headers and parses fragmented events", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"ev'));
        controller.enqueue(encoder.encode('ent","sequence":1}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(body, { status: 200 }));
    const messages: Array<Record<string, unknown>> = [];

    await streamJsonSse(
      "/research/runs/run-1/stream",
      new AbortController().signal,
      (message) => {
        messages.push(message);
      },
    );

    expect(messages).toEqual([{ type: "event", sequence: 1 }]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/research/runs/run-1/stream",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer test-token",
          "X-Agent-Id": "agent-1",
        },
      }),
    );
  });
});
