"""The approvals toolkit: escalate a blocked call to the one human who can decide it.

A Python `arcade-mcp` toolkit, like its sibling `mcp/loan_desk/loans.py`: outside the Bun
workspaces and outside the app's Docker image, shipped with `arcade deploy`. Python
because `arcade-mcp`, the framework the agent's tools are authored in, is
Python-only — the boundary is tool authoring, not domain.

`request_approval` is what the agent reaches for after the pre-hook denies it.
The hook's own remediation message is what sends it here; the system prompt says
nothing about approvals, because a control the model is *asked* to respect is
not a control.

Three things this toolkit does not do, each on purpose:

**It does not choose the approver.** Routing is deterministic — lowest
sufficient clearance, requester excluded — and lives in `routing.py`, checked
row for row against the TypeScript in `gate/engine` (#9). A model
that could name its own approver could name a friendly one.

**It does not decide anything.** `decide` records an outcome. Whether the
caller may decide is a `/pre` question about `LoanDesk.Decide` — role, limit,
and requester ≠ approver — answered by `apps/hooks` before this code runs, and
enforced in #19.

**It does not hand out authority.** The link in the Slack message is a pointer
to a request id: no token, no signature, no capability. The requester can read
the post she sent, so possession of the URL must not be permission.

**It does not reach the approvers itself.** Since 2026-10-17 night the store
posts the approver's copy to the approvers' channel as the workshop bot when it
records a Slack-routed request, and stamps the outcome on the record as
`approvers_post`; the requester is not a member of that channel, so a post with
her own token answered `not_in_channel`. The tool posts the room's copy, in the
requester's own workshop channel, with her token, and the approvers' copy too
only when the store says the attendees approve (`by: requester`).

`MCPApp(name="approvals")` below is what `arcade deploy` reads off `initialize`
and PascalCases into the toolkit name, so these are `LoanDesk.RequestApproval`
and `LoanDesk.Decide`. Measured on `mcp/loan_desk/loans.py`, derived here — `.env.example`
pins `ARCADE_TOOLKIT=Approvals` and #35 confirms it off the workers
API after the first deploy. A policy rule keyed on the wrong string matches
nothing, which is indistinguishable from a rule that permits.
"""

from enum import Enum
from typing import Annotated, Any

from arcade_core.errors import ToolExecutionError
from arcade_mcp_server import Context
from arcade_mcp_server.auth import Slack

from loan_desk.approvals_message import (
    ApprovalMessage,
    build_blocks,
    build_channel_blocks,
    build_channel_text,
    build_fallback_text,
    format_amount,
)
from loan_desk.approvals_routing import RoutingResult, Subject, route_approval
from loan_desk.approvals_slack import (
    SLACK_SCOPES,
    SlackError,
    post_message,
)
from loan_desk.approvals_store import (
    APPROVALS_STORE_TOKEN_SECRET,
    APP_HOST_SECRET,
    base_url,
    create_request,
    fetch_roster,
    record_decision,
)

__all__ = [
    "APPROVALS_STORE_TOKEN_SECRET",
    "APP_HOST_SECRET",
    "SLACK_SCOPES",
    "Decision",
    "app",
    "approval_url",
    "decide",
    "describe_rule",
    "request_approval",
]

from loan_desk.app import app

# The stock Slack provider, and four scopes rather than three: `users:read` is
# a prerequisite for `users:read.email`, and Slack refuses the authorize
# request outright without it (spike #3). The token this grants is the
# requester's own, so the DM arrives under her name — there is no bot here.
# Arcade routes this built-in provider through its own user verifier, not the
# app's custom one, so the requester must be a member of the Arcade project
# (measured by the human on 2026-09-26, undocumented by Arcade; DESIGN.md →
# Slack and Arcade accounts).
#
# This requirement is a credential check, not the governance gate. Arcade
# evaluates it *before* the `/pre` hook, so a refusal fires no hook, writes no
# audit row and shows nothing on the panel: rehearse the Slack authorization
# rather than discovering it on stage.
_requires_slack = Slack(scopes=SLACK_SCOPES)
# Both tools read the same two since #6: the app's host, where the store and the
# approval page both live, and the store's bearer.
_secrets = [APP_HOST_SECRET, APPROVALS_STORE_TOKEN_SECRET]

