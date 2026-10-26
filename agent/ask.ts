/**
 * Ask the agent one thing, as you.
 *
 *     bun run ask "Which loan applications are pending?"
 *
 * With `ARCADE_MCP_URL` set, the agent's tools are your gateway's, signed in as your workshop
 * identity (src/mastra/mcp.ts). Without it, the agent has no tools and answers from the model
 * alone. The run is traced like one from Studio: locally, and on the Mastra platform when
 * `.env` names your project.
 */
import { mastra, platformTracing } from "./src/mastra";
import { gateway } from "./src/mastra/mcp";

const prompt = process.argv.slice(2).join(" ").trim();
if (!prompt) {
  console.error('usage: bun run ask "<what to ask>"');
  process.exit(2);
}

const gw = gateway();
if (!gw) console.error("ARCADE_MCP_URL is not set: answering from the model alone, with no tools.");

try {
  const result = await mastra.getAgent("loanDesk").generate(prompt);
  console.log(result.text);
  if (gw && gw.links.length > 0) console.log(`\nOpen to allow, then ask again:\n${gw.links.map((l: string) => `  ${l}`).join("\n")}`);
  if (platformTracing) console.error("\nTraced on your Mastra platform project.");
} finally {
  await mastra.observability.shutdown().catch(() => undefined);
  await gw?.client.disconnect().catch(() => undefined);
}
