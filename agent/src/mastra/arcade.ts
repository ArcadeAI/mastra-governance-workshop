/**
 * Arcade catalog discovery and Tool Rec: the Mastra counterpart of `ArcadeMiddleware` in
 * deep-agents-governance.
 *
 * The agent starts each run with one tool, `arcade_select_tools`. The model describes the task in
 * plain English. Its Loan Desk tools come from the project catalog as the signed-in user; Tool Rec
 * (`POST /v1/tools/select`) adds tools for the task. This
 * processor hands them to the model as ordinary tools on its next step, schemas and all. It is
 * the same move as Mastra's own `ToolSearchProcessor`, with Arcade's selector in place of a local
 * index: `processInputStep` runs before every model step and sets that step's tools.
 *
 * Every call goes through the Arcade engine with your project key, as the person the workshop's
 * identity provider signed in (identity.ts): the verified email is the Arcade user id. Nobody
 * signed in, no tools. Tool Rec only offers what the project's contextual access lets that user see, and the same policy is
 * checked again on every call, so a refusal comes back from Arcade, not from this file. When a
 * tool needs that user's permission for an account (Slack, Gmail, the loan API), the call answers
 * with a sign-in link, which the agent passes on.
 *
 * With no `ARCADE_API_KEY` the agent has no tools and answers from the model alone.
 */
import Arcade from "@arcadeai/arcadejs";
import type { ProcessInputStepArgs, Processor } from "@mastra/core/processors";
import { jsonSchema, tool } from "ai";
import { z } from "zod";
import { signedIn } from "./identity";
import { guardTools } from "./jev";
import { inputSchema } from "./tool-schema";

/** The one tool the model always has. */
export const SELECT = "arcade_select_tools";

/** `top_k` changes Tool Rec's ranking, not just its length: ask for at least this many, then trim. */
const MIN_TOP_K = 10;
const PER_TASK = Number(process.env.ARCADE_TOOLS_PER_TASK ?? 5);

/** One tool as Tool Rec suggests it. */
export interface Suggestion {
  tool_name: string;
  description?: string;
  input_schema?: Record<string, unknown> | unknown[];
  toolkit?: string;
  version?: string;
}

interface SelectResponse {
  query_id?: string;
  results?: { task?: string; suggestions?: Suggestion[] }[];
}

/** A suggested tool as the model sees it: `Slack.SendMessage` becomes `Slack_SendMessage`. */
const modelName = (toolName: string) => toolName.replace(/[^A-Za-z0-9_-]/g, "_");

let client: Arcade | null | undefined;
/** The engine client, from `ARCADE_API_KEY` (and `ARCADE_BASE_URL`, if set), or null when there is no key. */
export function arcade(): Arcade | null {
  if (client !== undefined) return client;
  const apiKey = process.env.ARCADE_API_KEY?.trim();
  client = apiKey ? new Arcade({ apiKey, baseURL: process.env.ARCADE_BASE_URL?.trim() || undefined }) : null;
  return client;
}

/** The sign-in links Arcade asked for in this process, so `bun run ask` can print them at the end. */
export const links: string[] = [];

/** The Loan Desk catalog for this signed-in user, independent of recommendation ranking. */
async function loanDeskTools(c: Arcade, user: string): Promise<Suggestion[]> {
  const toolkit = process.env.ARCADE_TOOLKIT?.trim() || "LoanDesk";
  const tools: Suggestion[] = [];
  try {
    for await (const item of c.tools.list({ toolkit, user_id: user, limit: 100 })) {
      const fqn = item.fully_qualified_name || item.qualified_name;
      if (item.toolkit?.name !== toolkit || typeof fqn !== "string" || !fqn.startsWith(`${toolkit}.`)) continue;
      const [tool_name, version] = fqn.split("@", 2);
      tools.push({ tool_name, version: version || item.toolkit.version, toolkit, description: item.description, input_schema: item.input?.parameters ?? [] });
    }
  } catch {
    // Recommendations can still work when the catalog is unavailable. Execution remains policy-checked.
    return [];
  }
  return tools;
}

