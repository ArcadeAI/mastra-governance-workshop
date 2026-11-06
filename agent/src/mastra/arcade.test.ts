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
let catalog: unknown[], recommended: unknown[], catalogStatus: number, selectionStatus: number;
let listedUsers: Array<string | null>;
let executions: Array<Record<string, unknown>>;
let definitions: Record<string, unknown>, lookups: string[];

beforeEach(() => {
  catalog = [approve];
  recommended = [{ tool_name: "ZohoBooks.ApproveBill", input_schema: [] }];
  catalogStatus = 200;
  selectionStatus = 200;
  listedUsers = [];
  executions = [];
  definitions = {};
  lookups = [];
  // Mock only HTTP: selection, SDK pagination and schema normalization remain real.
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
    if (url.pathname === "/v1/tools") {
      listedUsers.push(url.searchParams.get("user_id"));
      return catalogStatus === 200
        ? Response.json({ items: catalog, total_count: catalog.length, offset: 0 })
        : Response.json({ message: "catalog unavailable" }, { status: catalogStatus });
    }
    if (url.pathname === "/v1/tools/select") return selectionStatus === 200
      ? Response.json({ query_id: "fixture-selection", results: [{ suggestions: recommended }] })
      : Response.json({ message: "selection unavailable" }, { status: selectionStatus });
    if (url.pathname === "/v1/tools/execute") {
      executions.push(JSON.parse(String(init?.body)));
      return Response.json({ success: false, output: { error: { kind: "CONTEXT_DENIED", message: "Above the officer clearance" } } });
    }
    if (url.pathname.startsWith("/v1/tools/")) {
      const name = decodeURIComponent(url.pathname.slice("/v1/tools/".length));
      lookups.push(name);
      return definitions[name] ? Response.json(definitions[name]) : Response.json({ message: "tool not found" }, { status: 404 });
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
  const result = await select(["Approve the loan and tell the room"], officer);
  expect(result.tools.map((tool) => tool.tool_name)).toEqual(["LoanDesk.ApproveLoan", "Slack.SendMessage"]);
  expect(result.tools[0].input_schema).toEqual(approve.input.parameters);
});

test("a recommended tool gets the catalog's current schema and version, looked up once per process", async () => {
  const stale = [{ name: "channel_name", value_schema: { val_type: "string" } }, { name: "message", required: true, value_schema: { val_type: "string" } }];
  const live = [{ name: "messages", required: true, value_schema: { val_type: "array", inner_val_type: "json", inner_properties: { conversation: { val_type: "string" }, text: { val_type: "string" } }, inner_required_keys: ["conversation", "text"] } }];
  recommended = [{ tool_name: "Slack.SendMessage", input_schema: stale }];
  definitions["Slack.SendMessage"] = { name: "SendMessage", fully_qualified_name: "Slack.SendMessage@6.2.0", qualified_name: "Slack.SendMessage", toolkit: { name: "Slack", version: "6.2.0" }, input: { parameters: live } };
  const first = await select(["Post a hello in #general"], officer);
  const slack = first.tools.find((tool) => tool.tool_name === "Slack.SendMessage");
  expect(slack?.input_schema).toEqual(live);
  expect(slack?.version).toBe("6.2.0");
  await select(["Post another hello in #general"], officer);
  expect(lookups).toEqual(["Slack.SendMessage"]);
});

test("a recommended tool keeps Tool Rec's schema when the catalog lookup fails", async () => {
  const fromToolRec = [{ name: "to", required: true, value_schema: { val_type: "string" } }];
  recommended = [{ tool_name: "Gmail.SendEmail", input_schema: fromToolRec }];
  const result = await select(["Email the borrower"], officer);
  const gmail = result.tools.find((tool) => tool.tool_name === "Gmail.SendEmail");
  expect(gmail?.input_schema).toEqual(fromToolRec);
  expect(gmail?.version).toBeUndefined();
  expect(lookups).toEqual(["Gmail.SendEmail"]);
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
