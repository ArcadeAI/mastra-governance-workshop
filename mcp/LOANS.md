# mcp/loan_desk/loans.py

The loan tools — `search_loans`, `get_loan`, `approve_loan`, `deny_loan` — as a
Python `arcade-mcp` toolkit. Each tool is a stateless client of the app's loan
module ([`api/`](../../api)), the bank's system of record, over
HTTP under `/bank` (#5; it was `apps/loan-app`, a service of its own). Nothing here
holds state, and nothing here decides anything.

The tool descriptions came across from the previous MCP surface verbatim. They
were written to be picked by a model without prompt coaching and reviewed on
that basis; the wording is the asset.

## Identity, not authority

Every tool requires OAuth against the loan desk's identity provider, Okta
(`IDP_ISSUER`, the bundled provider until the Okta move), registered in Arcade
under the provider id `app-identity` (`IDP_PROVIDER_ID`; `cg-idp` until #6). The id is
fixed: `OAuth2(id=...)` is read at import, so register the provider under
exactly this string. The tool forwards the
user's token to the API as a bearer token and the API derives the actor from it.
No tool takes an actor as an argument — the test suite asserts that.

The auth requirement is a credential check, not the governance gate. Arcade
evaluates it *before* the `/pre` hook, so a refusal there fires no hook, writes
no audit row and shows nothing on the panel. Limits, roles and separation of
duties stay in the control plane (`gate/service/`).

## Configuration

One value: `APP_PUBLIC_HOST`, HOST-form like every address in this repo: the
app's host, since the loan API is part of the app — the same secret, and the
same value, `mcp/loan_desk/approvals.py` holds (#6; it was the loan API's own variable
until then). The tools add the
`/bank` path themselves (`API_BASE_PATH`).
It reaches the deployed toolkit as an Arcade secret, uploaded by `arcade deploy`
from the repo's `.env`, because a secret is the one configuration channel a
deployed toolkit has.

## Run and test

```sh
uv sync --extra dev
uv run --extra dev pytest        # boots the real loan module (scripts/loans.ts) under Bun
uv run server.py http            # Streamable HTTP on 127.0.0.1:8000
```

## Deploy

```sh
arcade deploy                    # from this directory
```

`arcade deploy` starts `server.py`, reads `serverInfo.name` and `version` off
its `initialize` response, and ships the package under that name. The name is
the `MCPApp(name=...)` in `loan/__init__.py`, not the package name in
`pyproject.toml` — they happen to agree here.

## The toolkit name, measured

Deployed 2026-09-03 into the `mastra governance dev` Arcade project, alongside a
throwaway probe named `loan_mcp_probe` — an underscore and the substring `mcp`,
so one deploy discriminates every naming rule on the table. Read back with
`GET /v1/workers/<server>/tools`:

| `MCPApp(name=...)` | `toolkit.name` | `fully_qualified_name` |
|---|---|---|
| `loan` | `Loans` | `LoanDesk.SearchLoans@1.0.0`, `LoanDesk.GetLoan@1.0.0`, `LoanDesk.ApproveLoan@1.0.0`, `LoanDesk.DenyLoan@1.0.0` |
| `loan_mcp_probe` | `LoanMcpProbe` | `LoanMcpProbe.PingProbe@1.0.0` |

Three things follow, and two of them were not what the issue assumed:

1. **Normalised, not raw.** The toolkit is the server name split on
   underscores and PascalCased. `mcp` is *not* stripped — that is where
   `arcade deploy` differs from Remote MCP registration (spike #2).
2. **Underscores do not survive into a resolvable name.** Against
   `POST /v1/tools/execute`, `LoanMcpProbe.PingProbe` ran; `loan_mcp_probe.PingProbe`
   and `loan_mcp_probe.ping_probe` were `400 failed to parse tool name`;
   `LoanMcpProbe.ping_probe` was `tool_not_found`.
3. **Tool names are PascalCased too, by `arcade-mcp` itself**, before Arcade
   ever sees them: the function `get_loan` is the tool `GetLoan`. The MCP wire
   name the agent sees through a gateway is therefore `LoanDesk_GetLoan`. The
   descriptions still say `get_loan`; whether that wording should follow the
   wire name is a question for the prompt-coaching review, not this slice.
   **#14** (the first end-to-end slice) and any eval work should know the
   model reads "call `get_loan`" while holding a tool named `LoanDesk_GetLoan`.

So `ARCADE_TOOLKIT=Loans`, and policy rules key on `tool.toolkit = "LoanDesk"`
with `tool.name` in `SearchLoans`, `GetLoan`, `ApproveLoan`, `DenyLoan`.

`tool.toolkit` on a live `/pre` payload is recorded on #35 once a hook
extension in that project is pointed at a receiver; spike #2 found the
workers-API toolkit name and the payload's `tool.toolkit` identical.

Two operational notes. `APP_PUBLIC_HOST` has to be set as an Arcade secret to
the app's public host (the ngrok host) before a call can reach it
(`arcade secret set APP_PUBLIC_HOST <host>`), and a deployment that still holds
the old variable name must be redeployed, because the tools now declare the new
one. And the tools require the `app-identity` auth provider; until it is
registered a call fails the requirement check before any hook fires — by
design, see above.
