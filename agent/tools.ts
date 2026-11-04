/**
 * List the signed-in user's Loan Desk tools plus recommendations for a task, with no model involved.
 *
 *     bun run tools
 *     bun run tools "post a message in the workshop Slack channel"
 *
 * This proves the key, the sign-in and your policy's access hook (which filters discovery) before the agent asks anything. When nobody is signed in, it signs you in first.
 */
import { arcade, select } from "./src/mastra/arcade";
import { signIn, signedIn } from "./src/mastra/identity";

if (!arcade()) {
  console.error("ARCADE_API_KEY is not set: put your Arcade project key in agent/.env");
  process.exit(2);
}
const who = (await signedIn()) ?? (await signIn().catch((err: Error) => (console.error(err.message), process.exit(2))));

const task = process.argv.slice(2).join(" ").trim() || "review pending loan applications and request approval for one";
const { tools } = await select([task], who.email);
if (tools.length === 0) {
  console.log(`Arcade offered no tools for "${task}". Check your policy's access rules in the portal.`);
} else {
  console.log(`For "${task}", Arcade offers ${who.email}:\n`);
  for (const t of tools) console.log(`  ${t.tool_name}`);
}
