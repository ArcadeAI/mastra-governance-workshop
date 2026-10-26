"""The loans toolkit: four tools, each a stateless client of the app's loan module.

Nothing here holds state and nothing here decides anything. Every tool takes
the caller's OAuth token, hands it to the loan desk's API, and returns what the API
returns. The API derives who is acting from that token; the tools never say.

Whether a caller *may* do what they are asking is not this toolkit's question
either — that is decided by the control plane's hooks, on a path these tools
cannot see. The auth requirement on each tool carries identity, not authority.

The tool descriptions were written to be picked by a model without prompt
coaching and reviewed on that basis. They came across from the previous MCP
surface verbatim; change the wording only for a reason that survives review.
"""

from enum import Enum
from typing import Annotated, Any

import httpx
from arcade_core.errors import ToolExecutionError
from arcade_mcp_server import Context
from arcade_mcp_server.auth import OAuth2
from arcade_mcp_server.metadata import Behavior, Operation, ToolMetadata

__all__ = [
    "API_BASE_PATH",
    "APP_HOST_SECRET",
    "IDP_PROVIDER_ID",
    "app",
    "approve_loan",
    "deny_loan",
    "get_loan",
    "search_loans",
]

# The name here is what `arcade deploy` reads off `initialize` and becomes the
# server name Arcade files these tools under. Whether Arcade keeps it verbatim
# as the toolkit name or normalises it is measured on #35; the observed value
# is pinned in `.env.example` as ARCADE_TOOLKIT. Alphanumerics and
# underscores only, per MCPApp — a hyphen is rejected at construction, and a
# hyphenated toolkit could not form a parseable Arcade tool name anyway
# (measured in spike #2).
from loan_desk.app import app

# The Arcade auth provider id the app's identity module is registered under
# (hop 2). Fixed, not configurable: `OAuth2(id=...)` is read at import, so the
# README tells a developer to register the provider under exactly this id
# (DESIGN.md → Toolkit configuration). It was `cg-idp`, the demo's service
# name, until #6. Renaming it is this one line and the README's instruction.
# The scopes are the least that make `/oauth2/userinfo` return an email, which
# is how the API attributes the call. They are not a gate: a scope refusal
# happens before any hook fires and would be invisible to the control plane.
IDP_PROVIDER_ID = "app-identity"
IDP_SCOPES = ["openid", "email"]

# HOST-form, like every service address in this repo (see `.env.example`).
# Delivered to the deployed toolkit as an Arcade secret, because that is the
# one configuration channel a deployed toolkit has. The app's own host since
# #6: the loan API is a module of the app, and one secret names the app for
# both toolkits.
APP_HOST_SECRET = "APP_PUBLIC_HOST"

# Where the loan API sits on that host. Since #5 the loan API is a module of
# the app (`api/`), served under `/bank` because the app's board page is
# `/loans`: GET /bank/loans, GET /bank/loans/{id}, POST /bank/loans/{id}/approve
# and POST /bank/loans/{id}/deny. The secret still names the host alone.
API_BASE_PATH = "/bank"

_requires_auth = OAuth2(id=IDP_PROVIDER_ID, scopes=IDP_SCOPES)
_requires_secrets = [APP_HOST_SECRET]

# The previous MCP surface carried `readOnlyHint`, `destructiveHint`,
# `idempotentHint` and `openWorldHint` on each tool. `arcade-mcp`'s
# `Behavior` is the equivalent, so they come across too: reads are read-only
# and closed-world; writes are neither destructive (nothing is deleted or
# overwritten — a decision is appended) nor idempotent (approving twice is two
# rows, on purpose). MCP `title` has no equivalent and is dropped. No
# `Classification`: its service domains describe third-party SaaS, and the
# bank's own loan book is not one (`arcade-mcp` rejects a domain on a
# closed-world tool). Note that spike #2 measured this metadata does not reach
# hook payloads; it is for clients, not for policy.
_read = ToolMetadata(
    behavior=Behavior(
        operations=[Operation.READ], read_only=True, destructive=False, idempotent=True,
        open_world=False,
    ),
)
_write = ToolMetadata(
    behavior=Behavior(
        operations=[Operation.CREATE], read_only=False, destructive=False, idempotent=False,
        open_world=False,
    ),
)


class LoanStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


def _base_url(host: str) -> str:
    local = host.startswith("localhost") or host.startswith("127.0.0.1")
    return f"{'http' if local else 'https'}://{host}"


async def _call(
    context: Context,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    """One request to the API, on behalf of whoever holds the token."""
    url = _base_url(context.get_secret(APP_HOST_SECRET)) + API_BASE_PATH + path
    headers = {"Authorization": f"Bearer {context.get_auth_token_or_empty()}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.request(method, url, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "The loan desk could not be reached.",
                developer_message=f"{method} {url}: {exc!r}",
            ) from exc

    if response.is_success:
        return response.json()

    try:
        message = response.json().get("error", response.text)
    except ValueError:
        message = response.text
    raise ToolExecutionError(
        str(message),
        developer_message=f"{method} {url} -> {response.status_code}: {response.text}",
    )


LoanId = Annotated[
    str, "The loan applications ID, in the form LN-0000 — for example LN-2291."
]


@app.tool(requires_auth=_requires_auth, requires_secrets=_requires_secrets, metadata=_read)
async def search_loans(
    context: Context,
    status: Annotated[
        LoanStatus | None,
        "Return only requests in this state. 'pending' means no decision has been "
        "recorded yet.",
    ] = None,
    min_amount: Annotated[
        float | None, "Return only requests for a loan of at least this many US dollars."
    ] = None,
    max_amount: Annotated[
        float | None, "Return only requests for a loan of at most this many US dollars."
    ] = None,
) -> Annotated[dict[str, Any], "The number of matching requests and their list-view fields."]:
    """Find loan applications in the loan book, newest submission first: what is awaiting a decision, or what falls within a dollar range. All filters are optional and combine; with none supplied this returns every application on file. Each hit carries the list-view fields only — ID, borrower, amount, status, purpose and submission date. A borrower's financials, the underwriter's notes and the decisions already recorded are in get_loan's result for an ID from these hits."""
    params: dict[str, Any] = {}
    if status is not None:
        params["status"] = status.value
    if min_amount is not None:
        params["min_amount"] = min_amount
    if max_amount is not None:
        params["max_amount"] = max_amount
    return await _call(context, "GET", "/loans", params=params)


@app.tool(requires_auth=_requires_auth, requires_secrets=_requires_secrets, metadata=_read)
async def get_loan(
    context: Context,
    loan_id: LoanId,
) -> Annotated[dict[str, Any], "The complete loan application record."]:
    """Read one loan application's complete file by ID. Returns everything the loan book holds on it: borrower details including the contact email the outcome is sent to, the requested amount and purpose, credit score, annual revenue and years in business, the underwriter's notes, the borrower's bank account number and tax ID, and every approval or denial already recorded against it, oldest first."""
    return await _call(context, "GET", f"/loans/{loan_id}")


@app.tool(requires_auth=_requires_auth, requires_secrets=_requires_secrets, metadata=_write)
async def approve_loan(
    context: Context,
    loan_id: LoanId,
    amount: Annotated[
        float,
        "The loan to approve, in US dollars. Need not equal the amount requested — a "
        "request may be approved for less.",
    ],
) -> Annotated[dict[str, Any], "The request as it stands after the approval."]:
    """Approve a loan application for a given dollar amount, committing the decision to the loan book: the approval is appended to the application's decision history and its status becomes 'approved'. Returns the application as it stands after the approval."""
    return await _call(context, "POST", f"/loans/{loan_id}/approve", json={"amount": amount})


@app.tool(requires_auth=_requires_auth, requires_secrets=_requires_secrets, metadata=_write)
async def deny_loan(
    context: Context,
    loan_id: LoanId,
    reason: Annotated[
        str,
        "Why the request is being declined. Recorded verbatim in the decision history "
        "and read by auditors, so write it for a human.",
    ],
) -> Annotated[dict[str, Any], "The request as it stands after the denial."]:
    """Decline a loan application with a stated reason, committing the decision to the loan book: the denial is appended to the application's decision history and its status becomes 'denied'. Returns the application as it stands after the denial."""
    return await _call(context, "POST", f"/loans/{loan_id}/deny", json={"reason": reason})