/** Always include this user's Loan Desk catalog, then add Tool Rec's picks for other workflows. */
export async function select(tasks: string[], user: string, perTask = PER_TASK): Promise<{ queryId: string; tools: Suggestion[] }> {
  const c = arcade();
  if (!c) throw new Error("ARCADE_API_KEY is not set");
  const pinned = await loanDeskTools(c, user);
  const body: Record<string, unknown> = { tasks, top_k: Math.max(perTask, MIN_TOP_K), user_id: user };
  const gatewayId = process.env.ARCADE_GATEWAY_ID?.trim();
  if (gatewayId) body.gateway_id = gatewayId;
  let res: SelectResponse = {};
  try {
    res = await c.post<SelectResponse>("/v1/tools/select", { body });
  } catch (error) {
    if (pinned.length === 0) throw error;
  }
  const tools = new Map<string, Suggestion>(pinned.map((tool) => [tool.tool_name, tool]));
  const picks: Suggestion[] = [];
  for (const result of res.results ?? []) {
    for (const suggestion of (result.suggestions ?? []).slice(0, perTask)) {
      const [name, version] = suggestion.tool_name.split("@", 2);
      if (tools.has(name)) continue;
      const pick = { ...suggestion, tool_name: name, version: suggestion.version || version };
      tools.set(name, pick);
      picks.push(pick);
    }
  }
  await Promise.all(picks.map(async (pick) => Object.assign(pick, await current(c, pick.tool_name, user))));
  return { queryId: res.query_id ?? "", tools: [...tools.values()] };
}

/** The catalog's schema and version per tool name, kept for the process. Failed lookups are not kept. */
const definitions = new Map<string, Promise<Pick<Suggestion, "input_schema" | "version"> | null>>();

/**
 * Tool Rec can hand out a schema older than the tool it runs (Slack.SendMessage's `channel_name`
 * and `message` after 6.x moved to a `messages` list), so the model builds calls the tool rejects.
 * The catalog's definition wins, with its version so the call runs the schema the model saw; when
 * the lookup fails, Tool Rec's suggestion stands.
 */
function current(c: Arcade, name: string, user: string) {
  let found = definitions.get(name);
  if (!found) {
    found = c.tools.get(name, { user_id: user }).then(
      (t) => Array.isArray(t.input?.parameters) ? { input_schema: t.input.parameters, version: t.fully_qualified_name?.split("@", 2)[1] || t.toolkit?.version || undefined } : null,
      () => null,
    );
    definitions.set(name, found);
    found.then((d) => d || definitions.delete(name));
  }
  return found.then((d) => d ?? {});
}

/** Runs one tool through the engine, as `user_id`. Never throws: a refusal, an error or a sign-in link is an answer the model can read. */
export async function execute(s: Suggestion, input: unknown, queryId: string, user_id: string): Promise<unknown> {
  const c = arcade()!;
  try {
    const res = await c.tools.execute({
      tool_name: s.tool_name,
      tool_version: s.version || undefined,
      input: (input ?? {}) as Record<string, unknown>,
      user_id,
      ...(queryId ? { query_id: queryId } : {}),
    } as Parameters<Arcade["tools"]["execute"]>[0]);
    const auth = res.output?.authorization;
    if (auth?.url && auth.status !== "completed") return signIn(s, auth.url);
    if (res.success === false || res.output?.error) {
      const e = res.output?.error;
      if (e?.kind === "TOOL_RUNTIME_CONTEXT_REQUIRED" || /authoriz/i.test(e?.message ?? "")) {
        const url = await authorize(s, user_id);
        if (url) return signIn(s, url);
      }
      return { error: e?.message ?? "The tool call failed.", ...(e?.additional_prompt_content ? { guidance: e.additional_prompt_content } : {}) };
    }
    return res.output?.value ?? null;
  } catch (err) {
    const message = (err as Error).message;
    if (/authoriz/i.test(message)) {
      const url = await authorize(s, user_id);
      if (url) return signIn(s, url);
    }
    return { error: message };
  }
}

