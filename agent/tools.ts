/**
 * Connect to your gateway once and list what it offers.
 *
 *     bun run tools
 *
 * The first run opens the workshop identity provider's sign-in in your browser and keeps the
 * tokens in `.tokens.json`. No model is involved: this proves the gateway, the sign-in and your
 * policy's access hook (which decides which tools you see) before the agent asks anything.
 */
import { gateway, SERVER } from "./src/mastra/mcp";

const gw = gateway();
if (!gw) {
  console.error("ARCADE_MCP_URL is not set: copy your gateway's URL from the portal's gateway step into agent/.env");
  process.exit(2);
}

try {
  await gw.ready();
  const tools = await gw.client.listTools();
  const names = Object.keys(tools)
    .map((k) => k.replace(new RegExp(`^${SERVER}_`), ""))
    .sort();
  if (names.length === 0) {
    console.log("Signed in, but the gateway offered no tools. Check the gateway's tools in the portal, and your policy's access rules.");
  } else {
    console.log(`Your gateway offers ${names.length} tool${names.length === 1 ? "" : "s"}, as you:\n`);
    for (const n of names) console.log(`  ${n}`);
  }
} finally {
  await gw.client.disconnect().catch(() => undefined);
}
