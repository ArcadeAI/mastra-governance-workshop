"""The approval request, as a Slack Block Kit message.

Pure and deterministic: `build_blocks` is a function of its argument and
nothing else — no clock, no randomness, no network — so the message a reviewer
reads in a test is byte-for-byte the message Slack renders. Posting lives next
door in `slack.py`.

Two properties this file exists to hold to.

**The message is complete.** It states who asked, for what, how much, which
policy rule was tripped, and the justification — everything the approver needs
to decide without going and asking. An approver who has to reply "what is
this?" is a demo that has failed at the moment it is trying to succeed.

**The link carries no authority.** It is a pointer to a request ID, nothing
more: no token, no signature, no capability. The requester can read the DM she
sent, so possession of the URL must not be the same as permission —
authorization happens at click time, in #19. `tests/test_message.py` asserts
the rendered payload against that, because a signed link is exactly the sort of
convenience that gets added back later by someone who does not know why it is
absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ApprovalMessage", "build_blocks", "build_fallback_text", "format_amount"]

#: Slack's own limits, applied here so a long justification cannot make
#: `chat.postMessage` fail with `invalid_blocks` at the worst possible moment.
_HEADER_LIMIT = 150
_SECTION_LIMIT = 3000


def format_amount(amount: float) -> str:
    """`95000` → `$95,000.00`. Fixed to two places so no amount is ambiguous."""
    return f"${amount:,.2f}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class ApprovalMessage:
    """Everything the message states. Assembled by the tool, rendered here."""

    request_id: str
    requester_display_name: str
    requester_id: str
    approver_display_name: str
    action: str
    resource_id: str
    amount: float
    justification: str
    #: The policy rule the blocked call tripped, in words the approver reads.
    rule_tripped: str
    #: Absolute URL of the approval page. Must contain the request id and
    #: nothing else that could be mistaken for authority.
    approval_url: str
    #: Everyone routing found sufficient, lowest clearance first, by display
    #: name. The approver is the first. The rest are who was deliberately not
    #: bothered, which is the point being demonstrated.
    candidate_display_names: tuple[str, ...] = ()


def build_fallback_text(message: ApprovalMessage) -> str:
    """The `text` field: notification previews and any client that cannot render blocks."""
    return (
        f"{message.requester_display_name} is asking you to approve "
        f"{message.action} on {message.resource_id} for "
        f"{format_amount(message.amount)}."
    )


def build_channel_text(message: ApprovalMessage) -> str:
    """The `text` of the channel post: who asked whom, for what, and where to decide.

    The room reads this; the approver decides on the signed-in page the link
    opens. There is deliberately nothing to press here.
    """
    return (
        f"{message.requester_display_name} asked {message.approver_display_name} to approve "
        f"{message.action} on {message.resource_id} for {format_amount(message.amount)}. "
        f"Decide at {message.approval_url}"
    )


def build_channel_blocks(message: ApprovalMessage) -> list[dict[str, Any]]:
    """The DM's blocks without the button: the room reads, the approver decides elsewhere.

    The approval link is in the text and in the context line, as a link, never
    as something to press: nothing in the channel is an authority.
    """
    return [block for block in build_blocks(message) if block["type"] != "actions"]


def build_blocks(message: ApprovalMessage) -> list[dict[str, Any]]:
    """Render the request as Block Kit. Deterministic; safe to snapshot."""
    not_asked = message.candidate_display_names[1:]
    routing_note = (
        f"Routed to {message.approver_display_name}: the lowest approval authority "
        f"sufficient for {format_amount(message.amount)}."
    )
    if not_asked:
        routing_note += " Not asked: " + ", ".join(not_asked) + "."

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": _truncate("Approval requested", _HEADER_LIMIT),
                "emoji": False,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Requester*\n{message.requester_display_name} "
                    f"({message.requester_id})",
                },
                {"type": "mrkdwn", "text": f"*Action*\n`{message.action}`"},
                {"type": "mrkdwn", "text": f"*Resource*\n{message.resource_id}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{format_amount(message.amount)}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(f"*Policy rule tripped*\n{message.rule_tripped}", _SECTION_LIMIT),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(
                    f"*Justification given*\n{message.justification}", _SECTION_LIMIT
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Review this request", "emoji": False},
                    # No `style: primary` — the button opens a page, it does not
                    # approve anything, and nothing in this message should read
                    # as the approve action itself.
                    "url": message.approval_url,
                    "action_id": "open_approval_page",
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Request `{message.request_id}` · {routing_note}"},
                {
                    "type": "mrkdwn",
                    "text": "This link identifies the request. It does not authorise "
                    "anything — you will be checked when you decide.",
                },
            ],
        },
    ]
