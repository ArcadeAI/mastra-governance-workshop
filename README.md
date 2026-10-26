# Mastra governance workshop

A loan desk agent built with [Mastra](https://mastra.ai), run in Mastra Studio, whose tools come from an Arcade MCP gateway and whose limits live outside the model, in Arcade's contextual access policies. Every run is traced in Studio and on the attendee's Mastra platform project.

This is the Mastra counterpart of [deep-agents-governance](https://github.com/ArcadeAI/deep-agents-governance). The Arcade half (account, identity, Slack, the Loan Desk MCP server, contextual access, policies, gateway) is the same; only the agent differs. Attendees follow the workshop portal, which walks through both halves and fills in the values the agent needs.

| Folder | What it is |
|---|---|
| `agent/` | The Mastra agent (TypeScript, bun), served in Studio. Attendees run this. See `agent/README.md`. |
| `mcp/` | The Loan Desk MCP server (Python, uv) the workshop team deploys into each attendee's Arcade project. Unchanged from deep-agents-governance. |

## Quick start

```sh
cd agent
cp .env.example .env    # fill in ANTHROPIC_API_KEY, and ARCADE_MCP_URL once you have a gateway
bun install
bun run ask "Say hello"
```

With `ARCADE_MCP_URL` set, `bun run tools` signs you in to the gateway once (OAuth, in your browser) and lists the tools it holds. Then `bun run dev` opens Studio at http://localhost:4111 with the agent in it; `bun run ask "..."` asks the same agent from the terminal. With `MASTRA_PLATFORM_ACCESS_TOKEN` and `MASTRA_PROJECT_ID` set, the traces go to your project on [projects.mastra.ai](https://projects.mastra.ai) as well.

## License

MIT, see `LICENSE`.