#: The Slack channels an approval request reaches. The approvals store stamps
#: each request with the requester's chosen route (`channel` on the record: a
#: `#channel` for Slack, otherwise `email` or `facilitator`), their workshop
#: channel (`slack_channel`) and the approvers' channel (`approvals_channel`,
#: `#approvals` unless the store says otherwise), so the tool needs no secret
#: for any of them. On the Slack route the store itself posts the approver's
#: copy to the approvers' channel as the workshop bot and stamps the outcome as
#: `approvers_post` (until 2026-10-17 night the tool posted it as the requester,
#: and before that it was a DM); with "Let attendees approve" on the attendees
#: sit in that channel, the stamp says `by: requester`, and the tool posts it
#: with her token after all. Otherwise the store has sent the mail, or the
#: queue is the dashboard. The workshop channel gets the room's copy on every
#: route, posted here as the requester. The approver still decides on the
#: signed-in approval page: nothing in Slack is an authority, and authority here
#: is the IdP sign-in.
_request_secrets = _secrets
_DEFAULT_APPROVALS_CHANNEL = "#approvals"


class Decision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


def approval_url(app_host: str, request_id: str) -> str:
    """The approval page for one request. The id, and nothing else.

    No query string, because a query string is where a token arrives. #19
    authorises the clicker at click time; this URL identifies the request and
    says nothing about who may act on it.
    """
    return f"{base_url(app_host)}/approvals/{request_id}"


def describe_rule(
    rule: dict[str, Any] | None, requester: Subject | None, action: str, amount: float
) -> str:
    """The policy rule that was tripped, in words the approver reads.

    `rule` rides on the approval record itself, so the DM and #19's page cannot
    answer the question differently. The control plane names it when it can. When it cannot, this says the thing
    the toolkit does know for certain from the roster it just routed against —
    the requester's authority, and that the amount exceeded it. A message that
    left this blank would be a message the approver has to go and ask about,
    which is the failure the deterministic format exists to prevent.
    """
    if rule and rule.get("description"):
        described = str(rule["description"])
        rule_id = rule.get("id")
        return f"{described} (`{rule_id}`)" if rule_id else described
    if requester is not None:
        return (
            f"{action} for {format_amount(amount)} exceeds "
            f"{requester.display_name}'s approval authority of "
            f"{format_amount(requester.clearance)}."
        )
    return (
        f"{action} for {format_amount(amount)} exceeded the requester's approval "
        "authority. The control plane did not name the rule."
    )


