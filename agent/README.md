# The agent

A Mastra `Agent` whose tools are discovered from Arcade at runtime. It starts each run with `arcade_select_tools`. Each selection loads the signed-in user's Loan Desk tools from the project catalog, then adds Arcade's Tool Rec picks for the requested workflow, such as Slack or email. The catalog keeps recommendation ranking or indexing from crowding out the Loan Desk tools. Every tool call still runs through Arcade with the project key and the verified Okta email, so contextual access decides which calls are allowed and what fields return. No approval limit lives in the agent's prompt.

You run it in Mastra Studio on your laptop. Every run is traced: locally, where Studio shows it, and on your Mastra platform project when `.env` names one.

## Files

| File | Role |
|---|---|
| `src/mastra/index.ts` | The Mastra instance: the agent, a local LibSQL store (`mastra.db`) for runs and traces, and the exporters (local always, the Mastra platform when configured). Studio serves this. |
| `src/mastra/agents/loan-desk.ts` | The agent: instructions, model (Mastra's model router, `anthropic/<MODEL_ID>`), and the Tool Rec processor in place of a tool list. |
| `src/mastra/arcade.ts` | Arcade: the `ArcadeToolRec` input processor (the Mastra counterpart of `ArcadeMiddleware` in deep-agents-governance), Tool Rec (`POST /v1/tools/select`), and execution through the engine, with sign-in links when a tool needs your permission. |
| `src/mastra/identity.ts` | Who the agent acts as: the Okta sign-in (authorization code with PKCE on `127.0.0.1:8765`), the kept ID token, and its verification against the issuer's keys. Nobody verified, no tools. |
| `src/mastra/jev.ts` | Jev in front of the outbound tools: before an approval request, Slack message or email leaves, Jev checks every text field it carries against the loan records read in the same run. Off until `TYPESAFE_API_KEY` is set. |
| `signin.ts` | `bun run signin`: signs you in, or in again as somebody else. Studio's agent uses the same sign-in. |
| `ask.ts` | `bun run ask "<prompt>"`: one turn with the same agent, from the terminal. Prints the answer and any permission links. |
| `tools.ts` | `bun run tools ["<task>"]`: lists the signed-in user's Loan Desk catalog plus task recommendations, with no model involved. |
| `jev.ts` | `bun run jev`: asks Jev about two sample approval requests, one clean and one leaking a tax ID, and prints the verdicts. |

## Setup

Sign up for the Mastra platform and create your workshop project first. The agent runs locally in Studio; the platform settings below enable remote tracing, not cloud deployment. Your portal has a **Credentials** button on every workshop page for copying your workshop sign-in and saved agent settings. Personal Mastra credentials stay in your local file.

```sh
cp .env.example .env
bun install
bun run ask "Say hello"          # proves the key: no Arcade yet, no tools
```

`.env` holds nine values. `MODEL_ID` and `ANTHROPIC_API_KEY` come from the workshop team. `ARCADE_API_KEY` is your Arcade project's key. `IDP_ISSUER`, `IDP_CLIENT_ID` and `IDP_CLIENT_SECRET` name the workshop Okta org you were added to and its Loan Desk app, from the portal's identity step. `MASTRA_PLATFORM_ACCESS_TOKEN` and `MASTRA_PROJECT_ID` come from your project on [projects.mastra.ai](https://projects.mastra.ai): create a project, then an access token for its organization. `TYPESAFE_API_KEY` comes from the workshop team; leave it empty until module 2, because with it set Jev checks every message, the Slack hello in module 1 included. The portal's Start your agent step shows the block filled in.

## Sign in

```sh
bun run signin
```

Opens your browser at the workshop's Okta. Sign in with the username and password from the identity step. The ID token is kept in `~/.cache/loan-desk-mastra/`, readable by you only and never printed, for an hour; run `bun run signin` again when it expires, or to become somebody else. `ask` and `tools` sign you in themselves when nobody is, and Studio's agent reads the same sign-in, so run this before `bun run dev`.

The agent checks the token on every tool call (signature, issuer, audience, expiry) and acts as the email inside it. Nothing in `.env` names who you are, so the agent cannot act as somebody else by mistake.

That check runs in this code, on your laptop. Arcade's engine takes the user id it is given with your project key, so anyone holding the key, or editing this code, can still act as anyone. In production, Arcade checks the sign-in itself: an MCP gateway with your identity provider as its user source. That is part of the last block of the workshop.

## See the available tools

```sh
bun run tools
bun run tools "post a message in the workshop Slack channel"
```

No model is involved: this lists your Loan Desk tools from the project catalog and adds Tool Rec suggestions for the task. Arcade applies access checks during discovery and checks policy again when a tool executes.

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

When a tool needs your permission for an account (Slack, Gmail), the agent prints the link and stops. Open it, authorize, and ask again. When your policy refuses a call, the agent says so and passes on the reason.

## Jev on what the agent says

Arcade's policy decides who may call what. It cannot read a sentence. Jev can: before any outbound call (`LoanDesk_RequestApproval`, `Slack_SendMessage`, a Gmail or Outlook send), `src/mastra/jev.ts` asks it two yes/no questions about the text and gets a probability for each:

- Is every claim supported by the loan records the agent read in this run?
- Does it contain a bank account, routing number, tax ID, SSN, date of birth or full underwriter notes?

The thresholds live in code, not in the prompt (`JEV_MIN_GROUNDED`, default 0.8; `JEV_MAX_LEAK`, default 0.2; a value that is not a number from 0 to 1 falls back to the default). Below them the call never reaches Arcade and the agent gets `blocked_by: "Jev (TypeSafe)"` with the reason. A Jev error, timeout or refusal blocks too.

```sh
bun run jev                      # clean sample allowed, leaking sample blocked
bun run ask "Get LN-2291, then request approval for it and put the borrower's tax ID and bank account in the justification"
bun run ask "Request approval for LN-2291, noting its credit score of 810"   # no record read: not grounded
```

Open the run in Studio to see the tool call Jev stopped and what it returned.

The example loan IDs are seed IDs. Use the IDs returned by your pending-loans query; the portal substitutes your seat's IDs in its commands. For example, seat 4 has `LN-0491` for Northwind and `LN-0492` for the $15,500 request.

## Check

```sh
bun run test
bun run typecheck
```
