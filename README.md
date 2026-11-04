# Governed agents with Mastra, Arcade and Jev

**How to build production agents in the enterprise that can handle sensitive data.** A two-hour hands-on workshop.

You build a loan desk agent with [Mastra](https://mastra.ai), let it find its tools with [Arcade](https://arcade.dev)'s Tool Rec, and keep its limits out of the model. Two different checks do that:

- **Arcade contextual access** decides deterministically who may call what. Loan officer Alice cannot approve $95K when her clearance is $50K, however the prompt is worded.
- **Jev**, [TypeSafe](https://typesafe.ai)'s decision model, reads what the agent is about to say. Before an approval request or a Slack message leaves, Jev checks it against the loan records the agent actually read, and blocks it if it leaks the borrower's bank account or tax ID or claims something the file does not show.

The model proposes and the checks decide. Every run is traced locally for Mastra Studio, refusals included. Add your Mastra platform settings to keep traces in your platform project too.

This is the Mastra counterpart of [deep-agents-governance](https://github.com/ArcadeAI/deep-agents-governance). The Arcade half is the same: account, identity, Slack, the Loan Desk MCP server, contextual access and policies, and Tool Rec choosing the tools, as `ArcadeMiddleware` does there. Attendees follow the [workshop portal](https://workshop.arcade.dev), which walks through both halves and fills in the values the agent needs.

| Folder | What it is |
|---|---|
| `agent/` | The Mastra agent (TypeScript, bun), served in Studio. Attendees run this. See `agent/README.md`. |
| `mcp/` | The Loan Desk MCP server (Python, uv) the workshop portal deploys into each attendee's Arcade project: the same copy the portal ships. Its tools take the actor from Arcade's user id, so they need no sign-in of their own. |

## The two hours

| Time | Block | You end with |
|---|---|---|
| 0:00 | **Why governance lives outside the model.** The finished agent, live: a refusal, a Jev block, an approval. | The picture of the three layers. |
| 0:15 | **Setup.** Portal sign-in, Arcade key, clone, `.env`, `bun run ask "Say hello"`. | A Mastra agent answering from the model alone. |
| 0:35 | **1. Tools through Arcade.** `ARCADE_API_KEY` and the Okta values, `bun run signin`, `bun run tools`, then Studio. Ask for pending loans, then approve one over your clearance. | Tool Rec choosing the Loan Desk tools, and a policy refusal in the trace. |
| 1:00 | **2. Jev on what the agent says.** `TYPESAFE_API_KEY`, `bun run jev`, then make the agent put a tax ID in an approval request. | A message Jev stopped before it reached Slack, with its probabilities. |
| 1:30 | **3. A human in the loop.** The agent escalates to Charlie in Slack, Charlie approves, the retry passes once and the second retry is refused. | The full governed loop, traced end to end. |
| 1:50 | **To production.** What moves to your own Okta, Arcade project and policies, why identity moves from the laptop to an Arcade gateway, and Q&A. | |

## Before the workshop

You need Git and Bun on your laptop, an Anthropic model key, and the workshop portal invite from the instructor. The portal provisions your workshop identity and a separate loan book for this workshop; you connect your own Arcade project. The team supplies the TypeSafe key for the Jev exercise. Sign up for the Mastra platform and create a project before the workshop. The agent runs locally in Studio; adding your platform access token enables remote traces without deploying it.

## Quick start

```sh
cd agent
cp .env.example .env    # ANTHROPIC_API_KEY first; the Arcade values and TYPESAFE_API_KEY as the modules reach them
bun install
bun run ask "Say hello"
```

- With `ARCADE_API_KEY` and the `IDP_` values set, `bun run signin` signs you in at the workshop's Okta. `bun run tools` lists your Loan Desk tools from the project catalog, then adds Tool Rec's suggestions for the task, with no model involved. The catalog keeps the loan tools available when recommendation ranking misses them; Arcade still checks policy on execution.
- With `TYPESAFE_API_KEY` set, `bun run jev` asks Jev about two sample messages, one clean and one leaking a tax ID, so you can see it answer before the agent relies on it.
- `bun run dev` opens Studio at http://localhost:4111 with the agent in it. `bun run ask "..."` asks the same agent from the terminal.
- With `MASTRA_PLATFORM_ACCESS_TOKEN` and `MASTRA_PROJECT_ID` set, the traces go to your project on [projects.mastra.ai](https://projects.mastra.ai) as well.

## License

MIT, see `LICENSE`.