/** Execute reports a missing grant without a link; `tools.authorize` is the call that makes one. */
async function authorize(s: Suggestion, user_id: string): Promise<string> {
  try {
    const res = await arcade()!.tools.authorize({ tool_name: s.tool_name, user_id });
    return res.status === "completed" ? "" : (res.url ?? "");
  } catch {
    return "";
  }
}

function signIn(s: Suggestion, url: string) {
  if (!links.includes(url)) links.push(url);
  return { authorization_required: true, url, message: `${s.toolkit || s.tool_name} needs your permission. Give the officer this link, then stop until they say they have allowed it.` };
}

/** The tools this run has been handed, by model-facing name, and the selection that surfaced each. */
type RunState = { user?: string | null; catalog?: Record<string, { s: Suggestion; queryId: string }>; evidence?: unknown[] };

const STALE = "The sign-in this run started with has expired or changed, so this call was not made. Tell the officer to run `bun run signin` in agent/, then ask again.";
const NOBODY = "Nobody is signed in, so Arcade cannot act for anyone. Tell the officer to run `bun run signin` in agent/, then ask again.";

/**
 * The processor. Put it in the agent's `inputProcessors`; it gives each step the select tool,
 * every tool selected so far in this run, and Jev in front of the outbound ones (jev.ts).
 */
export class ArcadeToolRec implements Processor<"arcade-tool-rec"> {
  readonly id = "arcade-tool-rec";
  readonly name = "Arcade Tool Rec";

  async processInputStep({ tools, state }: ProcessInputStepArgs) {
    if (!arcade()) return {};
    const run = state as RunState;
    const catalog = (run.catalog ??= {});
    const evidence = (run.evidence ??= []);
    // Once per run: the kept sign-in, verified. A run that cannot verify anyone selects nothing.
    if (run.user === undefined) run.user = (await signedIn())?.email ?? null;
    const user = run.user;

    const selectTool = tool({
      description:
        "Find the external tools for a task (the loan book, Slack, email) and make them callable. The selected tools are available on your next step: call them directly by name. Write one plain-English task per workflow, folding its steps together; select again when the task changes.",
      inputSchema: z.object({ tasks: z.array(z.string()).min(1).describe("Plain-English task descriptions, one per workflow.") }),
      execute: async ({ tasks }) => {
        if (!user) return NOBODY;
        try {
          const { queryId, tools: found } = await select(tasks, user);
          if (found.length === 0) return "No tools matched, or your access policy offers none for this. Rephrase the task, or tell the officer.";
          // A model-facing name stands for one tool for the whole run: never the selector, never a second tool.
          const added = found.filter((s) => {
            const name = modelName(s.tool_name);
            if (name === SELECT || (catalog[name] && catalog[name].s.tool_name !== s.tool_name)) return false;
            catalog[name] = { s, queryId };
            return true;
          });
          if (added.length === 0) return "Tool Rec offered no tool that can be added to this run. Rephrase the task, or tell the officer.";
          return `Now callable:\n${added.map((s) => `- ${modelName(s.tool_name)}: ${oneLine(s.description)}`).join("\n")}`;
        } catch (err) {
          return `Tool Rec could not be reached (${(err as Error).message}).`;
        }
      },
    });

    const selected: Record<string, unknown> = {};
    for (const [name, { s, queryId }] of Object.entries(catalog)) {
      selected[name] = tool({
        description: s.description ?? s.tool_name,
        inputSchema: jsonSchema(inputSchema(s.input_schema) as Parameters<typeof jsonSchema>[0]),
        execute: async (input: unknown) => {
          // Checked again on every call: a sign-in that expired, or was replaced, mid-run acts for nobody.
          if ((await signedIn())?.email !== user) return { error: STALE };
          return execute(s, input, queryId, user!);
        },
      });
    }

    return { tools: { ...tools, [SELECT]: selectTool, ...guardTools(selected, evidence) } };
  }
}

const oneLine = (text = "", max = 160) => {
  const t = text.split(/\s+/).join(" ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
};
