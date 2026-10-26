# The agent

A Mastra `Agent` with no tools in its code. Everything it can do comes from one `MCPClient` pointed at your Arcade MCP gateway: the Loan Desk's six tools, Arcade's Slack and Gmail tools. Its instructions carry no limits on purpose. Who may approve what, which fields the model sees, and which calls wait for a human are policy on your contextual access tenant, checked by Arcade on every call.

You run it in Mastra Studio on your laptop. Every run is traced: locally, where Studio shows it, and on your Mastra platform project when `.env` names one.

## Files

| File | Role |
|---|---|
| `src/mastra/index.ts` | The Mastra instance: the agent, a local LibSQL store (`mastra.db`) for runs and traces, and the exporters (local always, the Mastra platform when configured). Studio serves this. |
| `src/mastra/agents/loan-desk.ts` | The agent: instructions, model (Mastra's model router, `anthropic/<MODEL_ID>`), and tools resolved from the gateway on every run. |
| `src/mastra/mcp.ts` | The gateway connection: `MCPClient` with an OAuth provider that signs in through your browser and keeps the tokens in `.tokens.json` (mode 0600, git-ignored). |
| `ask.ts` | `bun run ask "<prompt>"`: one turn with the same agent, from the terminal. Prints the answer and any permission links. |
| `tools.ts` | `bun run tools`: connects (signing in if needed) and lists the tools, stripped of their `arcade_` prefix. |

## Setup

```sh
cp .env.example .env
bun install
bun run ask "Say hello"          # proves the key: no gateway yet, no tools
```

`.env` holds five values. `MODEL_ID` and `ANTHROPIC_API_KEY` come from the workshop team. `MASTRA_PLATFORM_ACCESS_TOKEN` and `MASTRA_PROJECT_ID` come from your project on [projects.mastra.ai](https://projects.mastra.ai): create a project, then an access token for its organization. `ARCADE_MCP_URL` comes from the portal's gateway step. The portal's Start your agent step shows the block filled in.

## Sign in once

```sh
bun run tools
```

The first run prints a sign-in URL and opens it. Sign in with the workshop username and password from the identity step. The browser comes back to `http://127.0.0.1:8765/oauth/callback` (change the port with `OAUTH_CALLBACK_PORT`), the tokens land in `.tokens.json`, and the tool list prints. Delete `.tokens.json` to sign in again as someone else. Studio's chat triggers the same sign-in if you skip this step; it is just easier to see what happened from the terminal first.

## Studio

```sh
bun run dev
```

Serves the agent and opens Studio at http://localhost:4111. Chat with the Loan Desk agent there and open each run's trace: the model's steps, every tool call, and what Arcade answered, including a refusal from your policy. Studio reads the traces from `mastra.db`; the platform exporter sends the same traces to your project when `.env` names it.

## Ask from the terminal

```sh
bun run ask "Post a short hello from my agent in the workshop channel"
bun run ask "Which loan applications are pending?"
bun run ask "Approve LN-2292 and tell the room"
```

When a tool needs your permission for a third-party account (Slack, Gmail), the agent prints the link and stops. Open it, authorize, and ask again. When your policy refuses a call, the agent says so and passes on the reason.

## Check

```sh
bun run typecheck
```