@app.tool(requires_auth=_requires_slack, requires_secrets=_request_secrets)
async def request_approval(
    context: Context,
    action: Annotated[
        str,
        "The action the approval would cover — for example approve_loan.",
    ],
    resource_id: Annotated[
        str, "What the action would act on, such as a loan applications ID."
    ],
    amount: Annotated[
        float,
        "The amount the approval would cover, in US dollars.",
    ],
    justification: Annotated[
        str,
        "The case for the action. The approver reads it verbatim.",
    ],
) -> Annotated[
    dict[str, Any],
    "The request ID and who was notified.",
]:
    """Records a request for one person's approval of an action on a resource, and notifies the approver it routes to. Returns the request ID and who was notified."""
    requester_id = context.user_id or ""
    if not requester_id:
        raise ToolExecutionError(
            "No identity was supplied for this call, so there is nobody to escalate on "
            "behalf of and nobody to exclude from approving it.",
            developer_message="context.user_id was empty on request_approval.",
        )

    app_host = context.get_secret(APP_HOST_SECRET)
    store_token = context.get_secret(APPROVALS_STORE_TOKEN_SECRET)

    roster = await fetch_roster(app_host, store_token)
    try:
        routed: RoutingResult = route_approval(amount, requester_id, roster)
    except ValueError as exc:
        raise ToolExecutionError(
            f"{amount!r} is not an amount that can be routed for approval.",
            developer_message=str(exc),
        ) from exc

    if routed.approver is None:
        # An ordinary outcome of the rule, and an error for the caller: nothing
        # was recorded and nobody was messaged, so the agent must not be able to
        # read this as "requested". Deliberately not a fallback to the highest
        # authority — routing that quietly escalates past its own rule is a
        # control that does nothing.
        raise ToolExecutionError(
            f"Nobody holds authority sufficient to approve {action} for "
            f"{format_amount(amount)}, so no approval was requested.",
            developer_message=(
                f"route_approval: no eligible approver for amount={amount} "
                f"requester={requester_id} roster={len(roster)}"
            ),
        )

    approver = routed.approver
    requester = next((s for s in roster if s.user_id == requester_id), None)

    created = await create_request(
        app_host,
        store_token,
        {
            "requester_id": requester_id,
            "action": action,
            "resource_id": resource_id,
            "amount": amount,
            "justification": justification,
            "approver_id": approver.user_id,
            "candidate_approver_ids": [s.user_id for s in routed.candidates],
            "required_clearance": routed.required_clearance,
        },
    )
    record = created.get("request") or {}
    request_id = record.get("id")
    if not request_id:
        raise ToolExecutionError(
            "The approval request could not be recorded, so nobody was asked.",
            developer_message=f"POST /approvals returned no request id: {created!r}",
        )

    message = ApprovalMessage(
        request_id=str(request_id),
        requester_display_name=requester.display_name if requester else requester_id,
        requester_id=requester_id,
        approver_display_name=approver.display_name or approver.user_id,
        action=action,
        resource_id=resource_id,
        amount=amount,
        justification=justification,
        rule_tripped=describe_rule(record.get("rule"), requester, action, amount),
        approval_url=approval_url(app_host, str(request_id)),
        candidate_display_names=tuple(
            s.display_name or s.user_id for s in routed.candidates
        ),
    )

    route = str(record.get("channel") or "facilitator")
    dm_route = route.startswith("#")
    channel = _workshop_channel(record)
    approvers_channel = _approvers_channel(record)
    base = {
        "request_id": str(request_id),
        "status": str(record.get("status", "pending")),
        "approver": approver.user_id,
        "approver_display_name": approver.display_name or approver.user_id,
        "required_clearance": routed.required_clearance,
        "candidate_approvers": [s.user_id for s in routed.candidates],
        "approval_url": message.approval_url,
        "notice": "slack" if dm_route else route,
    }
    if not dm_route and not channel:
        # Email or the instructor queue, and no workshop channel to announce in:
        # the store has sent the notice (the mail) or the queue is the dashboard.
        return base

    slack_token = context.get_auth_token_or_empty()
    if dm_route:
        # The approvers' copy is normally the store's: posted as the workshop bot
        # when the request was recorded, outcome on the record. With "Let
        # attendees approve" on, the attendees sit in that channel and the store
        # stamps `by: requester`: then it is posted here, with her token, like
        # the room's copy. Either way a failure is the one thing the agent must
        # not read as "requested": the record exists and the routing stands,
        # but nobody has been told.
        post = record.get("approvers_post") if isinstance(record.get("approvers_post"), dict) else {}
        target = str(post.get("channel") or approvers_channel)
        # The approvers' channel is private (since 2026-10-17 evening), and Slack takes a private
        # channel's id, not its name: the store stamps the id next to the name when it knows it.
        post_to = str(post.get("id") or target)
        failure = str(post.get("error") or "")
        if post.get("by") == "requester":
            try:
                posted = await post_message(
                    slack_token, post_to, build_fallback_text(message), build_blocks(message)
                )
            except SlackError as exc:
                raise ToolExecutionError(
                    f"Approval request {request_id} was recorded and routed to "
                    f"{approver.display_name or 'the routed approver'}, but Slack method "
                    f"{exc.method} failed with error code {exc.error}; the notice was not "
                    "delivered. Do not retry this approval request: retrying would create a "
                    "duplicate. Stop and capture the method and error code for an administrator.",
                    developer_message=(
                        f"{exc.diagnostic_message()}; approval request {request_id} was "
                        "recorded and routed, but notice delivery failed; do not retry."
                    ),
                ) from exc
            base["approvers_channel"] = target
            base["approvers_post_ts"] = posted["ts"]
        elif failure:
            raise ToolExecutionError(
                f"Approval request {request_id} was recorded and routed to "
                f"{approver.display_name or 'the routed approver'}, but the notice to "
                f"{target} failed with error code {failure}; "
                "the notice was not delivered. Do not retry this approval request: retrying "
                "would create a duplicate. Stop and capture the error code for an administrator.",
                developer_message=(
                    f"approvers_post failed with {failure}; approval request {request_id} was "
                    "recorded and routed, but notice delivery failed; do not retry."
                ),
            )
        else:
            base["approvers_channel"] = target
            if post.get("ts"):
                base["approvers_post_ts"] = str(post["ts"])

    # The room's copy, on every route. Posted as the requester, with her token,
    # in her own workshop channel, so it needs no bot and no extra scope; a
    # refusal is reported, not raised: the request is recorded and the approvers
    # have been told, and that is the notice.
    if channel:
        try:
            announced = await post_message(
                slack_token, channel, build_channel_text(message), build_channel_blocks(message)
            )
            base.update({"slack_channel": channel, "slack_channel_ts": announced["ts"]})
        except SlackError as exc:
            base.update({"slack_channel": channel, "slack_channel_error": exc.error})
    return base


