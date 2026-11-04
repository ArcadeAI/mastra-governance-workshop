# Human approval tools

`LoanDesk.RequestApproval` records an escalation and notifies the configured
route. `LoanDesk.Decide` records a human decision. Both ship in the same Python
toolkit as the [loan tools](LOANS.md).

```text
request_approval(action, resource_id, amount, justification)
decide(request_id, decision, note?)
```

Routing chooses the lowest sufficient clearance from the service's roster,
excludes the requester, and breaks ties by user ID. The model cannot nominate
an approver. The routing cases live in
`tests/approvals/approver-routing-cases.json` and are checked by `test_routing.py`.

Arcade's contextual access policy decides whether a caller may record a
human decision before the tool runs. The approval URL identifies a request;
it carries no token or authority. Requesters cannot gain authority by reading it.

The service stores the selected route on each request. On the Slack route it
normally posts the approver's copy as the workshop bot; the toolkit posts the
room's copy as the requester. When the instructor enables attendee approvals,
the service returns `approvers_post.by: "requester"` and the toolkit posts both.
For the email and facilitator routes, the service handles the notification or
queue; a workshop-channel announcement can still be made by the toolkit.

## Configuration

The portal sets `APP_PUBLIC_HOST` to the loan service host and
`APPROVALS_STORE_TOKEN` to this attendee's service bearer. The latter is also
used as `LOAN_SERVICE_TOKEN`. Requests to the approvals API require the bearer.
`RequestApproval` requires the requester's Slack authorization; `Decide` has
no OAuth requirement. Policy enforcement remains a separate Arcade hook.

## Store contract

The real implementation lives in `workshop-portal/service/loan_service`.
The four endpoints require `Authorization: Bearer <attendee service token>`.
The local HTTP contract fixtures use synthetic tokens and never contact Slack.

### The record

One shape, returned by every endpoint that returns a request. `POST /api/approvals`,
`GET /api/approvals/{id}` and `POST /api/approvals/{id}/decision` return the *same*
fields — a page that can render the read is a page that can render the write.

| field | type | notes |
|---|---|---|
| `id` | string | Opaque. Minted by the store, never by a caller. |
| `requester_id` | string | Email. The `context.user_id` of whoever was refused. |
| `requester_display_name` | string | From the roster, so the page need not join. |
| `approver_id` | string | Email of the one person routing chose. |
| `approver_display_name` | string | |
| `candidate_approver_ids` | string[] | Everyone sufficient, lowest clearance first. `[0]` is the approver; the rest are who was deliberately not bothered. |
| `action` | string | The refused action, e.g. `approve_loan`. |
| `resource_id` | string | e.g. `LN-2291`. |
| `amount` | number | What determines who has the authority. |
| `required_clearance` | number | The bar a candidate had to clear: the amount. |
| `rule` | `{id, description}` \| null | The policy rule the blocked call tripped, when the control plane can name it. `null` when it cannot — the page and the DM both still state the authority that was exceeded. |
| `justification` | string | The requester's own words, rendered verbatim. |
| `status` | `pending` \| `approved` \| `denied` \| `expired` | |
| `created_at` | string | ISO 8601, `Z`-suffixed UTC, in the service's timestamp format. |
| `decided_at` | string \| null | `null` while pending. |
| `channel` | string | The requester's chosen route, stamped by the store: a `#channel` for Slack (the approver's copy is posted to `approvals_channel`), else `email` or `facilitator`. |
| `slack_channel` | string \| null | The requester's workshop channel. Every request is announced there, whatever the route; `null` when they have none. |
| `approvals_channel` | string \| null | Where the approvers sit (`#approvals` unless the store says otherwise). On the Slack route the store posts the approver's copy there as the workshop bot, naming the routed approver; since 2026-10-17, instead of a DM. |
| `approvers_post` | object | Slack route only, stamped by the store: `{channel, ts}` when the approvers' copy was posted as the bot, `{channel, error}` when it was not (`no_bot_token`, or Slack's error code), `{channel, by: "requester"}` when the attendees approve and the tool posts it itself. |
| `decided_by` | string \| null | Email of whoever decided. `null` while pending. |
| `note` | string \| null | The approver's note. `null` while pending or if none was given. |

There is deliberately **no separate `decision` field**: once decided, `status`
*is* the decision. Two fields carrying the same fact is two fields that can
disagree, and a page that rendered "approved" beside a status of `denied` would
be worse than one that rendered nothing.

`rule` lives on the record rather than beside it, so the read and the write
cannot answer the question differently.

### `GET /api/approvals/roster`

Every subject the control plane knows about. Routing needs the whole roster,
because who was *not* asked is as load-bearing as who was.

```json
200 { "subjects": [ { "user_id": "charlie@…", "display_name": "Charlie",
                      "role": "vp_credit", "clearance": 250000,
                      "attributes": {} } ] }
```

### `POST /api/approvals`

```json
<- { "requester_id": "alice@…", "action": "approve_loan",
     "resource_id": "LN-2291", "amount": 95000,
     "justification": "…", "approver_id": "charlie@…",
     "candidate_approver_ids": ["charlie@…", "michael@…"],
     "required_clearance": 95000 }

-> 201 { "request": <the record above, status "pending"> }
```

The store mints `id` and `created_at` — a server clock and a server id. An id
the toolkit invented would be an id the model could predict, and therefore ask
about before anyone had approved it.

`action` is a bare action name, not a fully-qualified tool. Resolving it to a
`ToolMatcher` needs the catalogue, which the control plane has and this toolkit
deliberately does not.

### `GET /api/approvals/{id}`

```json
-> 200 { "request": <the record above> }
-> 404 { "error": "no approval request apr_…" }
```

This is the endpoint the approval page reads. The link in the Slack message
carries the id and nothing else — no token, no signature, no capability — so
this response has to be enough to render the whole page: who asked, what for,
how much, which rule, and why. That is what the record above is sized to.

Answering `200` here is *not* authorization. The requester can read the DM she
sent, so anyone who has the link can reach this. Whether the person looking may
*decide* is settled at click time by a `/pre` decision on `LoanDesk.Decide` —
enforced through the configured contextual access policy.

### `POST /api/approvals/{id}/decision`

```json
<- { "decision": "approved" | "denied", "note": string | null,
     "decided_by": "charlie@…" }

-> 200 { "request": <the record above, status now the decision> }
-> 404 { "error": "no approval request apr_…" }
```

Recording, not deciding. Whether the caller may decide — role, authority, and
requester ≠ approver — is answered before this request is ever made.

One thing the service adds that this contract does not require of it: a
decision is recorded only while the request is `pending`, and a second one
answers `409`. The pre-hook's "a decision is final" rule is the control that
normally stops it; refusing in the store as well means a `denied` cannot be
rewritten to `approved` even by something holding the bearer.

`decided_by` travels in the body, and that is worth being explicit about
because the loan tools instead send the actor in `X-Actor`. Here too the value is `context.user_id`, read inside the tool. It is not a tool argument.

## Local tests

```sh
uv sync --extra dev
uv run --extra dev pytest tests/approvals tests/test_isolation.py
```

The suite runs local HTTP fixtures for the store and Slack, checks routing,
message formatting, authentication and failure handling. See [LOANS.md](LOANS.md)
for enabling the real loan-service integrations. Attendees deploy through the
portal; instructors can use `arcade deploy` from `mcp/`.
