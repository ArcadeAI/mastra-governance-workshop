/**
 * Ask the agent one thing, as you.
 *
 *     bun run ask "Which loan applications are pending?"
 *
 * With `ARCADE_API_KEY` set, the agent finds its tools with Tool Rec and calls them through Arcade
 * as the person signed in (src/mastra/identity.ts); when nobody is, this signs you in first, in
 * your browser. Without the key, it has no tools and answers from the model alone. The run is
 * traced like one from Studio: locally, and on the Mastra platform when `.env` names your project.
 */
import { mastra, platformTracing } from "./src/mastra";
import { arcade, links } from "./src/mastra/arcade";
import { signIn, signedIn } from "./src/mastra/identity";
import { jevEnabled } from "./src/mastra/jev";

const prompt = process.argv.slice(2).join(" ").trim();
if (!prompt) {
  console.error('usage: bun run ask "<what to ask>"');
  process.exit(2);
}

if (!arcade()) console.error("ARCADE_API_KEY is not set: answering from the model alone, with no tools.");
else {
  const who = (await signedIn()) ?? (await signIn().catch((err: Error) => (console.error(err.message), process.exit(2))));
  console.error(`As ${who.email}, until ${new Date(who.exp * 1000).toLocaleTimeString()}. Jev ${jevEnabled() ? "on" : "off"}.\n`);
}

try {
  const result = await mastra.getAgent("loanDesk").generate(prompt);
  console.log(result.text);
  if (links.length > 0) console.log(`\nOpen to allow, then ask again:\n${links.map((l) => `  ${l}`).join("\n")}`);
  if (platformTracing) console.error("\nTraced on your Mastra platform project.");
} finally {
  await mastra.observability.shutdown().catch(() => undefined);
}
