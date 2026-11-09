import { expect, it, vi, afterEach } from "vitest";
import { parseRunEvent } from "./runEventContract";
import { readRunEvents, SseDecoder } from "./runEventStream";
import {
  body,
  eventResponse,
  frame,
  started,
  success,
} from "../../tests/fixtures/chat";

afterEach(() => vi.unstubAllGlobals());
it("按真源校验全部正常事件，不接受额外字段和未声明类型", () => {
  for (const item of success) expect(parseRunEvent(item)).toEqual(item);
  expect(() => parseRunEvent({ ...body, secret: "x" })).toThrow();
  expect(() => parseRunEvent({ ...body, type: "new.hidden" })).toThrow();
  expect(() =>
    parseRunEvent({ ...body, data: { ...body.data, content: 1 } }),
  ).toThrow();
  expect(() =>
    parseRunEvent({
      ...body,
      data: { ...body.data, content: "x".repeat(4097) },
    }),
  ).toThrow();
});
it.each([0, 1.5, Number.MAX_SAFE_INTEGER + 1])(
  "拒绝不安全事件序号 %s",
  (sequence) => {
    expect(() => parseRunEvent({ ...body, sequence })).toThrow();
  },
);
it.each([
  "bad",
  "2026-02-30T00:00:00Z",
  "2026-11-09T00:00:00",
  "2026-11-09T24:00:00Z",
])("拒绝无效时间 %s", (timestamp) => {
  expect(() => parseRunEvent({ ...body, timestamp })).toThrow();
});
it("任意字符边界与 CRLF 拆分均重建相同事件，心跳不产生业务事件", () => {
  const source = `: keep-alive\r\n\r\n${success.map(frame).join("").replaceAll("\n", "\r\n")}`;
  const decoder = new SseDecoder();
  const actual = [...source].flatMap((character) => decoder.push(character));
  actual.push(...decoder.finish());
  expect(actual).toEqual(success);
});
it("支持多行 data，但拒绝帧类型不符、截断正文和过大帧", () => {
  const decoder = new SseDecoder();
  const formatted = JSON.stringify(body, null, 2)
    .split("\n")
    .map((line) => `data: ${line}`)
    .join("\n");
  expect(decoder.push(`event: message.delta\n${formatted}\n\n`)).toEqual([
    body,
  ]);
  expect(() =>
    new SseDecoder().push(
      `event: run.failed\ndata: ${JSON.stringify(body)}\n\n`,
    ),
  ).toThrow();
  const partial = new SseDecoder();
  partial.push(frame(body).slice(0, -1));
  expect(() => partial.finish()).toThrow();
  expect(() => new SseDecoder().push("x".repeat(1000001))).toThrow();
});
it("真实 fetch 入口携带 Bearer，不使用 Last-Event-ID；UTF-8 可跨字节拆分", async () => {
  const bytes = new TextEncoder().encode(frame(body));
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      new ReadableStream({
        start(c) {
          for (const byte of bytes) c.enqueue(new Uint8Array([byte]));
          c.close();
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const consumed: unknown[] = [];
  await readRunEvents(
    "/stream?after=2",
    "memory-token",
    new AbortController().signal,
    (item) => {
      consumed.push(item);
      return true;
    },
  );
  expect(consumed).toEqual([body]);
  expect(fetch.mock.calls[0]![1].headers).toEqual({
    Authorization: "Bearer memory-token",
    Accept: "text/event-stream",
  });
  expect(fetch.mock.calls[0]![0]).toBe("/stream?after=2");
});
it("401、非 SSE 响应明确拒绝，204 只报告已消费", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 401 }))
    .mockResolvedValueOnce(
      new Response("{}", { headers: { "Content-Type": "application/json" } }),
    )
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  const consume = vi.fn(() => true);
  const signal = new AbortController().signal;
  await expect(readRunEvents("/s", "t", signal, consume)).rejects.toMatchObject(
    { status: 401 },
  );
  await expect(readRunEvents("/s", "t", signal, consume)).rejects.toThrow(
    "Invalid event",
  );
  await expect(readRunEvents("/s", "t", signal, consume)).resolves.toBe(
    "consumed",
  );
  expect(consume).not.toHaveBeenCalled();
});
it("消费端结束时停止后续事件并关闭来源", async () => {
  const fetch = vi.fn().mockResolvedValue(eventResponse([...started, body]));
  vi.stubGlobal("fetch", fetch);
  const consume = vi.fn(() => false);
  await readRunEvents("/s", "t", new AbortController().signal, consume);
  expect(consume).toHaveBeenCalledTimes(1);
});
