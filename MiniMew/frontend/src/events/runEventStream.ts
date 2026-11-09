import { ApiError } from "../api";
import { EventProtocolError, parseRunEvent } from "./runEventContract";
import type { RunEvent } from "../generated/run-event-v1";

export class SseDecoder {
  private buffer = "";
  private name = "";
  private data: string[] = [];
  private size = 0;

  push(chunk: string): RunEvent[] {
    this.buffer += chunk;
    if (this.buffer.length + this.size > 1000000)
      throw new EventProtocolError();
    const events: RunEvent[] = [];
    while (true) {
      const index = this.buffer.search(/[\r\n]/);
      if (
        index < 0 ||
        (index === this.buffer.length - 1 && this.buffer[index] === "\r")
      )
        break;
      const line = this.buffer.slice(0, index);
      const length =
        this.buffer[index] === "\r" && this.buffer[index + 1] === "\n" ? 2 : 1;
      this.buffer = this.buffer.slice(index + length);
      if (line === "") {
        if (this.data.length) {
          let value: unknown;
          try {
            value = JSON.parse(this.data.join("\n"));
          } catch {
            throw new EventProtocolError();
          }
          const event = parseRunEvent(value);
          if (this.name !== event.type) throw new EventProtocolError();
          events.push(event);
        }
        this.name = "";
        this.data = [];
        this.size = 0;
      } else if (!line.startsWith(":")) {
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") this.name = value;
        if (field === "data") {
          this.data.push(value);
          this.size += value.length;
        }
      }
    }
    return events;
  }
  finish(): RunEvent[] {
    const events = this.buffer.endsWith("\r") ? this.push("\n") : [];
    if (this.buffer || this.data.length) throw new EventProtocolError();
    return events;
  }
}

// SSE 拥有独立的空闲预算，不能套用普通 JSON 请求的 15 秒总超时。
export async function readRunEvents(
  url: string,
  token: string,
  signal: AbortSignal,
  consume: (event: RunEvent) => boolean,
  idleMilliseconds = 45000,
): Promise<"eof" | "consumed"> {
  const controller = new AbortController();
  const combined = AbortSignal.any([signal, controller.signal]);
  let timer: ReturnType<typeof setTimeout>;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), idleMilliseconds);
  };
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  arm();
  try {
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "text/event-stream",
      },
      credentials: "same-origin",
      cache: "no-store",
      signal: combined,
    });
    if (!response.ok) throw new ApiError(response.status);
    if (response.status === 204) return "consumed";
    if (
      !response.headers
        .get("content-type")
        ?.toLowerCase()
        .startsWith("text/event-stream") ||
      !response.body
    )
      throw new EventProtocolError();
    reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const frames = new SseDecoder();
    while (true) {
      const chunk = await reader.read();
      arm();
      if (combined.aborted) throw new DOMException("Aborted", "AbortError");
      for (const event of frames.push(
        decoder.decode(chunk.value, { stream: !chunk.done }),
      ))
        if (!consume(event)) return "eof";
      if (chunk.done) {
        for (const event of frames.finish()) if (!consume(event)) break;
        return "eof";
      }
    }
  } finally {
    clearTimeout(timer!);
    controller.abort();
    if (reader) {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
}

export function waitForRetry(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, milliseconds);
    signal.addEventListener("abort", done, { once: true });
  });
}
