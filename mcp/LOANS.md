# Loan Desk tools

`SearchLoans`, `GetLoan`, `ApproveLoan` and `DenyLoan` are stateless Python clients
of the workshop loan service. They ship together with `RequestApproval` and
`Decide` as the `LoanDesk` toolkit. The portal deploys `mcp/` into each attendee's
Arcade project; attendees only run the TypeScript agent locally.

The loan service lives in `service/` in [workshop-portal](https://github.com/ArcadeAI/workshop-portal).
The toolkit calls its `/bank` API. It stores no loans or approval limits.

## Identity and configuration

| Arcade secret | Purpose |
|---|---|
| `APP_PUBLIC_HOST` | Loan service host. The toolkit adds `/bank` to the address. |
| `LOAN_SERVICE_TOKEN` | Attendee's book token, sent as `X-Loan-Book`. |

The portal sets both before deployment. The actor comes from Arcade's verified
`context.user_id`, sent as `X-Actor`; no tool accepts an actor argument. Calls
without an actor fail. Contextual access enforces authority on Arcade's hook
path before execution. Identity alone does not grant approval authority.

The full tool names are `LoanDesk.SearchLoans`, `LoanDesk.GetLoan`,
`LoanDesk.ApproveLoan` and `LoanDesk.DenyLoan`. The agent converts those names to
model-compatible tool names. `ARCADE_TOOLKIT`, when overridden, should name
`LoanDesk`.

## Local tests

```sh
uv sync --extra dev
uv run --extra dev pytest
```

The default suite uses local HTTP fixtures for approvals and skips loan-service
integrations unless `TEST_DATABASE_URL` is set. To run every test, clone
`workshop-portal` alongside this repository and initialize its schema in a
disposable local Postgres database (see its `docs/mastra-local-testing.md`):

```sh
TEST_DATABASE_URL=postgresql://127.0.0.1:55473/mastra_portal_ready_local_test \
TEST_LOAN_SERVICE_DIR=/path/to/workshop-portal/service \
uv run --extra dev pytest
```

`TEST_LOAN_SERVICE_DIR` defaults to the sibling `workshop-portal/service`.
`uv` installs the service's own dependencies. An enabled integration run fails
if the service is absent. Every test gets its own throwaway loan book and removes
it afterwards. These tests need no workshop keys and send no messages.

## Instructor deployment

```sh
uv run server.py http  # local server, 127.0.0.1:8000
arcade deploy         # deploys this directory into the selected project
```

Use the portal's hosting step for the workshop; manual deployment is for
instructors maintaining the toolkit. See [APPROVALS.md](APPROVALS.md) for the
human approval tools.