def _workshop_channel(record: dict[str, Any]) -> str:
    """The requester's workshop channel to announce in, or empty when they have none."""
    channel = str(record.get("slack_channel") or "").strip()
    return channel if channel.startswith("#") else ""


def _approvers_channel(record: dict[str, Any]) -> str:
    """The channel the approvers sit in, as the store stamps it; `#approvals` when it does not."""
    channel = str(record.get("approvals_channel") or "").strip()
    return channel if channel.startswith("#") else _DEFAULT_APPROVALS_CHANNEL


@app.tool(requires_secrets=_secrets)
async def decide(
    context: Context,
    request_id: Annotated[str, "The approval request being answered."],
    decision: Annotated[Decision, "The answer."],
    note: Annotated[
        str | None, "An optional note recorded with the decision."
    ] = None,
) -> Annotated[dict[str, Any], "The approval request as it stands after the decision."]:
    """Records an approver's answer against an approval request."""
    # No auth requirement on purpose. An OAuth requirement is evaluated *before*
    # the `/pre` hook, so a refusal there fires no hook, writes no audit row and
    # shows nothing on the panel — and the whole point of this tool is that its
    # refusal is visible. Identity comes from `context.user_id`, which Arcade
    # supplies and the model cannot write; authority is a `/pre` decision on
    # `LoanDesk.Decide`, enforced in #19.
    decided_by = context.user_id or ""
    if not decided_by:
        raise ToolExecutionError(
            "No identity was supplied for this call, so there is nobody to record as "
            "having decided.",
            developer_message="context.user_id was empty on decide.",
        )

    updated = await record_decision(
        context.get_secret(APP_HOST_SECRET),
        context.get_secret(APPROVALS_STORE_TOKEN_SECRET),
        request_id,
        {
            "decision": decision.value,
            "note": note,
            "decided_by": decided_by,
        },
    )
    return updated.get("request") or updated
