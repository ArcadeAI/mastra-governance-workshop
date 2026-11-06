import { afterAll, beforeEach, expect, test } from "bun:test";

const originalFetch = globalThis.fetch;
const previousKey = process.env.ARCADE_API_KEY;
const previousBase = process.env.ARCADE_BASE_URL;
process.env.ARCADE_API_KEY = "synthetic-arcade-discovery-test-key";
process.env.ARCADE_BASE_URL = "https://engine.example.test";
const { select, execute } = await import("./arcade");

const officer = "officer@example.test";
const approve = {
  name: "ApproveLoan", fully_qualified_name: "LoanDesk.ApproveLoan@1.0.0", qualified_name: "LoanDesk.ApproveLoan",
  toolkit: { name: "LoanDesk", version: "1.0.0" }, description: "Approve a business loan",
  input: { parameters: [{ name: "loan_id", required: true, value_schema: { val_type: "string" } }] },
};
let slackCatalog: unknown[];
let catalog: unknown[], recommended: unknown[], catalogStatus: number, selectionStatus: number;
let listedUsers: Array<string | null>;
let executions: Array<Record<string, unknown>>;

beforeEach(() => {
  catalog = [approve];
  slackCatalog = [];
  recommended = [{ tool_name: "ZohoBooks.ApproveBill", input_schema: [] }];
  catalogStatus = 200;
  selectionStatus = 200;
  listedUsers = [];
  executions = [];
  // Mock only HTTP: selection, SDK pagination and schema normalization remain real.
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
    if (url.pathname === "/v1/tools") {
      listedUsers.push(url.searchParams.get("user_id"));
      const items = url.searchParams.get("toolkit") === "Slack" ? slackCatalog : catalog;
      return catalogStatus === 200
        ? Response.json({ items, total_count: items.length, offset: 0 })
        : Response.json({ message: "catalog unavailable" }, { status: catalogStatus });
    }
    if (url.pathname === "/v1/tools/select") return selectionStatus === 200
      ? Response.json({ query_id: "fixture-selection", results: [{ suggestions: recommended }] })
      : Response.json({ message: "selection unavailable" }, { status: selectionStatus });
    if (url.pathname === "/v1/tools/execute") {
      executions.push(JSON.parse(String(init?.body)));
      return Response.json({ success: false, output: { error: { kind: "CONTEXT_DENIED", message: "Above the officer clearance" } } });
    }
    throw new Error("Unexpected network request in the discovery test");
  }) as typeof fetch;
});
afterAll(() => {
  globalThis.fetch = originalFetch;
  if (previousKey === undefined) delete process.env.ARCADE_API_KEY; else process.env.ARCADE_API_KEY = previousKey;
  if (previousBase === undefined) delete process.env.ARCADE_BASE_URL; else process.env.ARCADE_BASE_URL = previousBase;
});

test("loan approval remains available when recommendations return unrelated accounting tools", async () => {
  const result = await select(["Approve loan LN-1291"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).toEqual(["LoanDesk.ApproveLoan", "ZohoBooks.ApproveBill"]);
  expect(result.tools[0].version).toBe("1.0.0");
  expect(result.tools[0].input_schema).toEqual(approve.input.parameters);
  expect(listedUsers).toEqual([officer]);
});

test("catalog tools remain available during a recommendation outage", async () => {
  selectionStatus = 400;
  const result = await select(["Approve loan LN-1291"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).toEqual(["LoanDesk.ApproveLoan"]);
});

test("an access-denied catalog contributes no tools", async () => {
  catalogStatus = 403;
  const result = await select(["Send a message"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).toEqual(["ZohoBooks.ApproveBill"]);
});

test("deduplicates versioned catalog and recommendation names while retaining other workflows", async () => {
  recommended = [{ tool_name: "LoanDesk.ApproveLoan", input_schema: [] }, { tool_name: "Slack.SendMessage", input_schema: [] }];
  slackCatalog = [{ fully_qualified_name: "Slack.SendMessage@6.2.0", toolkit: { name: "Slack", version: "6.2.0" }, input: { parameters: [{ name: "messages", required: true, value_schema: { val_type: "array" } }] } }];
  const result = await select(["Approve the loan and tell the room"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).toEqual(["LoanDesk.ApproveLoan", "Slack.SendMessage"]);
  expect(result.tools[0].input_schema).toEqual(approve.input.parameters);
});

test("does not include an unrelated toolkit from a misfiltered catalog response", async () => {
  catalog.push({ ...approve, fully_qualified_name: "OtherToolkit.ApproveLoan@1.0.0", toolkit: { name: "OtherToolkit" } });
  const result = await select(["Approve the loan"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).not.toContain("OtherToolkit.ApproveLoan");
});

test("catalog-discovered approvals still receive Arcade policy refusals as the signed-in user", async () => {
  const selected = await select(["Approve loan LN-1291"], officer);
  const approval = selected.tools.find((tool) => tool.tool_name === "LoanDesk.ApproveLoan");
  expect(approval).toBeDefined();
  const result = await execute(approval!, { loan_id: "LN-1291", amount: 95000 }, selected.queryId, officer);
  expect(result).toEqual({ error: "Above the officer clearance" });
  expect(executions).toHaveLength(1);
  expect(executions[0]).toMatchObject({ tool_name: "LoanDesk.ApproveLoan", user_id: officer, input: { loan_id: "LN-1291", amount: 95000 } });
});


test("Slack sends use the catalog's current version and batch schema, not a stale recommendation", async () => {
  recommended = [{ tool_name: "Slack.SendMessage", input_schema: [{ name: "message", required: true }] }];
  const parameters = [{ name: "messages", required: true, value_schema: { val_type: "array", inner_val_type: "json", inner_properties: { conversation: { val_type: "string" }, text: { val_type: "string" } }, inner_required_keys: ["conversation", "text"] } }];
  slackCatalog = [{ fully_qualified_name: "Slack.SendMessage@6.2.0", toolkit: { name: "Slack", version: "6.2.0" }, input: { parameters } }];
  const result = await select(["Post to Slack"], officer);
  const slack = result.tools.find((tool) => tool.tool_name === "Slack.SendMessage")!;
  expect(slack.version).toBe("6.2.0");
  expect(slack.input_schema).toEqual(parameters);
  await execute(slack, { messages: [{ conversation: "#workshop", text: "Hello" }] }, result.queryId, officer);
  expect(executions[0]).toMatchObject({ tool_name: "Slack.SendMessage", tool_version: "6.2.0", input: { messages: [{ conversation: "#workshop", text: "Hello" }] } });
});

test("does not offer a stale Slack send when the current catalog entry is unavailable", async () => {
  recommended = [{ tool_name: "Slack.SendMessage", input_schema: [{ name: "message" }] }];
  const result = await select(["Post to Slack"], officer);
  expect(result.tools.some((tool) => tool.tool_name === "Slack.SendMessage")).toBe(false);
});
