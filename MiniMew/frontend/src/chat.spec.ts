import { expect, it } from "vitest";
import { parseMessagePage, parseRun } from "./chat";
import { ids, messages, run, time } from "../tests/fixtures/chat";

it("接收正式 Message / Run 字段，不把 Run 信息拼成 Message", () => {
  expect(
    parseMessagePage({ items: messages, previous_cursor: null }, ids.thread)
      .items,
  ).toEqual(messages);
  expect(parseRun(run, ids.thread, ids.run)).toEqual(run);
});
it("拒绝错序、重复、越界游标及错误 Thread 的历史", () => {
  for (const value of [
    { items: [...messages].reverse(), previous_cursor: null },
    { items: [messages[0], messages[0]], previous_cursor: null },
    { items: messages, previous_cursor: 99 },
    { items: [{ ...messages[0], thread_id: ids.run }], previous_cursor: null },
  ])
    expect(() => parseMessagePage(value, ids.thread)).toThrow();
  expect(() =>
    parseMessagePage({ items: messages, previous_cursor: null }, ids.thread, 2),
  ).toThrow();
});
it("Run 查询核对身份及终态，拒绝未知错误码和不完整终态", () => {
  expect(() =>
    parseRun({ ...run, id: ids.thread }, ids.thread, ids.run),
  ).toThrow();
  expect(() => parseRun({ ...run, status: "succeeded" }, ids.thread)).toThrow();
  expect(() =>
    parseRun(
      { ...run, status: "failed", finished_at: time, error_code: "raw error" },
      ids.thread,
    ),
  ).toThrow();
  expect(
    parseRun(
      {
        ...run,
        status: "cancelled",
        finished_at: time,
        cancel_requested_at: time,
      },
      ids.thread,
    ).status,
  ).toBe("cancelled");
});
