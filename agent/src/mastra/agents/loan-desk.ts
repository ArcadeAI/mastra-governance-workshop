/**
 * The loan desk agent: a Mastra `Agent` with no tools listed in its code. Registered in src/mastra/index.ts, so
 * `bun run ask` and Studio (`bun run dev`) run the same agent and both leave traces.
 *
 * Its tools are whatever your Arcade MCP gateway offers (mcp.ts): the Loan Desk's six tools,
 * Arcade's Slack and mail tools. Its instructions carry no limits on purpose. Who may approve
 * what, which fields the model may see, and which calls go to a human live in the policy on
 * your contextual access tenant, which Arcade checks on every call; a limit written here would
 * be advice the model can be talked out of.
 */
import { Agent, type ToolsInput } from "@mastra/core/agent";
import { gatewayTools } from "../mcp";

export const INSTRUCTIONS = `You are a loan officer's assistant at a small-business lender.
You act on the officer's behalf, as them, through the tools you are given: their loan book and the workshop's Slack.
Use the tools to find out rather than guessing. When a tool refuses a call, say so and pass on what it said about how to proceed; do not retry the same call.
When a tool answers with a link that needs the officer's permission, give them the link and stop.
Answer briefly, in plain language.`;

/** The model, through Mastra's model router: `MODEL_ID` names an Anthropic model, `ANTHROPIC_API_KEY` is the key shared in the room. */
export function model() {
  const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY is not set: put the key shared in the room in agent/.env");
  return { id: `anthropic/${process.env.MODEL_ID?.trim() || "claude-sonnet-5-5"}` as const, apiKey };
}

/**
 * The agent. Its tools are a function, not a list: Mastra asks for them on each run, and the answer is whatever
 * your gateway offers you at that moment, under your policy's access rules.
 */
export const loanDesk = new Agent({
  id: "loan-desk",
  name: "Loan Desk",
  instructions: INSTRUCTIONS,
  model: () => model(),
  tools: async () => (await gatewayTools()) as ToolsInput,
  defaultOptions: { modelSettings: { temperature: 0 } },
});
