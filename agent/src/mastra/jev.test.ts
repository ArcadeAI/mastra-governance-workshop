import { afterAll, afterEach, beforeEach, describe, expect, test } from "bun:test";
import { fileURLToPath } from "node:url";

// Synthetic configuration only. Every provider request is intercepted at HTTP.
process.env.TYPESAFE_API_KEY = "synthetic-local-test-key";
const { check, guardTools } = await import("./jev");
const originalFetch = globalThis.fetch;
let providerReply: unknown;
let providerError: Error | undefined;
let requests: Array<{ state: { evidence: string; outbound_message: string } }>;

beforeEach(() => {
  requests = [];
  providerError = undefined;
  providerReply = { answers: { grounded: { type: "noul", noul: 0.95 }, leaks_sensitive: { type: "noul", noul: 0.01 } } };
  globalThis.fetch = (async (_url: unknown, init: RequestInit) => {
    requests.push(JSON.parse(String(init.body)));
    if (providerError) throw providerError;
    return Response.json(providerReply);
  }) as typeof fetch;
});
afterEach(() => { globalThis.fetch = originalFetch; });
afterAll(() => { delete process.env.TYPESAFE_API_KEY; });

describe("outbound message checks with the real classifier and provider", () => {
  test("allows supported text below the sensitive-data threshold", async () => {
    expect(await check("A sample loan", [{ loan_id: "fixture", amount: 15500 }])).toEqual({ ok: true, grounded: 0.95, leak: 0.01 });
    expect(requests).toHaveLength(1);
    expect(requests[0].state).toEqual({ evidence: JSON.stringify([{ loan_id: "fixture", amount: 15500 }]), outbound_message: "A sample loan" });
  });

  test.each([
    [0.95, 0.21, "sensitive data"],
    [0.79, 0.01, "supported"],
    [1.1, 0.01, "could not be reached"],
    [0.95, -0.1, "could not be reached"],
  ])("blocks invalid or unsafe probabilities (%s, %s)", async (grounded, leak, reason) => {
    providerReply = { answers: { grounded: { type: "noul", noul: grounded }, leaks_sensitive: { type: "noul", noul: leak } } };
    const result = await check("fixture", []);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain(reason);
  });

  test("accepts exactly the configured threshold boundaries", async () => {
    providerReply = { answers: { grounded: { type: "noul", noul: 0.8 }, leaks_sensitive: { type: "noul", noul: 0.2 } } };
    expect((await check("fixture", [])).ok).toBe(true);
  });

  test("blocks incomplete answers", async () => {
    providerReply = { answers: { grounded: { type: "noul", noul: 0.95 } } };
    expect((await check("fixture", [])).ok).toBe(false);
  });

  test("blocks when the provider fails", async () => {
    providerError = new Error("synthetic provider failure");
    const result = await check("fixture", []);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("so the message was not sent");
  });

  test("blocks an aborted provider request", async () => {
    providerError = new DOMException("synthetic timeout", "TimeoutError");
    expect((await check("fixture", [])).ok).toBe(false);
  });
});

test("without a Jev key the earlier workshop exercises pass through without an HTTP request", () => {
  const result = Bun.spawnSync({
    cmd: [process.execPath, "--no-env-file", "-e", 'globalThis.fetch = () => { throw new Error("unexpected HTTP request"); }; const { guardTools, check, jevEnabled } = await import("./src/mastra/jev.ts"); const tools = { Slack_SendMessage: { execute: async () => ({ sent: true }) } }; if (jevEnabled() || guardTools(tools) !== tools || !(await check("hello", [])).ok) process.exit(1);'],
    cwd: fileURLToPath(new URL("../../", import.meta.url)),
    env: { PATH: process.env.PATH ?? "", HOME: "/private/tmp" },
  });
  expect(result.exitCode).toBe(0);
});

describe("tool execution", () => {
  test("includes records actually read in the evidence sent to Jev", async () => {
    const evidence: unknown[] = [];
    const wrapped = guardTools({
      LoanDesk_GetLoan: { execute: async () => ({ loan_id: "fixture", amount: 15500 }) },
      LoanDesk_RequestApproval: { execute: async (_input?: unknown) => ({ request_id: "local-fixture" }) },
    }, evidence);
    await wrapped.LoanDesk_GetLoan.execute();
    expect(await wrapped.LoanDesk_RequestApproval.execute({ justification: "A grounded fixture" })).toEqual({ request_id: "local-fixture" });
    expect(requests[0].state.evidence).toContain('"amount":15500');
  });

  test("a blocked nested message never reaches the outbound tool", async () => {
    providerReply = { answers: { grounded: { type: "noul", noul: 0.95 }, leaks_sensitive: { type: "noul", noul: 0.99 } } };
    const delivered: unknown[] = [];
    const wrapped = guardTools({ Slack_SendMessage: { execute: async (input: unknown) => { delivered.push(input); return { sent: true }; } } });
    const result = await wrapped.Slack_SendMessage.execute({ nested: { text: "synthetic sensitive text" } });
    expect(result).toMatchObject({ blocked_by: "Jev (TypeSafe)", sent: false });
    expect(delivered).toEqual([]);
    expect(requests[0].state.outbound_message).toContain("nested.text: synthetic sensitive text");
  });

  test("evidence is separate between runs", async () => {
    const tools = { LoanDesk_GetLoan: { execute: async () => ({ loan_id: "fixture" }) }, LoanDesk_RequestApproval: { execute: async (_input?: unknown) => ({ sent: true }) } };
    const first = guardTools(tools);
    await first.LoanDesk_GetLoan.execute();
    const second = guardTools(tools);
    await second.LoanDesk_RequestApproval.execute({ justification: "another run" });
    expect(requests[0].state.evidence).toBe("No loan record was read in this run.");
  });
});
