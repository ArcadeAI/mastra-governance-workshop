/**
 * The loan desk agent: a Mastra `Agent` with no tools listed in its code. Registered in src/mastra/index.ts, so
 * `bun run ask` and Studio (`bun run dev`) run the same agent and both leave traces.
 *
 * Its tools come from Arcade (arcade.ts): each selection includes the signed-in user's Loan Desk
 * catalog, then Tool Rec adds tools for the task, such as Slack or mail. Its instructions carry no limits on purpose. Who may approve
 * what, which fields the model may see, and which calls go to a human live in the policy on
 * your contextual access tenant, which Arcade checks on every call; a limit written here would
 * be advice the model can be talked out of.
 */
import { Agent } from "@mastra/core/agent";
import { arcade, ArcadeToolRec } from "../arcade";

export const INSTRUCTIONS = `You are a loan officer's assistant at a small-business lender.
You act on the officer's behalf, as them, through the tools you are given: their loan book and the workshop's Slack.
When you need the loan book, Slack or email, call arcade_select_tools with the task, then call the tools it gives you. Find out rather than guessing. When a tool refuses a call, say so and pass on what it said about how to proceed; do not retry the same call.
When a tool answers with a link that needs the officer's permission, give them the link and stop.
Answer briefly, in plain language.`;

/** Before Arcade is set up there are no tools, so the instructions say so instead of naming a selector the model does not have. */
const MODEL_ONLY = `You are a loan officer's assistant at a small-business lender.
You have no tools yet: you cannot see the loan book, Slack or email. If a question needs them, say that the tools are not connected yet.
Answer briefly, in plain language.`;

export const instructions = () => (arcade() ? INSTRUCTIONS : MODEL_ONLY);

/** The model, through Mastra's model router: `MODEL_ID` names an Anthropic model, `ANTHROPIC_API_KEY` is the key shared in the room. */
export function model() {
  const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY is not set: put the key shared in the room in agent/.env");
  // Accept the provider-qualified model value from the LangChain workshop too.
  const id = (process.env.MODEL_ID?.trim() || "claude-sonnet-5-5").replace(/^anthropic[:/]/, "");
  return { id: `anthropic/${id}` as const, apiKey };
}

/**
 * The agent. It lists no tools: the Tool Rec processor gives each step its tools, the ones the model selected
 * in this run, under your policy's access rules.
 */
export const loanDesk = new Agent({
  id: "loan-desk",
  name: "Loan Desk",
  instructions: () => instructions(),
  model: () => model(),
  inputProcessors: [new ArcadeToolRec()],
  defaultOptions: { modelSettings: { temperature: 0 } },
});
